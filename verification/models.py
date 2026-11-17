"""
The KYC data model.

    Platform ──< ApiKey
        │
        └──< Subject ──< Verification ──< Document
                              │
                              └──< Event        (append-only audit trail)

A PLATFORM is a client application (ONA Records, Eagles & Eaglets). A
SUBJECT is one of its users, known here only by the platform's own id for
them — this service has no user accounts and no profile data. A
VERIFICATION is one attempt at one level for one subject; a rejected person
starts a new one, so the history of attempts stays intact.

Identity only. What a platform needs beyond that (a CV, a church, a
portfolio) belongs in the platform, not here.
"""

import hashlib
import secrets
import uuid

from django.db import models
from django.db.models import Q


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Platform(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True, help_text='Short stable name, e.g. "ona-records".')
    name = models.CharField(max_length=120)
    webhook_url = models.URLField(blank=True, help_text='Where decisions are POSTed. Blank: no webhooks.')
    # Encrypted with the field keys: it must be readable to sign with, so it
    # cannot be hashed like an API key.
    webhook_secret_encrypted = models.TextField(blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ApiKey(models.Model):
    """
    A platform's credential. Shown once at creation as kyc_<prefix>_<secret>;
    only the prefix (to find the row) and a hash of the secret are stored.
    A plain SHA-256 is right here, unlike for passwords: the secret is 256
    random bits, so there is nothing to brute-force.
    """

    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=80, help_text='Where this key is used, e.g. "ONA staging backend".')
    prefix = models.CharField(max_length=16, unique=True, editable=False)
    secret_hash = models.CharField(max_length=64, editable=False)
    can_review = models.BooleanField(
        default=False, help_text="May approve or reject this platform's own verifications.")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.platform.slug}: {self.name} (kyc_{self.prefix}_…)'

    @classmethod
    def issue(cls, platform: Platform, name: str, *, can_review: bool = False) -> tuple['ApiKey', str]:
        """Create a key and return it with the raw value — the only time it exists."""
        prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        key = cls.objects.create(platform=platform, name=name, prefix=prefix,
                                 secret_hash=sha256_hex(secret), can_review=can_review)
        return key, f'kyc_{prefix}_{secret}'


class Subject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, related_name='subjects')
    external_ref = models.CharField(max_length=128, help_text="The platform's own id for this user.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['platform', 'external_ref'],
                                               name='one_subject_per_platform_ref')]

    def __str__(self):
        return f'{self.platform.slug}:{self.external_ref}'


class Status(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    SUBMITTED = 'submitted', 'Submitted'
    IN_REVIEW = 'in_review', 'In review'
    CHANGES_REQUESTED = 'changes_requested', 'Changes requested'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


OPEN_STATUSES = (Status.DRAFT, Status.SUBMITTED, Status.IN_REVIEW, Status.CHANGES_REQUESTED)


class IdType(models.TextChoices):
    GHANA_CARD = 'ghana_card', 'Ghana Card'
    PASSPORT = 'passport', 'Passport'


class Verification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='verifications')
    level = models.CharField(max_length=32)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)

    full_name = models.CharField(max_length=200, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    id_type = models.CharField(max_length=20, choices=IdType.choices, blank=True)
    id_number_encrypted = models.TextField(blank=True, editable=False)
    # Keyed hash of the normalised number: finds the same card on another
    # account without decrypting anything.
    id_number_fingerprint = models.CharField(max_length=64, blank=True, db_index=True, editable=False)
    id_number_last4 = models.CharField(max_length=4, blank=True, editable=False)

    # Machine-readable warnings for the reviewer, e.g. "duplicate_id".
    flags = models.JSONField(default=list, blank=True)
    # Shown to the person: why it was rejected, or what to change.
    decision_reason = models.TextField(blank=True)
    decided_by = models.CharField(max_length=200, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One attempt in progress per person per level; a finished one
            # (approved or rejected) does not block starting again.
            models.UniqueConstraint(fields=['subject', 'level'], condition=Q(status__in=OPEN_STATUSES),
                                    name='one_open_verification_per_level'),
        ]

    def __str__(self):
        return f'{self.subject} {self.level} ({self.status})'


class DocumentKind(models.TextChoices):
    ID_FRONT = 'id_front', 'ID front (or passport photo page)'
    ID_BACK = 'id_back', 'ID back'
    SELFIE = 'selfie', 'Selfie'


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    verification = models.ForeignKey(Verification, on_delete=models.CASCADE, related_name='documents')
    kind = models.CharField(max_length=20, choices=DocumentKind.choices)
    storage_key = models.CharField(max_length=300, blank=True)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    # Proves later that the file reviewed is the file uploaded.
    sha256 = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['verification', 'kind'], name='one_document_per_kind')]

    def __str__(self):
        return f'{self.kind} for {self.verification_id}'


class Event(models.Model):
    """
    What happened to a verification, by whom, in order. Append-only: the
    model refuses updates and deletes, so the trail cannot be edited through
    the application. It records field NAMES that changed, never values.
    """

    verification = models.ForeignKey(Verification, on_delete=models.CASCADE, related_name='events')
    at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=200)
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['at', 'id']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Audit events cannot be changed.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Audit events cannot be deleted.')

    def __str__(self):
        return f'{self.at:%Y-%m-%d %H:%M} {self.actor} {self.action}'


class WebhookDelivery(models.Model):
    """One message to one platform, retried until it is accepted (an outbox)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name='deliveries')
    event_type = models.CharField(max_length=60)
    payload = models.JSONField()
    attempts = models.PositiveSmallIntegerField(default=0)
    # None once delivered or given up.
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'webhook deliveries'

    def __str__(self):
        return f'{self.event_type} to {self.platform.slug}'
