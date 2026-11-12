from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from django.db import connection, connections
from django.contrib.auth.models import User
from rest_framework.exceptions import ValidationError
from core.models import Membership, StockBalance, Reservation, Movement
from core.domain import orders
from core.domain.common import execute
from .test_operations import stock, order


@pytest.mark.django_db(transaction=True)
def test_concurrent_allocations_cannot_oversell(setup):
    if connection.vendor!='postgresql':pytest.skip('Requires PostgreSQL row locking')
    s=setup;balance=stock(s,5)['balance']
    lines=[order(s,'sale',4,f'SALE-{i}').pk for i in range(2)]
    users=[]
    for i in range(2):
        u=User.objects.create_user(f'actor-{i}');Membership.objects.create(user=u,organization=s['org'],role='administrator');users.append(u.pk)
    barrier=Barrier(2)
    def run(index):
        try:
            user=User.objects.get(pk=users[index]);barrier.wait(timeout=10)
            try:
                execute(user,f'concurrent-{index}','allocate',{},lambda:orders.allocate(user,{'line':lines[index],'balance':balance,'quantity':4}))
                return 'ok'
            except ValidationError:return 'insufficient'
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sorted(results)==['insufficient','ok']
    b=StockBalance.objects.get(pk=balance);assert b.on_hand==5 and b.reserved==4


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_requests_post_once(setup):
    if connection.vendor!='postgresql':pytest.skip('Requires PostgreSQL row locking')
    s=setup;barrier=Barrier(2)
    def run(_):
        try:
            user=User.objects.get(pk=s['user'].pk);barrier.wait(timeout=10)
            return execute(user,'same-key','opening',{},lambda:stock({**s,'user':user},5))
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert results[0]==results[1] and Movement.objects.count()==1
