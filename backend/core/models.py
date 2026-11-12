import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F


def quantity_field(default=0):
    return models.DecimalField(max_digits=18, decimal_places=4, default=default)


class Organization(models.Model):
    name = models.CharField(max_length=160)
    currency = models.CharField(max_length=3, default='USD')
    timezone = models.CharField(max_length=80, default='UTC')


class Owned(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        abstract = True


class Warehouse(Owned):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)


class Bin(Owned):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    code = models.CharField(max_length=40)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['warehouse', 'code'], name='unique_bin')]


class Membership(Owned):
    ROLES = [(x, x.title()) for x in ['administrator', 'purchasing', 'warehouse', 'sales', 'readonly']]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLES, default='readonly')
    warehouses = models.ManyToManyField(Warehouse, blank=True)
    totp_secret = models.TextField(blank=True)
    totp_enabled = models.BooleanField(default=False)
    last_totp_step = models.BigIntegerField(default=-1)


class APIKey(Owned):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    digest = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()


class Product(Owned):
    TRACKING = [('none', 'Quantity'), ('lot', 'Lot'), ('serial', 'Serial')]
    sku = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=24, default='each')
    purchase_unit = models.CharField(max_length=24, default='each')
    purchase_factor = quantity_field(1)
    tracking = models.CharField(max_length=10, choices=TRACKING, default='none')
    metadata = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(purchase_factor__gt=0), name='positive_conversion')]


class Partner(Owned):
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=[('supplier', 'Supplier'), ('customer', 'Customer')])
    email = models.EmailField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)


class SupplyPolicy(Owned):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Partner, on_delete=models.PROTECT)
    lead_days = models.PositiveIntegerField(default=7)
    review_days = models.PositiveIntegerField(default=7)
    safety_stock = quantity_field()
    reorder_point = quantity_field()
    target_stock = quantity_field()
    minimum_order = quantity_field(1)
    pack_size = quantity_field(1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['product', 'warehouse'], name='unique_policy'),
                       models.CheckConstraint(condition=Q(pack_size__gt=0) & Q(minimum_order__gte=0) & Q(safety_stock__gte=0) & Q(reorder_point__gte=0) & Q(target_stock__gte=0), name='valid_policy')]


class StockIdentity(Owned):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    code = models.CharField(max_length=100, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['product', 'code'], name='unique_stock_identity')]


class StockBalance(Owned):
    identity = models.ForeignKey(StockIdentity, on_delete=models.PROTECT)
    bin = models.ForeignKey(Bin, on_delete=models.PROTECT)
    state = models.CharField(max_length=12, choices=[('available', 'Available'), ('quarantine', 'Quarantine'), ('transit', 'In transit')], default='available')
    on_hand = quantity_field()
    reserved = quantity_field()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['identity', 'bin', 'state'], name='unique_balance'),
                       models.CheckConstraint(condition=Q(on_hand__gte=0) & Q(reserved__gte=0) & Q(on_hand__gte=F('reserved')), name='stock_nonnegative')]
        indexes = [models.Index(fields=['bin', 'state'])]


class Document(Owned):
    kind = models.CharField(max_length=10, choices=[('purchase', 'Purchase order'), ('sale', 'Sales order')])
    reference = models.CharField(max_length=60, unique=True)
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, default='draft', choices=[(x, x.title()) for x in ['draft', 'approved', 'partial', 'complete', 'cancelled']])
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)


class DocumentLine(Owned):
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = quantity_field()
    unit_factor = quantity_field(1)
    processed = quantity_field()
    cancelled = quantity_field()
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(unit_factor__gt=0) & Q(processed__gte=0) & Q(cancelled__gte=0) & Q(quantity__gte=F('processed') + F('cancelled')), name='valid_line_quantities')]


class Reservation(Owned):
    line = models.ForeignKey(DocumentLine, on_delete=models.PROTECT)
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT)
    quantity = quantity_field()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['line', 'balance'], name='unique_reservation'),
                       models.CheckConstraint(condition=Q(quantity__gte=0), name='positive_reservation')]


class Shipment(Owned):
    document = models.ForeignKey(Document, on_delete=models.PROTECT)
    carrier = models.CharField(max_length=80, blank=True)
    tracking = models.CharField(max_length=200, blank=True)


class Transfer(Owned):
    source = models.ForeignKey(StockBalance, on_delete=models.PROTECT, related_name='+')
    destination = models.ForeignKey(Bin, on_delete=models.PROTECT)
    quantity = quantity_field()
    received = quantity_field()
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(received__gte=0) & Q(quantity__gte=F('received')), name='valid_transfer')]


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('Append-only record')
    def delete(self):
        raise ValueError('Append-only record')


class AppendOnly(Owned):
    objects = AppendOnlyQuerySet.as_manager()
    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('Append-only record')
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValueError('Append-only record')
    class Meta:
        abstract = True


class Movement(AppendOnly):
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT)
    quantity = quantity_field()
    reserved_delta = quantity_field()
    kind = models.CharField(max_length=24)
    line = models.ForeignKey(DocumentLine, null=True, blank=True, on_delete=models.PROTECT)
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.PROTECT)
    transfer = models.ForeignKey(Transfer, null=True, blank=True, on_delete=models.PROTECT)
    source_movement = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=500, blank=True)
    class Meta:
        indexes = [models.Index(fields=['balance', 'created_at']), models.Index(fields=['line', 'kind'])]


class AuditEvent(AppendOnly):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=100)
    entity = models.CharField(max_length=100)
    data = models.JSONField(default=dict)


class Idempotency(Owned):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'key'], name='unique_idempotency')]


class OutboxEvent(Owned):
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    topic = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)


class Webhook(Owned):
    url = models.URLField(max_length=500)
    secret = models.TextField()
    active = models.BooleanField(default=True)


class Delivery(Owned):
    event = models.ForeignKey(OutboxEvent, on_delete=models.PROTECT)
    webhook = models.ForeignKey(Webhook, on_delete=models.PROTECT)
    attempts = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, default='pending')
    next_attempt = models.DateTimeField()
    response_code = models.IntegerField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'webhook'], name='unique_delivery')]


class ImportBatch(Owned):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    rows = models.JSONField()
    errors = models.JSONField(default=list)
    committed_at = models.DateTimeField(null=True)


class ForecastRun(Owned):
    policy = models.ForeignKey(SupplyPolicy, on_delete=models.PROTECT)
    result = models.JSONField()
    recommended = quantity_field()
    document = models.ForeignKey(Document, null=True, on_delete=models.PROTECT)
