#!/usr/bin/env python3
"""Fail-closed refuse for destructive mail CLI verbs (KOO-37 / KOO-43).

Soft-delete / never physical purge. Hard-refuse purge|expunge|
empty-trash|delete-gone|drop-messages. No IMAP STORE \\Deleted,
EXPUNGE, Trash-purge, or server drop.

imap_tombstone / tombstone path is local present_on_server only.
Refuse IMAP STORE \\Deleted, EXPUNGE, and Trash-purge verbs.

Docs/tests/fail-closed only. No live IMAP. No MailArchive writers.
"""

from __future__ import annotations

import sys

DESTRUCTIVE_VERBS = frozenset(
    {
        "purge",
        "expunge",
        "empty-trash",
        "delete-gone",
        "drop-messages",
    }
)

# IMAP protocol verbs the tombstone path must never run.
IMAP_PURGE_VERBS = frozenset(
    {
        "store",
        "deleted",
        "\\deleted",
        "expunge",
        "trash-purge",
        "trash_purge",
        "uid-store",
    }
)

REFUSE_PREFIX = "soft-delete only: refuse"
IMAP_PURGE_PREFIX = "local present_on_server only: refuse IMAP"


class DestructiveRefuse(RuntimeError):
    """Hard refuse of a destructive CLI verb. Never includes secrets."""


def refuse_destructive_message(verb: str) -> str:
    return (
        "%s %s. Never physically purge. No EXPUNGE / STORE \\Deleted / "
        "Trash-purge / empty-trash / delete-gone / drop-messages. "
        "Local tombstone only (present_on_server)."
        % (REFUSE_PREFIX, verb)
    )


def _norm_verb_token(token: str) -> str:
    text = (token or "").strip()
    if text.startswith("--"):
        text = text[2:]
    elif text.startswith("-") and not text.startswith("--"):
        text = text[1:]
    return text.split("=", 1)[0].strip().lower()


def find_destructive_verb(argv: list[str] | None = None) -> str | None:
    """Return the first destructive CLI verb in argv, or None.

    Matches ``purge`` / ``--purge`` / ``--purge=1``. A longer query token
    such as ``purge the inbox`` is not a verb.
    """
    if argv is None:
        argv = sys.argv[1:]
    for arg in argv:
        name = _norm_verb_token(arg)
        if name in DESTRUCTIVE_VERBS:
            return name
    return None


def refuse_destructive_cli(argv: list[str] | None = None) -> None:
    """Raise DestructiveRefuse when argv names a destructive verb."""
    verb = find_destructive_verb(argv)
    if verb is not None:
        raise DestructiveRefuse(refuse_destructive_message(verb))


def refuse_imap_purge_message(verb: str) -> str:
    return (
        "%s %s. Never IMAP STORE \\Deleted, EXPUNGE, or Trash-purge. "
        "Tombstone path is local present_on_server only."
        % (IMAP_PURGE_PREFIX, verb)
    )


def find_imap_purge_verb(argv: list[str] | None = None) -> str | None:
    """Return the first IMAP STORE/EXPUNGE/Trash-purge verb, or None."""
    if argv is None:
        argv = sys.argv[1:]
    for arg in argv:
        name = _norm_verb_token(arg)
        if name in IMAP_PURGE_VERBS:
            return name
    return None


def refuse_imap_purge_cli(argv: list[str] | None = None) -> None:
    """Raise DestructiveRefuse for IMAP STORE \\Deleted / EXPUNGE / Trash-purge."""
    verb = find_imap_purge_verb(argv)
    if verb is not None:
        raise DestructiveRefuse(refuse_imap_purge_message(verb))


def main(argv: list[str] | None = None) -> int:
    try:
        refuse_destructive_cli(argv)
        refuse_imap_purge_cli(argv)
    except DestructiveRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    sys.stdout.write("ok: no destructive CLI verbs\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
