#!/usr/bin/env python3
"""Read-only fingerprint of a SQLite file.

Does not create a ``-wal`` or a ``-shm`` beside the fingerprinted
database, and does not modify the main-file bytes. Stats for those
paths are taken before the open. ``PRAGMA query_only=ON`` is set on
the connection.

A missing or empty ``-wal`` is opened ``file:...?mode=ro&immutable=1``.
That open does not read uncheckpointed frames (there are none). It is
also the open that works on SQLite 3.51.0: a plain ``mode=ro`` open of
a WAL database with no ``-shm`` fails there, because a read-only
connection cannot create the shared-memory file.

A non-empty ``-wal`` is never opened with ``immutable=1`` (that would
hide committed frames). The main file and the ``-wal`` are copied into
a private directory, and that copy is opened ``mode=ro``. The main file
is a new inode, not a hardlink. SQLite's unix VFS keeps one
``unixInodeInfo``, and the WAL ``-shm`` node on it, per device and
inode for the whole process. A hardlink would join a connection that
already has the source open and rewrite the source ``-shm``. The
private directory is removed after the read. The source directory must
have free space for the main file, the ``-wal``, and a margin. If it
does not, the process exits 2 with ``refuse: not enough free space``.
If the copy cannot be created, the process exits 2 with ``refuse:
cannot stage source wal``. The fingerprinted path is not opened in
place.

Every ordinary table is reported with a row count and a sha256 of its
quoted rows. The list is ``sqlite_master`` rows with ``type='table'``,
names that do not start with ``sqlite_``, and ``sql`` that is not a
``CREATE VIRTUAL TABLE`` statement. ``sql IS NULL`` stays included, so
shadow tables are hashed. FTS5 and vec0 virtual tables are skipped and
listed in ``skipped_virtual_tables``. Columns are in ``cid`` order.
Rows are ordered by the primary key, or by ``rowid`` when the table
has none. That is the same inclusion and ordering as the shell recipe
in ``docs/attachments/ATT-0-fingerprint.md``. The digest does not have
to match the shell bytes.

A SQLite error while reading one table exits 1 with ``ERROR`` and that
table name on stderr. The tool does not write a count or a hash for
it, and it does not hash empty input after a failed read.

``--exclude-table`` (repeatable) adds to the default ``ask_audit`` and
``drafts`` exclusions. Those two tables are written during serve.
``--exclude-column TABLE.COL`` (repeatable) adds to the default
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
import shutil
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

DEFAULT_EXCLUDE_TABLES = ("ask_audit", "drafts")
DEFAULT_EXCLUDE_COLUMNS = ("messages.has_attachments",)
_SQLITE_MAGIC = b"SQLite format 3\x00"
_IMAP_SOURCE = "imap-live"
# Shadow tables stay in the hash set. Virtual tables do not.
_ORDINARY_SQL = "(sql IS NULL OR sql NOT LIKE 'CREATE VIRTUAL TABLE%')"
_VIRTUAL_SQL = "sql LIKE 'CREATE VIRTUAL TABLE%'"


class FpRefuse(Exception):
    """Fingerprint refuse. The message has no filesystem path."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _file_uri(path: Path, query: str) -> str:
    posix = Path(os.path.abspath(str(path))).as_posix()
    return "file:%s?%s" % (quote(posix, safe="/:"), query)


def _ro_uri(path: Path) -> str:
    """Plain ``mode=ro``. Only for a private stage that already has a ``-wal``."""
    return _file_uri(path, "mode=ro")


def _immutable_uri(path: Path) -> str:
    """``mode=ro`` plus ``immutable=1``. For a missing or empty ``-wal``."""
    return _file_uri(path, "mode=ro&immutable=1")


def _wal_path(path: Path) -> Path:
    return Path(str(path) + "-wal")


