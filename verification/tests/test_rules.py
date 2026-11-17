"""The rules with no HTTP: ID formats, encryption, transitions, the audit log."""

import pytest
from rest_framework.exceptions import ValidationError

from verification import crypto, levels
from verification.models import Event, Status
from verification.states import TRANSITIONS, can_move


@pytest.mark.parametrize('typed', ['GHA-123456789-0', 'gha 123456789 0', 'GHA1234567890', ' gha-123456789-0 '])
def test_ghana_card_numbers_typed_any_way_become_one_form(typed):
    assert levels.normalise_id_number('ghana_card', typed) == 'GHA-123456789-0'


@pytest.mark.parametrize('typed', ['123456789', 'GHA-12345678-0', 'GHB-123456789-0', 'GHA-123456789-01'])
def test_malformed_ghana_card_numbers_are_refused(typed):
    with pytest.raises(ValidationError):
        levels.normalise_id_number('ghana_card', typed)


def test_passport_numbers():
    assert levels.normalise_id_number('passport', ' g1234567 ') == 'G1234567'
    with pytest.raises(ValidationError):
        levels.normalise_id_number('passport', 'G12-34')


def test_id_numbers_encrypt_and_fingerprint():
    token = crypto.encrypt('GHA-123456789-0')
    assert 'GHA' not in token
    assert crypto.decrypt(token) == 'GHA-123456789-0'
    assert crypto.fingerprint('x') == crypto.fingerprint('x') != crypto.fingerprint('y')


def test_passports_need_no_back_side():
    assert 'id_back' not in levels.required_documents('passport')
    assert 'id_back' in levels.required_documents('ghana_card')


def test_finished_verifications_cannot_move():
    assert TRANSITIONS[Status.APPROVED] == set() and TRANSITIONS[Status.REJECTED] == set()
    assert not can_move(Status.DRAFT, Status.APPROVED), 'a reviewer must not approve an unsubmitted draft'
    assert can_move(Status.CHANGES_REQUESTED, Status.SUBMITTED)


def test_audit_events_cannot_be_edited_or_deleted(api):
    vid = api.post('/v1/verifications/', {'subject_ref': 'u1'}).data['id']
    event = Event.objects.get(verification_id=vid)
    event.note = 'rewritten'
    with pytest.raises(ValueError):
        event.save()
    with pytest.raises(ValueError):
        event.delete()
