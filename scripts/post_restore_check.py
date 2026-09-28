#!/usr/bin/env python3
"""Read-only post-restore check for the mail archive.

Opens ``<archive>/mailroom.sqlite`` with a ``mode=ro`` URI and
``PRAGMA query_only``. Does not take a lock. Does not call Keychain.
Does not open the network.

Archive root: ``--archive``, else ``ARCHIVE_ROOT``, else ``MAILARCHIVE``.
There is no default path.

Each lock file is its own ``lsof -t -- FILE`` with stderr captured.
An all-digit stdout line means held. Exit 1 with empty stdout and
empty stderr means free. Any other lsof result is lsof-error (fail
closed). A missing write.lock is a failure. A missing daily.lock is
not a holder.

Each writer pattern is its own ``pgrep -f`` with stderr captured.
Exit 0 with a pid other than this process or its parent means
present. Exit 1 with empty output means absent. Any other pgrep
result is an error (fail closed). Return codes are branched
explicitly. A non-zero status other than 1 is never treated as absent.

Stdout is one line per check, then ``POST-RESTORE PASS`` or
``POST-RESTORE FAIL <checks>``. Exit status (also in ``--help``):

  0  every check passed
  1  usage (archive root or --since), before the checks run
  2  sqlite missing, unreadable, or counts unavailable
  3  last_daily_rag_ok missing or not newer than --since
  4  write.lock missing, held, or lsof-error
  5  daily.lock held or lsof-error
  6  a writer is present, or pgrep errored

If several of 2-6 fail, the status is the earliest of those classes.
The FAIL line still names every failed check, in that same order.

``G`` is the current count of messages with ``present_on_server`` 0.
Urgent texts are ``notify_log`` rows since ``--since`` whose
``message_id`` does not start with ``bills-``. Bill rows use a
timestamp column when one exists, otherwise the linked message's
``ingested_at``. ``--since`` is inclusive for those counts. The daily
stamp must be strictly newer than ``--since``.

``send_urgent_texts`` delivers one urgent text per ``message_id``.
logs/notify_log.sqlite is the only per-message urgent ledger, and every future per-message urgent sender must go through send_urgent_texts.
The helper is at-least-once if the process dies between the send returning and COMMIT.
It reads ``notify_log`` under ``BEGIN IMMEDIATE``,
calls the sender, and inserts the id only after that call returns.
The first time that file is created, every urgent id that already
exists is inserted with result ``baseline`` in that same transaction
and is not sent. Each later call sends at most URGENT_SEND_CAP new
ids. Ids over the cap stay unsent and unrecorded. A locked ledger
defers that id and does not send it. The read-only report sends only
when a sender is passed and the check result is 0. It never deletes
ledger rows and never writes ``mailroom.sqlite``.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DB = 2
EXIT_STAMP = 3
EXIT_WRITE_LOCK = 4
EXIT_DAILY_LOCK = 5
EXIT_PROCESS = 6

STAMP_NAME = "last_daily_rag_ok"
SOR_BASENAME = "mailroom.sqlite"
WRITE_LOCK_NAME = "mailroom.write.lock"
DAILY_LOCK_NAME = "mailroom.daily.lock"
BILLS_KEY_PREFIX = "bills-"
URGENT_TEXT_CHANNEL = "imessage"
URGENT_LEDGER_NAME = "notify_log.sqlite"
URGENT_SEND_CAP = 20
BASELINE_RESULT = "baseline"
_LEDGER_TIMEOUT_S = 90.0
BILL_TIME_COLUMNS = (
    "created_at",
    "inserted_at",
    "added_at",
    "ts",
    "ingested_at",
)
# Check name, then the pgrep -f pattern. curl is an anchored argv
# expression. The other patterns are literal process fragments.
WRITER_CHECKS = (
    ("mailroom_daily", "mailroom_daily"),
    ("imap_newmail", "imap_newmail"),
    ("imap_tombstone", "imap_tombstone"),
    ("imap_fetch_bodies", "imap_fetch_bodies"),
    ("notify_bills", "notify_bills"),
    ("rem-legacy", "rem-legacy"),
    ("meta_fill", "meta_fill"),
    ("migrate_att0", "migrate_att0"),
    ("embed_backfill", "embed_backfill"),
    ("embed_merge_shards", "embed_merge_shards"),
    ("embed_sidecar_apply", "embed_sidecar_apply"),
    ("post_rem_embed_batch", "post_rem_embed_batch"),
    ("with_writer_lock", "with_writer_lock"),
    ("security_find_generic", "security find-generic"),
    ("phaseP_", "phaseP_"),
    ("curl", "^/usr/bin/curl( |$)"),
)
_TABLES = frozenset({"messages", "notify_log", "bills"})

HELP_EPILOG = """
exit status:
  0  POST-RESTORE PASS
  1  usage: archive root or --since (no check lines)
  2  sqlite missing, unreadable, or counts unavailable
  3  last_daily_rag_ok missing or not newer than --since
  4  write.lock missing, held, or lsof-error
  5  daily.lock held or lsof-error (a missing daily.lock is OK)
  6  a writer process is present, or pgrep returned an error

