"""
Encryption and fingerprints for ID numbers.

encrypt/decrypt: reversible, for the reviewer who must compare the typed
number with the card. fingerprint: one-way and keyed, for spotting the same
card on two accounts. Without the hash key a fingerprint cannot be matched
against a list of guessed numbers, which a plain SHA-256 of an 11-digit
number could be in minutes.
"""

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    keys = [k.strip() for k in settings.KYC_FIELD_KEYS if k.strip()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode() if token else ''


def fingerprint(normalised: str) -> str:
    return hmac.new(settings.KYC_HASH_KEY.encode(), normalised.encode(), hashlib.sha256).hexdigest()
