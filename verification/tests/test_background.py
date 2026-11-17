"""Webhook retries and document retention."""

from datetime import timedelta

from django.utils import timezone

from conftest import fill
from verification import services, webhooks
from verification.models import Verification, WebhookDelivery


def approved(api, staff_api, ref='u1'):
    vid = api.post('/v1/verifications/', {'subject_ref': ref}).data['id']
    fill(api, vid)
    api.post(f'/v1/verifications/{vid}/submit/')
    staff_api.post(f'/v1/review/verifications/{vid}/decision/', {'decision': 'approved'})
    return Verification.objects.get(pk=vid)


def test_a_failed_webhook_is_retried_later_then_given_up(api, staff_api, webhook_calls):
    webhook_calls.status = 500
    approved(api, staff_api)
    d = WebhookDelivery.objects.get(event_type='verification.approved')
    assert d.attempts == 1 and d.delivered_at is None and d.last_error == 'HTTP 500'
    assert d.next_attempt_at > timezone.now()

    # Nothing is due yet.
    assert webhooks.retry_due() == 0

    # Make it due and let the platform recover.
    WebhookDelivery.objects.filter(pk=d.pk).update(next_attempt_at=timezone.now())
    webhook_calls.status = 200
    assert webhooks.retry_due() == 1
    d.refresh_from_db()
    assert d.delivered_at and d.next_attempt_at is None

    # A platform that never recovers is given up on after MAX_ATTEMPTS.
    webhook_calls.status = 503
    d2 = WebhookDelivery.objects.get(event_type='verification.submitted')
    WebhookDelivery.objects.filter(pk=d2.pk).update(delivered_at=None, next_attempt_at=timezone.now(),
                                                    attempts=webhooks.MAX_ATTEMPTS - 1)
    webhooks.deliver(d2.pk)
    d2.refresh_from_db()
    assert d2.attempts == webhooks.MAX_ATTEMPTS and d2.next_attempt_at is None


def test_scans_are_deleted_after_the_retention_period(api, staff_api, webhook_calls, bucket):
    v = approved(api, staff_api)
    assert services.purge_documents(90) == 0, 'too recent'

    Verification.objects.filter(pk=v.pk).update(decided_at=timezone.now() - timedelta(days=91))
    assert services.purge_documents(90) == 3
    assert not bucket.objects
    assert all(d.purged_at and not d.storage_key for d in v.documents.all())
    assert v.events.filter(action='documents_purged').count() == 1
    # The decision itself survives.
    v.refresh_from_db()
    assert v.status == 'approved' and v.id_number_fingerprint


def test_a_platform_without_a_secret_is_never_sent_a_message(api, staff_api, webhook_calls, platform):
    platform.webhook_secret_encrypted = ''
    platform.save()
    approved(api, staff_api)
    assert webhook_calls == []
    assert set(WebhookDelivery.objects.values_list('last_error', flat=True)) == {'platform has no webhook secret'}
