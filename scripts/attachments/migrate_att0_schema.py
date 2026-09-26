#!/usr/bin/env python3
"""ATT-0 attachment schema migration (idempotent). Code and tests only.

Refuses a database named mailroom.sqlite unless --allow-mailroom-sqlite
is passed, then still calls sor_writer_gate.refuse_if_sor_writer_conflict
and refuse_destructive.refuse_destructive_cli. Does not ALTER or DROP
pre-existing tables. Does not touch message_embeddings. Does not apply
itself to a system of record.

  python3 scripts/attachments/migrate_att0_schema.py --db /tmp/mailroom-copy.sqlite
  python3 scripts/attachments/migrate_att0_schema.py --db /tmp/mailroom.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from refuse_destructive import DestructiveRefuse, refuse_destructive_cli  # noqa: E402
from sor_writer_gate import (  # noqa: E402
    SOR_BASENAME,
    SorWriterRefuse,
    refuse_if_sor_writer_conflict,
)

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"

EXPECTED_COLUMNS = {
    "attachments": [
        "attachment_id",
        "message_id",
        "part_id",
        "filename",
        "mime",
        "size",
        "sha256",
        "status",
    ],
    "attachment_extracts": [
        "extract_id",
        "attachment_id",
        "extractor",
        "extractor_version",
        "text",
        "page_count",
        "status",
        "error",
        "timings",
    ],
    "attachment_chunks": [
        "chunk_id",
        "extract_id",
        "chunk_index",
        "page_start",
        "page_end",
        "text",
    ],
}

OWNED_EXACT = {
    "attachments",
    "attachment_extracts",
    "attachment_chunks",
    "attachment_chunks_fts",
    "idx_attachments_message_part",
    "idx_attachments_sha256",
    "idx_attachment_extracts_attachment_id",
    "idx_attachment_chunks_extract_index",
    "attachment_chunks_ai",
    "attachment_chunks_ad",
    "attachment_chunks_au",
}

TRIGGERS = (
    (
        "attachment_chunks_ai",
        """
        CREATE TRIGGER attachment_chunks_ai AFTER INSERT ON attachment_chunks BEGIN
          INSERT INTO attachment_chunks_fts(rowid, text)
          VALUES (new.chunk_id, new.text);
        END
        """,
    ),
    (
        "attachment_chunks_ad",
        """
        CREATE TRIGGER attachment_chunks_ad AFTER DELETE ON attachment_chunks BEGIN
          INSERT INTO attachment_chunks_fts(attachment_chunks_fts, rowid, text)
          VALUES ('delete', old.chunk_id, old.text);
        END
        """,
    ),
    (
        "attachment_chunks_au",
        """
        CREATE TRIGGER attachment_chunks_au AFTER UPDATE ON attachment_chunks BEGIN
          INSERT INTO attachment_chunks_fts(attachment_chunks_fts, rowid, text)
          VALUES ('delete', old.chunk_id, old.text);
          INSERT INTO attachment_chunks_fts(rowid, text)
          VALUES (new.chunk_id, new.text);
        END
        """,
    ),
)


class MigrateRefuse(RuntimeError):
    """ATT-0 migrate refuse. Never includes secrets or home paths."""


def _owned(name: str) -> bool:
    if name in OWNED_EXACT:
        return True
    return name.startswith("attachment_chunks_fts_")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table', 'view') LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(%s)" % table)]


def _protected_snapshot(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    snapshot = []
    for typ, name, sql in rows:
        if _owned(str(name)):
            continue
        snapshot.append((str(typ), str(name), sql))
    return snapshot


def _embeddings_fingerprint(conn: sqlite3.Connection) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='message_embeddings'"
    ).fetchone()
    if row is None:
        return ("absent", None, None)
    sql = row[0]
    try:
        count = conn.execute("SELECT COUNT(*) FROM message_embeddings").fetchone()[0]
    except sqlite3.DatabaseError:
        count = None
    return ("present", sql, count)


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _refuse_shape(conn: sqlite3.Connection) -> None:
    for table, expected in EXPECTED_COLUMNS.items():
        if not _table_exists(conn, table):
            continue
        found = _columns(conn, table)
        if found != expected:
            raise MigrateRefuse(
                "refuse: %s already exists with a different column list; "
                "will not ALTER or DROP an existing table" % table
            )
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='attachment_chunks_fts'"
    ).fetchone()
    if row is None or row[0] is None:
        return
    sql = str(row[0]).lower()
    if "fts5" not in sql or "attachment_chunks" not in sql:
        raise MigrateRefuse(
            "refuse: attachment_chunks_fts exists with an unexpected definition"
        )


def _ensure_triggers(conn: sqlite3.Connection) -> None:
    have = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    for name, sql in TRIGGERS:
        if name in have:
            continue
        conn.execute(sql)


def _apply_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    if not SCHEMA_SQL.is_file():
        raise MigrateRefuse("schema sql is missing")
    _refuse_shape(conn)
    before_master = _protected_snapshot(conn)
    before_embed = _embeddings_fingerprint(conn)
    before_uv = _user_version(conn)
    existed = {name: _table_exists(conn, name) for name in EXPECTED_COLUMNS}

    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    _ensure_triggers(conn)
    conn.execute(
        "INSERT INTO attachment_chunks_fts(attachment_chunks_fts) VALUES ('rebuild')"
    )

    after_master = _protected_snapshot(conn)
    after_embed = _embeddings_fingerprint(conn)
    after_uv = _user_version(conn)
    if before_master != after_master:
        raise MigrateRefuse("refuse: migration changed an existing object")
    if before_embed != after_embed:
        raise MigrateRefuse("refuse: message_embeddings changed")
    if before_uv != after_uv:
        raise MigrateRefuse("refuse: user_version changed")

    report: dict[str, Any] = {
        "tables": {
            name: "exists" if existed[name] else "created" for name in EXPECTED_COLUMNS
        },
        "fts": "ensured",
        "message_embeddings": "untouched" if before_embed[0] == "present" else "absent",
        "user_version": before_uv,
    }
    return report


def migrate_database(
    db: str | Path,
    *,
    allow_mailroom_sqlite: bool = False,
    argv: list[str] | None = None,
    lock_held: bool | None = None,
    cmdlines: Any = None,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Apply ATT-0 DDL. Opens the database only after the refuses pass."""
    path = Path(db)
    refuse_destructive_cli([] if argv is None else list(argv))
    if path.name == SOR_BASENAME and not allow_mailroom_sqlite:
        raise MigrateRefuse(
            "refuse: basename mailroom.sqlite "
            "(pass --allow-mailroom-sqlite to override)"
        )
    refuse_if_sor_writer_conflict(
        path,
        cmdlines=cmdlines,
        lock_path=lock_path,
        lock_held=lock_held,
    )
    parent = path.parent
    if parent != Path("") and not parent.is_dir():
        raise MigrateRefuse("database directory is missing")
    conn = sqlite3.connect(str(path))
    try:
        report = _apply_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    report["db_basename"] = path.name
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "att0 schema migrate",
        "db_basename=%s" % report.get("db_basename"),
        "message_embeddings=%s" % report.get("message_embeddings"),
        "user_version=%s" % report.get("user_version"),
        "fts=%s" % report.get("fts"),
    ]
    tables = report.get("tables") or {}
    for name in EXPECTED_COLUMNS:
        lines.append("%s=%s" % (name, tables.get(name)))
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ATT-0 attachment schema (idempotent). "
            "Refuses basename mailroom.sqlite unless --allow-mailroom-sqlite. "
            "Reuses sor_writer_gate and refuse_destructive. Does not apply itself."
        )
    )
    parser.add_argument("--db", required=True, help="SQLite path to migrate.")
    parser.add_argument(
        "--allow-mailroom-sqlite",
        action="store_true",
        help="Permit basename mailroom.sqlite. The writer gate still applies.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        refuse_destructive_cli(raw)
    except DestructiveRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    args = build_parser().parse_args(raw)
    try:
        report = migrate_database(
            args.db,
            allow_mailroom_sqlite=bool(args.allow_mailroom_sqlite),
            argv=raw,
        )
    except (MigrateRefuse, SorWriterRefuse, DestructiveRefuse) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except sqlite3.Error as exc:
        sys.stderr.write("error: sqlite migrate failed (%s)\n" % type(exc).__name__)
        return 2
    sys.stdout.write(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
