#!/usr/bin/env python3
"""Consistent snapshot of a SQLite database into a NEW file.

Opens the source with a ``file:...?mode=ro`` URI and
``PRAGMA query_only=ON``. Copies with ``sqlite3.Connection.backup``
into a temporary file beside the destination, runs ``PRAGMA
quick_check``, fsyncs, then ``os.replace``. A failure before the
replace leaves no destination and no temp file.

Reading the system of record is allowed. This script does not take the
writer lock. The operator decides whether to wrap the run in
``with_writer_lock.py``. Before the copy it calls
``sor_writer_gate.refuse_if_sor_writer_conflict`` on the source, the
same gate ``scripts/attachments/meta_fill.py`` uses. A live
``mailroom.sqlite`` source is refused when that gate reports a
conflict.

Refuses (exit 2) when the destination exists, the destination basename
is ``mailroom.sqlite``, the destination is the same file as the source,
the destination directory is missing, or the source is missing or not
SQLite. Never overwrites. Prints basenames only.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sor_writer_gate import (  # noqa: E402
    SOR_BASENAME,
    SorWriterRefuse,
    refuse_if_sor_writer_conflict,
)

_SQLITE_MAGIC = b"SQLite format 3\x00"


class BackupRefuse(Exception):
    """Backup refuse. The message has no filesystem path."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _ro_uri(path: Path) -> str:
    posix = Path(os.path.abspath(str(path))).as_posix()
    return "file:%s?mode=ro" % quote(posix, safe="/:")


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _same_file(src: Path, dest: Path) -> bool:
    if os.path.abspath(str(src)) == os.path.abspath(str(dest)):
        return True
    if src.exists() and dest.exists():
        try:
            return os.path.samefile(src, dest)
        except OSError:
            return False
    try:
        return Path(src).resolve() == Path(dest).resolve()
    except OSError:
        return False


def _lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _unlink_db(path: Path) -> None:
    _unlink_quiet(path)
    for suffix in ("-wal", "-shm", "-journal"):
        _unlink_quiet(Path(str(path) + suffix))


def _unlink_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        _unlink_quiet(Path(str(path) + suffix))


def _fsync(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _backup_pages(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection) -> None:
    """Copy every page. One step, one read snapshot of the source."""
    src_conn.backup(dst_conn)


def _quick_check(path: Path):
    try:
        conn = sqlite3.connect(_ro_uri(path), uri=True, isolation_level=None)
    except sqlite3.Error:
        raise BackupRefuse("refuse: quick_check failed", code=1) from None
    try:
        try:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute("PRAGMA quick_check").fetchall()
            journal = conn.execute("PRAGMA journal_mode").fetchone()
        except sqlite3.Error:
            raise BackupRefuse("refuse: quick_check failed", code=1) from None
    finally:
        conn.close()
    text = ",".join(str(row[0]) for row in rows if row)
    text = " ".join(text.split())
    if len(text) > 200:
        text = text[:200]
    mode = str(journal[0]) if journal else ""
    return text, mode


def _refuse_paths(src: Path, dest: Path) -> None:
    if dest.name == SOR_BASENAME:
        raise BackupRefuse("refuse: dest basename is %s" % SOR_BASENAME)
    if _same_file(src, dest):
        raise BackupRefuse("refuse: dest is the same file as src")
    if _lexists(dest):
        raise BackupRefuse("refuse: dest already exists (%s)" % dest.name)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(dest) + suffix)
        if _lexists(sidecar):
            raise BackupRefuse(
                "refuse: dest sidecar already exists (%s)" % sidecar.name
            )
    parent = dest.parent
    if parent == Path(""):
        parent = Path(".")
    if not parent.is_dir():
        raise BackupRefuse("refuse: dest directory is missing")
    if not src.exists():
        raise BackupRefuse("refuse: src is missing (%s)" % src.name)
    if not src.is_file() or not _is_sqlite(src):
        raise BackupRefuse("refuse: src is not sqlite (%s)" % src.name)


def backup_database(
    src,
    dest,
    *,
    cmdlines=None,
    lock_path=None,
    lock_held=None,
) -> str:
    """Copy ``src`` into a new file at ``dest``. Return the status line.

    ``cmdlines``, ``lock_path``, and ``lock_held`` are passed to the
    writer gate so tests can inject the probe. The CLI leaves them unset.
    """
    src_path = Path(src)
    dest_path = Path(dest)
    _refuse_paths(src_path, dest_path)
    try:
        refuse_if_sor_writer_conflict(
            src_path,
            cmdlines=cmdlines,
            lock_path=lock_path,
            lock_held=lock_held,
        )
    except SorWriterRefuse:
        raise BackupRefuse(
            "refuse: sor writer conflict on %s" % SOR_BASENAME
        ) from None
    started = time.monotonic()
    tmp_path = None
    src_conn = None
    dst_conn = None
    replaced = False
    journal = ""
    try:
        parent = dest_path.parent
        if parent == Path(""):
            parent = Path(".")
        fd, tmp_name = tempfile.mkstemp(
            prefix=".att0-backup-", suffix=".tmp", dir=str(parent)
        )
        tmp_path = Path(tmp_name)
        os.close(fd)
        try:
            src_conn = sqlite3.connect(
                _ro_uri(src_path), uri=True, isolation_level=None
            )
            src_conn.execute("PRAGMA query_only=ON")
            dst_conn = sqlite3.connect(str(tmp_path))
            _backup_pages(src_conn, dst_conn)
            try:
                dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            dst_conn.commit()
        except sqlite3.Error:
            raise BackupRefuse("refuse: backup failed", code=1) from None
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None
        _unlink_sidecars(tmp_path)
        qc, journal = _quick_check(tmp_path)
        if qc != "ok":
            raise BackupRefuse("refuse: quick_check=%s" % (qc or "failed"), code=1)
        try:
            _fsync(tmp_path)
            os.replace(str(tmp_path), str(dest_path))
        except OSError:
            raise BackupRefuse("refuse: backup failed", code=1) from None
        replaced = True
        tmp_path = None
        try:
            dir_fd = os.open(str(dest_path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        for conn in (dst_conn, src_conn):
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        if tmp_path is not None and not replaced:
            _unlink_db(tmp_path)
    elapsed = time.monotonic() - started
    size = dest_path.stat().st_size
    return (
        "backup_ok src=%s dest=%s seconds=%.1f size=%d quick_check=ok journal_mode=%s"
        % (src_path.name, dest_path.name, elapsed, size, journal)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Consistent SQLite snapshot into a new file. "
            "Reading the system of record is allowed. "
            "The operator decides whether to wrap the run in "
            "with_writer_lock.py. Calls "
            "sor_writer_gate.refuse_if_sor_writer_conflict on the source. "
            "Prints basenames only."
        )
    )
    parser.add_argument("src", help="Source SQLite database")
    parser.add_argument("dest", help="New destination path (must not exist)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    try:
        line = backup_database(args.src, args.dest)
    except BackupRefuse as exc:
        sys.stderr.write("%s\n" % exc)
        return exc.code
    sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
