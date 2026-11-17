from celery import shared_task
from django.conf import settings

from . import services, webhooks


@shared_task
def deliver_webhook(delivery_id: str) -> bool:
    return webhooks.deliver(delivery_id)


@shared_task
def retry_webhooks() -> int:
    return webhooks.retry_due()


@shared_task
def purge_documents() -> int:
    return services.purge_documents(settings.KYC_DOCUMENT_RETENTION_DAYS)
