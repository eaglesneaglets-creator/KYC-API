"""
Local development and tests.

Fills in throwaway values for every secret so `manage.py` and `pytest` run
with no setup. These values are public — they are in this file — so nothing
encrypted with them is protected. Deployed settings never import this file.
"""

import os

os.environ.setdefault('SECRET_KEY', 'local-dev-only-not-secret')
os.environ.setdefault('DATABASE_URL', 'sqlite:///db.sqlite3')
os.environ.setdefault('ENVIRONMENT_NAME', 'local')
# 32 zero bytes, base64 — a valid Fernet key that protects nothing.
os.environ.setdefault('KYC_FIELD_KEYS', 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.setdefault('KYC_HASH_KEY', 'local-dev-only-hash-key')

from .base import *  # noqa: E402

DEBUG = True
ALLOWED_HOSTS = ['*']
