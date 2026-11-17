"""
Everything that changes a verification goes through here.

Views translate HTTP into these calls and back; the admin calls them too.
Each function locks the row it changes (select_for_update), so two
reviewers clicking at once — or a person editing while a reviewer decides —
cannot interleave: the second waits, then sees the new status and is refused.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from . import crypto, levels, policy, storage, webhooks
from .models import OPEN_STATUSES, Document, Event, Platform, Status, Subject, Verification
from .states import DECISIONS, EDITABLE, NEEDS_REASON, can_move


class Conflict(APIException):
    status_code = 409
    default_detail = 'That is not possible in the verification\'s current status.'
    default_code = 'conflict'


def _locked(v: Verification) -> Verification:
    return Verification.objects.select_for_update().select_related('subject__platform').get(pk=v.pk)


def _log(v, actor, action, *, from_status='', to_status='', note=''):
    Event.objects.create(verification=v, actor=actor, action=action,
                         from_status=from_status, to_status=to_status, note=note)


def _move(v: Verification, to: str, actor: str, note: str = '') -> None:
    if not can_move(v.status, to):
        raise Conflict(f'A {v.get_status_display().lower()} verification cannot become '
                       f'{Status(to).label.lower()}.')
    _log(v, actor, to, from_status=v.status, to_status=to, note=note)
    v.status = to


# ── The person's side (called by the platform) ────────────────────────────

def start(platform: Platform, external_ref: str, level: str, actor: str) -> tuple[Verification, bool]:
    """The open verification for this person and level, creating it if needed."""
    if level not in levels.LEVELS:
        raise ValidationError({'level': f'Unknown level. Choose from: {", ".join(levels.LEVELS)}.'})
    subject, _ = Subject.objects.get_or_create(platform=platform, external_ref=external_ref)
    with transaction.atomic():
        # Lock the person, so two simultaneous starts make one verification.
        Subject.objects.select_for_update().get(pk=subject.pk)
        existing = subject.verifications.filter(level=level, status__in=OPEN_STATUSES).first()
        if existing:
            return existing, False
        v = Verification.objects.create(subject=subject, level=level)
        _log(v, actor, 'created', to_status=v.status)
    return v, True


def update_details(v: Verification, data: dict, actor: str) -> Verification:
    with transaction.atomic():
        v = _locked(v)
        if v.status not in EDITABLE:
            raise Conflict('Details can only change before submitting, or when changes are requested.')
        changed = []
        for field in ('full_name', 'date_of_birth', 'id_type'):
            if field in data and getattr(v, field) != data[field]:
                setattr(v, field, data[field])
                changed.append(field)
        if 'id_number' in data or ('id_type' in changed and v.id_number_encrypted):
            raw = data.get('id_number') or crypto.decrypt(v.id_number_encrypted)
            number = levels.normalise_id_number(v.id_type, raw)
            v.id_number_encrypted = crypto.encrypt(number)
            v.id_number_fingerprint = crypto.fingerprint(f'{v.id_type}:{number}')
            v.id_number_last4 = number[-4:]
            changed.append('id_number')
        if changed:
            v.save()
            _log(v, actor, 'updated', note=', '.join(sorted(set(changed))))
    return v


def attach_document(v: Verification, kind: str, file, actor: str) -> Document:
    with transaction.atomic():
        v = _locked(v)
        if v.status not in EDITABLE:
            raise Conflict('Documents can only change before submitting, or when changes are requested.')
        stored = storage.upload(file, kind=kind)
        old_key = ''
        doc = v.documents.filter(kind=kind).first()
        if doc:
            old_key = doc.storage_key
        else:
            doc = Document(verification=v, kind=kind)
        doc.storage_key = stored['key']
        doc.content_type, doc.size, doc.sha256 = stored['content_type'], stored['size'], stored['sha256']
        doc.purged_at = None
        doc.save()
        _log(v, actor, 'document_uploaded', note=kind + (' (replaced)' if old_key else ''))
        if old_key:
            transaction.on_commit(lambda: storage.delete(old_key))
    return doc


def submit(v: Verification, actor: str) -> Verification:
    with transaction.atomic():
        v = _locked(v)
        if not can_move(v.status, Status.SUBMITTED):
            raise Conflict('This verification has already been submitted.')
        gaps = levels.missing(v)
        if gaps:
            raise ValidationError({'missing': gaps})

        flags = [f for f in v.flags if f != 'duplicate_id']
        matches = list(
            Verification.objects.select_related('subject__platform')
            .filter(id_number_fingerprint=v.id_number_fingerprint, status=Status.APPROVED)
            .exclude(subject=v.subject)
        )
        if matches:
            outcome = policy.on_duplicate_id(v, matches)
            if outcome == policy.BLOCK:
                raise ValidationError({'id_number': 'This ID is already verified on another account. '
                                                    'Contact support if it is yours.'})
            if outcome == policy.FLAG:
                flags.append('duplicate_id')

        v.flags = flags
        _move(v, Status.SUBMITTED, actor)
        v.submitted_at = timezone.now()
        v.decision_reason = ''
        v.save()
        webhooks.enqueue(v, 'verification.submitted')
    return v


# ── The reviewer's side ───────────────────────────────────────────────────

def start_review(v: Verification, actor: str) -> Verification:
    with transaction.atomic():
        v = _locked(v)
        _move(v, Status.IN_REVIEW, actor)
        v.save()
    return v


def decide(v: Verification, decision: str, actor: str, reason: str = '') -> Verification:
    if decision not in DECISIONS:
        raise ValidationError({'decision': f'Choose one of: {", ".join(sorted(DECISIONS))}.'})
    reason = (reason or '').strip()
    if decision in NEEDS_REASON and not reason:
        raise ValidationError({'reason': 'Tell the person why, so they know what to do next.'})
    with transaction.atomic():
        v = _locked(v)
        _move(v, decision, actor, note=reason)
        v.decision_reason = reason
        if decision != Status.CHANGES_REQUESTED:
            v.decided_at, v.decided_by = timezone.now(), actor
        v.save()
        webhooks.enqueue(v, f'verification.{decision}')
    return v


def record_view(v: Verification, actor: str) -> None:
    """A reviewer opened the full record (ID number and scans). Logged."""
    _log(v, actor, 'viewed')


# ── Housekeeping ──────────────────────────────────────────────────────────

def purge_documents(older_than_days: int) -> int:
    """Delete scans of verifications decided more than N days ago."""
    cutoff = timezone.now() - timedelta(days=older_than_days)
    docs = (Document.objects.select_related('verification')
            .filter(purged_at__isnull=True, verification__decided_at__lt=cutoff,
                    verification__status__in=[Status.APPROVED, Status.REJECTED]))
    purged, seen = 0, set()
    for doc in docs:
        if storage.delete(doc.storage_key):
            doc.storage_key, doc.purged_at = '', timezone.now()
            doc.save(update_fields=['storage_key', 'purged_at'])
            purged += 1
            if doc.verification_id not in seen:
                seen.add(doc.verification_id)
                _log(doc.verification, 'system', 'documents_purged',
                     note=f'retention {older_than_days} days')
    return purged
