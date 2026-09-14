#!/usr/bin/env python3
"""Daily body/FTS: IMAP BODY.PEEK + FTS. Copy-only SoR bind.

Honors --db then $MAILROOM_DB via mailroom_copy_db.bind_copy_db.
Allowlist: mailroom-copy.sqlite | mailroom-daily-copy.sqlite.
Unset / mailroom.sqlite refuse (fail closed). No silent SoR default.

BODY.PEEK prefers Homebrew curl >= 8.17 at
/opt/homebrew/opt/curl/bin/curl. Apple /usr/bin/curl is fail-closed
for BODY.PEEK. The daily driver unsets CURL_BIN so this resolver can
pick Homebrew. This GitHub contract does not open IMAP or Keychain.

Keychain item name only: mailroom.imap.app-password (never secret
values). Destructive CLI verbs are hard-refused (soft-delete).

  /usr/bin/python3 imap_fetch_bodies_fts.py --db /tmp/mailroom-copy.sqlite
  /usr/bin/python3 imap_fetch_bodies_fts.py --db /tmp/mailroom.sqlite
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mailroom_copy_db import bind_copy_db, child_main

__all__ = [
    "APPLE_CURL",
    "HOMEBREW_CURL",
    "KEYCHAIN_ITEM_NAME",
    "MIN_CURL_VERSION",
    "CurlRefuse",
    "bind_copy_db",
    "main",
    "parse_curl_version",
    "refuse_apple_curl_for_body_peek",
    "resolve_bodies_curl",
]

HOMEBREW_CURL = Path("/opt/homebrew/opt/curl/bin/curl")
APPLE_CURL = Path("/usr/bin/curl")
MIN_CURL_VERSION = (8, 17)
KEYCHAIN_ITEM_NAME = "mailroom.imap.app-password"

_CURL_VERSION_RE = re.compile(
    r"curl\s+(\d+)\.(\d+)(?:\.(\d+))?",
    re.IGNORECASE,
)


class CurlRefuse(RuntimeError):
    """Fail-closed BODY.PEEK curl contract. Never includes secrets."""


def parse_curl_version(text: str) -> tuple[int, int, int] | None:
    """Parse ``curl 8.17.0`` from ``curl --version`` text. No subprocess."""
    match = _CURL_VERSION_RE.search(text or "")
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def version_at_least(
    version: tuple[int, int, int] | None,
    minimum: tuple[int, int] = MIN_CURL_VERSION,
) -> bool:
    if version is None:
        return False
    return version[:2] >= minimum


def is_apple_curl(path: str | Path) -> bool:
    return Path(path).expanduser() == APPLE_CURL


def refuse_apple_curl_for_body_peek(path: str | Path) -> None:
    if is_apple_curl(path):
        raise CurlRefuse(
            "Apple /usr/bin/curl fail-closed for BODY.PEEK; "
            "prefer Homebrew curl >= 8.17 at %s"
            % HOMEBREW_CURL
        )


def resolve_bodies_curl(
    *,
    curl_bin: str | None = None,
    version_text: str | None = None,
    exists_fn=None,
) -> Path:
    """Pick BODY.PEEK curl. Inject version_text / exists_fn — no live curl.

    Prefer ``/opt/homebrew/opt/curl/bin/curl`` when present and
    ``version_text`` is >= 8.17. Apple ``/usr/bin/curl`` is refused.
    """
    exists = exists_fn or (lambda p: Path(p).is_file())
    raw = (curl_bin if curl_bin is not None else os.environ.get("CURL_BIN") or "").strip()
    if raw:
        chosen = Path(raw).expanduser()
        refuse_apple_curl_for_body_peek(chosen)
    else:
        chosen = HOMEBREW_CURL
    if not exists(chosen):
        raise CurlRefuse(
            "BODY.PEEK curl missing: %s (prefer Homebrew curl >= 8.17)"
            % chosen
        )
    refuse_apple_curl_for_body_peek(chosen)
    if version_text is not None:
        parsed = parse_curl_version(version_text)
        if not version_at_least(parsed):
            raise CurlRefuse(
                "BODY.PEEK requires Homebrew curl >= 8.17 (got %s)"
                % (version_text.strip().splitlines()[0] if version_text.strip() else "unknown")
            )
    return chosen


def main(argv: list[str] | None = None) -> int:
    return child_main(argv, name="imap_fetch_bodies_fts")


if __name__ == "__main__":
    raise SystemExit(main())