If several of 2-6 fail, the status is the earliest class in that list.
The last stdout line is POST-RESTORE PASS, or POST-RESTORE FAIL plus
every failed check name in the same order.

Locks, one file per invocation, stderr captured:
  lsof -t -- FILE
  any all-digit stdout line -> held (bad)
  exit 1 and empty stdout and empty stderr -> free
  anything else -> lsof-error (bad, fail closed)
  missing write.lock -> bad
  missing daily.lock -> ok

Processes, one pattern per invocation, stderr captured:
  pgrep -f PATTERN
  exit 0 with a pid other than this process or its parent -> present (bad)
  exit 0 listing only this process and/or its parent -> absent
  exit 1 and empty stdout and empty stderr -> absent
  any other pgrep result -> error (bad, fail closed)
  curl PATTERN is ^/usr/bin/curl( |$)
  security_find_generic PATTERN is: security find-generic
""".strip()


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


def urgent_ledger_path(archive: Path) -> Path:
    """Durable ``notify_log`` beside the archive database.

    Restore replaces ``mailroom.sqlite``. This file is not that database,
    so a restore does not rewind ids that were already texted.
    """
    return Path(archive) / "logs" / URGENT_LEDGER_NAME


def urgent_message_ids(conn: sqlite3.Connection) -> list[str]:
    """Mail ids with ``urgent`` set. Order is by id."""
    columns = table_columns(conn, "messages")
    if "id" not in columns or "urgent" not in columns:
        return []
    rows = conn.execute(
        "SELECT id FROM messages WHERE urgent = 1 OR urgent = '1' ORDER BY id"
    ).fetchall()
    found: list[str] = []
    for row in rows:
        if row[0] is None:
            continue
        found.append(str(row[0]))
    return found


def _ledger_file(ledger_path: Path | str) -> Path:
    path = Path(ledger_path)
    if path.name.casefold() == SOR_BASENAME.casefold():
        raise CheckError("urgent texts do not write the archive sqlite")
    return path


def _ensure_notify_log(conn: sqlite3.Connection) -> None:
    """Create ``notify_log`` if it is missing. Never deletes rows."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notify_log (
          ts TEXT NOT NULL,
          message_id TEXT NOT NULL,
          channel TEXT NOT NULL,
          result TEXT,
          UNIQUE(message_id, channel)
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_notify_log_message_id_channel ON notify_log(message_id, channel)"
    )


class _SendReport(int):
    """Sent count. ``overflow`` and ``deferred`` ride along."""

    def __new__(cls, sent, overflow=0, deferred=()):
        obj = int.__new__(cls, sent)
        obj.overflow = overflow
        obj.deferred = tuple(deferred)
        return obj


