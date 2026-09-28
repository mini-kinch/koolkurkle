#!/usr/bin/env python3
"""Restore a backup SQLite file over a destination database.

Rollback after an ATT-0 migrate or fill. This helper does not take the
writer flock. It must be run as:

  scripts/with_writer_lock.py --purpose att0-restore -- python3 scripts/attachments/att0_restore.py ...

Refuses basename mailroom.sqlite unless --allow-mailroom-sqlite, then
still calls sor_writer_gate.refuse_if_sor_writer_conflict before opening
the database. Never modifies or deletes the source file.

The temp copy is sealed with PRAGMA journal_mode=DELETE before the
atomic replace. Header bytes 18-19 are then the rollback versions
(1, 1), so a later mode=ro open does not need a -shm file. The next
writer sets WAL (scripts/sqlite_pragmas.py).

The live destination is left uncheckpointed. Its -wal and -shm are
renamed aside before the temp file replaces the main file, and renamed
back if that replace does not happen. A missing or empty source -wal
is opened immutable=1. A non-empty source -wal is hardlinked into a
private directory and opened mode=ro there, so the source gains no
sidecars and uncheckpointed frames are still copied.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

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
_TEMP_PREFIX = ".att0-restore-"
# SQLite header bytes 18 and 19 are the file-format write and read versions.
# 1 = rollback journal, 2 = WAL. A WAL file (2, 2) with -shm deleted cannot
# be opened mode=ro on SQLite 3.51.0 (Apple /usr/bin/python3): that build
# cannot create the shared-memory file on a read-only connection.
_ROLLBACK_FORMAT = (1, 1)
# Source size + source -wal size + this margin must fit in the dest dir.
_FREE_MARGIN = 64 * 1024 * 1024


class RestoreRefuse(RuntimeError):
    """Safety refuse. Never includes a path or a secret."""


def _ro_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _immutable_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?immutable=1"


def _is_sor_basename(path: Path) -> bool:
    """Case-insensitive mailroom.sqlite check (APFS preserves case)."""
    return path.name.casefold() == SOR_BASENAME.casefold()


def _sidecar(path: Path, suffix: str) -> Path:
    return Path(str(path) + suffix)


def _format_versions(path: Path) -> tuple[int, int]:
    """Return SQLite header bytes 18-19 (write version, read version)."""
    try:
        with open(path, "rb") as handle:
            handle.seek(18)
            raw = handle.read(2)
    except OSError:
        raw = b""
    if len(raw) != 2:
        raise RestoreRefuse("refuse: journal_mode mismatch")
    return raw[0], raw[1]


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


def _fsync_fd(fd: int) -> None:
    os.fsync(fd)
    # Darwin's os.fsync does not push to stable storage. F_FULLFSYNC does.
    if sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        _fsync_fd(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        _fsync_fd(fd)
    finally:
        os.close(fd)


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


def _wal_nonempty(path: Path) -> bool:
    wal = _sidecar(path, "-wal")
    try:
        st = wal.stat()
    except FileNotFoundError:
        return False
    except OSError:
        raise RestoreRefuse("refuse: wal unreadable") from None
    return stat.S_ISREG(st.st_mode) and st.st_size > 0


def _require_free_space(source: Path, parent: Path) -> None:
    need = source.stat().st_size + _FREE_MARGIN
    wal = _sidecar(source, "-wal")
    if wal.is_file():
        need += wal.stat().st_size
    if shutil.disk_usage(parent).free < need:
        raise RestoreRefuse("refuse: not enough free space")


def _copy_bytes(src: Path, dest: Path) -> None:
    with open(src, "rb") as inp, open(dest, "wb") as out:
        shutil.copyfileobj(inp, out, length=1024 * 1024)


def _remove_private(private: Path | None) -> None:
    if private is None:
        return
    shutil.rmtree(private, ignore_errors=True)


def _open_source(source: Path) -> tuple[sqlite3.Connection, Path | None]:
    """Open source for backup without creating sidecars beside it.

    Missing or empty -wal: immutable=1. That open does not create -wal/-shm
    and does not see uncheckpointed frames, so it is only used when there
    are none. Non-empty -wal: hardlink the main file into a private directory
    on the same filesystem, copy the -wal bytes beside that link, and open
    the private path mode=ro. immutable=1 is never used in that case.
    """
    if not _wal_nonempty(source):
        return sqlite3.connect(_immutable_uri(source), uri=True), None
    private: Path | None = None
    try:
        private = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=str(source.parent)))
        staged = private / "db.sqlite"
        os.link(str(source), str(staged))
        _copy_bytes(_sidecar(source, "-wal"), _sidecar(staged, "-wal"))
        conn = sqlite3.connect(_ro_uri(staged), uri=True)
    except OSError:
        _remove_private(private)
        raise RestoreRefuse("refuse: cannot stage source wal") from None
    except Exception:
        _remove_private(private)
        raise
    return conn, private


def _preserve_dest_mode_owner(tmp: Path, dest: Path) -> None:
    try:
        st = dest.stat()
        os.chmod(str(tmp), stat.S_IMODE(st.st_mode))
        os.chown(str(tmp), st.st_uid, st.st_gid)
    except PermissionError:
        raise RestoreRefuse("refuse: cannot preserve dest mode") from None


def _aside_path(path: Path) -> Path:
    token = os.urandom(8).hex()
    return path.parent / ("%saside-%s-%s" % (_TEMP_PREFIX, token, path.name))


def _unpark(parked: list[tuple[Path, Path]]) -> None:
    for aside, original in reversed(parked):
        if _lexists(aside):
            os.replace(str(aside), str(original))


def _park_sidecars(dest: Path) -> list[tuple[Path, Path]]:
    """Rename existing dest -wal and -shm aside. The main inode stays."""
    parked: list[tuple[Path, Path]] = []
    try:
        for suffix in ("-wal", "-shm"):
            side = _sidecar(dest, suffix)
            if not _lexists(side):
                continue
            aside = _aside_path(side)
            os.replace(str(side), str(aside))
            parked.append((aside, side))
    except Exception:
        _unpark(parked)
        raise
    return parked


def _drop_parked(parked: list[tuple[Path, Path]]) -> None:
    for aside, _original in parked:
        _unlink_quiet(aside)


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
    if _is_sor_basename(target) and not allow_mailroom_sqlite:
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
    if _is_sor_basename(source):
        raise RestoreRefuse("refuse: src basename is mailroom.sqlite")
    if _same_file(source, target):
        raise RestoreRefuse("refuse: src and dest are the same file")
    if not _is_sqlite_file(source):
        raise RestoreRefuse("refuse: src is not a sqlite database")
    _require_free_space(source, parent)

    tmp_path: Path | None = None
    private: Path | None = None
    src_conn: sqlite3.Connection | None = None
    parked: list[tuple[Path, Path]] = []
    replaced = False
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=_TEMP_PREFIX,
            suffix=".sqlite",
            dir=str(parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        src_conn, private = _open_source(source)
        try:
            tmp_conn = sqlite3.connect(str(tmp_path))
            try:
                _backup(src_conn, tmp_conn)
                # Seal rollback mode before replace. The backup API can copy
                # a WAL header (bytes 18-19 == 2). Leaving that header and
                # then deleting -shm makes mode=ro fail on SQLite 3.51.0.
                # The next writer sets WAL; this file does not.
                _apply_journal_mode(tmp_conn, "delete")
                _integrity_ok(tmp_conn)
            finally:
                tmp_conn.close()
        finally:
            src_conn.close()
            src_conn = None
            _remove_private(private)
            private = None
        if _format_versions(tmp_path) != _ROLLBACK_FORMAT:
            raise RestoreRefuse("refuse: journal_mode mismatch")
        for suffix in ("-wal", "-shm", "-journal"):
            _unlink_quiet(_sidecar(tmp_path, suffix))
        if target.exists():
            _preserve_dest_mode_owner(tmp_path, target)
        _fsync_file(tmp_path)
        _fsync_dir(parent)
        parked = _park_sidecars(target)
        if _wal_nonempty(target):
            raise RestoreRefuse("refuse: dest wal recreated")
        os.replace(str(tmp_path), str(target))
        replaced = True
        _drop_parked(parked)
        parked = []
        for suffix in ("-wal", "-shm"):
            _unlink_quiet(_sidecar(target, suffix))
        _fsync_dir(parent)
    finally:
        if src_conn is not None:
            src_conn.close()
        _remove_private(private)
        if parked and not replaced:
            _unpark(parked)
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
            "Permit a destination basename that matches mailroom.sqlite "
            "in any case. Required when the dest basename matches. "
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
