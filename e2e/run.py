"""
End-to-end test of the KYC service in Docker.

Builds the real image, starts it with STAGING settings next to Postgres,
Redis, an S3-compatible bucket, the Celery worker and a pretend platform,
then drives the API the way ONA and Eagles would and checks every result.

    .venv/Scripts/python e2e/run.py            # build, test, tear down
    .venv/Scripts/python e2e/run.py --keep     # leave the stack running after

Exit code 0 only if every check passed.
"""

import hashlib
import hmac
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ['docker', 'compose', '-f', str(ROOT / 'e2e' / 'docker-compose.yml')]
API = 'http://localhost:8110'
HOOK = 'http://localhost:9012'
# Staging redirects plain HTTP to HTTPS; behind Railway's proxy this header
# says the original request was HTTPS. Here we say it ourselves.
PROXY = {'X-Forwarded-Proto': 'https'}

JPEG = b'\xff\xd8\xff\xe0' + b'e2e-jpeg' * 64
PNG = b'\x89PNG\r\n\x1a\n' + b'e2e-png!' * 64
FAKE_EXE = b'MZ\x90\x00 not a photo'

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    results.append((name, bool(ok), detail))
    print(f'  {"PASS" if ok else "FAIL"}  {name}' + (f'  — {detail}' if detail and not ok else ''))
    return ok


def sh(*args, input_text=None, timeout=600) -> subprocess.CompletedProcess:
    return subprocess.run([*COMPOSE, *args], input=input_text, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout, cwd=ROOT)


def django(code: str) -> dict:
    """Run Python in the web container's Django shell; it prints RESULT:<json>."""
    r = sh('exec', '-T', 'web', 'python', 'manage.py', 'shell', input_text=code)
    for line in r.stdout.splitlines():
        if line.startswith('RESULT:'):
            return json.loads(line[len('RESULT:'):])
    raise RuntimeError(f'Django shell gave no result.\nstdout: {r.stdout[-1500:]}\nstderr: {r.stderr[-1500:]}')


def client(key: str | None):
    s = requests.Session()
    s.headers.update(PROXY)
    if key:
        s.headers['Authorization'] = f'Bearer {key}'
    return s


def received() -> list[dict]:
    return requests.get(f'{HOOK}/received', timeout=5).json()


