import hashlib
import time
from datetime import timedelta
import pytest
import pyotp
from django.conf import settings
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import Membership, APIKey, Idempotency, StockBalance, Organization, Warehouse
from core.identity import credential_create, encrypt
from core.domain.common import execute
from .test_operations import stock, order


@pytest.fixture
def client(setup):
    c=APIClient();c.force_login(setup['user']);return c


def test_api_command_requires_key(client,setup):
    r=client.post('/api/v1/commands/inventory/adjust/',{'product':setup['product'].pk,'bin':setup['bin'].pk,'quantity':'2','reason':'Test'},format='json')
    assert r.status_code==400


def test_api_duplicate_command(client,setup):
    payload={'product':setup['product'].pk,'bin':setup['bin'].pk,'quantity':'2','reason':'Test'}
    first=client.post('/api/v1/commands/inventory/adjust/',payload,format='json',HTTP_IDEMPOTENCY_KEY='same')
    second=client.post('/api/v1/commands/inventory/adjust/',payload,format='json',HTTP_IDEMPOTENCY_KEY='same')
    assert first.status_code==200 and first.data==second.data
    assert StockBalance.objects.get().on_hand==2


def test_readonly_and_warehouse_isolation(setup):
    s=setup;stock(s,5)
    user=User.objects.create_user('viewer',password='ExamplePassword123!')
    membership=Membership.objects.create(organization=s['org'],user=user,role='readonly')
    membership.warehouses.add(s['other'])
    c=APIClient();c.force_login(user)
    assert c.get('/api/v1/balances/').data['count']==0
    assert c.get('/api/v1/movements/').data['count']==0
    assert c.get('/api/v1/dashboard/').data['on_hand']==0
    assert c.get('/api/v1/balances/1/').status_code==404
    assert c.get('/api/v1/audit/').status_code==403
    response=c.post('/api/v1/commands/inventory/adjust/',{'product':s['product'].pk,'bin':s['bin'].pk,'quantity':2,'reason':'Not allowed'},format='json',HTTP_IDEMPOTENCY_KEY='forbidden')
    assert response.status_code==403


def test_warehouse_role_cannot_write_other_warehouse(setup):
    s=setup;u=User.objects.create_user('operator');membership=Membership.objects.create(organization=s['org'],user=u,role='warehouse');membership.warehouses.add(s['other'])
    c=APIClient();c.force_login(u)
    result=c.post('/api/v1/commands/inventory/adjust/',{'product':s['product'].pk,'bin':s['bin'].pk,'quantity':1,'reason':'Attempt'},format='json',HTTP_IDEMPOTENCY_KEY='blocked')
    assert result.status_code==403


def test_api_credentials_hash_scope_expiry_and_revocation(setup):
    s=setup;issued=credential_create(s['user'],{'name':'Read only','scopes':['read'],'days':1});key=APIKey.objects.get(pk=issued['id'])
    assert key.digest==hashlib.sha256(issued['token'].encode()).hexdigest()
    c=APIClient();c.credentials(HTTP_AUTHORIZATION='Bearer '+issued['token'])
    assert c.get('/api/v1/products/').status_code==200
    assert c.post('/api/v1/commands/catalog/products/',{'sku':'NEW','name':'New'},format='json',HTTP_IDEMPOTENCY_KEY='x').status_code==403
    key.revoked=True;key.save();assert c.get('/api/v1/products/').status_code==401
    key.revoked=False;key.expires_at=timezone.now()-timedelta(seconds=1);key.save();assert c.get('/api/v1/products/').status_code==401


def test_issued_secrets_not_persisted_in_idempotency(setup):
    s=setup;result=execute(s['user'],'credential-key','credential',{},lambda:credential_create(s['user'],{'name':'Test','scopes':['read']}))
    assert result['token'] not in str(Idempotency.objects.get().response)
    assert 'token' not in execute(s['user'],'credential-key','credential',{},lambda:None)


