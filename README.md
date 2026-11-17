# KYC Service

Identity verification as a service. Any platform (ONA Records, Eagles &
Eaglets, and the next one) connects with an API key, sends its users through
the same check, and is told the decision by a signed webhook. Platforms keep
their own users and profiles; this service holds only what proves identity.

## How a platform uses it

```
Platform backend                          KYC service
────────────────                          ───────────
POST /v1/verifications/        ─────────>  start (or resume) for subject_ref
PATCH /v1/verifications/{id}/  ─────────>  name, date of birth, ID type + number
POST  …/{id}/documents/  ×3    ─────────>  id_front, id_back, selfie
POST  …/{id}/submit/           ─────────>  locked, queued for review
                               <─────────  webhook  verification.submitted
            (reviewer decides)
                               <─────────  webhook  verification.approved | rejected | changes_requested
GET   /v1/subjects/{ref}/      ─────────>  { verified: true } — gate features on this
```

Every call sends `Authorization: Bearer kyc_<prefix>_<secret>`. The browser
never talks to this service: the platform's backend does, so the key stays
on a server. A platform sees only its own users; anyone else's id is a 404.

`subject_ref` is the platform's own id for the user (e.g. ONA's user UUID).
No email, phone or profile is sent here.

Interactive API docs: `/docs/` (OpenAPI schema at `/schema/`).

### Levels

| Level | Fields | Documents |
|---|---|---|
| `identity` | full name, date of birth, ID type (`ghana_card` / `passport`), ID number | Ghana Card: front, back, selfie. Passport: photo page, selfie |

Ghana Card numbers are accepted typed any way (`gha 123456789 0`) and stored
as `GHA-123456789-0`. New levels are added in `verification/levels.py`.

### Statuses

`draft → submitted → in_review → approved | rejected | changes_requested`.
`changes_requested` reopens editing; the person fixes and submits again.
`approved` and `rejected` are final; a rejected person may start a new
verification. The full table is `verification/states.py`.

### Webhooks

POSTed to the platform's `webhook_url` with:

```
KYC-Event-Id:  <uuid>            — ignore one you have already handled (retries)
KYC-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>
```

Verify before trusting (Python):

```python
import hashlib, hmac, time

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:      # replayed or stale
        return False
    expected = hmac.new(secret.encode(), f'{parts["t"]}.'.encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

Answer 2xx quickly. Anything else is retried with backoff for about a day and
a half (`verification/webhooks.py`). Payloads carry status only: no names,
numbers or images.

### Reviewing

Two ways, both through the same rules and the same audit trail:

- **KYC staff** sign in to this service's admin (private `ADMIN_URL`) and use
  the review endpoints `/v1/review/…` with their session.
- **A platform's own staff**, through the platform's console, with a key
  issued `--can-review`. The platform must send `reviewer` (who decided);
  the trail records `platform:ona-records/kofi@ona`.

A reviewer's detail view shows the full ID number and links to the scans
that expire after 5 minutes. Each opening is logged.

A platform reviewing in its own console should not hand bucket links to a
browser: it fetches each scan with
`GET /v1/review/verifications/{id}/documents/{id_front|id_back|selfie}/?reviewer=<who>`
and relays the bytes to its signed-in staff (ONA shows them from `blob:`
URLs). Each fetch is logged as a view of that document.

## What changed from the Eagles & Eaglets KYC

| Before (inside the Eagles monolith) | Now |
|---|---|
| `MentorKYC` and `MenteeKYC`, near-copies; admin views branch on a `role` that defaults to mentor | One `Verification`, with levels as configuration |
| Identity mixed with app data (church, testimony, marital status, CV) | Identity only; app data stays in the app |
| Status checks repeated per view | One transition table, enforced with row locks |
| Last reviewer and note overwritten | Append-only event log, including who viewed an ID |
| Approval creates a Nest and sends email inline | Signed webhook; the platform does its own follow-up |
| ID number checked as "6–20 characters" | Ghana Card format, encrypted, keyed fingerprint for duplicates |
| Files kept indefinitely | Private bucket, signature-checked uploads, scans deleted after `KYC_DOCUMENT_RETENTION_DAYS` |

## Running it locally

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows; bin/ elsewhere
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py createsuperuser
.venv/Scripts/python manage.py create_platform ona-records --name "ONA Records" --key-name local
.venv/Scripts/python manage.py runserver 8100
.venv/Scripts/python -m pytest
```

Local settings use SQLite and public throwaway keys. CI runs the tests on
Postgres. To test against Postgres locally, set `DATABASE_URL`.

### End-to-end test in Docker

```bash
.venv/Scripts/python e2e/run.py          # add --keep to leave the stack running
```

Builds the real image and runs it with staging settings beside Postgres,
Redis, an S3-compatible bucket (RustFS), the Celery worker and a pretend
platform webhook endpoint (ports 8110 and 9012). It drives the API as a
platform would — including simultaneous requests, a platform that is down
and recovers, and the retention purge — then runs the unit tests inside the
image on Postgres, checks the logs for tracebacks, and tears everything down.

## Deploying (Railway)

One project, its own Postgres and bucket, separate from every platform's:

1. Service from this repo; settings: healthcheck `/health/`, start command
   default (`./start.sh`).
2. Add Postgres, a Storage Bucket, and (recommended) Redis.
3. Variables from `.env.example`. Generate `KYC_FIELD_KEYS` and
   `KYC_HASH_KEY` once and keep a copy somewhere safe: losing the field key
   makes every stored ID number unreadable.
4. A second service from the same repo with start command
   `./start-worker.sh`, `CELERY_BEAT=1`, the same variables.
5. In the shell: `python manage.py createsuperuser`, then for each platform
   `python manage.py create_platform <slug> --name … --webhook-url … --key-name …`.
   Put the printed `KYC_API_KEY` and `KYC_WEBHOOK_SECRET` in that platform's variables.

The service refuses to start in staging/production without a bucket,
encryption keys, or with `ADMIN_URL` left as `admin/`.

## Still to decide

- **Duplicate IDs** — the same card already approved on another account is
  blocked on the same platform and flagged for the reviewer across
  platforms. Confirm or change in `verification/policy.py`.
- **Retention** — 90 days after a decision is the default; confirm against
  ONA's and Eagles' obligations under the Data Protection Act, 2012 (Act 843),
  and register with the Data Protection Commission as a data controller.
- **Automated checks** — a provider (NIA Ghana Card lookup through a vendor,
  face match) can be added later as a step before human review, without
  changing the API platforms use.
