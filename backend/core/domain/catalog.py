from django.db import transaction
from rest_framework.exceptions import ValidationError
from core.models import Product, Partner, Warehouse, Bin, SupplyPolicy
from .common import member, require, record, get, warehouse_access

CATALOG = {
    'products': (Product, ['sku', 'name', 'unit', 'purchase_unit', 'purchase_factor', 'tracking', 'metadata', 'active'], ('purchasing',)),
    'partners': (Partner, ['name', 'kind', 'email', 'metadata'], ('purchasing', 'sales')),
    'warehouses': (Warehouse, ['code', 'name'], ()),
    'bins': (Bin, ['warehouse', 'code'], ('warehouse',)),
    'policies': (SupplyPolicy, ['product', 'warehouse', 'supplier', 'lead_days', 'review_days', 'safety_stock', 'reorder_point', 'target_stock', 'minimum_order', 'pack_size'], ('purchasing',)),
}


@transaction.atomic
def create(actor, resource, data):
    if resource not in CATALOG:
        raise ValidationError('Unknown catalog resource.')
    model, fields, roles = CATALOG[resource]
    require(actor, *roles)
    values = {key: value for key, value in data.items() if key in fields}
    for field, related in [('warehouse', Warehouse), ('product', Product), ('supplier', Partner)]:
        if field in values:
            values[field] = get(actor, related, values[field])
    if 'warehouse' in values:
        warehouse_access(actor, values['warehouse'])
    if resource == 'policies' and values.get('lead_days', 7) + values.get('review_days', 7) > 365:
        raise ValidationError('Combined lead and review period must not exceed 365 days.')
    if resource == 'policies' and values['supplier'].kind != 'supplier':
        raise ValidationError('Policy partner must be a supplier.')
    if resource == 'partners' and member(actor).role != 'administrator':
        require(actor, 'purchasing' if values.get('kind') == 'supplier' else 'sales')
    obj = model(organization=member(actor).organization, **values)
    obj.full_clean()
    if hasattr(obj, 'metadata') and not isinstance(obj.metadata, dict):
        raise ValidationError('Metadata must be a JSON object.')
    if resource == 'policies' and obj.product.tracking == 'serial' and any(v != v.to_integral_value() for v in [obj.pack_size, obj.minimum_order]):
        raise ValidationError('Serialized products require whole-unit pack sizes and minimum orders.')
    obj.save()
    record(actor, f'{resource}.created', obj)
    return {'id': obj.pk}


@transaction.atomic
def update(actor, resource, pk, data):
    model, fields, roles = CATALOG[resource]
    require(actor, *roles)
    obj = get(actor, model, pk, lock=True)
    if resource == 'warehouses':
        warehouse_access(actor, obj)
    if resource == 'products':
        from core.models import StockIdentity, DocumentLine
        used = StockIdentity.objects.filter(product=obj).exists() or DocumentLine.objects.filter(product=obj).exists()
        if used and any(key in data and data[key] != getattr(obj, key) for key in ['unit', 'tracking']):
            raise ValidationError('Base unit and tracking cannot change after stock or order history exists.')
    if resource == 'partners' and 'kind' in data and data['kind'] != obj.kind:
        raise ValidationError('Partner type is immutable. Create a separate partner record.')
    if resource == 'partners':
        require(actor, 'purchasing' if obj.kind == 'supplier' else 'sales')
    if resource in ['bins', 'policies'] and any(k in data and int(data[k]) != getattr(obj, k + '_id') for k in (['warehouse'] if resource == 'bins' else ['warehouse', 'product'])):
        raise ValidationError('Location/product links are immutable; create a new record instead.')
    for key, value in data.items():
        if key not in fields:
            continue
        if key in ['warehouse', 'product', 'supplier']:
            value = get(actor, {'warehouse': Warehouse, 'product': Product, 'supplier': Partner}[key], value)
        if key == 'supplier' and value.kind != 'supplier':
            raise ValidationError('Policy partner must be a supplier.')
        setattr(obj, key, value)
    if resource == 'policies' and obj.lead_days + obj.review_days > 365:
        raise ValidationError('Combined lead and review period must not exceed 365 days.')
    obj.full_clean()
    if hasattr(obj, 'metadata') and not isinstance(obj.metadata, dict):
        raise ValidationError('Metadata must be a JSON object.')
    if resource == 'policies' and obj.product.tracking == 'serial' and any(v != v.to_integral_value() for v in [obj.pack_size, obj.minimum_order]):
        raise ValidationError('Serialized products require whole-unit pack sizes and minimum orders.')
    obj.save()
    record(actor, f'{resource}.updated', obj, {'fields': sorted(data)})
    return {'id': obj.pk}
