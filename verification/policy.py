"""
Business rules that are a judgement call rather than a mechanism.

Kept apart from services.py so the decision is easy to find, test and
change on its own.
"""

from .models import Verification

BLOCK, FLAG, ALLOW = 'block', 'flag', 'allow'


def on_duplicate_id(verification: Verification, matches: list[Verification]) -> str:
    """
    The ID number just submitted already appears on another person's
    APPROVED verification. Decide what happens to this submission.

    `matches` are those other verifications (never empty when this is
    called). Each has .subject.platform (which platform that person is on)
    and .subject.external_ref.

    Return one of:
        BLOCK  refuse the submission: the person sees an error and the
               verification stays editable.
        FLAG   accept it, but mark it "duplicate_id" so the reviewer sees
               a warning before deciding.
        ALLOW  accept it silently.
    """
    # Same platform: one person, two accounts. Refuse before a reviewer
    # spends time on it; support can sort out a genuine lost account.
    if any(m.subject.platform_id == verification.subject.platform_id for m in matches):
        return BLOCK
    # Another platform: usually the same honest person using both services,
    # which is what a shared KYC service is for. Let a human look.
    return FLAG
