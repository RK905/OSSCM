from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection
from django.db.models import Q, F, Sum
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler as drf_exception_handler
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from core import models as m
from core.domain import catalog, inventory, orders, integrations, planning
from core.domain.common import member, require, execute, get, warehouse_access
from core import identity


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)
    elif isinstance(exc, IntegrityError):
        exc = ValidationError('This operation conflicts with an existing record or violates an inventory constraint.')
    elif isinstance(exc, (KeyError, ValueError, TypeError)):
        exc = ValidationError('Invalid or missing input. Consult the endpoint schema.')
    return drf_exception_handler(exc, context)


RESOURCES = {
    'products': m.Product, 'partners': m.Partner, 'warehouses': m.Warehouse, 'bins': m.Bin, 'policies': m.SupplyPolicy,
    'identities': m.StockIdentity, 'balances': m.StockBalance, 'documents': m.Document, 'lines': m.DocumentLine,
    'reservations': m.Reservation, 'shipments': m.Shipment, 'transfers': m.Transfer, 'movements': m.Movement,
    'forecasts': m.ForecastRun, 'imports': m.ImportBatch, 'deliveries': m.Delivery, 'webhooks': m.Webhook,
    'credentials': m.APIKey, 'memberships': m.Membership, 'audit': m.AuditEvent,
}
WAREHOUSE_PATHS = {
    'bins': 'warehouse', 'policies': 'warehouse', 'balances': 'bin__warehouse', 'documents': 'warehouse',
    'lines': 'document__warehouse', 'reservations': 'balance__bin__warehouse', 'shipments': 'document__warehouse',
    'movements': 'balance__bin__warehouse', 'forecasts': 'policy__warehouse',
}
ADMIN_RESOURCES = {'deliveries', 'webhooks', 'credentials', 'memberships', 'audit'}


def scoped(actor, resource):
    membership = member(actor)
    if resource in ADMIN_RESOURCES:
        require(actor)
    qs = RESOURCES[resource].objects.filter(organization=membership.organization)
    if membership.role != 'administrator':
        warehouses = membership.warehouses.all()
        if resource == 'warehouses':
            qs = qs.filter(pk__in=warehouses)
        elif resource in WAREHOUSE_PATHS:
            qs = qs.filter(**{WAREHOUSE_PATHS[resource] + '__in': warehouses})
        elif resource == 'transfers':
            qs = qs.filter(source__bin__warehouse__in=warehouses, destination__warehouse__in=warehouses)
        elif resource == 'identities':
            qs = qs.filter(stockbalance__bin__warehouse__in=warehouses).distinct()
        elif resource == 'imports':
            qs = qs.filter(actor=actor)
    return qs


SERIALIZERS = {}
for resource, model in RESOURCES.items():
    excluded = {'webhooks': ['secret'], 'credentials': ['digest'], 'memberships': ['totp_secret', 'last_totp_step']}.get(resource, [])
    meta = type('Meta', (), {'model': model, 'exclude': excluded} if excluded else {'model': model, 'fields': '__all__'})
    extra = {}
    if resource in ['lines', 'policies', 'identities']:
        extra = {'product_sku': serializers.CharField(source='product.sku', read_only=True), 'product_name': serializers.CharField(source='product.name', read_only=True)}
    if resource == 'memberships':
        extra = {'username': serializers.CharField(source='user.username', read_only=True), 'active': serializers.BooleanField(source='user.is_active', read_only=True)}
    SERIALIZERS[resource] = type(model.__name__ + 'Serializer', (serializers.ModelSerializer,), {'Meta': meta, '__module__': __name__, **extra})


class ReadResource(viewsets.ReadOnlyModelViewSet):
    resource = ''
    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return RESOURCES[self.resource].objects.none()
        qs = scoped(self.request.user, self.resource)
        if self.resource in ['lines', 'policies', 'identities']:
            qs = qs.select_related('product')
        if self.resource == 'memberships':
            qs = qs.select_related('user').prefetch_related('warehouses')
        query = self.request.query_params.get('q', '').strip()[:200]
        if query:
            fields = [f.name for f in qs.model._meta.fields if f.name in ['name', 'sku', 'reference', 'code', 'kind', 'action']]
            if self.resource in ['lines', 'policies', 'identities']:
                fields += ['product__sku', 'product__name']
            condition = Q()
            for field in fields:
                condition |= Q(**{field + '__icontains': query})
            qs = qs.filter(condition)
        filters = {'partners': ['kind'], 'balances': ['identity', 'bin', 'state'], 'movements': ['balance', 'line', 'shipment', 'transfer'],
                   'documents': ['kind', 'status', 'warehouse'], 'lines': ['document', 'product'],
                   'reservations': ['line', 'balance'], 'bins': ['warehouse'], 'identities': ['product'],
                   'forecasts': ['policy']}.get(self.resource, [])
        for field in filters:
            if field in self.request.query_params:
                qs = qs.filter(**{field: self.request.query_params[field]})
        if self.resource == 'movements':
            if self.request.query_params.get('product'):
                qs = qs.filter(balance__identity__product_id=self.request.query_params['product'])
            if self.request.query_params.get('warehouse'):
                qs = qs.filter(balance__bin__warehouse_id=self.request.query_params['warehouse'])
            if self.request.query_params.get('identity'):
                qs = qs.filter(balance__identity_id=self.request.query_params['identity'])
        return qs.order_by('-pk')


