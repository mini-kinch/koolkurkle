#!/usr/bin/env python3
"""Frozen JSONL immutability gate (KOO-50).

Refuse rewrite / reconcile / mutation paths that touch frozen
``icloud_mail_all.jsonl``. Docs/tests/guards only. No dump rewrite.
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN_JSONL_BASENAME = "icloud_mail_all.jsonl"
REFUSE_PREFIX = "frozen jsonl: refuse"

MUTATION_VERBS = frozenset(
    {
        "rewrite",
        "reconcile",
        "overwrite",
        "replace",
        "truncate",
        "write",
        "unlink",
        "rm",
        "delete",
        "dump-rewrite",
        "dump_rewrite",
    }
)


class FrozenJsonlRefuse(RuntimeError):
    """Hard refuse of a frozen JSONL mutation. Never includes secrets."""


def is_frozen_jsonl(path: str | Path | None) -> bool:
    if path is None:
        return False
    name = Path(str(path)).name
    return name == FROZEN_JSONL_BASENAME


def refuse_frozen_jsonl_message(verb: str) -> str:
    return (
        "%s %s of %s. Frozen dump is immutable. No rewrite / reconcile / "
        "overwrite. No dump rewrite."
        % (REFUSE_PREFIX, verb, FROZEN_JSONL_BASENAME)
    )


def refuse_frozen_jsonl_touch(
    path: str | Path | None,
    verb: str = "rewrite",
) -> None:
    """Raise FrozenJsonlRefuse when path is the frozen JSONL dump."""
    if is_frozen_jsonl(path):
        raise FrozenJsonlRefuse(refuse_frozen_jsonl_message(verb))


def _norm_verb_token(token: str) -> str:
    text = (token or "").strip()
    if text.startswith("--"):
        text = text[2:]
    elif text.startswith("-") and not text.startswith("--"):
        text = text[1:]
    return text.split("=", 1)[0].strip().lower()


def find_frozen_jsonl_path(argv: list[str] | None = None) -> str | None:
    if argv is None:
        argv = sys.argv[1:]
    for arg in argv:
        raw = arg.split("=", 1)[-1] if "=" in arg and arg.startswith("-") else arg
        if is_frozen_jsonl(raw):
            return raw
    return None


def find_mutation_verb(argv: list[str] | None = None) -> str | None:
    if argv is None:
        argv = sys.argv[1:]
    for arg in argv:
        name = _norm_verb_token(arg)
        if name in MUTATION_VERBS:
            return name
    return None


def refuse_frozen_jsonl_cli(argv: list[str] | None = None) -> None:
    """Refuse argv that names the frozen dump plus a rewrite/reconcile verb."""
    if argv is None:
        argv = sys.argv[1:]
    path = find_frozen_jsonl_path(argv)
    if path is None:
        return
    verb = find_mutation_verb(argv)
    if verb is None:
        return
    refuse_frozen_jsonl_touch(path, verb)


def main(argv: list[str] | None = None) -> int:
    try:
        refuse_frozen_jsonl_cli(argv)
    except FrozenJsonlRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    sys.stdout.write("ok: frozen jsonl not rewritten\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