def test_csrf_protects_login_and_commands(setup):
    c=APIClient(enforce_csrf_checks=True)
    assert c.post('/api/v1/session/',{'username':'admin','password':'LongRandomPassword-123!'},format='json').status_code==403
    csrf=c.get('/api/v1/session/').json()['csrf']
    r=c.post('/api/v1/session/',{'username':'admin','password':'LongRandomPassword-123!'},format='json',HTTP_X_CSRFTOKEN=csrf)
    assert r.status_code==200
    assert c.post('/api/v1/commands/catalog/products/',{'sku':'X','name':'Test'},format='json',HTTP_IDEMPOTENCY_KEY='no-csrf').status_code==403


def test_login_throttling(setup):
    c=APIClient()
    for _ in range(10):assert c.post('/api/v1/session/',{'username':'admin','password':'wrong'},format='json').status_code==401
    assert c.post('/api/v1/session/',{'username':'admin','password':'wrong'},format='json').status_code==429


@override_settings(MFA_REQUIRED=True)
def test_admin_mfa_gate_and_enrollment(setup):
    c=APIClient();c.force_login(setup['user'])
    assert c.get('/api/v1/products/').status_code==403
    secret=c.post('/api/v1/mfa/',{'action':'begin'},format='json').json()['secret']
    result=c.post('/api/v1/mfa/',{'action':'confirm','otp':pyotp.TOTP(secret).now()},format='json')
    assert result.status_code==200
    assert c.get('/api/v1/products/').status_code==200
    m=Membership.objects.get(user=setup['user']);assert secret not in m.totp_secret
    c.logout()
    assert c.post('/api/v1/session/',{'username':'admin','password':'LongRandomPassword-123!','otp':pyotp.TOTP(secret).now()},format='json').status_code==401


def test_cross_organization_foreign_keys_rejected(client,setup):
    other=Organization.objects.create(name='Other organization')
    warehouse=Warehouse.objects.create(organization=other,code='FOREIGN',name='Foreign')
    r=client.post('/api/v1/commands/catalog/bins/',{'warehouse':warehouse.pk,'code':'BAD'},format='json',HTTP_IDEMPOTENCY_KEY='foreign')
    assert r.status_code==400


def test_documentation_and_schema_are_served(client):
    assert client.get('/api/docs/').status_code==200
    assert client.get('/api/schema/').status_code==200


def test_catalog_updates_preserve_tracking_after_use(client,setup):
    stock(setup,1)
    r=client.post(f'/api/v1/commands/catalog/products/{setup["product"].pk}/update/',{'tracking':'serial'},format='json',HTTP_IDEMPOTENCY_KEY='tracking-change')
    assert r.status_code==400
    r=client.post(f'/api/v1/commands/catalog/products/{setup["product"].pk}/update/',{'name':'Updated name','metadata':{'industry':'retail'}},format='json',HTTP_IDEMPOTENCY_KEY='name-change')
    assert r.status_code==200
    setup['product'].refresh_from_db();assert setup['product'].name=='Updated name' and setup['product'].metadata=={'industry':'retail'}


def test_mfa_enrollment_throttling(setup):
    c=APIClient();c.force_login(setup['user'])
    c.post('/api/v1/mfa/',{'action':'begin'},format='json')
    for _ in range(9):assert c.post('/api/v1/mfa/',{'otp':'invalid'},format='json').status_code==400
    assert c.post('/api/v1/mfa/',{'otp':'invalid'},format='json').status_code==429


def test_missing_required_command_fields_are_rejected(client):
    assert client.post('/api/v1/commands/orders/receive/',{},format='json',HTTP_IDEMPOTENCY_KEY='missing').status_code==400


def test_purchase_scopes_cannot_create_sales_orders(setup):
    issued=credential_create(setup['user'],{'name':'Purchasing','scopes':['read','purchasing']})
    c=APIClient();c.credentials(HTTP_AUTHORIZATION='Bearer '+issued['token'])
    response=c.post('/api/v1/commands/orders/create/',{'kind':'sale','reference':'NO','partner':setup['customer'].pk,'warehouse':setup['warehouse'].pk,'lines':[{'product':setup['product'].pk,'quantity':'1'}]},format='json',HTTP_IDEMPOTENCY_KEY='no-sales')
    assert response.status_code==403


@pytest.mark.parametrize('body',[[],{'username':[]},{'password':{}},'invalid'])
def test_invalid_login_payload_is_not_server_error(setup,body):
    assert APIClient().post('/api/v1/session/',body,format='json').status_code==400
