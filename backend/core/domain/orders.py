from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from core.models import Document, DocumentLine, Product, Partner, Warehouse, Bin, StockBalance, Reservation, Shipment, Movement
from .common import amount, get, member, require, record, warehouse_access
from .inventory import product_lock, identity_for, balance_for, post, ensure_usable


@transaction.atomic
def create_document(actor, data):
    kind = data['kind']
    if kind not in ['purchase', 'sale']:
        raise ValidationError('Invalid order kind.')
    require(actor, 'purchasing' if kind == 'purchase' else 'sales')
    warehouse = get(actor, Warehouse, data['warehouse'])
    warehouse_access(actor, warehouse)
    partner = get(actor, Partner, data['partner'])
    if partner.kind != ('supplier' if kind == 'purchase' else 'customer'):
        raise ValidationError('Partner type does not match order kind.')
    if not data.get('lines') or len(data['lines']) > 200:
        raise ValidationError('An order must contain 1–200 lines.')
    document = Document(organization=member(actor).organization, kind=kind, reference=data['reference'], partner=partner,
                        warehouse=warehouse, due_date=data.get('due_date') or None, notes=data.get('notes', ''))
    document.full_clean()
    document.save()
    for entry in data['lines']:
        product = get(actor, Product, entry['product'])
        if not product.active:
            raise ValidationError('Product is inactive.')
        factor = product.purchase_factor if kind == 'purchase' and entry.get('purchase_units', False) else Decimal(1)
        qty = amount(amount(entry['quantity']) * factor)
        if product.tracking == 'serial' and qty != qty.to_integral_value():
            raise ValidationError('Serialized items must have whole quantities.')
        DocumentLine.objects.create(organization=document.organization, document=document, product=product, quantity=qty, unit_factor=factor)
    record(actor, f'{kind}.created', document)
    return {'id': document.pk, 'reference': document.reference}


def update_status(document):
    lines = list(document.lines.all())
    if all(x.processed + x.cancelled == x.quantity for x in lines):
        document.status = 'complete' if any(x.processed for x in lines) else 'cancelled'
    elif any(x.processed for x in lines):
        document.status = 'partial'
    else:
        document.status = 'approved'
    document.save(update_fields=['status'])


@transaction.atomic
def approve(actor, data):
    document = get(actor, Document, data['document'], lock=True)
    require(actor, 'purchasing' if document.kind == 'purchase' else 'sales')
    if document.status != 'draft':
        raise ValidationError('Only draft orders can be approved.')
    document.status = 'approved'
    document.approved_at = timezone.now()
    document.save(update_fields=['status', 'approved_at'])
    record(actor, f'{document.kind}.approved', document)
    return {'id': document.pk, 'status': document.status}


def locked_line(actor, pk, kind):
    initial = get(actor, DocumentLine, pk)
    document = get(actor, Document, initial.document_id, lock=True)
    line = get(actor, DocumentLine, pk, lock=True)
    if document.kind != kind or document.status not in ['approved', 'partial']:
        raise ValidationError('Order is not open for this operation.')
    product_lock(actor, line.product_id)
    return document, line


@transaction.atomic
def receive(actor, data):
    require(actor, 'warehouse', 'purchasing')
    document, line = locked_line(actor, data['line'], 'purchase')
    qty = amount(data['quantity'])
    if data.get('purchase_units'):
        qty = amount(qty * line.unit_factor)
    if qty > line.quantity - line.processed - line.cancelled:
        raise ValidationError('Receipt exceeds outstanding order quantity.')
    bin = get(actor, Bin, data['bin'])
    if bin.warehouse_id != document.warehouse_id:
        raise ValidationError('Receipt bin is outside the order warehouse.')
    identity = identity_for(actor, line.product, data.get('code'), data.get('expires_on'))
    state = 'quarantine' if data.get('quarantine') or (identity.expires_on and identity.expires_on < timezone.localdate()) else 'available'
    balance = balance_for(actor, identity, bin, state)
    movement = post(actor, balance, qty, kind='receipt', line=line)
    line.processed += qty
    line.save(update_fields=['processed'])
    update_status(document)
    return {'id': movement.pk, 'balance': balance.pk, 'status': document.status}


