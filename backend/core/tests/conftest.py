import pytest
from django.contrib.auth.models import User
from core.models import Organization, Membership, Warehouse, Bin, Product, Partner


@pytest.fixture
def setup(db):
    org = Organization.objects.create(name='Test organization')
    user = User.objects.create_user('admin', password='LongRandomPassword-123!')
    Membership.objects.create(organization=org, user=user, role='administrator')
    warehouse = Warehouse.objects.create(organization=org, code='ONE', name='First warehouse')
    other = Warehouse.objects.create(organization=org, code='TWO', name='Second warehouse')
    bin = Bin.objects.create(organization=org, warehouse=warehouse, code='A')
    destination = Bin.objects.create(organization=org, warehouse=other, code='B')
    product = Product.objects.create(organization=org, sku='SKU-1', name='Test item', purchase_factor=12)
    supplier = Partner.objects.create(organization=org, name='Supplier', kind='supplier')
    customer = Partner.objects.create(organization=org, name='Customer', kind='customer')
    return dict(org=org, user=user, warehouse=warehouse, other=other, bin=bin, destination=destination, product=product, supplier=supplier, customer=customer)
