import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from verification import storage
from verification.models import ApiKey, Platform
from verification.webhooks import new_secret

# Smallest files that pass the signature check.
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class FakeBucket:
    """Stands in for the S3 client: remembers what was stored and deleted."""

    def __init__(self):
        self.objects, self.deleted = {}, []

    def upload_fileobj(self, f, bucket, key, ExtraArgs=None):
        self.objects[key] = f.read()

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def get_object(self, Bucket, Key):
        import io
        return {'Body': io.BytesIO(self.objects[Key])}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f'https://bucket.test/{Params["Key"]}?expires={ExpiresIn}'


@pytest.fixture(autouse=True)
def bucket(monkeypatch, settings):
    settings.CELERY_BROKER_URL = ''
    # Each test runs inside a transaction that is rolled back, so commits
    # never happen; run after-commit work (webhooks, old-scan deletes) at once.
    from django.db import transaction
    monkeypatch.setattr(transaction, 'on_commit', lambda fn, using=None, robust=False: fn())
    fake = FakeBucket()
    monkeypatch.setattr(storage, '_client', lambda: fake)
    return fake


def upload_file(name='id.jpg', content=JPEG, content_type='image/jpeg'):
    return SimpleUploadedFile(name, content, content_type=content_type)


@pytest.fixture
def platform(db):
    p = Platform.objects.create(slug='ona-records', name='ONA Records',
                                webhook_url='https://ona.test/kyc/webhook/')
    p.secret = new_secret(p)
    return p


def _client(raw_key):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {raw_key}')
    return c


@pytest.fixture
def api(platform):
    _, raw = ApiKey.issue(platform, 'tests')
    return _client(raw)


@pytest.fixture
def reviewer_api(platform):
    _, raw = ApiKey.issue(platform, 'tests (review)', can_review=True)
    return _client(raw)


@pytest.fixture
def other_api(db):
    p = Platform.objects.create(slug='eagles', name='Eagles & Eaglets')
    _, raw = ApiKey.issue(p, 'tests')
    return _client(raw)


@pytest.fixture
def staff_api(django_user_model):
    user = django_user_model.objects.create_user('reviewer', 'r@kyc.test', 'pw', is_staff=True)
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def webhook_calls(monkeypatch):
    """Capture outgoing webhook POSTs; answer 200 unless told otherwise."""
    import verification.webhooks as wh

    class Calls(list):
        status = 200

    calls = Calls()

    class Resp:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, data, headers, timeout, allow_redirects):
        calls.append({'url': url, 'body': data, 'headers': headers})
        return Resp(calls.status)

    monkeypatch.setattr(wh.requests, 'post', fake_post)
    return calls


def fill(api, vid, *, id_type='ghana_card', id_number='GHA-123456789-0', docs=('id_front', 'id_back', 'selfie')):
    """Complete a verification so it can be submitted."""
    r = api.patch(f'/v1/verifications/{vid}/', {'full_name': 'Ama Mensah', 'date_of_birth': '1995-04-02',
                                                'id_type': id_type, 'id_number': id_number})
    assert r.status_code == 200, r.data
    for kind in docs:
        r = api.post(f'/v1/verifications/{vid}/documents/', {'kind': kind, 'file': upload_file()},
                     format='multipart')
        assert r.status_code == 201, r.data