@transaction.atomic
def allocate(actor, data):
    require(actor, 'sales', 'warehouse')
    document, line = locked_line(actor, data['line'], 'sale')
    balance = get(actor, StockBalance, data['balance'], lock=True)
    ensure_usable(balance)
    if balance.bin.warehouse_id != document.warehouse_id or balance.identity.product_id != line.product_id:
        raise ValidationError('Stock does not match the order product and warehouse.')
    qty = amount(data['quantity'])
    reserved = Reservation.objects.filter(line=line).aggregate(n=Sum('quantity'))['n'] or 0
    if qty > line.quantity - line.processed - line.cancelled - reserved:
        raise ValidationError('Allocation exceeds unallocated order quantity.')
    reservation, _ = Reservation.objects.get_or_create(organization=document.organization, line=line, balance=balance)
    post(actor, balance, reserved_delta=qty, kind='allocation', line=line)
    reservation.quantity += qty
    reservation.save(update_fields=['quantity'])
    return {'id': reservation.pk, 'quantity': str(reservation.quantity)}


@transaction.atomic
def ship(actor, data):
    require(actor, 'warehouse')
    document, line = locked_line(actor, data['line'], 'sale')
    reservation = get(actor, Reservation, data['reservation'], lock=True)
    if reservation.line_id != line.pk:
        raise ValidationError('Reservation belongs to another order line.')
    balance = get(actor, StockBalance, reservation.balance_id, lock=True)
    ensure_usable(balance)
    qty = amount(data['quantity'])
    if qty > reservation.quantity:
        raise ValidationError('Shipment exceeds reserved quantity.')
    shipment = Shipment(organization=document.organization, document=document, carrier=data.get('carrier', ''), tracking=data.get('tracking', ''))
    shipment.full_clean()
    shipment.save()
    movement = post(actor, balance, -qty, -qty, kind='shipment', line=line, shipment=shipment)
    reservation.quantity -= qty
    reservation.save(update_fields=['quantity'])
    line.processed += qty
    line.save(update_fields=['processed'])
    update_status(document)
    return {'id': shipment.pk, 'movement': movement.pk, 'status': document.status}


@transaction.atomic
def cancel(actor, data):
    document = get(actor, Document, data['document'], lock=True)
    require(actor, 'purchasing' if document.kind == 'purchase' else 'sales')
    if document.status in ['complete', 'cancelled']:
        raise ValidationError('Order has no outstanding quantity.')
    for line in document.lines.select_for_update().order_by('product_id', 'id'):
        product_lock(actor, line.product_id)
        for reservation in Reservation.objects.filter(line=line, quantity__gt=0).select_for_update():
            balance = get(actor, StockBalance, reservation.balance_id, lock=True)
            post(actor, balance, reserved_delta=-reservation.quantity, kind='unallocation', line=line)
            reservation.quantity = 0
            reservation.save(update_fields=['quantity'])
        line.cancelled = line.quantity - line.processed
        line.save(update_fields=['cancelled'])
    update_status(document)
    record(actor, f'{document.kind}.cancelled', document, {'warehouse': document.warehouse_id, 'was_approved': document.approved_at is not None, 'lines': [{'product': line.product_id, 'quantity': str(line.cancelled)} for line in document.lines.all()]})
    return {'id': document.pk, 'status': document.status}


@transaction.atomic
def return_goods(actor, data):
    require(actor, 'warehouse')
    original = get(actor, Movement, data['movement'])
    warehouse_access(actor, original.balance.bin.warehouse)
    if original.kind not in ['receipt', 'shipment']:
        raise ValidationError('Returns must reference an original receipt or shipment.')
    product_lock(actor, original.balance.identity.product_id)
    original = get(actor, Movement, original.pk, lock=True)
    qty = amount(data['quantity'])
    returned = Movement.objects.filter(source_movement=original, kind__in=['customer_return', 'supplier_return']).aggregate(n=Sum('quantity'))['n'] or 0
    if qty + abs(returned) > abs(original.quantity):
        raise ValidationError('Return exceeds original quantity less prior returns.')
    if not data.get('reason'):
        raise ValidationError('Return reason is required.')
    if original.kind == 'shipment':
        bin = get(actor, Bin, data.get('bin', original.balance.bin_id))
        if bin.warehouse_id != original.balance.bin.warehouse_id:
            raise ValidationError('Return to the original warehouse, then transfer if needed.')
        balance = balance_for(actor, original.balance.identity, bin, 'quarantine')
        movement = post(actor, balance, qty, kind='customer_return', source_movement=original, line=original.line, reason=data['reason'])
    else:
        balance = get(actor, StockBalance, data.get('balance', original.balance_id), lock=True)
        if balance.identity_id != original.balance.identity_id or balance.bin.warehouse_id != original.balance.bin.warehouse_id or balance.state == 'transit':
            raise ValidationError('Supplier return must match original identity and warehouse.')
        movement = post(actor, balance, -qty, kind='supplier_return', source_movement=original, line=original.line, reason=data['reason'])
    return {'id': movement.pk, 'balance': balance.pk}
