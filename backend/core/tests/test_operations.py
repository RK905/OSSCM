from decimal import Decimal
from datetime import timedelta
import pytest
from django.db import connection, transaction, DatabaseError
from django.db.models import Sum
from django.utils import timezone
from django.core.management import call_command
from rest_framework.exceptions import ValidationError
from core.models import Document, DocumentLine, StockBalance, Movement, AuditEvent, Reservation, StockIdentity
from core.domain import orders, inventory
from core.domain.common import execute


def order(s, kind='purchase', quantity=10, reference='ORDER-1', purchase_units=False):
    result = orders.create_document(s['user'], {'kind': kind, 'reference': reference, 'partner':s['supplier' if kind=='purchase' else 'customer'].pk,
                                               'warehouse':s['warehouse'].pk, 'lines':[{'product':s['product'].pk,'quantity':quantity,'purchase_units':purchase_units}]})
    orders.approve(s['user'], {'document':result['id']})
    return DocumentLine.objects.get(document_id=result['id'])


def stock(s, quantity=20, **kwargs):
    return inventory.adjust(s['user'], {'product':s['product'].pk,'bin':s['bin'].pk,'quantity':quantity,'reason':'Opening stock',**kwargs})


def test_complete_purchase_transfer_fulfillment_return_trace(setup):
    s=setup; s['product'].tracking='lot';s['product'].save()
    line=order(s,quantity=2,purchase_units=True)
    assert line.quantity==24 and line.unit_factor==12
    receipt=orders.receive(s['user'],{'line':line.pk,'bin':s['bin'].pk,'quantity':1,'purchase_units':True,'code':'LOT-1'})
    assert Document.objects.get(pk=line.document_id).status=='partial'
    transfer=inventory.transfer_send(s['user'],{'balance':receipt['balance'],'destination':s['destination'].pk,'quantity':4})
    assert StockBalance.objects.aggregate(n=Sum('on_hand'))['n']==12
    inventory.transfer_receive(s['user'],{'transfer':transfer['id'],'quantity':2})
    assert StockBalance.objects.get(state='transit').on_hand==2
    sale=order(s,'sale',6,'SALE-1')
    reservation=orders.allocate(s['user'],{'line':sale.pk,'balance':receipt['balance'],'quantity':6})
    balance=StockBalance.objects.get(pk=receipt['balance']);assert balance.on_hand==8 and balance.reserved==6
    shipment=orders.ship(s['user'],{'line':sale.pk,'reservation':reservation['id'],'quantity':3,'carrier':'Example','tracking':'TRACK-1'})
    returned=orders.return_goods(s['user'],{'movement':shipment['movement'],'quantity':2,'reason':'Customer return'})
    quarantine=StockBalance.objects.get(pk=returned['balance']);assert quarantine.state=='quarantine' and quarantine.on_hand==2
    inventory.disposition(s['user'],{'balance':quarantine.pk,'quantity':1,'disposition':'release','reason':'Inspected'})
    inventory.disposition(s['user'],{'balance':quarantine.pk,'quantity':1,'disposition':'scrap','reason':'Damaged'})
    assert Movement.objects.filter(balance__identity__code='LOT-1').count()>10
    assert StockBalance.objects.aggregate(n=Sum('on_hand'))['n']==10
    call_command('reconcile')


def test_duplicate_requests_and_key_conflict(setup):
    s=setup;payload={'quantity':'5'}
    result=execute(s['user'],'one-key','adjust',payload,lambda:stock(s,5))
    duplicate=execute(s['user'],'one-key','adjust',payload,lambda:stock(s,5))
    assert duplicate==result and Movement.objects.count()==1
    with pytest.raises(ValidationError):execute(s['user'],'one-key','adjust',{'quantity':'6'},lambda:stock(s,6))


def test_failed_receipt_rolls_back_everything(setup):
    s=setup;line=order(s,quantity=2)
    before=AuditEvent.objects.count()
    with pytest.raises(ValidationError):orders.receive(s['user'],{'line':line.pk,'bin':s['bin'].pk,'quantity':3})
    assert Movement.objects.count()==0 and StockBalance.objects.count()==0 and AuditEvent.objects.count()==before
    line.refresh_from_db();assert line.processed==0


