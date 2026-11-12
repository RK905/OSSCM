from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.sessions.models import Session
from core.models import Membership
from core.domain.common import record


class Command(BaseCommand):
    help = 'Audited operator recovery for a lost authenticator; invalidates all sessions for that user.'
    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--confirm', action='store_true')
    @transaction.atomic
    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError('Pass --confirm after verifying the account owner out of band.')
        m = Membership.objects.select_for_update().select_related('user').get(user__username=options['username'])
        m.totp_enabled = False
        m.totp_secret = ''
        m.last_totp_step = -1
        m.save(update_fields=['totp_enabled', 'totp_secret', 'last_totp_step'])
        for session in Session.objects.iterator():
            if str(session.get_decoded().get('_auth_user_id')) == str(m.user_id):
                session.delete()
        record(m.user, 'identity.mfa_operator_reset', m)
        self.stdout.write('MFA reset. The administrator must enroll again at next login.')
