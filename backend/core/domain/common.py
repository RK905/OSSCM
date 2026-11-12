import hashlib
import json
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from core.models import AuditEvent, OutboxEvent, Delivery, Webhook, Idempotency, Membership


def amount(value, positive=True):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or abs(n) >= Decimal('100000000000000') or n.as_tuple().exponent < -4:
            raise ValueError()
        if positive and n <= 0:
            raise ValueError()
        return n
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Quantity must be a finite decimal with at most four decimal places and be positive where required.')


def member(actor):
    try:
        return actor.membership
    except Membership.DoesNotExist:
        raise PermissionDenied('No organization membership.')


def require(actor, *roles):
    m = member(actor)
    if m.role != 'administrator' and m.role not in roles:
        raise PermissionDenied('Your role does not allow this action.')
    return m


def warehouse_access(actor, warehouse):
    m = member(actor)
    if warehouse.organization_id != m.organization_id or (m.role != 'administrator' and not m.warehouses.filter(pk=warehouse.pk).exists()):
        raise PermissionDenied('Warehouse access denied.')


def get(actor, model, pk, lock=False):
    qs = model.objects.filter(organization_id=member(actor).organization_id)
    if lock:
        qs = qs.select_for_update()
    try:
        obj = qs.get(pk=pk)
    except (model.DoesNotExist, ValueError, TypeError):
        raise ValidationError(f'{model.__name__} not found.')
    warehouse = getattr(obj, 'warehouse', None)
    if warehouse is None and hasattr(obj, 'bin'):
        warehouse = obj.bin.warehouse
    if warehouse is None and hasattr(obj, 'document') and obj.document:
        warehouse = obj.document.warehouse
    if warehouse:
        warehouse_access(actor, warehouse)
    return obj


def record(actor, action, obj, data=None):
    organization = member(actor).organization
    entity = f'{obj.__class__.__name__}:{obj.pk}'
    payload = {'entity': entity, **(data or {})}
    AuditEvent.objects.create(organization=organization, actor=actor, action=action, entity=entity, data=payload)
    event = OutboxEvent.objects.create(organization=organization, topic=action, payload=payload)
    Delivery.objects.bulk_create([Delivery(organization=organization, event=event, webhook=w, next_attempt=timezone.now())
                                 for w in Webhook.objects.filter(organization=organization, active=True)])


@transaction.atomic
def execute(actor, key, action, payload, callback):
    if not key or len(key) > 128:
        raise ValidationError('A nonempty Idempotency-Key header of at most 128 characters is required.')
    # Serialize keys per actor; domain locks additionally protect stock across actors.
    Membership.objects.select_for_update().get(user=actor)
    fingerprint = hashlib.sha256(json.dumps([action, payload], sort_keys=True, default=str).encode()).hexdigest()
    prior = Idempotency.objects.filter(actor=actor, key=key).first()
    if prior:
        if prior.fingerprint != fingerprint:
            raise ValidationError('Idempotency key was already used with different input.')
        return prior.response
    response = callback()
    response = json.loads(json.dumps(response, default=str))
    Idempotency.objects.create(organization=member(actor).organization, actor=actor, key=key, fingerprint=fingerprint, response={k: v for k, v in response.items() if k not in ['token', 'secret']})
    return response
