"""
KYC service — production.

This service holds scans of national ID cards, so it fails closed: no bucket,
no encryption keys or the default admin path means it does not start.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from decouple import Csv, config

DEBUG = False

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
# Railway's health check calls over plain HTTP inside the network.
SECURE_REDIRECT_EXEMPT = [r'^health/$']
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 8 * 3600
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

for _name in ('S3_BUCKET', 'S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY'):
    if not globals()[_name]:
        raise ImproperlyConfigured(f'{_name} is not set: this service cannot store ID documents.')
if ADMIN_URL.strip('/') == 'admin':  # noqa: F405
    raise ImproperlyConfigured('ADMIN_URL is the default "admin/". Set a private path.')
