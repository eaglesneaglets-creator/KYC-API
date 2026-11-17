"""The whole journey over HTTP, as a platform and a reviewer would drive it."""

import hashlib
import hmac
import json

from conftest import PNG, fill, upload_file
from verification.models import ApiKey, Event, Verification


def start(api, ref='user-42'):
    return api.post('/v1/verifications/', {'subject_ref': ref, 'level': 'identity'})


# ── Keys and isolation ───────────────────────────────────────────────────

def test_no_key_no_access(db, client):
    assert client.post('/v1/verifications/', {'subject_ref': 'x'}).status_code == 401


def test_wrong_or_revoked_key_is_refused(api, platform):
    from rest_framework.test import APIClient
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer kyc_deadbeef_nottherealsecret')
    assert c.post('/v1/verifications/', {'subject_ref': 'x'}).status_code == 401

    key, raw = ApiKey.issue(platform, 'to revoke')
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
    assert start(c).status_code == 201
    key.revoked_at = key.created_at
    key.save()
    assert start(c).status_code == 401


def test_a_platform_cannot_see_another_platforms_users(api, other_api):
    vid = start(api).data['id']
    assert other_api.get(f'/v1/verifications/{vid}/').status_code == 404
    assert other_api.get('/v1/subjects/user-42/').status_code == 404


# ── The person's side ────────────────────────────────────────────────────

def test_start_is_idempotent_per_person_and_level(api):
    first, again = start(api), start(api)
    assert first.status_code == 201 and again.status_code == 200
    assert first.data['id'] == again.data['id']
    assert first.data['missing'] == ['full_name', 'date_of_birth', 'id_type', 'id_number',
                                     'document:id_front', 'document:id_back', 'document:selfie']


def test_details_are_stored_encrypted_and_shown_only_as_last_four(api):
    vid = start(api).data['id']
    r = api.patch(f'/v1/verifications/{vid}/', {'full_name': '  Ama   Mensah ', 'id_type': 'ghana_card',
                                                'id_number': 'gha 123456789 0'})
    assert r.status_code == 200
    assert r.data['full_name'] == 'Ama Mensah'
    assert r.data['id_number_last4'] == '89-0'
    assert 'id_number' not in r.data
    v = Verification.objects.get(pk=vid)
    assert 'GHA' not in v.id_number_encrypted and v.id_number_fingerprint
    # The audit trail names the fields, never their values.
    note = Event.objects.filter(verification=v, action='updated').get().note
    assert 'id_number' in note and '123456789' not in note


def test_bad_id_number_and_birth_date_are_explained(api):
    vid = start(api).data['id']
    r = api.patch(f'/v1/verifications/{vid}/', {'id_type': 'ghana_card', 'id_number': '12345'})
    assert r.status_code == 400 and 'GHA-123456789-0' in str(r.data['id_number'])
    r = api.patch(f'/v1/verifications/{vid}/', {'date_of_birth': '2999-01-01'})
    assert r.status_code == 400 and 'date_of_birth' in r.data


def test_a_file_must_be_what_it_claims(api, bucket):
    vid = start(api).data['id']
    lying = upload_file(content=b'MZ\x90\x00 an exe', content_type='image/jpeg')
    r = api.post(f'/v1/verifications/{vid}/documents/', {'kind': 'id_front', 'file': lying}, format='multipart')
    assert r.status_code == 400 and not bucket.objects
    r = api.post(f'/v1/verifications/{vid}/documents/',
                 {'kind': 'selfie', 'file': upload_file('s.pdf', b'%PDF-1.7', 'application/pdf')},
                 format='multipart')
    assert r.status_code == 400, 'a selfie must be a photo'


def test_uploading_again_replaces_and_deletes_the_old_scan(api, bucket):
    vid = start(api).data['id']
    for content in (PNG, PNG + b'2'):
        r = api.post(f'/v1/verifications/{vid}/documents/',
                     {'kind': 'id_front', 'file': upload_file('a.png', content, 'image/png')}, format='multipart')
        assert r.status_code == 201
    assert len(bucket.objects) == 1 and len(bucket.deleted) == 1
    assert Verification.objects.get(pk=vid).documents.count() == 1


def test_submit_lists_what_is_missing(api):
    vid = start(api).data['id']
    r = api.post(f'/v1/verifications/{vid}/submit/')
    assert r.status_code == 400 and 'document:selfie' in r.data['missing']


def test_submitted_verifications_are_locked(api, webhook_calls):
    vid = start(api).data['id']
    fill(api, vid)
    r = api.post(f'/v1/verifications/{vid}/submit/')
    assert r.status_code == 200 and r.data['status'] == 'submitted'
    assert api.patch(f'/v1/verifications/{vid}/', {'full_name': 'Someone Else'}).status_code == 409
    assert api.post(f'/v1/verifications/{vid}/submit/').status_code == 409
    assert [json.loads(c['body'])['type'] for c in webhook_calls] == ['verification.submitted']


def test_a_passport_needs_only_the_photo_page_and_a_selfie(api, webhook_calls):
    vid = start(api).data['id']
    fill(api, vid, id_type='passport', id_number='G1234567', docs=('id_front', 'selfie'))
    assert api.post(f'/v1/verifications/{vid}/submit/').status_code == 200


# ── The reviewer's side ──────────────────────────────────────────────────

def submitted(api, ref='user-42'):
    vid = start(api, ref).data['id']
    fill(api, vid)
    assert api.post(f'/v1/verifications/{vid}/submit/').status_code == 200
    return vid


