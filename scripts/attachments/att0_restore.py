#!/usr/bin/env python3
"""Restore a backup SQLite file over a destination database.

Rollback after an ATT-0 migrate or fill. This helper does not take the
writer flock. It must be run as:

  scripts/with_writer_lock.py --purpose att0-restore -- python3 scripts/attachments/att0_restore.py ...

Refuses basename mailroom.sqlite unless --allow-mailroom-sqlite, then
still calls sor_writer_gate.refuse_if_sor_writer_conflict before opening
the database. Never modifies or deletes the source file.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sor_writer_gate import (  # noqa: E402
    SOR_BASENAME,
    SorWriterRefuse,
    refuse_if_sor_writer_conflict,
)

_WRAPPER = (
    "scripts/with_writer_lock.py --purpose att0-restore -- "
    "python3 scripts/attachments/att0_restore.py ..."
)
_RESTORE_PURPOSE = "att0-restore"
_SQLITE_HEADER = b"SQLite format 3\x00"
_JOURNAL_MODES = ("delete", "truncate", "persist", "memory", "wal", "off")
_TEMP_PREFIX = ".att0-restore-"


class RestoreRefuse(RuntimeError):
    """Safety refuse. Never includes a path or a secret."""


def _ro_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _sidecar(path: Path, suffix: str) -> Path:
    return Path(str(path) + suffix)


def _is_sqlite_file(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == _SQLITE_HEADER
    except OSError:
        return False


def _same_file(src: Path, dest: Path) -> bool:
    try:
        src_res = src.resolve()
        dest_res = dest.resolve()
    except OSError:
        return False
    if src_res == dest_res:
        return True
    try:
        src_stat = os.stat(src_res)
        dest_stat = os.stat(dest_res)
    except OSError:
        return False
    return src_stat.st_ino == dest_stat.st_ino and src_stat.st_dev == dest_stat.st_dev


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _remove_sqlite_family(path: Path) -> None:
    _unlink_quiet(path)
    for suffix in ("-wal", "-shm", "-journal"):
        _unlink_quiet(_sidecar(path, suffix))


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _spawned_by_att0_restore_wrapper(lock_path: Path | None) -> bool:
    """True when the parent process holds the att0-restore writer lock.

    The required wrapper holds the flock before it starts this process.
    That holder is this run, not a second writer. A different purpose or
    a different pid stays a conflict.
    """
    import with_writer_lock as wwl
    from sor_writer_gate import writer_lock_held

    lock = Path(lock_path).expanduser() if lock_path is not None else wwl.default_lock_path()
    if not lock.is_file():
        return False
    info = wwl.read_lock_info(lock)
    if info.purpose != _RESTORE_PURPOSE:
        return False
    if info.pid is None or info.pid != os.getppid():
        return False
    held, _detail = writer_lock_held(lock)
    return bool(held)


def _refuse_writer_conflict(
    path: Path,
    *,
    cmdlines: Any,
    lock_path: Path | None,
    lock_held: bool | None,
) -> None:
    try:
        refuse_if_sor_writer_conflict(
            path,
            cmdlines=cmdlines,
            lock_path=lock_path,
            lock_held=lock_held,
        )
    except SorWriterRefuse:
        if lock_held is not None or not _spawned_by_att0_restore_wrapper(lock_path):
            raise
        refuse_if_sor_writer_conflict(
            path,
            cmdlines=cmdlines,
            lock_path=lock_path,
            lock_held=False,
        )


def _checkpoint_nonempty_wal(dest: Path) -> None:
    wal = _sidecar(dest, "-wal")
    try:
        nonempty = wal.is_file() and wal.stat().st_size > 0
    except OSError:
        nonempty = False
    if not nonempty:
        return
    if not dest.is_file():
        raise RestoreRefuse("refuse: wal checkpoint busy")
    conn = sqlite3.connect(str(dest))
    try:
        try:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "busy" in text or "locked" in text:
                raise RestoreRefuse("refuse: wal checkpoint busy") from None
            raise
    finally:
        conn.close()
    if row is None or int(row[0]) != 0:
        raise RestoreRefuse("refuse: wal checkpoint busy")


def _dest_journal_mode(dest: Path) -> str:
    if not dest.is_file() or not _is_sqlite_file(dest):
        return "wal"
    conn = sqlite3.connect(_ro_uri(dest), uri=True)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    if row is None:
        raise RestoreRefuse("refuse: journal_mode mismatch")
    mode = str(row[0]).lower()
    if mode not in _JOURNAL_MODES:
        raise RestoreRefuse("refuse: journal_mode mismatch")
    return mode


def _apply_journal_mode(conn: sqlite3.Connection, mode: str) -> None:
    row = conn.execute("PRAGMA journal_mode=%s" % mode).fetchone()
    if row is None or str(row[0]).lower() != mode:
        raise RestoreRefuse("refuse: journal_mode mismatch")


def _integrity_ok(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if len(rows) != 1 or str(rows[0][0]).lower() != "ok":
        raise RestoreRefuse("refuse: integrity_check failed")


def _backup(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    source.backup(target)


def restore_database(
    src: str | Path,
    dest: str | Path,
    *,
    allow_mailroom_sqlite: bool = False,
    lock_held: bool | None = None,
    cmdlines: Any = None,
    lock_path: Path | None = None,
) -> dict[str, str]:
    """Replace dest with a backup of src. Opens nothing before the writer gate."""
    source = Path(src).expanduser()
    target = Path(dest).expanduser()
    if target.name == SOR_BASENAME and not allow_mailroom_sqlite:
        raise RestoreRefuse(
            "refuse: basename mailroom.sqlite "
            "(pass --allow-mailroom-sqlite to override)"
        )
    _refuse_writer_conflict(
        target,
        cmdlines=cmdlines,
        lock_path=lock_path,
        lock_held=lock_held,
    )
    parent = target.parent
    if not parent.is_dir():
        raise RestoreRefuse("refuse: dest directory is missing")
    if not source.is_file():
        raise RestoreRefuse("refuse: src is missing")
    if source.name == SOR_BASENAME:
        raise RestoreRefuse("refuse: src basename is mailroom.sqlite")
    if _same_file(source, target):
        raise RestoreRefuse("refuse: src and dest are the same file")
    if not _is_sqlite_file(source):
        raise RestoreRefuse("refuse: src is not a sqlite database")

    mode = _dest_journal_mode(target)

    tmp_path: Path | None = None
    replaced = False
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=_TEMP_PREFIX,
            suffix=".sqlite",
            dir=str(parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        src_conn = sqlite3.connect(_ro_uri(source), uri=True)
        try:
            tmp_conn = sqlite3.connect(str(tmp_path))
            try:
                _backup(src_conn, tmp_conn)
                _apply_journal_mode(tmp_conn, mode)
                if mode == "wal":
                    row = tmp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if row is None or int(row[0]) != 0:
                        raise RestoreRefuse("refuse: wal checkpoint busy")
                _apply_journal_mode(tmp_conn, mode)
                _integrity_ok(tmp_conn)
            finally:
                tmp_conn.close()
        finally:
            src_conn.close()
        for suffix in ("-wal", "-shm", "-journal"):
            _unlink_quiet(_sidecar(tmp_path, suffix))
        _checkpoint_nonempty_wal(target)
        _fsync_file(tmp_path)
        os.replace(str(tmp_path), str(target))
        replaced = True
        for suffix in ("-wal", "-shm"):
            _unlink_quiet(_sidecar(target, suffix))
        _fsync_dir(parent)
    finally:
        if tmp_path is not None and not replaced:
            _remove_sqlite_family(tmp_path)
    return {"src": source.name, "dest": target.name}


def format_report(report: dict[str, str]) -> str:
    return "restored src=%s dest=%s\n" % (report["src"], report["dest"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore a backup SQLite file over a destination database. "
            "This helper does not take the writer flock. "
            "It must be run as the command in the epilog."
        ),
        epilog=(
            "This helper does not take the writer flock. It must be run as:\n%s"
            % _WRAPPER
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--src", required=True, help="Backup SQLite file to copy from.")
    parser.add_argument("--dest", required=True, help="Database file to replace.")
    parser.add_argument(
        "--allow-mailroom-sqlite",
        action="store_true",
        help=(
            "Permit destination basename mailroom.sqlite. "
            "Required when the dest basename is mailroom.sqlite. "
            "The writer gate still applies."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(raw)
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        try:
            return int(code)
        except (TypeError, ValueError):
            return 2
    try:
        report = restore_database(
            args.src,
            args.dest,
            allow_mailroom_sqlite=bool(args.allow_mailroom_sqlite),
        )
    except (RestoreRefuse, SorWriterRefuse) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except Exception as exc:
        sys.stderr.write("error: restore failed (%s)\n" % type(exc).__name__)
        return 1
    sys.stdout.write(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
