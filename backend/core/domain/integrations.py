import csv
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import secrets
import socket
import ssl
from datetime import timedelta
from urllib.parse import urlparse
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from core.models import ImportBatch, Product, Webhook, Delivery
from core.identity import encrypt, decrypt
from .common import member, require, record, get
from .catalog import create


def public_addresses(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.port not in [None, 443]:
        raise ValidationError('Webhook URL must use HTTPS on port 443 without credentials or a fragment.')
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)})
    except OSError:
        raise ValidationError('Webhook hostname could not be resolved.')
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValidationError('Webhook destinations must resolve only to public IP addresses.')
    return parsed, addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address
    def connect(self):
        # Pin the validated address while retaining hostname certificate verification/SNI.
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def send_webhook(url, secret, event):
    parsed, addresses = public_addresses(url)  # Revalidate on every delivery, including retries.
    body = json.dumps({'id': str(event.event_id), 'type': event.topic, 'created_at': event.created_at.isoformat(), 'data': event.payload}, separators=(',', ':')).encode()
    timestamp = str(int(timezone.now().timestamp()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    connection = PinnedHTTPSConnection(parsed.hostname, addresses[0])
    try:
        connection.request('POST', parsed.path or '/' if not parsed.query else (parsed.path or '/') + '?' + parsed.query,
                           body, {'Content-Type': 'application/json', 'X-OSSCM-Event': str(event.event_id),
                                  'X-OSSCM-Timestamp': timestamp, 'X-OSSCM-Signature': 'sha256=' + signature})
        response = connection.getresponse()
        code = response.status
        response.read(1024)
        return code  # Redirects are deliberately not followed.
    finally:
        connection.close()


@transaction.atomic
def webhook_create(actor, data):
    require(actor)
    public_addresses(data['url'])
    secret = secrets.token_urlsafe(32)
    hook = Webhook.objects.create(organization=member(actor).organization, url=data['url'], secret=encrypt(secret))
    record(actor, 'integrations.webhook_created', hook)
    return {'id': hook.pk, 'secret': secret}


@transaction.atomic
def webhook_update(actor, data):
    require(actor)
    hook = get(actor, Webhook, data['webhook'], lock=True)
    hook.active = bool(data['active'])
    hook.save(update_fields=['active'])
    record(actor, 'integrations.webhook_updated', hook, {'active': hook.active})
    return {'id': hook.pk, 'active': hook.active}


@transaction.atomic
def replay(actor, data):
    require(actor)
    delivery = get(actor, Delivery, data['delivery'], lock=True)
    delivery.status = 'pending'
    delivery.attempts = 0
    delivery.next_attempt = timezone.now()
    delivery.save(update_fields=['status', 'attempts', 'next_attempt'])
    record(actor, 'integrations.delivery_replayed', delivery)
    return {'id': delivery.pk, 'event_id': str(delivery.event.event_id)}


@transaction.atomic
def preview_import(actor, data):
    require(actor, 'purchasing')
    source = data.get('csv', '')
    if not isinstance(source, str) or len(source.encode()) > 1024 * 1024:
        raise ValidationError('CSV must be at most 1 MiB.')
    reader = csv.DictReader(io.StringIO(source.lstrip('\ufeff')))
    if not reader.fieldnames or not {'sku', 'name'} <= set(reader.fieldnames):
        raise ValidationError('CSV requires sku and name headers.')
    rows, errors, seen = [], [], set()
    for number, row in enumerate(reader, 2):
        if number > 5001:
            raise ValidationError('Maximum 5,000 products per import.')
        if None in row:
            errors.append({'row': number, 'detail': 'More values than headers.'})
            continue
        values = {k: (v or '').strip() for k, v in row.items() if k in ['sku', 'name', 'unit', 'purchase_unit', 'purchase_factor', 'tracking'] and v}
        product = Product(organization=member(actor).organization, **values)
        try:
            product.full_clean()
            if product.sku in seen:
                raise ValueError('Duplicate SKU within file.')
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError
            if not isinstance(exc, (DjangoValidationError, ValueError)):
                raise
            errors.append({'row': number, 'detail': str(exc)})
        seen.add(product.sku)
        rows.append(values)
    if not rows:
        errors.append({'row': 1, 'detail': 'No data rows.'})
    batch = ImportBatch.objects.create(organization=member(actor).organization, actor=actor, rows=rows, errors=errors)
    record(actor, 'integrations.import_previewed', batch, {'rows': len(rows), 'errors': len(errors)})
    return {'id': batch.pk, 'rows': rows, 'errors': errors}


@transaction.atomic
def commit_import(actor, data):
    require(actor, 'purchasing')
    batch = get(actor, ImportBatch, data['batch'], lock=True)
    if batch.errors or batch.committed_at:
        raise ValidationError('Import has errors or was already committed.')
    # Revalidate inside the transaction: concurrent imports cannot silently overwrite SKUs.
    for row in batch.rows:
        create(actor, 'products', row)
    batch.committed_at = timezone.now()
    batch.save(update_fields=['committed_at'])
    record(actor, 'integrations.import_committed', batch, {'rows': len(batch.rows)})
    return {'id': batch.pk, 'imported': len(batch.rows)}