# Every command has a concrete request serializer, used for validation and OpenAPI.
def field(kind, required=True):
    options = {'required': required}
    if kind == 'id': return serializers.IntegerField(min_value=1, **options)
    if kind == 'qty': return serializers.DecimalField(max_digits=18, decimal_places=4, **options)
    if kind == 'bool': return serializers.BooleanField(**options)
    if kind == 'date': return serializers.DateField(allow_null=True, **options)
    if kind == 'json': return serializers.JSONField(**options)
    return serializers.CharField(max_length=500, allow_blank=not required, **options)

COMMANDS = {
    'orders/create': (orders.create_document, 'sales', {'kind': 'str', 'reference': 'str', 'partner': 'id', 'warehouse': 'id', 'lines': 'json', 'due_date?': 'date', 'notes?': 'str'}),
    'orders/approve': (orders.approve, 'sales', {'document': 'id'}),
    'orders/cancel': (orders.cancel, 'sales', {'document': 'id'}),
    'orders/receive': (orders.receive, 'warehouse', {'line': 'id', 'bin': 'id', 'quantity': 'qty', 'purchase_units?': 'bool', 'code?': 'str', 'expires_on?': 'date', 'quarantine?': 'bool'}),
    'orders/allocate': (orders.allocate, 'sales', {'line': 'id', 'balance': 'id', 'quantity': 'qty'}),
    'orders/ship': (orders.ship, 'warehouse', {'line': 'id', 'reservation': 'id', 'quantity': 'qty', 'carrier?': 'str', 'tracking?': 'str'}),
    'orders/return': (orders.return_goods, 'warehouse', {'movement': 'id', 'quantity': 'qty', 'reason': 'str', 'bin?': 'id', 'balance?': 'id'}),
    'inventory/adjust': (inventory.adjust, 'warehouse', {'product': 'id', 'bin': 'id', 'quantity': 'qty', 'reason': 'str', 'code?': 'str', 'expires_on?': 'date', 'state?': 'str', 'count?': 'bool'}),
    'inventory/reverse': (inventory.reverse, 'warehouse', {'movement': 'id', 'reason': 'str'}),
    'inventory/transfer-send': (inventory.transfer_send, 'warehouse', {'balance': 'id', 'destination': 'id', 'quantity': 'qty'}),
    'inventory/transfer-receive': (inventory.transfer_receive, 'warehouse', {'transfer': 'id', 'quantity': 'qty'}),
    'inventory/disposition': (inventory.disposition, 'warehouse', {'balance': 'id', 'quantity': 'qty', 'disposition': 'str', 'reason': 'str'}),
    'planning/forecast': (planning.forecast, 'planning', {'policy': 'id'}),
    'planning/accept': (planning.accept, 'planning', {'forecast': 'id', 'reference': 'str'}),
    'imports/preview': (integrations.preview_import, 'catalog', {'csv': 'str'}),
    'imports/commit': (integrations.commit_import, 'catalog', {'batch': 'id'}),
    'webhooks/create': (integrations.webhook_create, 'integrations', {'url': 'str'}),
    'webhooks/update': (integrations.webhook_update, 'integrations', {'webhook': 'id', 'active': 'bool'}),
    'deliveries/replay': (integrations.replay, 'integrations', {'delivery': 'id'}),
    'credentials/create': (identity.credential_create, 'admin', {'name': 'str', 'scopes': 'json', 'days?': 'id'}),
    'credentials/revoke': (identity.credential_revoke, 'admin', {'credential': 'id'}),
    'users/create': (identity.user_create, 'admin', {'username': 'str', 'password': 'str', 'role': 'str', 'warehouses': 'json', 'email?': 'str'}),
    'users/permissions': (identity.permissions_update, 'admin', {'membership': 'id', 'role': 'str', 'warehouses': 'json', 'active?': 'bool'}),
}


class CommandView(APIView):
    command = ''
    scope = ''
    request_serializer = serializers.Serializer
    def check_permissions(self, request):
        # Purchase and sales documents share endpoints, but never credential scopes.
        if self.command in ['orders/create', 'orders/approve', 'orders/cancel']:
            kind = request.data.get('kind')
            if self.command != 'orders/create' and request.user.is_authenticated:
                kind = get(request.user, m.Document, request.data.get('document')).kind
            self.scope = 'purchasing' if kind == 'purchase' else 'sales'
        super().check_permissions(request)
    def post(self, request):
        serializer = self.request_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        callback = COMMANDS[self.command][0]
        result = execute(request.user, request.headers.get('Idempotency-Key'), self.command, data, lambda: callback(request.user, data))
        return Response(result)


