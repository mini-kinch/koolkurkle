#!/usr/bin/env python3
"""KOO-60 fetch/auth error ≠ tombstone + UID+UIDVALIDITY persistence.

Docs/tests/fail-closed only. No live IMAP. Empty fetch ≠ gone.
"""

from __future__ import annotations

from typing import Any

UIDVALIDITY_KEY = "uidvalidity"
UID_KEY = "uid"


class FetchErrorTombstoneRefuse(RuntimeError):
    """Fail-closed tombstone decision. Never includes secrets."""


def persist_uid_pair(uid: str | int | None, uidvalidity: str | int | None) -> dict[str, str]:
    """UID alone is not identity. Persist UID + UIDVALIDITY together."""
    if uid is None or str(uid).strip() == "":
        raise FetchErrorTombstoneRefuse("UID+UIDVALIDITY persistence requires uid")
    if uidvalidity is None or str(uidvalidity).strip() == "":
        raise FetchErrorTombstoneRefuse(
            "UID+UIDVALIDITY persistence requires uidvalidity"
        )
    return {UID_KEY: str(uid), UIDVALIDITY_KEY: str(uidvalidity)}


def same_uid_identity(
    left_uid: str | int | None,
    left_uidvalidity: str | int | None,
    right_uid: str | int | None,
    right_uidvalidity: str | int | None,
) -> bool:
    if left_uidvalidity is None or right_uidvalidity is None:
        return False
    return (
        str(left_uid) == str(right_uid)
        and str(left_uidvalidity) == str(right_uidvalidity)
    )


def may_tombstone(
    *,
    fetch_ok: bool,
    auth_ok: bool,
    empty_fetch: bool = False,
    listed_on_server: bool | None = None,
    fetch_error: str | None = None,
    auth_error: str | None = None,
) -> bool:
    """True only when IMAP listing proves the message is gone.

    Fetch/auth error ≠ tombstone. Empty fetch ≠ gone.
    """
    if not auth_ok or auth_error:
        return False
    if not fetch_ok or fetch_error:
        return False
    if empty_fetch:
        return False
    if listed_on_server is None:
        return False
    return listed_on_server is False


def tombstone_decision(**kwargs: Any) -> dict[str, Any]:
    allowed = may_tombstone(**kwargs)
    reason = "listed_absent" if allowed else "fetch_or_auth_error_or_empty_not_gone"
    if kwargs.get("empty_fetch"):
        reason = "empty_fetch_neq_gone"
    elif not kwargs.get("auth_ok", True) or kwargs.get("auth_error"):
        reason = "auth_error_neq_tombstone"
    elif not kwargs.get("fetch_ok", True) or kwargs.get("fetch_error"):
        reason = "fetch_error_neq_tombstone"
    return {
        "may_tombstone": allowed,
        "reason": reason,
        "present_on_server_write": 0 if allowed else None,
    }