def _connect_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=_LEDGER_TIMEOUT_S,
        isolation_level=None,
    )
    try:
        conn.execute("PRAGMA busy_timeout=%d" % int(_LEDGER_TIMEOUT_S * 1000))
        _ensure_notify_log(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _discard_sqlite(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        extra = path if suffix == "" else Path(str(path) + suffix)
        try:
            extra.unlink()
        except OSError:
            continue


def _unique_ids(message_ids) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in message_ids:
        if raw is None:
            continue
        message_id = str(raw)
        if message_id in seen:
            continue
        seen.add(message_id)
        unique.append(message_id)
    return unique


def _publish_baseline_if_absent(ledger_path: Path | str, message_ids) -> bool:
    """Create the ledger and seed baseline rows, or do nothing.

    The destination appears only after the baseline transaction commits.
    A crash before that publish leaves no ledger file to burst from.
    A later open of an existing file does not seed again.
    """
    path = _ledger_file(ledger_path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        ".%s.%d.%d.creating" % (path.name, os.getpid(), time.monotonic_ns())
    )
    _discard_sqlite(tmp)
    conn = sqlite3.connect(
        str(tmp),
        timeout=_LEDGER_TIMEOUT_S,
        isolation_level=None,
    )
    try:
        conn.execute("PRAGMA busy_timeout=%d" % int(_LEDGER_TIMEOUT_S * 1000))
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_notify_log(conn)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO notify_log(ts, message_id, channel, result) "
                "VALUES (?, ?, ?, ?)",
                [
                    (now, message_id, URGENT_TEXT_CHANNEL, BASELINE_RESULT)
                    for message_id in _unique_ids(message_ids)
                ],
            )
            conn.execute("COMMIT")
        except Exception:
            _rollback(conn)
            raise
    except Exception:
        conn.close()
        _discard_sqlite(tmp)
        raise
    conn.close()
    try:
        os.link(tmp, path)
    except FileExistsError:
        _discard_sqlite(tmp)
        return False
    except OSError:
        _discard_sqlite(tmp)
        raise
    _discard_sqlite(tmp)
    return True


def _recorded_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    conn = _connect_ledger(path)
    try:
        rows = conn.execute(
            "SELECT message_id FROM notify_log WHERE channel = ?",
            (URGENT_TEXT_CHANNEL,),
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows if row[0] is not None}


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _send_one(path: Path, message_id: str, send) -> bool:
    """Send ``message_id`` once. Return True only when this call sent it.

    The write lock is held across the sender call so a second run cannot
    pass the ledger check first. The row is inserted only after ``send``
    returns. A raised ``send`` rolls the transaction back and leaves no row.
    The helper is at-least-once if the process dies between the send
    returning and COMMIT.
    """
    conn = _connect_ledger(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT 1 FROM notify_log WHERE message_id = ? AND channel = ?",
                (message_id, URGENT_TEXT_CHANNEL),
            ).fetchone()
            if row is not None:
                conn.execute("COMMIT")
                return False
            send(message_id)
            conn.execute(
                "INSERT INTO notify_log(ts, message_id, channel, result) "
                "VALUES (?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    message_id,
                    URGENT_TEXT_CHANNEL,
                    "ok",
                ),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            _rollback(conn)
            raise
    finally:
        conn.close()


def send_urgent_texts(ledger_path: Path | str, message_ids, send) -> _SendReport:
    """Send each ``message_id`` at most once. Return how many this call sent.

    ``send(message_id)`` must raise when the text is not confirmed. The
    same id later in ``message_ids``, or in a later call, does not send
    again. Failed calls leave the id unrecorded so a retry can send it.
    At most ``URGENT_SEND_CAP`` ids are sent. The rest stay unrecorded.
    ``sqlite3.OperationalError`` defers that id and does not send it.
    The helper is at-least-once if the process dies between the send
    returning and COMMIT.
    """
    path = _ledger_file(ledger_path)
    recorded = _recorded_ids(path)
    pending = [message_id for message_id in _unique_ids(message_ids) if message_id not in recorded]
    chosen = pending[:URGENT_SEND_CAP]
    overflow = len(pending) - len(chosen)
    sent = 0
    deferred: list[str] = []
    for message_id in chosen:
        try:
            did_send = _send_one(path, message_id, send)
        except sqlite3.OperationalError:
            deferred.append(message_id)
            continue
        if did_send:
            sent += 1
    if overflow:
        sys.stdout.write("urgent_texts_overflow=%d\n" % overflow)
    for message_id in deferred:
        sys.stdout.write("urgent_text_deferred=%s\n" % message_id)
    return _SendReport(sent, overflow, deferred)


def rescan_urgent_texts(ledger_path: Path | str, message_ids, send) -> int:
    """Post-restore re-scan. Does not delete or replace ledger rows."""
    return send_urgent_texts(ledger_path, message_ids, send)


def _retain_confirmed(path: Path, message_ids: list[str]) -> None:
    """Copy ids already confirmed in the archive log. Does not send.

    ``INSERT OR IGNORE`` keeps an existing row, including its timestamp.
    """
    if not message_ids:
        return
    conn = _connect_ledger(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for message_id in message_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO notify_log"
                    "(ts, message_id, channel, result) VALUES (?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        message_id,
                        URGENT_TEXT_CHANNEL,
                        "ok",
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            _rollback(conn)
            raise
    finally:
        conn.close()


def deliver_archive_urgent_texts(archive: Path, send) -> _SendReport:
    """Text urgent mail ids from the archive once each.

    Message ids come from ``messages.id`` where ``urgent`` is set. The
    first time ``logs/notify_log.sqlite`` is created, those ids are
    stored with result ``baseline`` and nothing is sent. Later calls
    send new ids through ``send_urgent_texts``, at most
    ``URGENT_SEND_CAP`` per call. An id already in the archive
    ``notify_log`` is pinned and is not texted. Nothing here is written
    to ``mailroom.sqlite``.
    """
    conn = open_readonly(Path(archive) / SOR_BASENAME)
    try:
        pending = urgent_message_ids(conn)
        columns = table_columns(conn, "notify_log")
        already: set[str] = set()
        if "message_id" in columns and "channel" in columns:
            rows = conn.execute(
                "SELECT message_id FROM notify_log WHERE channel = ?",
                (URGENT_TEXT_CHANNEL,),
            ).fetchall()
            already = {str(row[0]) for row in rows if row[0] is not None}
    finally:
        conn.close()
    ledger = urgent_ledger_path(archive)
    if _publish_baseline_if_absent(ledger, pending):
        return _SendReport(0, 0, ())
    pinned = [message_id for message_id in pending if message_id in already]
    fresh = [message_id for message_id in pending if message_id not in already]
    _retain_confirmed(ledger, pinned)
    return send_urgent_texts(ledger, fresh, send)


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


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def classify_lsof(rc: int, stdout: str, stderr: str) -> str:
    """Return free, held, or lsof-error. One file's lsof result only."""
    lines = _nonempty_lines(stdout)
    if any(line.isdigit() for line in lines):
        return "held"
    if rc == 1 and not lines and not (stderr or "").strip():
        return "free"
    return "lsof-error"


def run_lsof(path: str) -> tuple[int, str, str]:
    """Run ``lsof -t -- FILE`` for one path. Stderr is captured."""
    try:
        proc = subprocess.run(
            ["lsof", "-t", "--", path],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 127, "", "exec-failed"
    return int(proc.returncode), proc.stdout or "", proc.stderr or ""


def probe_lock_file(path: Path) -> str:
    """Return missing, free, held, or lsof-error. Does not take a lock."""
    if not path.exists():
        return "missing"
    rc, stdout, stderr = run_lsof(str(path))
    return classify_lsof(rc, stdout, stderr)


def current_self_pids() -> set[int]:
    """This process and its parent. Neither is a writer match."""
    pids = {os.getpid()}
    try:
        parent = os.getppid()
    except OSError:
        parent = 0
    if isinstance(parent, int) and parent > 0:
        pids.add(parent)
    return pids


def classify_pgrep(
    rc: int,
    stdout: str,
    stderr: str,
    self_pids: set[int],
) -> str:
    """Return absent, present, or error.

    Exit 0 is present only when a pid other than this process or its
    parent remains. Exit 1 with empty output is absent. Every other
    result is error. There is no branch that maps a failed probe to
    absent.
    """
    lines = _nonempty_lines(stdout)
    if rc == 0:
        foreign: list[int] = []
        if not lines:
            return "error"
        for line in lines:
            if not line.isdigit():
                return "error"
            pid = int(line)
            if pid not in self_pids:
                foreign.append(pid)
        if foreign:
            return "present"
        return "absent"
    if rc == 1 and not lines and not (stderr or "").strip():
        return "absent"
    return "error"


def run_pgrep(pattern: str) -> tuple[int, str, str]:
    """Run ``pgrep -f PATTERN`` for one pattern. Stderr is captured."""
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pattern],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 127, "", "exec-failed"
    return int(proc.returncode), proc.stdout or "", proc.stderr or ""


def probe_writers(self_pids: set[int] | None = None) -> list[tuple[str, str]]:
    """One pgrep per writer pattern. Returns (check name, state)."""
    mine = current_self_pids() if self_pids is None else set(self_pids)
    found: list[tuple[str, str]] = []
    for name, pattern in WRITER_CHECKS:
        rc, stdout, stderr = run_pgrep(pattern)
        found.append((name, classify_pgrep(rc, stdout, stderr, mine)))
    return found


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


def _earliest(current: int, candidate: int) -> int:
    if current == EXIT_OK:
        return candidate
    if candidate == EXIT_OK:
        return current
    return min(current, candidate)


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
    writers: list[tuple[str, str]],
    failed: list[str],
) -> list[str]:
    lines = [
        "last_daily_rag_ok=%s newer_than_since=%s"
        % (stamp_value, "yes" if stamp_newer else "no"),
        "G=%s" % gone,
        "messages_inserted_since=%s" % messages_inserted,
        "urgent_texts_sent_since=%s" % urgent_texts,
        "bills_added_since=%s" % bills_added,
        "write.lock=%s" % write_lock,
        "daily.lock=%s" % daily_lock,
    ]
    for name, state in writers:
        lines.append("%s=%s" % (name, state))
    if failed:
        lines.append("POST-RESTORE FAIL %s" % ",".join(failed))
    else:
        lines.append("POST-RESTORE PASS")
    return lines


def evaluate(
    archive: Path,
    since: datetime,
    urgent_send=None,
) -> tuple[list[str], int]:
    stamp_path = archive / "logs" / STAMP_NAME
    try:
        stamp_raw = read_stamp(stamp_path)
    except CheckError:
        stamp_raw = None
    stamp_value = stamp_raw if stamp_raw is not None else "missing"
    newer = stamp_is_newer(stamp_raw, since)
    write_lock = probe_lock_file(archive / WRITE_LOCK_NAME)
    daily_lock = probe_lock_file(archive / DAILY_LOCK_NAME)
    writers = probe_writers()
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
    failed: list[str] = []
    code = EXIT_OK
    if not db_ok:
        failed.append("sqlite")
        code = _earliest(code, EXIT_DB)
    if not newer:
        failed.append("last_daily_rag_ok")
        code = _earliest(code, EXIT_STAMP)
    if write_lock != "free":
        failed.append("write.lock")
        code = _earliest(code, EXIT_WRITE_LOCK)
    if daily_lock not in ("free", "missing"):
        failed.append("daily.lock")
        code = _earliest(code, EXIT_DAILY_LOCK)
    for name, state in writers:
        if state != "absent":
            failed.append(name)
            code = _earliest(code, EXIT_PROCESS)
    lines = build_lines(
        stamp_value=stamp_value,
        stamp_newer=newer,
        gone=gone,
        messages_inserted=inserted,
        urgent_texts=urgent,
        bills_added=bills,
        write_lock=write_lock,
        daily_lock=daily_lock,
        writers=writers,
        failed=failed,
    )
    if urgent_send is not None and code == EXIT_OK:
        report = deliver_archive_urgent_texts(archive, urgent_send)
        extra: list[str] = []
        if report.overflow:
            extra.append("urgent_texts_overflow=%d" % report.overflow)
        for message_id in report.deferred:
            extra.append("urgent_text_deferred=%s" % message_id)
        if extra:
            lines = lines[:-1] + extra + lines[-1:]
    return lines, code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only post-restore check. "
            "Archive root is --archive, ARCHIVE_ROOT, or MAILARCHIVE."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
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
    lines, code = evaluate(archive, since)
    sys.stdout.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
