import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from core.models import Organization, Membership, SupplyPolicy, Document, DocumentLine
from core.domain import catalog, inventory, orders


class Command(BaseCommand):
    help = 'Create a disposable local demo in an empty database. Never runs in production.'
    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG or Organization.objects.exists():
            raise CommandError('Demo requires DEBUG=true and an empty database.')
        password = secrets.token_urlsafe(18)
        user = User.objects.create_user('demo', password=password)
        org = Organization.objects.create(name='Northline Supply', currency='USD')
        Membership.objects.create(organization=org, user=user, role='administrator')
        wh1 = catalog.create(user, 'warehouses', {'code':'NYC','name':'Brooklyn distribution'})['id']
        wh2 = catalog.create(user, 'warehouses', {'code':'EWR','name':'Newark fulfillment'})['id']
        bins = [catalog.create(user, 'bins', {'warehouse':wh1,'code':'A-01'})['id'], catalog.create(user,'bins',{'warehouse':wh2,'code':'B-01'})['id']]
        supplier = catalog.create(user,'partners',{'name':'Atlas Components','kind':'supplier','email':'supply@example.com'})['id']
        customer = catalog.create(user,'partners',{'name':'Harbor Retail','kind':'customer','email':'orders@example.com'})['id']
        products = []
        for sku,name,tracking,qty in [('PKG-001','Recycled shipping cartons','none',480),('ELE-024','USB-C power adapter','lot',240),('AUT-108','Precision bearing kit','lot',120),('CGS-016','Botanical hand wash','lot',96),('TEC-042','Industrial barcode scanner','serial',1)]:
            pid=catalog.create(user,'products',{'sku':sku,'name':name,'tracking':tracking,'unit':'each','purchase_unit':'case' if tracking!='serial' else 'each','purchase_factor':12 if tracking!='serial' else 1})['id']
            products.append(pid)
            po=orders.create_document(user,{'kind':'purchase','reference':f'PO-2026-{pid:03d}','partner':supplier,'warehouse':wh1,'due_date':str(timezone.localdate()+timedelta(days=5)), 'lines':[{'product':pid,'quantity':qty+24}]})
            orders.approve(user,{'document':po['id']})
            line=DocumentLine.objects.get(document_id=po['id'])
            receipt=orders.receive(user,{'line':line.pk,'bin':bins[0],'quantity':qty,'code':'' if tracking=='none' else f'LOT-{pid:03d}' if tracking=='lot' else 'SN-00001','expires_on':str(timezone.localdate()+timedelta(days=21)) if tracking=='lot' else None})
            catalog.create(user,'policies',{'product':pid,'warehouse':wh1,'supplier':supplier,'lead_days':14,'review_days':7,'safety_stock':12,'reorder_point':100,'target_stock':300,'minimum_order':12,'pack_size':12})
            if tracking!='serial':
                sale=orders.create_document(user,{'kind':'sale','reference':f'SO-2026-{pid:03d}','partner':customer,'warehouse':wh1,'due_date':str(timezone.localdate()+timedelta(days=2)), 'lines':[{'product':pid,'quantity':36}]})
                orders.approve(user,{'document':sale['id']})
                sl=DocumentLine.objects.get(document_id=sale['id'])
                reservation=orders.allocate(user,{'line':sl.pk,'balance':receipt['balance'],'quantity':36})
                orders.ship(user,{'line':sl.pk,'reservation':reservation['id'],'quantity':12,'carrier':'Demo carrier','tracking':f'DEMO-{pid:04d}'})
                transfer=inventory.transfer_send(user,{'balance':receipt['balance'],'destination':bins[1],'quantity':24})
                if pid%2==0:
                    inventory.transfer_receive(user,{'transfer':transfer['id'],'quantity':24})
        # Synthetic order demand is explicitly demo-only and not presented as real business history.
        for week in range(1,65):
            order=Document.objects.create(organization=org,kind='sale',reference=f'DEMO-HISTORY-{week:03d}',partner_id=customer,warehouse_id=wh1,status='approved',approved_at=timezone.now()-timedelta(weeks=week),notes='Synthetic historical demo demand')
            DocumentLine.objects.create(organization=org,document=order,product_id=products[0],quantity=20+(week%4)*8)
        self.stdout.write(self.style.SUCCESS('Demo created. Username: demo'))
        self.stdout.write('One-time generated password: ' + password)
