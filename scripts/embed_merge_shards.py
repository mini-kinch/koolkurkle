#!/usr/bin/env python3
"""Merge missing embed rows after separate shard writers (HARD DECK).

Supported path: one `embed_backfill.py` writer per `.sqlite` (copy vs
SoR-named, or other separate files). After **both EXIT 0**, pause the
other writer for the merge window and run this CLI.

Wraps `embed_lib.merge_shards`: missing-only copy of `embedding_meta` +
`message_embeddings` from `--secondary` into `--primary`. Leaves an
existing primary `(message_id, model, model_version)` untouched even if
`text_hash` differs. Never deletes primary rows. Never writes
`messages` / FTS. Never calls Ollama or IMAP. Idempotent.

`--primary` and `--secondary` must be different files. This CLI does
**not** make same-file 2-wide writers safe. Do not run two embed
writers on one sqlite.

  python embed_merge_shards.py --primary primary.sqlite --secondary shard.sqlite --dry-run
  python embed_merge_shards.py --primary primary.sqlite --secondary shard.sqlite
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from embed_lib import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_VERSION,
    EmbedError,
    load_sqlite_vec,
    merge_shards,
)
from sor_writer_gate import SorWriterRefuse, refuse_if_sor_writer_conflict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Missing-only merge of embed rows from --secondary into --primary "
            "after separate shard writers EXIT 0. Wraps embed_lib.merge_shards. "
            "Never two embed writers on one sqlite (HARD DECK). "
            "No Ollama, no IMAP, no messages/FTS writes."
        )
    )
    parser.add_argument(
        "--primary",
        required=True,
        help=(
            "Primary sqlite (receives missing embed rows). Must be a different "
            "file from --secondary."
        ),
    )
    parser.add_argument(
        "--secondary",
        required=True,
        help=(
            "Secondary sqlite (shard/copy source). Missing-only: rows already "
            "on primary are left untouched."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would insert; no commit.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Stored model tag / alias (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--model-version",
        default=DEFAULT_MODEL_VERSION,
        help=f"embedding_meta.model_version (default: {DEFAULT_MODEL_VERSION}).",
    )
    parser.add_argument(
        "--vec-extension",
        default=None,
        help="Path to vec0.dylib / vec0.so when message_embeddings is vec0.",
    )
    return parser


def resolve_merge_paths(primary: str, secondary: str) -> tuple[Path, Path]:
    """Require two existing, distinct sqlite files. No live-path default."""
    pri = Path(primary).expanduser()
    sec = Path(secondary).expanduser()
    if not pri.is_file():
        raise EmbedError(f"primary DB not found: {pri}")
    if not sec.is_file():
        raise EmbedError(f"secondary DB not found: {sec}")
    pri_r = pri.resolve()
    sec_r = sec.resolve()
    same = pri_r == sec_r
    if not same:
        try:
            same = pri_r.samefile(sec_r)
        except OSError:
            same = False
    if same:
        raise EmbedError(
            "primary and secondary must be different files. "
            "merge_shards copies after separate shard runs; "
            "same-file 2-wide writers are HARD DECK."
        )
    return pri_r, sec_r


def _message_embeddings_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'message_embeddings'"
    ).fetchone()
    if row is None or not row[0]:
        return ""
    return str(row[0])


def connect_merge_db(
    db_path: str | Path, extension_path: str | None = None
) -> sqlite3.Connection:
    """Open a shard DB. Load sqlite-vec only for vec0 or a missing table.

    Fixture tests use a non-vec0 ``float[N]`` table so this stays offline
    and does not require a live SoR / MailArchive sqlite.
    """
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    sql = _message_embeddings_sql(conn)
    uses_vec0 = bool(re.search(r"\busing\s+vec0\b", sql, flags=re.I))
    if uses_vec0 or not sql:
        load_sqlite_vec(conn, extension_path)
    return conn


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        primary, secondary = resolve_merge_paths(args.primary, args.secondary)
        refuse_if_sor_writer_conflict(primary)
        pri_conn = connect_merge_db(primary, args.vec_extension)
        try:
            sec_conn = connect_merge_db(secondary, args.vec_extension)
            try:
                merge_shards(
                    pri_conn,
                    sec_conn,
                    model=args.model,
                    model_version=args.model_version,
                    dry_run=args.dry_run,
                )
            finally:
                sec_conn.close()
        finally:
            pri_conn.close()
    except (EmbedError, SorWriterRefuse) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