def wait_for(fn, seconds: float, every: float = 1.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return fn()


def signature_ok(msg: dict, secret: str) -> bool:
    parts = dict(p.split('=', 1) for p in msg['signature'].split(','))
    expected = hmac.new(secret.encode(), f'{parts["t"]}.'.encode() + msg['body'].encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts['v1']) and abs(time.time() - int(parts['t'])) < 300


def hooks_of(event_type: str, vid: str) -> list[dict]:
    out = []
    for m in received():
        body = json.loads(m['body'])
        if body['type'] == event_type and body['data']['verification_id'] == vid:
            out.append({**m, 'json': body})
    return out


# ──────────────────────────────────────────────────────────────────────────

def bring_up() -> None:
    print('== Building the image and starting the stack (first build takes a few minutes)')
    r = sh('up', '--build', '-d', timeout=1800)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit('Stack failed to start.')

    def healthy():
        try:
            return requests.get(f'{API}/health/', timeout=3).status_code == 200
        except requests.RequestException:
            return False

    if not wait_for(healthy, 180, 2):
        print(sh('logs', '--tail', '80', 'web').stdout)
        sys.exit('Web service never became healthy.')


def setup() -> dict:
    print('== Creating the bucket, two platforms and their keys')
    django("""
import boto3, json
from django.conf import settings as s
c = boto3.client('s3', endpoint_url=s.S3_ENDPOINT_URL, aws_access_key_id=s.S3_ACCESS_KEY_ID,
                 aws_secret_access_key=s.S3_SECRET_ACCESS_KEY, region_name=s.S3_REGION)
if s.S3_BUCKET not in [b['Name'] for b in c.list_buckets().get('Buckets', [])]:
    c.create_bucket(Bucket=s.S3_BUCKET)
print('RESULT:' + json.dumps({'ok': True}))
""")
    # The real management command (it insists on https), then point the
    # webhook at the in-network receiver, which speaks plain http.
    r = sh('exec', '-T', 'web', 'python', 'manage.py', 'create_platform', 'ona-records',
           '--name', 'ONA Records', '--webhook-url', 'https://hook.invalid/kyc', '--key-name', 'ONA backend')
    out = dict(line.split('=', 1) for line in r.stdout.splitlines() if line.startswith('KYC_'))
    check('create_platform prints a key and a webhook secret once',
          out.get('KYC_API_KEY', '').startswith('kyc_') and out.get('KYC_WEBHOOK_SECRET', '').startswith('whsec_'),
          r.stdout + r.stderr)
    r = sh('exec', '-T', 'web', 'python', 'manage.py', 'create_platform', 'ona-records',
           '--name', 'ONA Records', '--webhook-url', 'http://hook:9000/ona')
    check('create_platform refuses a plain-http webhook URL on staging',
          r.returncode != 0 and 'https' in (r.stdout + r.stderr))
    rest = django("""
import json
from verification.models import ApiKey, Platform
ona = Platform.objects.get(slug='ona-records')
ona.webhook_url = 'http://hook:9000/ona'
ona.save()
_, ona_review = ApiKey.issue(ona, 'ONA console', can_review=True)
eagles = Platform.objects.create(slug='eagles', name='Eagles & Eaglets')
_, eagles_key = ApiKey.issue(eagles, 'Eagles backend')
print('RESULT:' + json.dumps({'ona_review': ona_review, 'eagles': eagles_key}))
""")
    return {'ona': out['KYC_API_KEY'], 'secret': out['KYC_WEBHOOK_SECRET'], **rest}


def complete(ona, ref: str, id_number='GHA-123456789-0') -> str:
    vid = ona.post(f'{API}/v1/verifications/', json={'subject_ref': ref}).json()['id']
    ona.patch(f'{API}/v1/verifications/{vid}/', json={
        'full_name': 'Ama Mensah', 'date_of_birth': '1995-04-02',
        'id_type': 'ghana_card', 'id_number': id_number}).raise_for_status()
    for kind, (name, data, mime) in {'id_front': ('f.jpg', JPEG, 'image/jpeg'),
                                     'id_back': ('b.png', PNG, 'image/png'),
                                     'selfie': ('s.jpg', JPEG, 'image/jpeg')}.items():
        ona.post(f'{API}/v1/verifications/{vid}/documents/', data={'kind': kind},
                 files={'file': (name, data, mime)}).raise_for_status()
    return vid


def scenario(keys: dict) -> None:
    ona, reviewer = client(keys['ona']), client(keys['ona_review'])
    eagles, anon = client(keys['eagles']), client(None)

    print('== Service and access')
    h = anon.get(f'{API}/health/').json()
    check('health answers, running with staging settings', h == {'status': 'ok', 'environment': 'staging'}, h)
    check('no key: 401', anon.post(f'{API}/v1/verifications/', json={'subject_ref': 'x'}).status_code == 401)
    check('made-up key: 401', client('kyc_deadbeef_wrong').get(f'{API}/v1/subjects/x/').status_code == 401)
    check('API docs are served', anon.get(f'{API}/schema/').status_code == 200)
    check('admin is at the private path; /admin/ is 404',
          anon.get(f'{API}/back-office-e2e/login/').status_code == 200
          and anon.get(f'{API}/admin/').status_code == 404)

    print('== Starting at the same moment (Postgres row lock)')
    with ThreadPoolExecutor(6) as pool:
        starts = list(pool.map(lambda _: ona.post(f'{API}/v1/verifications/', json={'subject_ref': 'ama'}),
                               range(6)))
    ids = {r.json()['id'] for r in starts}
    codes = sorted(r.status_code for r in starts)
    check('6 simultaneous starts make ONE verification (one 201, five 200)',
          len(ids) == 1 and codes == [200] * 5 + [201], f'ids={len(ids)} codes={codes}')
    vid = ids.pop()

    print('== The person fills it in')
    r = ona.patch(f'{API}/v1/verifications/{vid}/', json={'id_type': 'ghana_card', 'id_number': '12345'})
    check('malformed Ghana Card number refused with the expected format', r.status_code == 400
          and 'GHA-123456789-0' in r.text, r.text)
    r = ona.post(f'{API}/v1/verifications/{vid}/documents/', data={'kind': 'id_front'},
                 files={'file': ('id.jpg', FAKE_EXE, 'image/jpeg')})
    check('a program renamed .jpg is refused', r.status_code == 400, r.text)
    r = ona.post(f'{API}/v1/verifications/{vid}/submit/')
    check('submitting early lists what is missing', r.status_code == 400 and 'document:selfie' in r.text, r.text)

    r = ona.patch(f'{API}/v1/verifications/{vid}/', json={
        'full_name': 'Ama Mensah', 'date_of_birth': '1995-04-02',
        'id_type': 'ghana_card', 'id_number': 'gha 123456789 0'})
    check('details saved; platform sees only the last four', r.status_code == 200
          and r.json()['id_number_last4'] == '89-0' and 'id_number' not in r.json(), r.text)
    for kind, (name, data, mime) in {'id_front': ('f.jpg', JPEG, 'image/jpeg'),
                                     'id_back': ('b.png', PNG, 'image/png'),
                                     'selfie': ('s.jpg', JPEG, 'image/jpeg')}.items():
        r = ona.post(f'{API}/v1/verifications/{vid}/documents/', data={'kind': kind},
                     files={'file': (name, data, mime)})
        check(f'{kind} stored in the bucket', r.status_code == 201, r.text)
    db = django(f"""
import json
from verification.models import Verification
v = Verification.objects.get(pk='{vid}')
print('RESULT:' + json.dumps({{'enc': v.id_number_encrypted[:12], 'plain_in_db': '123456789' in v.id_number_encrypted}}))
""")
    check('ID number is encrypted in Postgres', not db['plain_in_db'] and db['enc'].startswith('gAAAA'), db)

    r = ona.post(f'{API}/v1/verifications/{vid}/submit/')
    check('submit → submitted', r.status_code == 200 and r.json()['status'] == 'submitted', r.text)
    check('edits after submitting are refused (409)',
          ona.patch(f'{API}/v1/verifications/{vid}/', json={'full_name': 'Someone Else'}).status_code == 409)
    check('another platform cannot see it (404)', eagles.get(f'{API}/v1/verifications/{vid}/').status_code == 404)

    msgs = wait_for(lambda: hooks_of('verification.submitted', vid), 30)
    check('worker delivered verification.submitted to the platform', len(msgs) == 1, f'{len(msgs)} received')
    if msgs:
        check('webhook signature verifies with the platform secret', signature_ok(msgs[0], keys['secret']))
        check('webhook carries no name or ID number',
              'Ama' not in msgs[0]['body'] and '123456789' not in msgs[0]['body'])

    print('== Review')
    queue = reviewer.get(f'{API}/v1/review/verifications/').json()['results']
    check('it is in the review queue', [q['id'] for q in queue] == [vid], queue)
    check('an ordinary platform key cannot review (403)',
          ona.post(f'{API}/v1/review/verifications/{vid}/decision/',
                   json={'decision': 'approved', 'reviewer': 'x'}).status_code == 403)
    detail = reviewer.get(f'{API}/v1/review/verifications/{vid}/?reviewer=kofi@ona').json()
    check('reviewer sees the full ID number', detail.get('id_number') == 'GHA-123456789-0', detail.get('id_number'))
    fetched = django(f"""
import hashlib, json, urllib.request
from verification.models import Document
out = {{}}
for d in Document.objects.filter(verification_id='{vid}'):
    from verification.storage import url_for
    body = urllib.request.urlopen(url_for(d.storage_key), timeout=10).read()
    out[d.kind] = hashlib.sha256(body).hexdigest() == d.sha256
print('RESULT:' + json.dumps(out))
""")
    check('scan links download the exact bytes uploaded (sha256 match)',
          fetched == {'id_front': True, 'id_back': True, 'selfie': True}, fetched)

    r = reviewer.post(f'{API}/v1/review/verifications/{vid}/start-review/', json={'reviewer': 'kofi@ona'})
    check('start review → in_review', r.json().get('status') == 'in_review', r.text)
    r = reviewer.post(f'{API}/v1/review/verifications/{vid}/decision/',
                      json={'decision': 'approved', 'reviewer': 'kofi@ona'})
    check('approve → approved', r.status_code == 200 and r.json()['status'] == 'approved', r.text)
    msgs = wait_for(lambda: hooks_of('verification.approved', vid), 30)
    check('worker delivered verification.approved', len(msgs) == 1)
    status = ona.get(f'{API}/v1/subjects/ama/').json()['levels']['identity']
    check('platform status endpoint says verified', status['verified'] is True, status)
    trail = django(f"""
import json
from verification.models import Verification
v = Verification.objects.get(pk='{vid}')
print('RESULT:' + json.dumps([[e.actor, e.action] for e in v.events.all()]))
""")
    actions = [a for _, a in trail]
    check('audit trail is complete and in order',
          actions[:1] == ['created'] and actions[-4:] == ['submitted', 'viewed', 'in_review', 'approved']
          and ['platform:ona-records/kofi@ona', 'approved'] in trail, trail)

    print('== Two reviewers deciding at the same moment')
    race = complete(ona, 'kwame', id_number='GHA-222222222-2')
    ona.post(f'{API}/v1/verifications/{race}/submit/').raise_for_status()
    bodies = [{'decision': 'approved', 'reviewer': 'a@ona'},
              {'decision': 'rejected', 'reviewer': 'b@ona', 'reason': 'Photo does not match.'}] * 3
    with ThreadPoolExecutor(6) as pool:
        outcomes = list(pool.map(
            lambda b: reviewer.post(f'{API}/v1/review/verifications/{race}/decision/', json=b), bodies))
    codes = sorted(r.status_code for r in outcomes)
    final = django(f"""
import json
from verification.models import Verification
v = Verification.objects.get(pk='{race}')
print('RESULT:' + json.dumps({{'status': v.status,
    'decisions': v.events.filter(action__in=['approved', 'rejected']).count()}}))
""")
    check('6 simultaneous decisions: exactly one wins, the rest get 409',
          codes == [200] + [409] * 5 and final['decisions'] == 1, f'codes={codes} {final}')

    print('== Same Ghana Card on another account')
    dup = complete(ona, 'ama-second-account', id_number='GHA 123456789 0')
    r = ona.post(f'{API}/v1/verifications/{dup}/submit/')
    check('same platform: blocked, stays editable', r.status_code == 400 and 'already verified' in r.text, r.text)
    eg = client(keys['eagles'])
    evid = eg.post(f'{API}/v1/verifications/', json={'subject_ref': 'eagle-77'}).json()['id']
    eg.patch(f'{API}/v1/verifications/{evid}/', json={'full_name': 'Ama Mensah', 'date_of_birth': '1995-04-02',
                                                      'id_type': 'ghana_card', 'id_number': 'GHA1234567890'})
    for kind in ('id_front', 'id_back', 'selfie'):
        eg.post(f'{API}/v1/verifications/{evid}/documents/', data={'kind': kind},
                files={'file': ('x.jpg', JPEG, 'image/jpeg')})
    r = eg.post(f'{API}/v1/verifications/{evid}/submit/')
    flags = django(f"""
import json
from verification.models import Verification
print('RESULT:' + json.dumps(Verification.objects.get(pk='{evid}').flags))
""")
    check('other platform: accepted and flagged duplicate_id for the reviewer',
          r.status_code == 200 and flags == ['duplicate_id'], f'{r.status_code} {flags}')

    print('== A platform that is down, then recovers (Celery beat retry)')
    requests.post(f'{HOOK}/control', json={'status': 500}, timeout=5)
    retry = complete(ona, 'efua', id_number='GHA-333333333-3')
    ona.post(f'{API}/v1/verifications/{retry}/submit/').raise_for_status()
    failed = wait_for(lambda: [m for m in hooks_of('verification.submitted', retry) if m['answered'] == 500], 30)
    check('first delivery answered 500', bool(failed))
    requests.post(f'{HOOK}/control', json={'status': 200}, timeout=5)
    print('     waiting for the scheduled retry (up to ~2.5 minutes)…')
    ok = wait_for(lambda: [m for m in hooks_of('verification.submitted', retry) if m['answered'] == 200], 170, 5)
    same_id = ok and failed and ok[0]['event_id'] == failed[0]['event_id']
    check('retried by the worker and accepted, with the same KYC-Event-Id', bool(same_id),
          f'{len(ok or [])} successful deliveries')

    print('== Retention: scans deleted after the period')
    purge = django(f"""
import json, boto3
from datetime import timedelta
from django.conf import settings as s
from django.utils import timezone
from verification import services
from verification.models import Document, Verification
keys = list(Document.objects.filter(verification_id='{vid}').values_list('storage_key', flat=True))
Verification.objects.filter(pk='{vid}').update(decided_at=timezone.now() - timedelta(days=91))
n = services.purge_documents(90)
c = boto3.client('s3', endpoint_url=s.S3_ENDPOINT_URL, aws_access_key_id=s.S3_ACCESS_KEY_ID,
                 aws_secret_access_key=s.S3_SECRET_ACCESS_KEY, region_name=s.S3_REGION)
left = [k for k in keys if c.list_objects_v2(Bucket=s.S3_BUCKET, Prefix=k).get('KeyCount', 0)]
v = Verification.objects.get(pk='{vid}')
print('RESULT:' + json.dumps({{'purged': n, 'left_in_bucket': left, 'status': v.status}}))
""")
    check('3 scans deleted from the bucket; the decision is kept',
          purge['purged'] == 3 and not purge['left_in_bucket'] and purge['status'] == 'approved', purge)


def unit_tests_on_postgres() -> None:
    print('== The unit test suite, inside the image, on Postgres')
    r = sh('exec', '-T', '-e', 'DJANGO_SETTINGS_MODULE=config.settings.local', 'web',
           'python', '-m', 'pytest', '-p', 'no:cacheprovider', '-p', 'no:warnings', '-q', timeout=600)
    last = (r.stdout.strip().splitlines() or [''])[-1]
    check(f'pytest on Postgres: {last}', r.returncode == 0, r.stdout[-2500:] + r.stderr[-1500:])


def main() -> int:
    keep = '--keep' in sys.argv
    bring_up()
    try:
        keys = setup()
        scenario(keys)
        unit_tests_on_postgres()
        logs = sh('logs', 'web', 'worker').stdout
        check('no tracebacks in web or worker logs', 'Traceback' not in logs,
              logs[logs.find('Traceback') - 200: logs.find('Traceback') + 1500] if 'Traceback' in logs else '')
    except Exception as exc:  # report, then still tear down
        check('scenario ran to the end', False, repr(exc))
    finally:
        if not keep:
            sh('down', '-v', '--remove-orphans')
    failed = [r for r in results if not r[1]]
    print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