class CatalogCreate(APIView):
    resource = ''
    scope = 'catalog'
    request_serializer = serializers.Serializer
    def post(self, request):
        serializer = self.request_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Model serializers validate wire types; domain service owns all mutations.
        data = {k: v.pk if hasattr(v, 'pk') else v for k, v in serializer.validated_data.items()}
        return Response(execute(request.user, request.headers.get('Idempotency-Key'), self.resource + '.create', data,
                                lambda: catalog.create(request.user, self.resource, data)))


class Dashboard(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        balances = scoped(request.user, 'balances')
        docs = scoped(request.user, 'documents')
        today = timezone.localdate()
        physical = balances.exclude(state='transit')
        usable = balances.filter(state='available').exclude(identity__expires_on__lt=today)
        sums = physical.aggregate(on_hand=Sum('on_hand'), reserved=Sum('reserved'))
        available = usable.aggregate(n=Sum(F('on_hand') - F('reserved')))['n'] or 0
        lines = scoped(request.user, 'lines').filter(document__kind='purchase', document__status__in=['approved', 'partial'])
        inbound = lines.aggregate(n=Sum(F('quantity') - F('processed') - F('cancelled')))['n'] or 0
        alerts = []
        for b in balances.filter(on_hand__gt=0, identity__expires_on__lte=today + timedelta(days=30)).select_related('identity__product', 'bin__warehouse')[:30]:
            alerts.append({'type': 'expiry', 'label': f'{b.identity.product.sku} · {b.bin.warehouse.code}/{b.bin.code} · {b.identity.code} expires {b.identity.expires_on}', 'resource': 'balances', 'id': b.pk})
        for d in docs.filter(due_date__lt=today, status__in=['approved', 'partial'])[:30]:
            alerts.append({'type': 'overdue', 'label': f'{d.reference} is overdue', 'resource': 'documents', 'id': d.pk})
        for policy in scoped(request.user, 'policies').select_related('product', 'warehouse'):
            stock = usable.filter(identity__product=policy.product, bin__warehouse=policy.warehouse).aggregate(n=Sum(F('on_hand') - F('reserved')))['n'] or 0
            if stock <= policy.reorder_point:
                alerts.append({'type': 'low_stock', 'label': f'{policy.product.sku} · {policy.warehouse.code}: {stock} available', 'resource': 'policies', 'id': policy.pk})
        recent = scoped(request.user, 'movements').select_related('balance__identity__product', 'balance__bin__warehouse').order_by('-pk')[:12]
        return Response({'on_hand': sums['on_hand'] or 0, 'reserved': sums['reserved'] or 0, 'available': available, 'inbound': inbound,
                         'quarantined': balances.filter(state='quarantine').aggregate(n=Sum('on_hand'))['n'] or 0,
                         'in_transit': balances.filter(state='transit').aggregate(n=Sum('on_hand'))['n'] or 0,
                         'open_orders': docs.filter(status__in=['approved', 'partial'], kind='sale').count(),
                         'products': scoped(request.user, 'products').count(), 'warehouses': scoped(request.user, 'warehouses').count(),
                         'alerts': alerts[:60], 'recent': [{'id': x.pk, 'kind': x.kind, 'sku': x.balance.identity.product.sku, 'warehouse': x.balance.bin.warehouse.code, 'quantity': x.quantity, 'created_at': x.created_at} for x in recent]})


class Trace(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        stock = scoped(request.user, 'identities').filter(pk=pk).first()
        if not stock:
            raise ValidationError('Stock identity not found.')
        movements = scoped(request.user, 'movements').filter(balance__identity=stock).order_by('-pk')[:500]
        return Response({'identity': SERIALIZERS['identities'](stock).data,
                         'balances': SERIALIZERS['balances'](scoped(request.user, 'balances').filter(identity=stock), many=True).data,
                         'movements': SERIALIZERS['movements'](movements, many=True).data,
                         'more': f'/api/v1/movements/?identity={pk}'})


def health(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return JsonResponse({'status': 'ok'})


class CatalogUpdate(CatalogCreate):
    def post(self, request, pk):
        obj = get(request.user, catalog.CATALOG[self.resource][0], pk)
        serializer = self.request_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = {k: v.pk if hasattr(v, 'pk') else v for k, v in serializer.validated_data.items()}
        return Response(execute(request.user, request.headers.get('Idempotency-Key'), f'{self.resource}.{pk}.update', data,
                                lambda: catalog.update(request.user, self.resource, pk, data)))