def _wal_nonempty(path: Path) -> bool:
    wal = _wal_path(path)
    try:
        info = wal.stat()
    except FileNotFoundError:
        return False
    except OSError:
        raise FpRefuse("refuse: wal unreadable (%s)" % wal.name, code=2) from None
    return stat.S_ISREG(info.st_mode) and info.st_size > 0


def _copy_bytes(src: Path, dest: Path) -> None:
    with open(src, "rb") as inp, open(dest, "wb") as out:
        shutil.copyfileobj(inp, out, length=1024 * 1024)


# The private stage is a byte copy of the main file and the ``-wal``.
# SQLite then creates a ``-shm`` beside that copy. The margin covers
# that file and filesystem overhead. A hardlink would not write the
# main-file bytes; this copy does.
_STAGE_FREE_MARGIN = 64 * 1024 * 1024


def _stage_space_needed(path: Path) -> int:
    """Bytes to stage a non-empty ``-wal``: main, wal, and margin."""
    return (
        path.stat().st_size
        + _wal_path(path).stat().st_size
        + _STAGE_FREE_MARGIN
    )


def _require_stage_space(path: Path) -> None:
    """Refuse when the source directory cannot hold the private copy."""
    try:
        need = _stage_space_needed(path)
        free = shutil.disk_usage(path.parent).free
    except OSError:
        raise FpRefuse("refuse: cannot stage source wal", code=2) from None
    if free < need:
        raise FpRefuse("refuse: not enough free space", code=2)


def _remove_private(private: Path | None) -> None:
    if private is not None:
        shutil.rmtree(str(private), ignore_errors=True)


def release_readonly(conn: sqlite3.Connection | None, private: Path | None) -> None:
    """Close ``conn`` and remove a staged private directory, if any."""
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _remove_private(private)


def _apply_query_only(conn: sqlite3.Connection, label: str) -> None:
    try:
        conn.execute("PRAGMA query_only=ON")
        flag = conn.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error:
        raise FpRefuse(
            "refuse: database is not readable (%s)" % label, code=1
        ) from None
    if flag is None or int(flag[0]) != 1:
        raise FpRefuse("refuse: query_only did not stick", code=1)


def connect_without_sidecars(
    path: Path, temp_prefix: str = ".att0-fp-"
) -> tuple[sqlite3.Connection, Path | None]:
    """Open ``path`` for reading without creating sidecars beside it.

    Missing or empty ``-wal``: ``mode=ro`` and ``immutable=1``. Non-empty
    ``-wal``: copy the main file and the ``-wal`` into a private
    directory and open that copy ``mode=ro``. The main file is not a
    hardlink. SQLite's unix VFS keys ``unixInodeInfo`` and its
    ``pShmNode`` by device and inode inside this process, so a hardlink
    would read and write the source ``-shm`` whenever another connection
    here already has the source open. ``immutable=1`` is never used for
    a non-empty ``-wal``. The source directory must have room for the
    main file, the ``-wal``, and :data:`_STAGE_FREE_MARGIN`.

    Returns ``(connection, private_dir)``. ``private_dir`` is None when
    no stage was needed. The caller must :func:`release_readonly`.
    """
    label = path.name
    if not _wal_nonempty(path):
        try:
            conn = sqlite3.connect(
                _immutable_uri(path), uri=True, isolation_level=None
            )
        except sqlite3.Error:
            raise FpRefuse(
                "refuse: database is not readable (%s)" % label, code=1
            ) from None
        try:
            _apply_query_only(conn, label)
        except FpRefuse:
            conn.close()
            raise
        return conn, None
    private: Path | None = None
    conn: sqlite3.Connection | None = None
    try:
        _require_stage_space(path)
        private = Path(tempfile.mkdtemp(prefix=temp_prefix, dir=str(path.parent)))
        staged = private / "db.sqlite"
        _copy_bytes(path, staged)
        _copy_bytes(_wal_path(path), Path(str(staged) + "-wal"))
        conn = sqlite3.connect(_ro_uri(staged), uri=True, isolation_level=None)
        _apply_query_only(conn, label)
    except FpRefuse:
        release_readonly(conn, private)
        raise
    except sqlite3.Error:
        release_readonly(conn, private)
        raise FpRefuse(
            "refuse: database is not readable (%s)" % label, code=1
        ) from None
    except OSError:
        release_readonly(conn, private)
        raise FpRefuse("refuse: cannot stage source wal", code=2) from None
    return conn, private


