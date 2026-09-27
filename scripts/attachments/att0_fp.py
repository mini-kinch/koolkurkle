#!/usr/bin/env python3
"""Read-only fingerprint of a SQLite file.

Opens the database only with a ``file:...?mode=ro`` URI and
``PRAGMA query_only=ON``. It does not write SQL. On a WAL database,
SQLite itself may still create an empty ``-wal`` and a ``-shm``
read-mark file; those paths are statted before the connection opens,
and the main-file bytes are not modified.

Every ordinary table (``sqlite_master`` type ``table``, skipping
``sqlite_%``) is reported with a row count and a sha256 of its quoted
rows. Columns are in ``cid`` order. Rows are ordered by the primary
key, or by ``rowid`` when the table has none. That is the same
inclusion and ordering as the shell recipe in
``docs/attachments/ATT-0-fingerprint.md``. The digest does not have to
match the shell bytes.

``--exclude-table`` (repeatable) adds to the default ``ask_audit`` and
``drafts`` exclusions. Those two tables are written by ``ask_mail.py``
serve. ``--exclude-column TABLE.COL`` (repeatable) adds to the default
``messages.has_attachments``. The JSON records the exclusion set that
was applied.

Aggregate imap-live counts are included when ``messages`` has the
needed columns. Folder names are not. ``imap_live_by_folder`` is not
emitted. Subjects, addresses, and message ids are not emitted either.
File stats use basenames only.

JSON goes to stdout, or to ``--out`` when that flag is set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

DEFAULT_EXCLUDE_TABLES = ("ask_audit", "drafts")
DEFAULT_EXCLUDE_COLUMNS = ("messages.has_attachments",)
_SQLITE_MAGIC = b"SQLite format 3\x00"
_IMAP_SOURCE = "imap-live"


class FpRefuse(Exception):
    """Fingerprint refuse. The message has no filesystem path."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _ro_uri(path: Path) -> str:
    posix = Path(os.path.abspath(str(path))).as_posix()
    return "file:%s?mode=ro" % quote(posix, safe="/:")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat_record(path: Path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return {
        "basename": path.name,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def parse_exclusions(extra_tables=None, extra_columns=None):
    """Return sorted ``(tables, columns)`` including the defaults.

    Raises ``FpRefuse`` when a flag is empty or a column is not
    ``TABLE.COL``.
    """
    tables = []
    for name in list(DEFAULT_EXCLUDE_TABLES) + list(extra_tables or ()):
        cleaned = name.strip().lower()
        if not cleaned or "\x00" in cleaned:
            raise FpRefuse("refuse: invalid exclude-table")
        if cleaned not in tables:
            tables.append(cleaned)
    columns = []
    for spec in list(DEFAULT_EXCLUDE_COLUMNS) + list(extra_columns or ()):
        text = spec.strip().lower()
        if "." not in text or "\x00" in text:
            raise FpRefuse("refuse: invalid exclude-column")
        table, column = text.split(".", 1)
        table = table.strip()
        column = column.strip()
        if not table or not column:
            raise FpRefuse("refuse: invalid exclude-column")
        norm = "%s.%s" % (table, column)
        if norm not in columns:
            columns.append(norm)
    return tuple(sorted(tables)), tuple(sorted(columns))


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only with ``query_only`` on."""
    try:
        conn = sqlite3.connect(_ro_uri(path), uri=True, isolation_level=None)
    except sqlite3.Error:
        raise FpRefuse(
            "refuse: database is not readable (%s)" % path.name, code=1
        ) from None
    try:
        conn.execute("PRAGMA query_only=ON")
        flag = conn.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error:
        conn.close()
        raise FpRefuse(
            "refuse: database is not readable (%s)" % path.name, code=1
        ) from None
    if flag is None or int(flag[0]) != 1:
        conn.close()
        raise FpRefuse("refuse: query_only did not stick", code=1)
    return conn


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
        "ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def _table_info(conn: sqlite3.Connection, table: str):
    return conn.execute(
        "SELECT cid, name, pk FROM pragma_table_info(%s) ORDER BY cid"
        % _sql_string(table)
    ).fetchall()


def _excluded_columns(columns: tuple[str, ...], table: str) -> set[str]:
    prefix = table.lower() + "."
    return {spec[len(prefix) :] for spec in columns if spec.startswith(prefix)}


def _row_sha256(conn: sqlite3.Connection, table: str, info, skip: set[str]):
    columns = [name for _cid, name, _pk in info if name.lower() not in skip]
    pk_cols = [
        name
        for _pk, name in sorted(
            (pk, name) for _cid, name, pk in info if pk
        )
    ]
    order = (
        ", ".join(_quote_ident(name) for name in pk_cols) if pk_cols else "rowid"
    )
    ident = _quote_ident(table)
    count = conn.execute("SELECT COUNT(*) FROM %s" % ident).fetchone()[0]
    if columns:
        expr = " || ',' || ".join(
            "quote(%s)" % _quote_ident(name) for name in columns
        )
    else:
        expr = "''"
    sql = "SELECT %s FROM %s ORDER BY %s" % (expr, ident, order)
    digest = hashlib.sha256()
    for (line,) in conn.execute(sql):
        text = line if isinstance(line, str) else ""
        digest.update(text.encode("utf-8"))
        digest.update(b"\n")
    return int(count), digest.hexdigest()


def _imap_counts(conn: sqlite3.Connection, tables: list[str]) -> dict:
    result = {
        "imap_live_total": None,
        "imap_live_null_folder": None,
        "imap_live_folders_n": None,
        "imap_live_not_on_server": None,
    }
    messages = None
    for name in tables:
        if name.lower() == "messages":
            messages = name
            break
    if messages is None:
        return result
    info = _table_info(conn, messages)
    cols = {name.lower() for _cid, name, _pk in info}
    if "source" not in cols:
        return result
    ident = _quote_ident(messages)
    where = "source=%s" % _sql_string(_IMAP_SOURCE)
    result["imap_live_total"] = int(
        conn.execute("SELECT COUNT(*) FROM %s WHERE %s" % (ident, where)).fetchone()[0]
    )
    if "folder" in cols:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN folder IS NULL OR TRIM(folder)='' THEN 1 ELSE 0 END), "
            "COUNT(DISTINCT CASE WHEN folder IS NULL OR TRIM(folder)='' "
            "THEN NULL ELSE folder END) "
            "FROM %s WHERE %s" % (ident, where)
        ).fetchone()
        result["imap_live_null_folder"] = int(row[0] or 0)
        result["imap_live_folders_n"] = int(row[1] or 0)
    if "present_on_server" in cols:
        result["imap_live_not_on_server"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM %s WHERE %s AND present_on_server=0"
                % (ident, where)
            ).fetchone()[0]
        )
    return result


def fingerprint(db, extra_tables=None, extra_columns=None) -> dict:
    """Return the fingerprint dict for ``db``. Does not write the file."""
    path = Path(db)
    exclude_tables, exclude_columns = parse_exclusions(extra_tables, extra_columns)
    if not path.exists():
        raise FpRefuse("refuse: database is missing (%s)" % path.name)
    if not path.is_file() or not _is_sqlite(path):
        raise FpRefuse("refuse: database is not sqlite (%s)" % path.name)
    stat_main = _stat_record(path)
    stat_wal = _stat_record(Path(str(path) + "-wal"))
    stat_shm = _stat_record(Path(str(path) + "-shm"))
    main_sha = _sha256_file(path)
    conn = open_readonly(path)
    try:
        try:
            conn.execute("BEGIN")
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            names = _table_names(conn)
            tables = {}
            skipped = {name.lower() for name in exclude_tables}
            for name in names:
                if name.lower() in skipped:
                    continue
                info = _table_info(conn, name)
                skip = _excluded_columns(exclude_columns, name)
                count, digest = _row_sha256(conn, name, info, skip)
                tables[name] = {"count": count, "sha256": digest}
            imap = _imap_counts(conn, names)
            conn.execute("COMMIT")
        except FpRefuse:
            raise
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise FpRefuse(
                "refuse: database is not readable (%s)" % path.name, code=1
            ) from None
    finally:
        conn.close()
    doc = {
        "db_basename": path.name,
        "journal_mode": journal,
        "main_sha256": main_sha,
        "stat_main": stat_main,
        "stat_wal": stat_wal,
        "stat_shm": stat_shm,
        "exclusions": {
            "tables": list(exclude_tables),
            "columns": list(exclude_columns),
        },
        "tables": tables,
    }
    doc.update(imap)
    return doc


def render_json(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _write_out(path: Path, text: str) -> None:
    if not path.parent.is_dir():
        raise FpRefuse("refuse: --out directory is missing")
    fd, tmp_name = tempfile.mkstemp(
        prefix=".att0-fp-", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise FpRefuse("refuse: cannot write --out (%s)" % path.name) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only SQLite fingerprint as JSON. "
            "Stdout, or --out. Basenames only. "
            "Does not emit folder names or imap_live_by_folder."
        )
    )
    parser.add_argument("db", help="SQLite database path")
    parser.add_argument(
        "--out",
        default=None,
        help="Write the JSON here instead of stdout",
    )
    parser.add_argument(
        "--exclude-table",
        action="append",
        default=None,
        metavar="NAME",
        help="Also skip NAME (default already skips ask_audit and drafts)",
    )
    parser.add_argument(
        "--exclude-column",
        action="append",
        default=None,
        metavar="TABLE.COL",
        help=(
            "Also omit TABLE.COL from that table's row hash "
            "(default already omits messages.has_attachments)"
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    try:
        doc = fingerprint(
            args.db,
            extra_tables=args.exclude_table,
            extra_columns=args.exclude_column,
        )
        text = render_json(doc)
        if args.out:
            out = Path(args.out)
            db = Path(args.db)
            try:
                if out.exists() and db.exists() and out.samefile(db):
                    raise FpRefuse("refuse: --out is the database file")
            except OSError:
                raise FpRefuse("refuse: cannot write --out (%s)" % out.name) from None
            _write_out(out, text)
        else:
            sys.stdout.write(text)
    except FpRefuse as exc:
        sys.stderr.write("%s\n" % exc)
        return exc.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
