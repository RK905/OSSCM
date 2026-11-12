from datetime import date
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from core.models import Product, StockIdentity, StockBalance, Movement, Bin, Transfer
from .common import amount, get, member, require, record, warehouse_access


def product_lock(actor, product_id):
    return get(actor, Product, product_id, lock=True)


def identity_for(actor, product, code='', expires_on=None):
    code = str(code or '').strip()
    if len(code) > 100:
        raise ValidationError('Tracking code is too long.')
    if (product.tracking == 'none' and (code or expires_on)) or (product.tracking != 'none' and not code):
        raise ValidationError('Tracking identity does not match the product tracking mode.')
    try:
        expiry = date.fromisoformat(str(expires_on)) if expires_on else None
    except ValueError:
        raise ValidationError('Invalid expiry date.')
    identity, created = StockIdentity.objects.get_or_create(organization=member(actor).organization, product=product, code=code, defaults={'expires_on': expiry})
    if not created and expiry is not None and identity.expires_on != expiry:
        raise ValidationError('Expiry must match the existing lot or serial.')
    return identity


def balance_for(actor, identity, bin, state='available'):
    warehouse_access(actor, bin.warehouse)
    obj, _ = StockBalance.objects.get_or_create(organization=member(actor).organization, identity=identity, bin=bin, state=state)
    return StockBalance.objects.select_for_update().get(pk=obj.pk)


def post(actor, balance, delta=0, reserved_delta=0, kind='adjustment', **links):
    delta, reserved_delta = amount(delta, False), amount(reserved_delta, False)
    balance.refresh_from_db()
    new_hand = balance.on_hand + delta
    new_reserved = balance.reserved + reserved_delta
    if new_hand < 0 or new_reserved < 0 or new_reserved > new_hand:
        raise ValidationError('Insufficient available inventory.')
    product = balance.identity.product
    if balance.state != 'available' and new_reserved:
        raise ValidationError('Only available stock may be reserved.')
    if product.tracking == 'serial':
        if delta != delta.to_integral_value() or reserved_delta != reserved_delta.to_integral_value():
            raise ValidationError('Serial quantities must be whole units.')
        total = StockBalance.objects.filter(identity=balance.identity).aggregate(n=Sum('on_hand'))['n'] or Decimal(0)
        if total + delta > 1:
            raise ValidationError('Serial number is already in stock.')
    balance.on_hand, balance.reserved = new_hand, new_reserved
    balance.save(update_fields=['on_hand', 'reserved'])
    movement = Movement.objects.create(organization=member(actor).organization, actor=actor, balance=balance,
                                      quantity=delta, reserved_delta=reserved_delta, kind=kind, **links)
    record(actor, f'inventory.{kind}', movement, {'balance_id': balance.pk, 'quantity': str(delta), 'reserved_delta': str(reserved_delta)})
    return movement


def ensure_usable(balance):
    if balance.state != 'available' or (balance.identity.expires_on and balance.identity.expires_on < timezone.localdate()):
        raise ValidationError('Stock is quarantined, in transit, or expired.')


@transaction.atomic
def adjust(actor, data):
    require(actor, 'warehouse')
    product = product_lock(actor, data['product'])
    bin = get(actor, Bin, data['bin'])
    identity = identity_for(actor, product, data.get('code'), data.get('expires_on'))
    state = data.get('state', 'available')
    if state not in ['available', 'quarantine']:
        raise ValidationError('Adjustments cannot change in-transit stock.')
    balance = balance_for(actor, identity, bin, state)
    reason = str(data.get('reason', '')).strip()
    if not reason or len(reason) > 500:
        raise ValidationError('A reason of 1–500 characters is required.')
    delta = amount(data['quantity'], False)
    if data.get('count', False):
        if delta < 0:
            raise ValidationError('Count cannot be negative.')
        delta -= balance.on_hand
    movement = post(actor, balance, delta, kind='count' if data.get('count') else 'adjustment', reason=reason)
    return {'id': movement.pk, 'balance': balance.pk}


@transaction.atomic
def reverse(actor, data):
    require(actor, 'warehouse')
    original = get(actor, Movement, data['movement'])
    warehouse_access(actor, original.balance.bin.warehouse)
    product_lock(actor, original.balance.identity.product_id)
    if original.kind not in ['adjustment', 'count'] or Movement.objects.filter(source_movement=original, kind='reversal').exists():
        raise ValidationError('Only an unreversed adjustment/count can be reversed; use returns for receipts/shipments.')
    reason = str(data.get('reason', '')).strip()
    if not reason:
        raise ValidationError('Reversal reason is required.')
    balance = get(actor, StockBalance, original.balance_id, lock=True)
    movement = post(actor, balance, -original.quantity, kind='reversal', source_movement=original, reason=reason)
    return {'id': movement.pk}


@transaction.atomic
def transfer_send(actor, data):
    require(actor, 'warehouse')
    source = get(actor, StockBalance, data['balance'])
    product_lock(actor, source.identity.product_id)
    source = get(actor, StockBalance, source.pk, lock=True)
    ensure_usable(source)
    destination = get(actor, Bin, data['destination'])
    if source.bin_id == destination.pk:
        raise ValidationError('Choose a different destination bin.')
    qty = amount(data['quantity'])
    transfer = Transfer.objects.create(organization=member(actor).organization, source=source, destination=destination, quantity=qty)
    transit = balance_for(actor, source.identity, source.bin, 'transit')
    post(actor, source, -qty, kind='transfer_send', transfer=transfer)
    post(actor, transit, qty, kind='transfer_transit', transfer=transfer)
    return {'id': transfer.pk}


@transaction.atomic
def transfer_receive(actor, data):
    require(actor, 'warehouse')
    transfer = get(actor, Transfer, data['transfer'], lock=True)
    warehouse_access(actor, transfer.source.bin.warehouse)
    warehouse_access(actor, transfer.destination.warehouse)
    product_lock(actor, transfer.source.identity.product_id)
    qty = amount(data['quantity'])
    if qty > transfer.quantity - transfer.received:
        raise ValidationError('Receipt exceeds outstanding transfer quantity.')
    transit = balance_for(actor, transfer.source.identity, transfer.source.bin, 'transit')
    destination = balance_for(actor, transfer.source.identity, transfer.destination)
    post(actor, transit, -qty, kind='transfer_unload', transfer=transfer)
    post(actor, destination, qty, kind='transfer_receive', transfer=transfer)
    transfer.received += qty
    transfer.save(update_fields=['received'])
    return {'id': transfer.pk, 'received': str(transfer.received)}


@transaction.atomic
def disposition(actor, data):
    require(actor, 'warehouse')
    source = get(actor, StockBalance, data['balance'])
    product_lock(actor, source.identity.product_id)
    source = get(actor, StockBalance, source.pk, lock=True)
    if source.state != 'quarantine':
        raise ValidationError('Disposition requires quarantined stock.')
    qty = amount(data['quantity'])
    action = data['disposition']
    if action not in ['release', 'scrap']:
        raise ValidationError('Choose release or scrap.')
    if not data.get('reason'):
        raise ValidationError('Disposition reason is required.')
    if action == 'release' and source.identity.expires_on and source.identity.expires_on < timezone.localdate():
        raise ValidationError('Expired stock cannot be released.')
    post(actor, source, -qty, kind='disposition', reason=data['reason'])
    if action == 'release':
        target = balance_for(actor, source.identity, source.bin)
        post(actor, target, qty, kind='release', reason=data['reason'])
    return {'id': source.pk, 'disposition': action}
