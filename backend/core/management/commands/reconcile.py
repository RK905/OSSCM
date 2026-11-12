from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from core.models import StockBalance, Reservation


class Command(BaseCommand):
    help = 'Read-only check: ledger totals and reservations must match every stock balance.'
    def handle(self, *args, **options):
        mismatches = []
        balances = StockBalance.objects.annotate(ledger_hand=Sum('movement__quantity'), ledger_reserved=Sum('movement__reserved_delta'))
        for b in balances.iterator(chunk_size=1000):
            reserved = Reservation.objects.filter(balance=b).aggregate(n=Sum('quantity'))['n'] or Decimal(0)
            if b.on_hand != (b.ledger_hand or 0) or b.reserved != (b.ledger_reserved or 0) or b.reserved != reserved:
                mismatches.append(b.pk)
        if mismatches:
            raise CommandError(f'Balance reconciliation failed: {mismatches[:100]}')
        self.stdout.write(self.style.SUCCESS(f'Reconciled {balances.count()} balances.'))
