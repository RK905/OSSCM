import getpass
from zoneinfo import ZoneInfo
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Organization, Membership
from core.domain.common import record


class Command(BaseCommand):
    help = 'Create the organization and first administrator interactively. No default credentials.'
    def add_arguments(self, parser):
        parser.add_argument('--organization', required=True)
        parser.add_argument('--username', required=True)
        parser.add_argument('--currency', default='USD')
        parser.add_argument('--timezone', default='UTC')
    @transaction.atomic
    def handle(self, *args, **options):
        if Organization.objects.exists():
            raise CommandError('Already initialized. Create team members through the application.')
        if len(options['currency']) != 3 or not options['currency'].isalpha():
            raise CommandError('Currency must be a three-letter code.')
        try:
            ZoneInfo(options['timezone'])
        except Exception:
            raise CommandError('Unknown IANA timezone.')
        password = getpass.getpass('Administrator password (12+ characters): ')
        if password != getpass.getpass('Repeat password: '):
            raise CommandError('Passwords do not match.')
        user = User(username=options['username'])
        validate_password(password, user)
        user.set_password(password)
        user.full_clean()
        user.save()
        org = Organization.objects.create(name=options['organization'], currency=options['currency'].upper(), timezone=options['timezone'])
        membership = Membership.objects.create(organization=org, user=user, role='administrator')
        record(user, 'identity.bootstrap', membership)
        self.stdout.write(self.style.SUCCESS('Initialized. Sign in and enroll MFA before production use.'))