def test_an_ordinary_key_cannot_review(api, webhook_calls):
    vid = submitted(api)
    r = api.post(f'/v1/review/verifications/{vid}/decision/', {'decision': 'approved', 'reviewer': 'x'})
    assert r.status_code == 403


def test_staff_review_queue_detail_and_approval(api, staff_api, webhook_calls, platform):
    vid = submitted(api)
    queue = staff_api.get('/v1/review/verifications/').data['results']
    assert [q['id'] for q in queue] == [vid]

    detail = staff_api.get(f'/v1/review/verifications/{vid}/').data
    assert detail['id_number'] == 'GHA-123456789-0'
    assert all(d['url'].startswith('https://bucket.test/kyc/') for d in detail['documents'])

    assert staff_api.post(f'/v1/review/verifications/{vid}/start-review/').data['status'] == 'in_review'
    r = staff_api.post(f'/v1/review/verifications/{vid}/decision/', {'decision': 'approved'})
    assert r.status_code == 200 and r.data['status'] == 'approved'

    v = Verification.objects.get(pk=vid)
    assert v.decided_by == 'staff:reviewer' and v.decided_at
    actions = list(v.events.values_list('action', flat=True))
    assert actions[-3:] == ['viewed', 'in_review', 'approved']

    status = api.get('/v1/subjects/user-42/').data['levels']['identity']
    assert status == {'verified': True, 'latest_status': 'approved', 'latest_verification_id': vid}

    # The webhook is signed with the platform's secret and carries no personal data.
    last = webhook_calls[-1]
    body = last['body']
    payload = json.loads(body)
    assert payload['type'] == 'verification.approved' and payload['data']['subject_ref'] == 'user-42'
    assert 'Ama' not in body.decode() and '123456789' not in body.decode()
    t, v1 = (p.split('=', 1)[1] for p in last['headers']['KYC-Signature'].split(','))
    expected = hmac.new(platform.secret.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(v1, expected)


def test_rejection_and_change_requests_need_a_reason(reviewer_api, api, webhook_calls):
    vid = submitted(api)
    url = f'/v1/review/verifications/{vid}/decision/'
    assert reviewer_api.post(url, {'decision': 'rejected', 'reviewer': 'kofi@ona'}).status_code == 400
    assert reviewer_api.post(url, {'decision': 'approved'}).status_code == 400, 'platform must name the reviewer'
    r = reviewer_api.post(url, {'decision': 'changes_requested', 'reviewer': 'kofi@ona',
                                'reason': 'The back of the card is blurred.'})
    assert r.status_code == 200
    v = Verification.objects.get(pk=vid)
    assert v.decided_at is None, 'asking for changes is not a final decision'
    assert v.events.last().actor == 'platform:ona-records/kofi@ona'

    # The person fixes it and resubmits; the old reason is cleared.
    assert api.patch(f'/v1/verifications/{vid}/', {'full_name': 'Ama A. Mensah'}).status_code == 200
    r = api.post(f'/v1/verifications/{vid}/submit/')
    assert r.status_code == 200 and r.data['decision_reason'] == ''


def test_a_decided_verification_cannot_be_decided_again(api, staff_api, webhook_calls):
    vid = submitted(api)
    url = f'/v1/review/verifications/{vid}/decision/'
    assert staff_api.post(url, {'decision': 'rejected', 'reason': 'Not the same person.'}).status_code == 200
    assert staff_api.post(url, {'decision': 'approved'}).status_code == 409
    # Rejected people can try again with a fresh verification.
    again = start(api)
    assert again.status_code == 201 and again.data['id'] != vid


def test_the_same_id_is_blocked_on_one_platform_and_flagged_across_platforms(
        api, other_api, staff_api, webhook_calls):
    vid = submitted(api, 'first-account')
    staff_api.post(f'/v1/review/verifications/{vid}/decision/', {'decision': 'approved'})

    # A second account on the same platform, typed differently: refused, still editable.
    second = start(api, 'second-account').data['id']
    fill(api, second, id_number='gha 123456789 0')
    r = api.post(f'/v1/verifications/{second}/submit/')
    assert r.status_code == 400 and 'already verified' in str(r.data['id_number'])
    assert Verification.objects.get(pk=second).status == 'draft'

    # The same person on another platform: accepted, with a warning for the reviewer.
    elsewhere = start(other_api, 'their-user').data['id']
    fill(other_api, elsewhere)
    assert other_api.post(f'/v1/verifications/{elsewhere}/submit/').status_code == 200
    assert staff_api.get(f'/v1/review/verifications/{elsewhere}/').data['flags'] == ['duplicate_id']


def test_a_platform_reviewer_cannot_decide_another_platforms_users(api, other_api, webhook_calls):
    from rest_framework.test import APIClient

    from verification.models import Platform
    _, raw = ApiKey.issue(Platform.objects.get(slug='eagles'), 'review', can_review=True)
    eagles_reviewer = APIClient()
    eagles_reviewer.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
    vid = submitted(api)
    r = eagles_reviewer.post(f'/v1/review/verifications/{vid}/decision/',
                             {'decision': 'approved', 'reviewer': 'x'})
    assert r.status_code == 404
    assert eagles_reviewer.get('/v1/review/verifications/').data['results'] == []


def test_api_schema_is_published(db, client):
    r = client.get('/schema/')
    assert r.status_code == 200 and b'/v1/verifications/' in r.content
