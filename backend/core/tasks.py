from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from core.models import Delivery
from core.identity import decrypt
from core.domain.integrations import send_webhook


@shared_task
def deliver_outbox(limit=100):
    processed = 0
    for _ in range(limit):
        with transaction.atomic():
            delivery = Delivery.objects.select_for_update(skip_locked=True).filter(status='pending', next_attempt__lte=timezone.now(), webhook__active=True).select_related('event', 'webhook').order_by('next_attempt').first()
            if not delivery:
                break
            # Bounded I/O under row lock prevents two workers sending the same attempt.
            # A crash after delivery can still repeat an event: consumers deduplicate event IDs.
            code = None
            try:
                code = send_webhook(delivery.webhook.url, decrypt(delivery.webhook.secret), delivery.event)
            except Exception:
                # Never persist exception strings: they can contain credentials or remote bodies.
                pass
            delivery.attempts += 1
            delivery.response_code = code
            delivery.status = 'delivered' if code and 200 <= code < 300 else ('failed' if delivery.attempts >= 8 else 'pending')
            delivery.next_attempt = timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** delivery.attempts))
            delivery.save(update_fields=['attempts', 'response_code', 'status', 'next_attempt'])
            processed += 1
    return processed
