import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
import pyotp
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from core.models import APIKey, LoginAttempt, Membership, Organization, Warehouse
from core.domain.common import require, member, record, get

SCOPES = {'read', 'catalog', 'purchasing', 'warehouse', 'sales', 'planning', 'integrations'}


def cipher():
    key = settings.FIELD_ENCRYPTION_KEY or base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode()


class APIKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return None
        token = header[7:]
        key = APIKey.objects.select_related('user', 'user__membership').filter(digest=hashlib.sha256(token.encode()).hexdigest(), revoked=False, expires_at__gt=timezone.now()).first()
        if not key or not key.user.is_active or key.organization_id != key.user.membership.organization_id:
            raise AuthenticationFailed('Invalid or expired API credential.')
        return key.user, key
    def authenticate_header(self, request):
        return 'Bearer'


class AccessPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        m = member(request.user)
        if request.auth:
            scope = 'read' if request.method in ['GET', 'HEAD', 'OPTIONS'] else getattr(view, 'scope', 'admin')
            if scope not in request.auth.scopes:
                raise PermissionDenied('API credential does not have the required scope.')
        elif settings.MFA_REQUIRED and m.role == 'administrator' and (not m.totp_enabled or not request.session.get('mfa_verified')):
            raise PermissionDenied('Enroll and verify MFA before using the application.')
        return True


def session_data(request):
    if not request.user.is_authenticated:
        return {'user': None, 'csrf': get_token(request)}
    m = member(request.user)
    return {'user': {'id': request.user.pk, 'username': request.user.username, 'role': m.role,
                     'organization': m.organization.name, 'currency': m.organization.currency,
                     'mfa_enabled': m.totp_enabled, 'mfa_required': settings.MFA_REQUIRED and m.role == 'administrator',
                     'mfa_verified': bool(request.session.get('mfa_verified'))}, 'csrf': get_token(request)}


@csrf_protect
@require_http_methods(['GET', 'POST'])
def session_view(request):
    if request.method == 'GET':
        return JsonResponse(session_data(request))
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'detail': 'Invalid JSON.'}, status=400)
    if not isinstance(data, dict) or any(not isinstance(data.get(k, ''), str) for k in ['username', 'password', 'otp', 'action']):
        return JsonResponse({'detail': 'Expected a JSON object with string credentials.'}, status=400)
    if data.get('action') == 'logout':
        logout(request)
        return JsonResponse({'user': None})
    username = str(data.get('username', ''))[:150]
    # Database-backed throttles survive multiple gunicorn workers. Do not trust forwarded IP headers.
    identifiers = ['username:' + username.lower(), 'ip:' + (request.META.get('HTTP_X_REAL_IP', '') if settings.TRUST_PROXY_IP else request.META.get('REMOTE_ADDR', ''))]
    with transaction.atomic():
        attempts = []
        for identifier in sorted(identifiers):
            attempt, _ = LoginAttempt.objects.get_or_create(key=hashlib.sha256(identifier.encode()).hexdigest(), defaults={'started_at': timezone.now()})
            attempt = LoginAttempt.objects.select_for_update().get(pk=attempt.pk)
            if timezone.now() - attempt.started_at > timedelta(minutes=15):
                attempt.failures = 0
                attempt.started_at = timezone.now()
            attempts.append(attempt)
        if any(a.failures >= 10 for a in attempts):
            return JsonResponse({'detail': 'Too many attempts. Try again in 15 minutes.'}, status=429)
        user = authenticate(request, username=username, password=data.get('password', ''))
        valid = user is not None and Membership.objects.filter(user=user).exists()
        verified = False
        if valid:
            m = Membership.objects.select_for_update().get(user=user)
            if m.totp_enabled:
                step = int(time.time()) // 30
                verified = step > m.last_totp_step and pyotp.TOTP(decrypt(m.totp_secret)).verify(str(data.get('otp', '')))
                valid = verified
                if verified:
                    m.last_totp_step = step
                    m.save(update_fields=['last_totp_step'])
        for attempt in attempts:
            if not valid:
                attempt.failures += 1
            attempt.save()
        if not valid:
            return JsonResponse({'detail': 'Invalid credentials or verification code.'}, status=401)
        login(request, user)
        request.session['mfa_verified'] = verified
        record(user, 'identity.login', m)
        return JsonResponse(session_data(request))


