"""
Telling platforms about decisions: signed webhooks through an outbox.

The delivery row is written in the same transaction as the decision, then
sent after commit. So a decision is never announced without being saved,
and never saved without being announced — a failed POST is retried until
the platform accepts it (2xx) or the attempts run out.

Signature (the platform checks it before trusting a message):

    KYC-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>

Reject messages whose t is more than 5 minutes old (replays), and use the
KYC-Event-Id header to ignore a message already handled (retries).

Payloads carry status only — no names, ID numbers or images. A platform
that needs more asks the API, with its key.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import crypto
from .models import Platform, Verification, WebhookDelivery

logger = logging.getLogger(__name__)

# Seconds to wait after attempt 1, 2, … ; the last entry repeats. About a day
# and a half in all before giving up.
BACKOFF = (60, 300, 900, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 12 * 3600)
MAX_ATTEMPTS = len(BACKOFF)


def new_secret(platform: Platform) -> str:
    secret = 'whsec_' + secrets.token_urlsafe(32)
    platform.webhook_secret_encrypted = crypto.encrypt(secret)
    platform.save(update_fields=['webhook_secret_encrypted'])
    return secret


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()


def enqueue(v: Verification, event_type: str) -> WebhookDelivery | None:
    """Record a message for the verification's platform; sent after commit."""
    platform = v.subject.platform
    if not platform.webhook_url:
        return None
    d = WebhookDelivery(platform=platform, event_type=event_type, next_attempt_at=timezone.now())
    d.payload = {
        'id': str(d.id),
        'type': event_type,
        'created_at': timezone.now().isoformat(),
        'data': {
            'verification_id': str(v.id),
            'subject_ref': v.subject.external_ref,
            'level': v.level,
            'status': v.status,
            'reason': v.decision_reason,
            'decided_at': v.decided_at.isoformat() if v.decided_at else None,
        },
    }
    d.save()
    transaction.on_commit(lambda: _dispatch(d.id))
    return d


def _dispatch(delivery_id) -> None:
    if settings.CELERY_BROKER_URL:
        from .tasks import deliver_webhook
        deliver_webhook.delay(str(delivery_id))
    else:
        deliver(delivery_id)


def deliver(delivery_id) -> bool:
    # The row is locked while it is sent, so the worker that picked it up
    # after commit and the retry timer cannot both send it. A locked row is
    # skipped: someone else has it.
    with transaction.atomic():
        d = (WebhookDelivery.objects.select_for_update(skip_locked=True, of=('self',))
             .select_related('platform').filter(pk=delivery_id).first())
        if d is None:
            return False
        if d.delivered_at or d.next_attempt_at is None:
            return bool(d.delivered_at)
        return _send(d)


def _send(d: WebhookDelivery) -> bool:
    if not d.platform.webhook_secret_encrypted:
        # Never send a message signed with an empty secret: anyone could forge one.
        d.last_error, d.next_attempt_at = 'platform has no webhook secret', None
        d.save(update_fields=['last_error', 'next_attempt_at'])
        return False
    body = json.dumps(d.payload, separators=(',', ':'), sort_keys=True).encode()
    ts = int(time.time())
    secret = crypto.decrypt(d.platform.webhook_secret_encrypted)
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'kyc-service-webhooks/1',
        'KYC-Event-Id': str(d.id),
        'KYC-Signature': f't={ts},v1={sign(secret, ts, body)}',
    }
    error = ''
    try:
        r = requests.post(d.platform.webhook_url, data=body, headers=headers, timeout=10,
                          allow_redirects=False)
        if not 200 <= r.status_code < 300:
            error = f'HTTP {r.status_code}'
    except requests.RequestException as exc:
        error = type(exc).__name__

    now = timezone.now()
    d.attempts += 1
    if not error:
        d.delivered_at, d.next_attempt_at, d.last_error = now, None, ''
    else:
        d.last_error = error[:300]
        d.next_attempt_at = (now + timedelta(seconds=BACKOFF[d.attempts - 1])
                             if d.attempts < MAX_ATTEMPTS else None)
        logger.warning('Webhook %s to %s failed (attempt %d): %s',
                       d.id, d.platform.slug, d.attempts, error)
    d.save(update_fields=['attempts', 'delivered_at', 'next_attempt_at', 'last_error'])
    return not error


def retry_due(limit: int = 100) -> int:
    due = (WebhookDelivery.objects
           .filter(delivered_at__isnull=True, next_attempt_at__lte=timezone.now())
           .values_list('id', flat=True)[:limit])
    return sum(1 for pk in due if deliver(pk))