def _format_versions(path: Path) -> tuple[int, int] | None:
    """SQLite header bytes 18-19. 1 is rollback, 2 is WAL."""
    try:
        with path.open("rb") as handle:
            handle.seek(18)
            raw = handle.read(2)
    except OSError:
        return None
    if len(raw) != 2:
        return None
    return raw[0], raw[1]


def _journal_mode(path: Path, conn: sqlite3.Connection) -> str:
    """File journal mode. Header bytes win over an immutable connection.

    ``immutable=1`` reports ``delete`` even when the file is WAL. Bytes
    18 and 19 equal to 2 mean the file is WAL. Other persistent files
    use ``PRAGMA journal_mode`` (``delete`` for a rollback file).
    """
    if _format_versions(path) == (2, 2):
        return "wal"
    row = conn.execute("PRAGMA journal_mode").fetchone()
    if row is None or row[0] is None:
        raise sqlite3.Error("journal_mode")
    return str(row[0])


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


_STAGED: dict[int, Path] = {}


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only with ``query_only`` on.

    Uses the same no-sidecar rule as :func:`connect_without_sidecars`.
    A non-empty ``-wal`` stages a private directory; close that
    connection with :func:`close_readonly` so the directory is removed.
    """
    conn, private = connect_without_sidecars(path)
    if private is not None:
        _STAGED[id(conn)] = private
    return conn


def close_readonly(conn: sqlite3.Connection) -> None:
    """Close a connection from :func:`open_readonly` and drop its stage."""
    private = _STAGED.pop(id(conn), None)
    release_readonly(conn, private)


def _master_names(conn: sqlite3.Connection, sql_pred: str) -> list[str]:
    query = (
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
        "AND " + sql_pred + " ORDER BY name"
    )
    rows = conn.execute(query).fetchall()
    return [row[0] for row in rows]


def _hash_table(conn: sqlite3.Connection, table: str, exclude_columns: tuple):
    """Return ``(count, sha256)``. A SQLite error is ``ERROR <table>``.

    The hash is computed only after the reads succeed. A failed read
    does not hash empty input.
    """
    try:
        info = _table_info(conn, table)
        skip = _excluded_columns(exclude_columns, table)
        return _row_sha256(conn, table, info, skip)
    except sqlite3.Error:
        raise FpRefuse("ERROR %s" % table, code=1) from None


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
    stat_wal = _stat_record(_wal_path(path))
    stat_shm = _stat_record(Path(str(path) + "-shm"))
    main_sha = _sha256_file(path)
    conn, private = connect_without_sidecars(path)
    try:
        try:
            conn.execute("BEGIN")
            journal = _journal_mode(path, conn)
            names = _master_names(conn, _ORDINARY_SQL)
            virtual = _master_names(conn, _VIRTUAL_SQL)
            tables = {}
            skipped = {name.lower() for name in exclude_tables}
            for name in names:
                if name.lower() in skipped:
                    continue
                count, digest = _hash_table(conn, name, exclude_columns)
                tables[name] = {"count": count, "sha256": digest}
            try:
                imap = _imap_counts(conn, names)
            except sqlite3.Error:
                label = "messages"
                for name in names:
                    if name.lower() == "messages":
                        label = name
                        break
                raise FpRefuse("ERROR %s" % label, code=1) from None
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
        release_readonly(conn, private)
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
        "skipped_virtual_tables": virtual,
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