@csrf_protect
@require_http_methods(['POST'])
def mfa_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({'detail': 'Sign in first.'}, status=401)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return JsonResponse({'detail': 'Expected a JSON object.'}, status=400)
        with transaction.atomic():
            attempt, _ = LoginAttempt.objects.get_or_create(key=hashlib.sha256(f'mfa:{request.user.pk}'.encode()).hexdigest(), defaults={'started_at': timezone.now()})
            attempt = LoginAttempt.objects.select_for_update().get(pk=attempt.pk)
            if timezone.now() - attempt.started_at > timedelta(minutes=15):
                attempt.failures = 0
                attempt.started_at = timezone.now()
            if attempt.failures >= 10:
                return JsonResponse({'detail': 'Too many verification attempts. Try again in 15 minutes.'}, status=429)
            attempt.failures += 1
            attempt.save()
            m = Membership.objects.select_for_update().get(user=request.user)
            if m.totp_enabled:
                return JsonResponse({'detail': 'MFA already enabled. Use the administrator recovery command if necessary.'}, status=400)
            if data.get('action') == 'begin':
                secret = pyotp.random_base32()
                m.totp_secret = encrypt(secret)
                m.save(update_fields=['totp_secret'])
                return JsonResponse({'secret': secret, 'uri': pyotp.TOTP(secret).provisioning_uri(request.user.username, issuer_name='OSSCM')})
            if not m.totp_secret or not pyotp.TOTP(decrypt(m.totp_secret)).verify(str(data.get('otp', ''))):
                return JsonResponse({'detail': 'Invalid verification code.'}, status=400)
            m.totp_enabled = True
            m.last_totp_step = int(time.time()) // 30
            m.save(update_fields=['totp_enabled', 'last_totp_step'])
            request.session['mfa_verified'] = True
            record(request.user, 'identity.mfa_enabled', m)
            return JsonResponse(session_data(request))
    except (ValueError, TypeError):
        return JsonResponse({'detail': 'Invalid request.'}, status=400)


@transaction.atomic
def credential_create(actor, data):
    require(actor)
    scopes = data.get('scopes', ['read'])
    if not isinstance(scopes, list) or not scopes or not set(scopes) <= SCOPES:
        raise ValidationError('Invalid API scopes.')
    days = int(data.get('days', 90))
    if not 1 <= days <= 365:
        raise ValidationError('Credential lifetime must be 1–365 days.')
    token = 'osscm_' + secrets.token_urlsafe(32)
    key = APIKey.objects.create(organization=member(actor).organization, user=actor, name=data['name'],
                               digest=hashlib.sha256(token.encode()).hexdigest(), prefix=token[:12], scopes=scopes,
                               expires_at=timezone.now() + timedelta(days=days))
    record(actor, 'identity.credential_created', key, {'scopes': scopes})
    return {'id': key.pk, 'token': token, 'expires_at': key.expires_at.isoformat()}


@transaction.atomic
def credential_revoke(actor, data):
    require(actor)
    key = get(actor, APIKey, data['credential'], lock=True)
    key.revoked = True
    key.save(update_fields=['revoked'])
    record(actor, 'identity.credential_revoked', key)
    return {'id': key.pk, 'revoked': True}


@transaction.atomic
def user_create(actor, data):
    require(actor)
    if data.get('role') not in dict(Membership.ROLES):
        raise ValidationError('Invalid role.')
    user = User(username=data['username'], email=data.get('email', ''))
    validate_password(data['password'], user)
    user.full_clean(exclude=['password'])
    user.set_password(data['password'])
    user.save()
    m = Membership.objects.create(organization=member(actor).organization, user=user, role=data['role'])
    m.warehouses.set([get(actor, Warehouse, pk) for pk in data.get('warehouses', [])])
    record(actor, 'identity.user_created', m, {'role': m.role})
    return {'id': user.pk}


@transaction.atomic
def permissions_update(actor, data):
    require(actor)
    m = get(actor, Membership, data['membership'], lock=True)
    if m.user_id == actor.pk:
        raise ValidationError('Another administrator must change your access.')
    if data['role'] not in dict(Membership.ROLES):
        raise ValidationError('Invalid role.')
    m.role = data['role']
    m.save(update_fields=['role'])
    m.warehouses.set([get(actor, Warehouse, pk) for pk in data.get('warehouses', [])])
    m.user.is_active = bool(data.get('active', True))
    m.user.save(update_fields=['is_active'])
    record(actor, 'identity.permissions_updated', m, {'role': m.role, 'warehouses': list(m.warehouses.values_list('pk', flat=True)), 'active': m.user.is_active})
    return {'id': m.pk}


from drf_spectacular.extensions import OpenApiAuthenticationExtension

class APIKeySchema(OpenApiAuthenticationExtension):
    target_class = 'core.identity.APIKeyAuthentication'
    name = 'bearerAuth'
    def get_security_definition(self, auto_schema):
        return {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'OSSCM API credential'}
