import json
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from core.domain import integrations, planning
from core.models import Product, ImportBatch, SupplyPolicy, ForecastRun, Document, DocumentLine, Webhook, Delivery, OutboxEvent
from core.identity import encrypt
from core.tasks import deliver_outbox


def test_import_preview_errors_and_atomic_commit(setup):
    s=setup
    invalid=integrations.preview_import(s['user'],{'csv':'sku,name,purchase_factor\nNEW,New product,1\nNEW,Duplicate,0'})
    assert len(invalid['errors'])==1
    with pytest.raises(ValidationError):integrations.commit_import(s['user'],{'batch':invalid['id']})
    assert not Product.objects.filter(sku='NEW').exists()
    valid=integrations.preview_import(s['user'],{'csv':'sku,name,tracking\nA,Apple,lot\nB,Banana,none'})
    assert valid['errors']==[]
    assert integrations.commit_import(s['user'],{'batch':valid['id']})['imported']==2
    with pytest.raises(ValidationError):integrations.commit_import(s['user'],{'batch':valid['id']})


@pytest.mark.parametrize('history',[[],[1,2,3],[0]*20,[0,0,5,0]*12,[10,20,30,40]*20])
def test_forecasting_sparse_zero_intermittent_and_seasonal(history):
    r=planning.evaluate(history)
    assert all(v>=0 for v in r['forecast'])
    if len(history)<8:assert r['method']=='manual_reorder'
    elif not any(history):assert r['metrics']['wape'] is None and r['metrics']['mae']==0
    else:assert r['metrics']['mae']>=0
    if len(history)>=60:assert 'seasonal_naive' in r['candidates']


def test_backtesting_passes_only_past_values():
    calls=[];real=planning.predict
    def spy(method,history,horizon):
        calls.append(list(history));return real(method,history,horizon)
    history=list(range(20))
    with patch('core.domain.planning.predict',side_effect=spy):planning.evaluate(history)
    assert all(values==history[:len(values)] for values in calls)
    assert any(len(values)<len(history) for values in calls)


def test_recommendation_creates_draft_and_cannot_accept_twice(setup):
    s=setup;policy=SupplyPolicy.objects.create(organization=s['org'],product=s['product'],warehouse=s['warehouse'],supplier=s['supplier'],reorder_point=5,target_stock=19,minimum_order=12,pack_size=12)
    result=planning.forecast(s['user'],{'policy':policy.pk})
    assert result['method']=='manual_reorder' and Decimal(result['recommended'])==24
    accepted=planning.accept(s['user'],{'forecast':result['id'],'reference':'SUGGEST-1'})
    doc=Document.objects.get(pk=accepted['id']);assert doc.status=='draft' and 'Forecast' in doc.notes
    assert DocumentLine.objects.get(document=doc).quantity==24
    with pytest.raises(ValidationError):planning.accept(s['user'],{'forecast':result['id'],'reference':'SUGGEST-2'})


@pytest.mark.parametrize('url',['http://example.com/hook','https://127.0.0.1/hook','https://user:pass@example.com/','https://example.com:8443/','https://[::1]/','https://169.254.169.254/'])
def test_webhooks_reject_unsafe_destinations(url):
    with pytest.raises(ValidationError):integrations.public_addresses(url)


def test_mixed_public_private_dns_rejected():
    addresses=[(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('10.0.0.1',443))]
    with patch('socket.getaddrinfo',return_value=addresses),pytest.raises(ValidationError):integrations.public_addresses('https://example.com/hook')


def test_delivery_retry_replay_stable_id(setup):
    s=setup;hook=Webhook.objects.create(organization=s['org'],url='https://example.com/hook',secret=encrypt('signing-key'))
    event=OutboxEvent.objects.create(organization=s['org'],topic='test',payload={'x':1})
    delivery=Delivery.objects.create(organization=s['org'],event=event,webhook=hook,next_attempt=timezone.now())
    with patch('core.tasks.send_webhook',return_value=503):assert deliver_outbox()==1
    delivery.refresh_from_db();assert delivery.status=='pending' and delivery.attempts==1
    with patch('core.tasks.send_webhook',return_value=200):assert deliver_outbox()==0
    replay=integrations.replay(s['user'],{'delivery':delivery.pk});assert replay['event_id']==str(event.event_id)
    with patch('core.tasks.send_webhook',return_value=204):deliver_outbox()
    delivery.refresh_from_db();assert delivery.status=='delivered'


def test_webhook_signing_and_pinned_dns():
    from types import SimpleNamespace
    event=SimpleNamespace(event_id='stable-id',topic='test',created_at=timezone.now(),payload={'sku':'A'})
    with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]),patch('core.domain.integrations.PinnedHTTPSConnection') as cls:
        cls.return_value.getresponse.return_value.status=204
        assert integrations.send_webhook('https://example.com/hook?version=1','secret',event)==204
        cls.assert_called_once_with('example.com','93.184.216.34')
        args=cls.return_value.request.call_args.args
        assert args[1]=='/hook?version=1' and args[3]['X-OSSCM-Event']=='stable-id'
        import hashlib,hmac
        expected=hmac.new(b'secret',args[3]['X-OSSCM-Timestamp'].encode()+b'.'+args[2],hashlib.sha256).hexdigest()
        assert args[3]['X-OSSCM-Signature']=='sha256='+expected


def test_later_cancellation_does_not_rewrite_historical_demand(setup):
    from core.domain import orders
    s=setup
    po=SupplyPolicy.objects.create(organization=s['org'],product=s['product'],warehouse=s['warehouse'],supplier=s['supplier'])
    created=orders.create_document(s['user'],{'kind':'sale','reference':'HISTORY','partner':s['customer'].pk,'warehouse':s['warehouse'].pk,'lines':[{'product':s['product'].pk,'quantity':10}]})
    orders.approve(s['user'],{'document':created['id']})
    Document.objects.filter(pk=created['id']).update(approved_at=timezone.now()-timedelta(weeks=3))
    before=planning.demand_history(po)[0]
    orders.cancel(s['user'],{'document':created['id']})
    after,cancelled,_,_,_=planning.demand_history(po)
    assert before==after and sum(after)==10 and sum(cancelled)==0  # Current week is excluded.
    _,cancelled,_,_,_=planning.demand_history(po,today=timezone.localdate()+timedelta(weeks=1))
    assert sum(cancelled)==10
