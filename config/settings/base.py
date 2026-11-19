"""
KYC service — settings shared by every environment.

Environment files (local / staging / production) extend this one and list
only what differs. Secrets come from the environment and have no defaults
here, so a deployment that forgets one refuses to start instead of running
with a guessable value.
"""

from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = False
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())
ENVIRONMENT_NAME = config('ENVIRONMENT_NAME', default='production')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'verification',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]

# Connections are reused for a minute; a health check first means one that
# Postgres closed meanwhile (restart, idle timeout) is replaced, not an error.
DATABASES = {'default': dj_database_url.parse(config('DATABASE_URL'), conn_max_age=60,
                                              conn_health_checks=True)}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Africa/Accra'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# The Django admin (reviewer back office) lives at a private path in deployed
# environments; launch the service with a value that is not "admin/".
ADMIN_URL = config('ADMIN_URL', default='admin/')

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
# Platforms authenticate with an API key; reviewers signed in to this
# service's admin use their session. Nothing is public except /health/.
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'verification.auth.PlatformKeyAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'KYC Service API',
    'DESCRIPTION': 'Identity verification shared by every platform: start a check, '
                   'upload documents, submit, and receive a signed webhook on the decision.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ---------------------------------------------------------------------------
# Personal data
# ---------------------------------------------------------------------------
# ID numbers are encrypted with these Fernet keys. The FIRST key encrypts;
# every key decrypts — so rotation is: put a new key first, keep the old one
# after it, re-save, then drop the old one. Generate a key with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
KYC_FIELD_KEYS = config('KYC_FIELD_KEYS', cast=Csv())

# Keyed hash of each ID number, so the same card can be found on two
# accounts without decrypting anything. Must never change once data exists:
# a new key makes every stored fingerprint unmatchable.
KYC_HASH_KEY = config('KYC_HASH_KEY')

# Days after a final decision before ID scans and selfies are deleted from
# the bucket. The decision, the fingerprint and the audit trail are kept.
KYC_DOCUMENT_RETENTION_DAYS = config('KYC_DOCUMENT_RETENTION_DAYS', default=90, cast=int)

# ---------------------------------------------------------------------------
# File storage — a private S3-compatible bucket (Railway Storage Buckets)
# ---------------------------------------------------------------------------
S3_BUCKET = config('S3_BUCKET', default='')
S3_ENDPOINT_URL = config('S3_ENDPOINT_URL', default='')
S3_ACCESS_KEY_ID = config('S3_ACCESS_KEY_ID', default='')
S3_SECRET_ACCESS_KEY = config('S3_SECRET_ACCESS_KEY', default='')
S3_REGION = config('S3_REGION', default='auto')
S3_ADDRESSING_STYLE = config('S3_ADDRESSING_STYLE', default='virtual')
# Reviewer links to ID scans are short-lived on purpose.
S3_URL_EXPIRY_SECONDS = config('S3_URL_EXPIRY_SECONDS', default=300, cast=int)
S3_KEY_PREFIX = 'kyc'

# ---------------------------------------------------------------------------
# Background work — webhook delivery and document clean-up
# ---------------------------------------------------------------------------
# Without a broker, webhooks are sent inline after the decision commits and
# retries wait for the next decision; set REDIS_URL and run the worker.
CELERY_BROKER_URL = config('REDIS_URL', default='')
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    'retry-webhooks': {'task': 'verification.tasks.retry_webhooks', 'schedule': 60.0},
    'purge-documents': {'task': 'verification.tasks.purge_documents', 'schedule': 6 * 3600.0},
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
    'root': {'handlers': ['console'], 'level': config('LOG_LEVEL', default='INFO')},
}
