import os
from pathlib import Path
from urllib.parse import urlparse
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('SECRET_KEY is required outside DEBUG mode')
    SECRET_KEY = 'development-only-not-for-production-change-this-key'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv('CSRF_TRUSTED_ORIGINS', 'http://127.0.0.1:5173,http://localhost:5173' if DEBUG else '').split(',') if x]
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
                  'django.contrib.staticfiles', 'rest_framework', 'drf_spectacular', 'drf_spectacular_sidecar', 'core']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://localhost/osscm')
if DATABASE_URL == 'sqlite://':
    if not DEBUG:
        raise ImproperlyConfigured('SQLite is allowed only for local development; PostgreSQL is required for production')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'dev.sqlite3'}}
else:
    db = urlparse(DATABASE_URL)
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': db.path.lstrip('/'),
                            'USER': db.username or '', 'PASSWORD': db.password or '',
                            'HOST': db.hostname or '', 'PORT': db.port or 5432, 'CONN_MAX_AGE': 60}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_TZ = True
USE_I18N = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['core.identity.APIKeyAuthentication', 'rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['core.identity.AccessPermission'],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination', 'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'core.api.exception_handler',
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.UserRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'user': '600/minute'},
}
SPECTACULAR_SETTINGS = {'TITLE': 'OSSCM API', 'DESCRIPTION': 'Operational commands require Idempotency-Key. See docs/API.md.', 'VERSION': '1.0.0', 'SERVE_INCLUDE_SCHEMA': False}
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_AGE = 8 * 60 * 60
MFA_REQUIRED = os.getenv('MFA_REQUIRED', str(not DEBUG)).lower() == 'true'
FIELD_ENCRYPTION_KEY = os.getenv('FIELD_ENCRYPTION_KEY', '')
if not DEBUG and not FIELD_ENCRYPTION_KEY:
    raise ImproperlyConfigured('FIELD_ENCRYPTION_KEY (Fernet key) is required in production')
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'amqp://guest:guest@localhost:5672//')
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {'webhook-outbox': {'task': 'core.tasks.deliver_outbox', 'schedule': 30.0}}
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

TRUST_PROXY_IP = os.getenv('TRUST_PROXY_IP', 'false').lower() == 'true'

SPECTACULAR_SETTINGS.update({'SWAGGER_UI_DIST': 'SIDECAR', 'SWAGGER_UI_FAVICON_HREF': 'SIDECAR', 'ENUM_NAME_OVERRIDES': {'PartnerKindEnum': [('supplier', 'Supplier'), ('customer', 'Customer')], 'DocumentKindEnum': [('purchase', 'Purchase order'), ('sale', 'Sales order')]}})

TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True, 'OPTIONS': {'context_processors': []}}]

if not DEBUG and len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('Production SECRET_KEY must be at least 50 characters.')
if FIELD_ENCRYPTION_KEY:
    from cryptography.fernet import Fernet
    try:
        Fernet(FIELD_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError):
        raise ImproperlyConfigured('FIELD_ENCRYPTION_KEY must be a valid Fernet key.')
