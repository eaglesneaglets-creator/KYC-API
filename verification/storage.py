"""
ID scans and selfies in a private S3-compatible bucket (Railway Storage
Buckets). Adapted from the ONA backend's core/storage.py, with one addition
these files need: the first bytes must match the declared type. A browser
sends whatever Content-Type the uploader's client claims; the bytes do not lie.

Nothing here is public. url_for() gives a reviewer a link that expires in
minutes (S3_URL_EXPIRY_SECONDS) and is built per request, never stored.
"""

import hashlib
import logging
import uuid
from functools import lru_cache

from django.conf import settings
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024

# What each type's file starts with. WebP is RIFF....WEBP, checked below.
SIGNATURES = {
    'image/jpeg': (b'\xff\xd8\xff',),
    'image/png': (b'\x89PNG\r\n\x1a\n',),
    'image/webp': (b'RIFF',),
    'application/pdf': (b'%PDF-',),
}
EXTENSIONS = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'application/pdf': '.pdf'}

# A selfie must be a photo; an ID may also arrive as a scanned PDF.
ALLOWED = {
    'id_front': {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'},
    'id_back': {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'},
    'selfie': {'image/jpeg', 'image/png', 'image/webp'},
}


@lru_cache(maxsize=1)
def _client():
    if not (settings.S3_BUCKET and settings.S3_ACCESS_KEY_ID and settings.S3_SECRET_ACCESS_KEY):
        return None
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': settings.S3_ADDRESSING_STYLE},
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
        ),
    )


def _check(file, kind: str) -> tuple[str, int, str]:
    """Validate type, size and signature. Returns (mime, size, sha256)."""
    if not file:
        raise ValidationError({'file': 'A file is required.'})
    mime = getattr(file, 'content_type', '') or ''
    if mime not in ALLOWED[kind]:
        raise ValidationError({'file': f'Upload a {", ".join(sorted(ALLOWED[kind]))} file.'})
    size = file.size
    if size > MAX_BYTES:
        raise ValidationError({'file': 'That file is larger than 10 MB.'})

    file.seek(0)
    head = file.read(16)
    ok = any(head.startswith(sig) for sig in SIGNATURES[mime])
    if mime == 'image/webp':
        ok = ok and head[8:12] == b'WEBP'
    if not ok:
        raise ValidationError({'file': 'The file does not match its type. Upload the original photo or scan.'})

    file.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: file.read(64 * 1024), b''):
        digest.update(chunk)
    file.seek(0)
    return mime, size, digest.hexdigest()


def upload(file, *, kind: str) -> dict:
    """Store an ID document. Returns {'key', 'content_type', 'size', 'sha256'}."""
    mime, size, sha = _check(file, kind)
    client = _client()
    if client is None:
        logger.error('No storage bucket is configured; upload rejected')
        raise ValidationError({'file': 'Uploads are not available right now.'})
    key = f'{settings.S3_KEY_PREFIX}/documents/{uuid.uuid4().hex}{EXTENSIONS[mime]}'
    try:
        client.upload_fileobj(file, settings.S3_BUCKET, key, ExtraArgs={'ContentType': mime})
    except Exception:
        # The provider's error text can carry the bucket name and signatures.
        logger.exception('Bucket upload failed')
        raise ValidationError({'file': 'The upload failed. Please try again.'}) from None
    return {'key': key, 'content_type': mime, 'size': size, 'sha256': sha}


def delete(key: str) -> bool:
    """False rather than raising: a failed delete is retried by the purge job."""
    try:
        client = _client()
        if client is None or not key:
            return False
        client.delete_object(Bucket=settings.S3_BUCKET, Key=key)
        return True
    except Exception:
        logger.exception('Bucket delete failed for %s', key)
        return False


def url_for(key: str) -> str:
    if not key:
        return ''
    try:
        client = _client()
        if client is None:
            return ''
        return client.generate_presigned_url(
            'get_object', Params={'Bucket': settings.S3_BUCKET, 'Key': key},
            ExpiresIn=settings.S3_URL_EXPIRY_SECONDS,
        )
    except Exception:
        logger.exception('Could not sign a URL for %s', key)
        return ''
