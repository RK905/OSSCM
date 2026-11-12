import json
import os
import platform
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction, connections
from core.models import Organization, Membership, Product, Warehouse, Bin, StockIdentity, StockBalance, Movement
from core.identity import credential_create


class Command(BaseCommand):
    help = 'Seed or run the reproducible inventory HTTP benchmark in a disposable *bench* database.'
    def add_arguments(self, parser):
        parser.add_argument('--seed', action='store_true')
        parser.add_argument('--url', default='http://127.0.0.1:8001')
        parser.add_argument('--requests-per-user', type=int, default=100)
        parser.add_argument('--output', default='benchmark-results.json')
    def handle(self, *args, **options):
        if not settings.DEBUG or 'bench' not in connection.settings_dict['NAME'] or connection.vendor != 'postgresql':
            raise CommandError('Requires DEBUG=true and a dedicated PostgreSQL database whose name contains bench.')
        if options['seed']:
            self.seed()
            return
        users=list(User.objects.filter(username__startswith='bench-').order_by('pk'))
        if len(users)!=20:
            raise CommandError('Seed first.')
        tokens=[credential_create(u,{'name':'Benchmark run','scopes':['read','warehouse'],'days':1})['token'] for u in users]
        products=list(Product.objects.order_by('pk').values_list('pk',flat=True))
        bins=list(Bin.objects.order_by('pk').values_list('pk',flat=True))
        initial_movements=Movement.objects.count()
        def worker(index):
            samples=[];errors=[]
            for n in range(options['requests_per_user']):
                write=n%2==1
                url=options['url']+('/api/v1/commands/inventory/adjust/' if write else f'/api/v1/balances/?page={1+(index+n)%100}')
                body=json.dumps({'product':products[(index*101+n)%len(products)],'bin':bins[index%5],'quantity':'1','reason':'Synthetic benchmark adjustment'}).encode() if write else None
                request=Request(url,data=body,headers={'Authorization':'Bearer '+tokens[index],'Content-Type':'application/json','Idempotency-Key':str(uuid.uuid4())})
                begin=time.perf_counter()
                try:
                    with urlopen(request,timeout=30) as response:
                        response.read()
                        if response.status!=200:errors.append(str(response.status))
                except Exception as exc:errors.append(type(exc).__name__+': '+str(exc))
                samples.append(('write' if write else 'read',time.perf_counter()-begin))
            return samples,errors
        begin=time.perf_counter()
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(worker,range(20)))
        elapsed=time.perf_counter()-begin
        samples=[x for sample,_ in results for x in sample];errors=[x for _,error in results for x in error]
        stats={}
        for kind in ['read','write']:
            times=sorted(t for k,t in samples if k==kind)
            stats[kind]={'requests':len(times),'p50_ms':round(statistics.median(times)*1000,2),'p95_ms':round(times[max(0,int(len(times)*.95)-1)]*1000,2),'max_ms':round(max(times)*1000,2)}
        report={'platform':platform.platform(),'machine':platform.machine(),'logical_cpus':os.cpu_count(),'python':platform.python_version(),
                'postgresql':connection.pg_version,'concurrent_users':20,'products':len(products),'warehouses':5,'baseline_movements':initial_movements,
                'http_endpoint':options['url'],'elapsed_seconds':round(elapsed,2),'requests_per_second':round(len(samples)/elapsed,2),
                'results':stats,'error_count':len(errors),'errors':errors[:10],
                'passes_target':not errors and all(s['p95_ms']<1000 for s in stats.values()),
                'methodology':'Actual HTTP requests: 20 independent bearer credentials, alternating paginated balance reads and atomic adjustment writes. Warm database; no WAN latency. Synthetic fixture insertion bypasses operational services only during setup.'}
        with open(options['output'],'w') as f:json.dump(report,f,indent=2)
        self.stdout.write(json.dumps(report,indent=2))
        if errors:raise CommandError('Benchmark had HTTP errors.')
    @transaction.atomic
    def seed(self):
        if Organization.objects.exists():raise CommandError('Benchmark seed requires an empty database.')
        org=Organization.objects.create(name='Synthetic benchmark')
        users=User.objects.bulk_create([User(username=f'bench-{i}',password='!') for i in range(20)])
        Membership.objects.bulk_create([Membership(organization=org,user=u,role='administrator') for u in users])
        warehouses=Warehouse.objects.bulk_create([Warehouse(organization=org,code=f'WH-{i}',name=f'Warehouse {i}') for i in range(5)])
        bins=Bin.objects.bulk_create([Bin(organization=org,warehouse=w,code='MAIN') for w in warehouses])
        products=Product.objects.bulk_create([Product(organization=org,sku=f'BENCH-{i:05d}',name=f'Benchmark product {i}') for i in range(10000)],batch_size=1000)
        identities=StockIdentity.objects.bulk_create([StockIdentity(organization=org,product=p) for p in products],batch_size=1000)
        StockBalance.objects.bulk_create([StockBalance(organization=org,identity=i,bin=b,on_hand=20) for i in identities for b in bins],batch_size=1000)
        with connection.cursor() as cursor:
            cursor.execute('''INSERT INTO core_movement (organization_id,created_at,balance_id,quantity,reserved_delta,kind,actor_id,reason)
                SELECT %s, NOW(), b.id, 1, 0, 'adjustment', %s, 'Synthetic benchmark fixture'
                FROM core_stockbalance b CROSS JOIN generate_series(1,20)''',[org.pk,users[0].pk])
        self.stdout.write('Seeded 10,000 SKUs, five warehouses, 50,000 balances, and 1,000,000 movements.')
