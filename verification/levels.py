"""
Verification levels: what a check requires before it can be submitted.

A platform asks for a level by name. Adding a level is a change to this
file (and a test), never a new model — the Eagles monolith grew a second
near-identical KYC model per role; this is what replaces that.
"""

import re
from datetime import date

from rest_framework.exceptions import ValidationError

from .models import DocumentKind, IdType, Verification

LEVELS = {
    # A real person, matched to a government ID and a live photo of them.
    'identity': {
        'label': 'Identity',
        'fields': ('full_name', 'date_of_birth', 'id_type', 'id_number'),
    },
}

GHANA_CARD = re.compile(r'^GHA-\d{9}-\d$')
PASSPORT = re.compile(r'^[A-Z0-9]{6,12}$')


def required_documents(id_type: str) -> tuple[str, ...]:
    # A passport has one photo page; a Ghana Card has two sides.
    if id_type == IdType.PASSPORT:
        return (DocumentKind.ID_FRONT, DocumentKind.SELFIE)
    return (DocumentKind.ID_FRONT, DocumentKind.ID_BACK, DocumentKind.SELFIE)


def normalise_id_number(id_type: str, raw: str) -> str:
    """
    The canonical form of an ID number, or a ValidationError.

    People type a Ghana Card number every way: "gha 123456789 0",
    "GHA1234567890", "GHA-123456789-0". All become GHA-123456789-0, so the
    same card always produces the same fingerprint.
    """
    s = re.sub(r'\s+', '', raw or '').upper()
    if id_type == IdType.GHANA_CARD:
        digits = re.sub(r'\D', '', s.removeprefix('GHA'))
        if s.startswith('GHA') and len(digits) == 10:
            s = f'GHA-{digits[:9]}-{digits[9]}'
        if not GHANA_CARD.match(s):
            raise ValidationError({'id_number': 'A Ghana Card number looks like GHA-123456789-0.'})
    elif id_type == IdType.PASSPORT:
        if not PASSPORT.match(s):
            raise ValidationError({'id_number': 'A passport number is 6 to 12 letters and digits.'})
    else:
        raise ValidationError({'id_type': 'Choose the type of ID first.'})
    return s


def validate_date_of_birth(value: date) -> date:
    today = date.today()
    if value >= today or value.year < 1900:
        raise ValidationError('Enter a real date of birth.')
    return value


def missing(v: Verification) -> list[str]:
    """Everything still needed before submitting, as field and document names."""
    spec = LEVELS[v.level]
    out = []
    for field in spec['fields']:
        value = v.id_number_encrypted if field == 'id_number' else getattr(v, field)
        if not value:
            out.append(field)
    have = {d.kind for d in v.documents.all() if d.storage_key}
    out += [f'document:{k}' for k in required_documents(v.id_type) if k not in have]
    return out