def test_reservation_prevents_overselling_and_cancel_releases(setup):
    s=setup;balance=stock(s,5)['balance'];line=order(s,'sale',10)
    orders.allocate(s['user'],{'line':line.pk,'balance':balance,'quantity':4})
    with pytest.raises(ValidationError):orders.allocate(s['user'],{'line':line.pk,'balance':balance,'quantity':2})
    orders.cancel(s['user'],{'document':line.document_id})
    b=StockBalance.objects.get(pk=balance);assert b.on_hand==5 and b.reserved==0
    assert Document.objects.get(pk=line.document_id).status=='cancelled'
    call_command('reconcile')


@pytest.mark.parametrize('quantity', ['NaN','Infinity','0.00001','-1'])
def test_invalid_receipt_quantity(setup,quantity):
    s=setup;line=order(s)
    with pytest.raises(ValidationError):orders.receive(s['user'],{'line':line.pk,'bin':s['bin'].pk,'quantity':quantity})


def test_lot_and_expiry_validation(setup):
    s=setup;s['product'].tracking='lot';s['product'].save()
    with pytest.raises(ValidationError):stock(s,1)
    tomorrow=timezone.localdate()+timedelta(days=1)
    stock(s,2,code='LOT',expires_on=str(tomorrow))
    with pytest.raises(ValidationError):stock(s,1,code='LOT',expires_on=str(tomorrow+timedelta(days=1)))


def test_serial_uniqueness_fractional_and_transfer(setup):
    s=setup;s['product'].tracking='serial';s['product'].save()
    first=stock(s,1,code='SN-1')
    with pytest.raises(ValidationError):stock(s,1,code='SN-1')
    with pytest.raises(ValidationError):stock(s,.5,code='SN-2')
    transfer=inventory.transfer_send(s['user'],{'balance':first['balance'],'destination':s['destination'].pk,'quantity':1})
    inventory.transfer_receive(s['user'],{'transfer':transfer['id'],'quantity':1})
    assert StockBalance.objects.filter(identity__code='SN-1').aggregate(n=Sum('on_hand'))['n']==1
    call_command('reconcile')


def test_expired_stock_quarantined_on_receipt_and_cannot_release(setup):
    s=setup;s['product'].tracking='lot';s['product'].save();line=order(s)
    result=orders.receive(s['user'],{'line':line.pk,'bin':s['bin'].pk,'quantity':2,'code':'OLD','expires_on':str(timezone.localdate()-timedelta(days=1))})
    assert StockBalance.objects.get(pk=result['balance']).state=='quarantine'
    with pytest.raises(ValidationError):inventory.disposition(s['user'],{'balance':result['balance'],'quantity':1,'disposition':'release','reason':'Attempt'})


def test_supplier_return_cannot_exceed_original(setup):
    s=setup;line=order(s)
    receipt=orders.receive(s['user'],{'line':line.pk,'bin':s['bin'].pk,'quantity':3})
    orders.return_goods(s['user'],{'movement':receipt['id'],'quantity':2,'reason':'Rejected'})
    with pytest.raises(ValidationError):orders.return_goods(s['user'],{'movement':receipt['id'],'quantity':2,'reason':'Rejected again'})
    assert StockBalance.objects.get(pk=receipt['balance']).on_hand==1


def test_counts_and_reversals(setup):
    s=setup;stock(s,10)
    counted=inventory.adjust(s['user'],{'product':s['product'].pk,'bin':s['bin'].pk,'quantity':8,'count':True,'reason':'Cycle count'})
    inventory.reverse(s['user'],{'movement':counted['id'],'reason':'Counting error'})
    assert StockBalance.objects.get(pk=counted['balance']).on_hand==10
    with pytest.raises(ValidationError):inventory.reverse(s['user'],{'movement':counted['id'],'reason':'Again'})
    call_command('reconcile')


def test_database_enforces_append_only(setup):
    stock(setup,1)
    with pytest.raises(ValueError):Movement.objects.all().update(quantity=42)
    if connection.vendor=='postgresql':
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:cursor.execute('UPDATE core_movement SET quantity=42')
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:cursor.execute('DELETE FROM core_auditevent')


def test_wrong_bin_and_product_rejected(setup):
    s=setup;line=order(s)
    with pytest.raises(ValidationError):orders.receive(s['user'],{'line':line.pk,'bin':s['destination'].pk,'quantity':1})


def test_quarantine_cannot_be_allocated(setup):
    s=setup;b=stock(s,5,state='quarantine')['balance'];line=order(s,'sale')
    with pytest.raises(ValidationError):orders.allocate(s['user'],{'line':line.pk,'balance':b,'quantity':1})
