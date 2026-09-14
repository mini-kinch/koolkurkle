#!/usr/bin/env python3
"""Fail-closed SQL maintenance denylist (KOO-49).

Refuse DELETE FROM messages / DROP TABLE messages / TRUNCATE in SQL
helpers. Soft-delete one-pager §5. No SoR open required.

Does not refuse sibling tables (messages_ids, messages_fts,
message_embeddings). Docs/tests/fail-closed only. No live writers.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from typing import Any, Sequence

REFUSE_PREFIX = "sql-maintenance denylist: refuse"

_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
_WS_RE = re.compile(r"\s+")

# DELETE FROM [schema.]messages — not messages_ids / messages_fts.
_DELETE_MESSAGES = re.compile(
    r"(?i)\bDELETE\s+FROM\s+"
    r"(?:(?:[\"`\[])?\w+(?:[\"`\]])?\s*\.\s*)?"
    r"(?:[\"`\[])?messages(?:[\"`\]])?(?!\w)"
)
_DROP_MESSAGES = re.compile(
    r"(?i)\bDROP\s+TABLE\s+"
    r"(?:IF\s+EXISTS\s+)?"
    r"(?:(?:[\"`\[])?\w+(?:[\"`\]])?\s*\.\s*)?"
    r"(?:[\"`\[])?messages(?:[\"`\]])?(?!\w)"
)
_TRUNCATE = re.compile(r"(?i)\bTRUNCATE\b")


class SqlMaintenanceRefuse(RuntimeError):
    """Hard refuse of DELETE/DROP/TRUNCATE on messages. Never includes secrets."""


def normalize_sql(sql: str) -> str:
    text = _COMMENT_RE.sub(" ", sql or "")
    return _WS_RE.sub(" ", text).strip()


def find_denied_sql(sql: str | None) -> str | None:
    """Return the denied verb, or None if the statement is allowed."""
    text = normalize_sql(sql or "")
    if not text:
        return None
    if _TRUNCATE.search(text):
        return "TRUNCATE"
    if _DROP_MESSAGES.search(text):
        return "DROP TABLE messages"
    if _DELETE_MESSAGES.search(text):
        return "DELETE FROM messages"
    return None


def refuse_sql_maintenance_message(verb: str) -> str:
    return (
        "%s %s. Soft-delete only. Never DELETE FROM messages / "
        "DROP TABLE messages / TRUNCATE. Local tombstone only "
        "(present_on_server). See docs/soft-delete.md §5."
        % (REFUSE_PREFIX, verb)
    )


def refuse_sql_maintenance(sql: str | None) -> None:
    """Raise SqlMaintenanceRefuse when sql names a denied statement."""
    verb = find_denied_sql(sql)
    if verb is not None:
        raise SqlMaintenanceRefuse(refuse_sql_maintenance_message(verb))


def execute_sql(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | None = None,
) -> sqlite3.Cursor:
    """Fail-closed execute. Inspects sql; does not open a SoR path."""
    refuse_sql_maintenance(sql)
    if params is None:
        return conn.execute(sql)
    return conn.execute(sql, params)


def main(argv: list[str] | None = None) -> int:
    text = " ".join(argv if argv is not None else sys.argv[1:]).strip()
    try:
        refuse_sql_maintenance(text)
    except SqlMaintenanceRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    sys.stdout.write("ok: sql not on maintenance denylist\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
