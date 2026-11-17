"""
Who is calling: a platform (API key) or a reviewer (staff session).

Platforms send   Authorization: Bearer kyc_<prefix>_<secret>
"""

import hmac
from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from .models import ApiKey, sha256_hex


class PlatformPrincipal:
    """Stands in for request.user when a platform calls."""

    is_authenticated = True
    is_staff = False

    def __init__(self, key: ApiKey):
        self.key = key
        self.platform = key.platform
        self.can_review = key.can_review

    def __str__(self):
        return f'platform:{self.platform.slug}'


class PlatformKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = get_authorization_header(request).decode('latin-1')
        if not header.startswith('Bearer kyc_'):
            return None  # not ours: let the session (reviewers) try
        parts = header[len('Bearer '):].strip().split('_', 2)
        if len(parts) != 3:
            raise AuthenticationFailed('Invalid API key.')
        key = (ApiKey.objects.select_related('platform')
               .filter(prefix=parts[1], revoked_at__isnull=True, platform__is_active=True).first())
        # Same failure for an unknown prefix and a wrong secret, compared in
        # constant time, so neither can be probed.
        expected = key.secret_hash if key else '0' * 64
        if not hmac.compare_digest(expected, sha256_hex(parts[2])) or key is None:
            raise AuthenticationFailed('Invalid API key.')
        now = timezone.now()
        if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=5):
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        return PlatformPrincipal(key), key

    def authenticate_header(self, request):
        return 'Bearer'


def is_platform(request) -> bool:
    return isinstance(request.user, PlatformPrincipal)


def is_staff_reviewer(request) -> bool:
    u = request.user
    return not is_platform(request) and bool(u and u.is_authenticated and u.is_active and u.is_staff)


class IsPlatform(BasePermission):
    message = 'This endpoint takes a platform API key.'

    def has_permission(self, request, view):
        return is_platform(request)


class IsReviewer(BasePermission):
    """KYC staff, or a platform key allowed to decide on its own users."""

    message = 'Reviewing needs a staff account or a platform key with review rights.'

    def has_permission(self, request, view):
        return is_staff_reviewer(request) or (is_platform(request) and request.user.can_review)
