#!/usr/bin/env python3
"""Read-only post-restore check for the mail archive.

Opens ``<archive>/mailroom.sqlite`` with a ``mode=ro`` URI and
``PRAGMA query_only``. Never takes ``flock`` (lock state comes from
``/proc/locks``, or from ``lsof`` only when that file cannot be read).
Never calls Keychain. Never opens the network.

Archive root: ``--archive``, else ``ARCHIVE_ROOT``, else ``MAILARCHIVE``.
There is no default path.

Stdout is one fact per line. Exit 0 when every check is OK. Other
exits are bits, OR-ed when more than one class fails:

  1  usage (archive root or --since)
  2  sqlite missing, unreadable, or counts unavailable
  4  last_daily_rag_ok missing or not newer than --since
  8  write.lock or daily.lock held, or the probe could not run
  16 meta_fill, with_writer_lock, or daily process running, or ps failed

``G`` is the current count of messages with ``present_on_server`` 0.
Urgent texts are ``notify_log`` rows since ``--since`` whose
``message_id`` does not start with ``bills-`` (the daily digest key).
Bill rows use a timestamp column when one exists, otherwise the
ingested time of the linked message. ``--since`` is inclusive for
those counts. The daily stamp must be strictly newer than ``--since``.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DB = 2
EXIT_STAMP = 4
EXIT_LOCK = 8
EXIT_PROCESS = 16

STAMP_NAME = "last_daily_rag_ok"
SOR_BASENAME = "mailroom.sqlite"
WRITE_LOCK_NAME = "mailroom.write.lock"
DAILY_LOCK_NAME = "mailroom.daily.lock"
BILLS_KEY_PREFIX = "bills-"
BILL_TIME_COLUMNS = (
    "created_at",
    "inserted_at",
    "added_at",
    "ts",
    "ingested_at",
)
PROCESS_NEEDLES = (
    ("meta_fill", ("meta_fill.py",)),
    ("with_writer_lock", ("with_writer_lock.py",)),
    ("daily_process", ("mailroom_daily.py", "run_mailroom_daily.sh")),
)
_TABLES = frozenset({"messages", "notify_log", "bills"})


class CheckError(RuntimeError):
    """Check failure. The message never includes a path or a secret."""


def parse_ts(raw: object) -> datetime | None:
    """Parse an ISO-8601 timestamp. Naive values are UTC."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def archive_root_from(argv_value: str | None) -> str | None:
    if argv_value is not None and str(argv_value).strip():
        return str(argv_value).strip()
    for key in ("ARCHIVE_ROOT", "MAILARCHIVE"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return None


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open sqlite read-only. Does not create the file."""
    if not path.is_file():
        raise CheckError("sqlite missing or unreadable")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        raise CheckError("sqlite missing or unreadable") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in _TABLES:
        raise CheckError("sqlite missing or unreadable")
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if found is None:
        return set()
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    return {str(row[1]) for row in rows}


def _count_since(values: list[object], since: datetime) -> int:
    total = 0
    for raw in values:
        parsed = parse_ts(raw)
        if parsed is not None and parsed >= since:
            total += 1
    return total


def gone_count(conn: sqlite3.Connection) -> int:
    columns = table_columns(conn, "messages")
    if "present_on_server" not in columns:
        raise CheckError("sqlite missing or unreadable")
    row = conn.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE present_on_server = 0 OR present_on_server = '0'"
    ).fetchone()
    return int(row[0])


def messages_inserted_since(conn: sqlite3.Connection, since: datetime) -> int:
    columns = table_columns(conn, "messages")
    if "ingested_at" not in columns:
        raise CheckError("sqlite missing or unreadable")
    rows = conn.execute("SELECT ingested_at FROM messages").fetchall()
    return _count_since([row[0] for row in rows], since)


def urgent_texts_since(conn: sqlite3.Connection, since: datetime) -> int:
    columns = table_columns(conn, "notify_log")
    if not columns:
        return 0
    if "ts" not in columns or "message_id" not in columns:
        raise CheckError("sqlite missing or unreadable")
    rows = conn.execute("SELECT ts, message_id FROM notify_log").fetchall()
    kept: list[object] = []
    for row in rows:
        message_id = "" if row[1] is None else str(row[1])
        if message_id.startswith(BILLS_KEY_PREFIX):
            continue
        kept.append(row[0])
    return _count_since(kept, since)


def bills_added_since(conn: sqlite3.Connection, since: datetime) -> int:
    columns = table_columns(conn, "bills")
    if not columns:
        return 0
    for name in BILL_TIME_COLUMNS:
        if name in columns:
            rows = conn.execute("SELECT %s FROM bills" % name).fetchall()
            return _count_since([row[0] for row in rows], since)
    if "message_id" not in columns:
        raise CheckError("sqlite missing or unreadable")
    message_columns = table_columns(conn, "messages")
    if "id" not in message_columns or "ingested_at" not in message_columns:
        raise CheckError("sqlite missing or unreadable")
    rows = conn.execute(
        "SELECT m.ingested_at FROM bills b "
        "LEFT JOIN messages m ON m.id = b.message_id"
    ).fetchall()
    return _count_since([row[0] for row in rows], since)


def read_stamp(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CheckError("last_daily_rag_ok unreadable") from exc
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def stamp_is_newer(value: str | None, since: datetime) -> bool:
    if value is None:
        return False
    parsed = parse_ts(value)
    if parsed is None:
        return False
    return parsed > since


def _inode_token(path: Path) -> str:
    stat = path.stat()
    return "%02x:%02x:%d" % (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino)


def lock_held_in_proc(path: Path, proc_locks: str) -> bool:
    token = _inode_token(path)
    for line in proc_locks.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        if parts[1] in {"FLOCK", "POSIX", "OFDLCK"} and parts[5] == token:
            return True
    return False


def lock_state(path: Path) -> str:
    """Return free, held, or unavailable. Does not take a lock."""
    if not path.exists():
        return "free"
    try:
        proc_locks = Path("/proc/locks").read_text(encoding="utf-8", errors="replace")
    except OSError:
        proc_locks = None
    if proc_locks is not None:
        try:
            return "held" if lock_held_in_proc(path, proc_locks) else "free"
        except OSError:
            return "unavailable"
    return _lsof_state(path)


def _lsof_state(path: Path) -> str:
    try:
        proc = subprocess.run(
            ["lsof", "-F", "p", "--", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unavailable"
    if proc.returncode == 0 and (proc.stdout or "").strip():
        return "held"
    if proc.returncode == 1:
        return "free"
    return "unavailable"


def read_process_table() -> str | None:
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "args="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


def classify_processes(ps_text: str) -> dict[str, str]:
    found = {name: False for name, _needles in PROCESS_NEEDLES}
    for line in ps_text.splitlines():
        for name, needles in PROCESS_NEEDLES:
            if any(needle in line for needle in needles):
                found[name] = True
    return {name: ("running" if flag else "absent") for name, flag in found.items()}


def process_states() -> dict[str, str]:
    text = read_process_table()
    if text is None:
        return {name: "unavailable" for name, _needles in PROCESS_NEEDLES}
    return classify_processes(text)


def _db_counts(path: Path, since: datetime) -> tuple[str, str, str, str]:
    conn = open_readonly(path)
    try:
        gone = str(gone_count(conn))
        inserted = str(messages_inserted_since(conn, since))
        urgent = str(urgent_texts_since(conn, since))
        bills = str(bills_added_since(conn, since))
    finally:
        conn.close()
    return gone, inserted, urgent, bills


def build_lines(
    *,
    stamp_value: str,
    stamp_newer: bool,
    gone: str,
    messages_inserted: str,
    urgent_texts: str,
    bills_added: str,
    write_lock: str,
    daily_lock: str,
    processes: dict[str, str],
) -> list[str]:
    return [
        "last_daily_rag_ok=%s newer_than_since=%s"
        % (stamp_value, "yes" if stamp_newer else "no"),
        "G=%s" % gone,
        "messages_inserted_since=%s" % messages_inserted,
        "urgent_texts_sent_since=%s" % urgent_texts,
        "bills_added_since=%s" % bills_added,
        "write.lock=%s" % write_lock,
        "daily.lock=%s" % daily_lock,
        "meta_fill=%s" % processes["meta_fill"],
        "with_writer_lock=%s" % processes["with_writer_lock"],
        "daily_process=%s" % processes["daily_process"],
    ]


def _failure_bits(
    *,
    db_ok: bool,
    stamp_newer: bool,
    write_lock: str,
    daily_lock: str,
    processes: dict[str, str],
) -> int:
    bits = EXIT_OK
    if not db_ok:
        bits |= EXIT_DB
    if not stamp_newer:
        bits |= EXIT_STAMP
    if write_lock != "free" or daily_lock != "free":
        bits |= EXIT_LOCK
    if any(state != "absent" for state in processes.values()):
        bits |= EXIT_PROCESS
    return bits


def evaluate(archive: Path, since: datetime) -> tuple[list[str], int]:
    stamp_path = archive / "logs" / STAMP_NAME
    try:
        stamp_raw = read_stamp(stamp_path)
    except CheckError:
        stamp_raw = None
    stamp_value = stamp_raw if stamp_raw is not None else "missing"
    newer = stamp_is_newer(stamp_raw, since)
    write_lock = lock_state(archive / WRITE_LOCK_NAME)
    daily_lock = lock_state(archive / DAILY_LOCK_NAME)
    processes = process_states()
    db_path = archive / SOR_BASENAME
    try:
        gone, inserted, urgent, bills = _db_counts(db_path, since)
        db_ok = True
    except CheckError:
        gone = inserted = urgent = bills = "unavailable"
        db_ok = False
    except sqlite3.Error:
        gone = inserted = urgent = bills = "unavailable"
        db_ok = False
    lines = build_lines(
        stamp_value=stamp_value,
        stamp_newer=newer,
        gone=gone,
        messages_inserted=inserted,
        urgent_texts=urgent,
        bills_added=bills,
        write_lock=write_lock,
        daily_lock=daily_lock,
        processes=processes,
    )
    bits = _failure_bits(
        db_ok=db_ok,
        stamp_newer=newer,
        write_lock=write_lock,
        daily_lock=daily_lock,
        processes=processes,
    )
    return lines, bits


def _note_failures(bits: int) -> None:
    if bits & EXIT_DB:
        sys.stderr.write("error: sqlite missing or unreadable\n")
    if bits & EXIT_STAMP:
        sys.stderr.write("error: last_daily_rag_ok missing or not newer than --since\n")
    if bits & EXIT_LOCK:
        sys.stderr.write("error: write.lock or daily.lock held or unprobed\n")
    if bits & EXIT_PROCESS:
        sys.stderr.write(
            "error: meta_fill, with_writer_lock, or daily process running or unprobed\n"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only post-restore check. "
            "Archive root is --archive, ARCHIVE_ROOT, or MAILARCHIVE."
        )
    )
    parser.add_argument(
        "--archive",
        default=None,
        help="Archive root. No default path.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO-8601 timestamp. Naive values are UTC. Counts are inclusive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = EXIT_OK if exc.code in (0, None) else EXIT_USAGE
        return code
    root = archive_root_from(args.archive)
    if root is None:
        sys.stderr.write("error: archive root required (--archive or ARCHIVE_ROOT)\n")
        return EXIT_USAGE
    if not args.since:
        sys.stderr.write("error: --since timestamp required\n")
        return EXIT_USAGE
    since = parse_ts(args.since)
    if since is None:
        sys.stderr.write("error: --since is not a timestamp\n")
        return EXIT_USAGE
    archive = Path(root).expanduser()
    if not archive.is_dir():
        sys.stderr.write("error: archive root is not a directory\n")
        return EXIT_USAGE
    lines, bits = evaluate(archive, since)
    sys.stdout.write("\n".join(lines) + "\n")
    if bits != EXIT_OK:
        _note_failures(bits)
    return bits


if __name__ == "__main__":
    sys.exit(main())
