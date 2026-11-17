"""
KYC service — staging. Production in every respect except its name and its
bucket prefix, so staging scans never sit beside real ones.
"""

from .production import *  # noqa: F403

ENVIRONMENT_NAME = 'staging'
S3_KEY_PREFIX = 'kyc-staging'
