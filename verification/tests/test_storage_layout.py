"""Each platform's scans live in its own folder of the bucket."""

from django.core.management import call_command

from conftest import JPEG, fill
from verification.models import Document, Event


def _start(api, ref='u1'):
    return api.post('/v1/verifications/', {'subject_ref': ref}).data['id']


def test_uploads_go_to_the_platform_and_verification_folder(api, other_api, bucket):
    ona_vid, eagles_vid = _start(api), _start(other_api)
    fill(api, ona_vid)
    fill(other_api, eagles_vid)

    ona = set(Document.objects.filter(verification_id=ona_vid).values_list('storage_key', flat=True))
    eagles = set(Document.objects.filter(verification_id=eagles_vid).values_list('storage_key', flat=True))
    assert len(ona) == len(eagles) == 3
    assert all(k.startswith(f'kyc/platforms/ona-records/{ona_vid}/') for k in ona)
    assert all(k.startswith(f'kyc/platforms/eagles/{eagles_vid}/') for k in eagles)
    # The file name says which document it is.
    assert {k.rsplit('/', 1)[1].split('-')[0] for k in ona} == {'id_front', 'id_back', 'selfie'}
    assert set(bucket.objects) == ona | eagles


def test_old_flat_keys_are_moved_into_the_platform_folder(api, bucket):
    vid = _start(api)
    fill(api, vid)
    doc = Document.objects.get(verification_id=vid, kind='selfie')
    # A scan stored under the old, shared layout.
    old = 'kyc-staging/documents/0123abcd.jpg'
    bucket.objects[old] = JPEG
    del bucket.objects[doc.storage_key]
    Document.objects.filter(pk=doc.pk).update(storage_key=old)

    call_command('relocate_documents', '--dry-run')
    assert Document.objects.get(pk=doc.pk).storage_key == old

    call_command('relocate_documents')
    doc.refresh_from_db()
    assert doc.storage_key.startswith(f'kyc/platforms/ona-records/{vid}/selfie-')
    assert doc.storage_key.endswith('.jpg')
    assert bucket.objects[doc.storage_key] == JPEG and old not in bucket.objects
    assert Event.objects.filter(verification_id=vid, action='document_relocated').count() == 1

    # Running it again moves nothing.
    keys = dict(bucket.objects)
    call_command('relocate_documents')
    assert bucket.objects == keys
