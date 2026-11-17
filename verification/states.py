"""
Every status change a verification may make, in one table.

If a move is not listed here, nothing in the service can make it — the
views and the admin all go through services.transition(), which checks
this. (In the Eagles monolith each view had its own `if status in [...]`.)

    draft ──submit──> submitted ──start──> in_review
      ^                   │                   │
      │                   ├──────approve──────┼──> approved
      │                   ├──────reject───────┼──> rejected
      │                   └──request changes──┴──> changes_requested
      │                                                 │
      └────────── (person edits, then submits again) ───┘
"""

from .models import Status as S

TRANSITIONS: dict[str, set[str]] = {
    S.DRAFT: {S.SUBMITTED},
    S.CHANGES_REQUESTED: {S.SUBMITTED},
    S.SUBMITTED: {S.IN_REVIEW, S.APPROVED, S.REJECTED, S.CHANGES_REQUESTED},
    S.IN_REVIEW: {S.APPROVED, S.REJECTED, S.CHANGES_REQUESTED},
    S.APPROVED: set(),
    S.REJECTED: set(),
}

# The person may change details and documents only here.
EDITABLE = {S.DRAFT, S.CHANGES_REQUESTED}

# Decisions a reviewer makes; each sends a webhook. The last two need a
# reason the person can act on.
DECISIONS = {S.APPROVED, S.REJECTED, S.CHANGES_REQUESTED}
NEEDS_REASON = {S.REJECTED, S.CHANGES_REQUESTED}


def can_move(current: str, to: str) -> bool:
    return to in TRANSITIONS.get(current, set())
