#!/usr/bin/env python3
"""Metadata-only attachment catalog fill. Never stores part bytes.

Option 1 (``--source imap``): rows with ``source='imap-live'``. Fetches
``(BODYSTRUCTURE)`` only. Option 2 (``--source jsonl``): other rows that
already have ``jsonl_offset``. Seeks that offset and reads ``jsonl_len``
bytes, then parses MIME headers.

Default is ``--dry-run`` (counts only). ``--apply`` writes. Filename text
is stored only with ``--store-filenames`` (default off); otherwise the
filename column stays NULL. Refuses basename ``mailroom.sqlite`` unless
``--allow-mailroom-sqlite``, then still calls the writer gate.

Does not create the ATT-0 schema and does not reshape ``messages``.
A live mailbox or the system of record needs a separate approval.
"""

from __future__ import annotations

import argparse
import datetime
import imaplib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from refuse_destructive import DestructiveRefuse, refuse_destructive_cli  # noqa: E402
from sor_writer_gate import (  # noqa: E402
    SOR_BASENAME,
    SorWriterRefuse,
    refuse_if_sor_writer_conflict,
)

try:
    from .bodystructure import (
        ParseError,
        bodystructure_from_fetch,
        parts_from_bodystructure,
    )
    from .mime_meta import MimePart, has_attachments_flag, parts_from_rfc822
except ImportError:  # python3 scripts/attachments/meta_fill.py
    from bodystructure import (  # type: ignore
        ParseError,
        bodystructure_from_fetch,
        parts_from_bodystructure,
    )
    from mime_meta import (  # type: ignore
        MimePart,
        has_attachments_flag,
        parts_from_rfc822,
    )

_BODYSTRUCTURE_ITEM = "(BODYSTRUCTURE)"
_IMAP_SOURCE = "imap-live"
_DEFAULT_MAX_MESSAGES = 200
_DEFAULT_MAX_PARTS = 100
_DEFAULT_TIMEOUT_S = 30
_DEFAULT_MAX_RECORD_BYTES = 2_000_000


class FillRefuse(RuntimeError):
    """Metadata fill refuse. Never includes secrets or filesystem paths."""


class _Oversized(Exception):
    """Record longer than the byte cap. Not marked scanned."""


class ImapBodystructureClient:
    """UID FETCH of ``(BODYSTRUCTURE)`` over imaplib.IMAP4.

    Host, user, and password come from the caller. This class does not
    read a default host and does not put the password in fetch results.
    """

    def __init__(
        self,
        host: str,
        user: str | None,
        password: str | None,
        port: int = 143,
        mailbox: str = "INBOX",
        timeout: float = 30,
        imap_factory: Any = None,
    ) -> None:
        if not host:
            raise FillRefuse("imap host is required")
        self.host = host
        self.user = user or ""
        self._password = password or ""
        self.port = int(port)
        self.mailbox = mailbox or "INBOX"
        self.timeout = timeout
        self._factory = imaplib.IMAP4 if imap_factory is None else imap_factory
        self._conn: Any = None

    def __enter__(self) -> "ImapBodystructureClient":
        self._conn = self._factory(self.host, self.port, timeout=self.timeout)
        try:
            self._conn.login(self.user, self._password)
            typ, _data = self._conn.select(self.mailbox, readonly=True)
        except Exception:
            self._close()
            raise
        if typ != "OK":
            self._close()
            raise RuntimeError("imap select failed")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close()

    def _close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.logout()
        except Exception:
            return

    def fetch_bodystructure(self, uid: str) -> str:
        if uid is None or str(uid).strip() == "":
            raise ValueError("missing uid")
        typ, data = self._conn.uid("FETCH", str(uid), _BODYSTRUCTURE_ITEM)
        if typ != "OK":
            raise ValueError("bodystructure fetch failed")
        return bodystructure_from_fetch(data)


def _default_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stored_filename(part: MimePart, enabled: bool) -> str | None:
    """NULL unless the filename flag is on. No hash and no extension substitute."""
    if not enabled:
        return None
    name = part.filename
    if name is None:
        return None
    text = str(name).strip()
    return text or None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(%s)" % table)]


def _require_schema(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "messages"):
        raise FillRefuse("messages table is missing")
    message_cols = _columns(conn, "messages")
    if "has_attachments" not in message_cols:
        raise FillRefuse("messages.has_attachments is missing; migrate first")
    if "source" not in message_cols:
        raise FillRefuse("messages.source is missing")
    if not _table_exists(conn, "attachment_meta_scans"):
        raise FillRefuse("attachment_meta_scans is missing; migrate first")
    if not _table_exists(conn, "attachments"):
        raise FillRefuse("attachments is missing; migrate first")
    found = _columns(conn, "attachments")
    for name in (
        "message_id",
        "part_id",
        "filename",
        "mime",
        "size",
        "sha256",
        "status",
        "content_disposition",
    ):
        if name not in found:
            raise FillRefuse("attachments.%s is missing; migrate first" % name)


def _connect(path: Path, apply: bool) -> sqlite3.Connection:
    if apply:
        conn = sqlite3.connect(str(path), isolation_level=None)
        return conn
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rfc822_from_slice(blob: bytes) -> bytes:
    stripped = blob.lstrip()
    if not stripped.startswith(b"{"):
        return blob
    try:
        obj = json.loads(blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("jsonl record is not utf-8 json") from exc
    if not isinstance(obj, dict):
        raise ValueError("jsonl record is not an object")
    raw = obj.get("rfc822")
    if raw is None:
        raw = obj.get("raw")
    if not isinstance(raw, str):
        raise ValueError("jsonl record has no rfc822 text")
    return raw.encode("utf-8")


def _empty_report(path: Path, source: str, apply: bool) -> dict[str, Any]:
    return {
        "dry_run": not apply,
        "source": source,
        "db_basename": path.name,
        "messages": 0,
        "parts": 0,
        "has_attachments": 0,
        "filenames": 0,
        "bytes_stored": 0,
        "scanned": 0,
        "stopped": "",
        "capped": 0,
        "errors": 0,
    }


def _select_rows(conn: sqlite3.Connection, source: str):
    if source == "imap":
        where = "source = ?"
        params: tuple = (_IMAP_SOURCE,)
        order = "id"
    elif source == "jsonl":
        where = "source != ? AND jsonl_offset IS NOT NULL"
        params = (_IMAP_SOURCE,)
        order = "jsonl_offset, id"
    else:
        raise FillRefuse("source must be imap or jsonl")
    scanned = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE %s AND id IN "
        "(SELECT message_id FROM attachment_meta_scans)" % where,
        params,
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT id, uid, jsonl_offset, jsonl_len, source FROM messages "
        "WHERE %s AND id NOT IN (SELECT message_id FROM attachment_meta_scans) "
        "ORDER BY %s" % (where, order),
        params,
    ).fetchall()
    return int(scanned), rows


def _record_message(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    message_id: str,
    source_label: str,
    parts: list[MimePart],
    store_filenames: bool,
    now: Callable[[], str],
) -> tuple[int, int, int]:
    flag = has_attachments_flag(parts)
    names = 0
    started = False
    try:
        if apply:
            conn.execute("BEGIN")
            started = True
        for part in parts:
            filename = stored_filename(part, store_filenames)
            if filename:
                names += 1
            if not apply:
                continue
            conn.execute(
                "INSERT INTO attachments ("
                "message_id, part_id, filename, mime, size, sha256, status, "
                "content_disposition) VALUES (?, ?, ?, ?, ?, NULL, 'meta', ?)",
                (
                    message_id,
                    part.part_id,
                    filename,
                    part.mime,
                    part.size,
                    part.content_disposition,
                ),
            )
        if apply:
            conn.execute(
                "UPDATE messages SET has_attachments=? WHERE id=?",
                (flag, message_id),
            )
            conn.execute(
                "INSERT INTO attachment_meta_scans ("
                "message_id, source, part_count, has_attachments, scanned_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_id, source_label, len(parts), flag, now()),
            )
            conn.commit()
    except Exception:
        if started:
            conn.rollback()
        raise
    return flag, names, len(parts)


def _parts_for_row(
    row: tuple,
    *,
    source: str,
    fh,
    imap_client: Any,
    max_record_bytes: int,
) -> list[MimePart]:
    _message_id, uid, offset, length, _row_source = row
    if source == "imap":
        if uid is None or str(uid).strip() == "":
            raise ValueError("missing uid")
        text = imap_client.fetch_bodystructure(str(uid))
        return parts_from_bodystructure(text)
    if offset is None or length is None:
        raise ValueError("bad offset")
    try:
        offset_i = int(offset)
        length_i = int(length)
    except (TypeError, ValueError) as exc:
        raise ValueError("bad offset") from exc
    if offset_i < 0 or length_i < 0:
        raise ValueError("bad offset")
    if length_i > max_record_bytes:
        raise _Oversized()
    fh.seek(offset_i)
    blob = fh.read(length_i)
    if len(blob) != length_i:
        raise ValueError("short read")
    raw = _rfc822_from_slice(blob)
    del blob
    parts = parts_from_rfc822(raw)
    del raw
    return parts


def fill_metadata(
    db: str | Path,
    *,
    source: str,
    jsonl_path: str | Path | None = None,
    apply: bool = False,
    store_filenames: bool = False,
    allow_mailroom_sqlite: bool = False,
    argv: list[str] | None = None,
    lock_held: bool | None = None,
    cmdlines: Any = None,
    lock_path: Path | None = None,
    imap_client: Any = None,
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    port: int = 143,
    mailbox: str = "INBOX",
    max_messages: int = _DEFAULT_MAX_MESSAGES,
    max_parts: int = _DEFAULT_MAX_PARTS,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_record_bytes: int = _DEFAULT_MAX_RECORD_BYTES,
    clock: Callable[[], float] | None = None,
    now: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Count or write attachment metadata. ``bytes_stored`` is always 0.

    ``imap_client`` skips socket setup. The CLI builds
    ``ImapBodystructureClient`` only when this is omitted and host is set.
    """
    path = Path(db)
    refuse_destructive_cli([] if argv is None else list(argv))
    if path.name == SOR_BASENAME and not allow_mailroom_sqlite:
        raise FillRefuse(
            "refuse: basename mailroom.sqlite "
            "(pass --allow-mailroom-sqlite to override)"
        )
    refuse_if_sor_writer_conflict(
        path,
        cmdlines=cmdlines,
        lock_path=lock_path,
        lock_held=lock_held,
    )
    if not path.is_file():
        raise FillRefuse("database not found")
    if source not in ("imap", "jsonl"):
        raise FillRefuse("source must be imap or jsonl")
    if source == "jsonl":
        if jsonl_path is None:
            raise FillRefuse("jsonl path is required")
        dump = Path(jsonl_path)
        if not dump.is_file():
            raise FillRefuse("jsonl path is missing")
    else:
        dump = None

    report = _empty_report(path, source, apply)
    tick = time.monotonic if clock is None else clock
    stamp = _default_now if now is None else now
    conn = _connect(path, apply)
    fh = None
    opened_client = False
    client = imap_client
    try:
        _require_schema(conn)
        scanned, rows = _select_rows(conn, source)
        report["scanned"] = scanned
        if timeout_s <= 0:
            report["stopped"] = "timeout"
            return report
        if max_messages <= 0:
            report["stopped"] = "max_messages"
            return report
        if source == "jsonl" and rows:
            try:
                fh = open(dump, "rb")
            except OSError:
                raise FillRefuse("jsonl read failed") from None
        if source == "imap" and rows and client is None:
            if not host:
                raise FillRefuse("imap host is required")
            client = ImapBodystructureClient(
                host,
                user,
                password,
                port=port,
                mailbox=mailbox,
                timeout=timeout_s if timeout_s > 0 else _DEFAULT_TIMEOUT_S,
            )
            try:
                client.__enter__()
            except FillRefuse:
                raise
            except Exception:
                raise FillRefuse("imap login failed") from None
            opened_client = True
        start = tick()
        for row in rows:
            if tick() - start > timeout_s:
                report["stopped"] = "timeout"
                break
            if report["messages"] + report["capped"] + report["errors"] >= max_messages:
                report["stopped"] = "max_messages"
                break
            message_id = str(row[0])
            row_source = str(row[4] or "")
            try:
                parts = _parts_for_row(
                    row,
                    source=source,
                    fh=fh,
                    imap_client=client,
                    max_record_bytes=max_record_bytes,
                )
            except _Oversized:
                report["capped"] += 1
                continue
            except (ParseError, ValueError, json.JSONDecodeError, OSError, TypeError):
                report["errors"] += 1
                continue
            if max_parts <= 0:
                report["capped"] += 1
                continue
            if len(parts) > max_parts:
                parts = parts[:max_parts]
            try:
                flag, names, count = _record_message(
                    conn,
                    apply=apply,
                    message_id=message_id,
                    source_label=_IMAP_SOURCE if source == "imap" else row_source,
                    parts=parts,
                    store_filenames=store_filenames,
                    now=stamp,
                )
            except sqlite3.Error:
                report["errors"] += 1
                continue
            report["messages"] += 1
            report["parts"] += count
            report["has_attachments"] += flag
            report["filenames"] += names
    finally:
        if opened_client and client is not None:
            client.__exit__(None, None, None)
        if fh is not None:
            fh.close()
        conn.close()
    report["bytes_stored"] = 0
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "att0 meta fill",
        "dry_run=%s" % (1 if report.get("dry_run") else 0),
        "source=%s" % report.get("source"),
        "db_basename=%s" % report.get("db_basename"),
        "messages=%s" % report.get("messages"),
        "parts=%s" % report.get("parts"),
        "has_attachments=%s" % report.get("has_attachments"),
        "filenames=%s" % report.get("filenames"),
        "bytes_stored=%s" % report.get("bytes_stored"),
        "scanned=%s" % report.get("scanned"),
        "stopped=%s" % report.get("stopped"),
        "capped=%s" % report.get("capped"),
        "errors=%s" % report.get("errors"),
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ATT-0 metadata-only attachment fill. Default is dry-run counts. "
            "Refuses basename mailroom.sqlite unless --allow-mailroom-sqlite. "
            "Does not store part bytes."
        )
    )
    parser.add_argument("--db", required=True, help="Existing SQLite database.")
    parser.add_argument(
        "--source",
        required=True,
        choices=("imap", "jsonl"),
        help="imap: source imap-live. jsonl: rows with jsonl_offset.",
    )
    parser.add_argument("--jsonl", help="Archive dump to read (rb only).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write metadata. Omit for a dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Counts only. This is the default. Conflicts with --apply.",
    )
    parser.add_argument(
        "--store-filenames",
        action="store_true",
        help="Store raw filenames. Default off leaves filename NULL.",
    )
    parser.add_argument(
        "--allow-mailroom-sqlite",
        action="store_true",
        help="Permit basename mailroom.sqlite. The writer gate still applies.",
    )
    parser.add_argument("--max-messages", type=int, default=_DEFAULT_MAX_MESSAGES)
    parser.add_argument("--max-parts", type=int, default=_DEFAULT_MAX_PARTS)
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--max-record-bytes",
        type=int,
        default=_DEFAULT_MAX_RECORD_BYTES,
    )
    parser.add_argument("--host", help="IMAP host. Else MAILROOM_IMAP_HOST.")
    parser.add_argument("--user", help="IMAP user. Else MAILROOM_IMAP_USER.")
    parser.add_argument(
        "--password",
        help="IMAP password. Else MAILROOM_IMAP_PASSWORD. Not logged.",
    )
    parser.add_argument("--port", type=int, help="IMAP port. Else MAILROOM_IMAP_PORT or 143.")
    parser.add_argument("--mailbox", help="IMAP mailbox. Default INBOX.")
    return parser


def _env_text(env: dict[str, str], args_value: str | None, key: str) -> str | None:
    if args_value is not None:
        return args_value
    value = env.get(key)
    if value is None or value == "":
        return None
    return value


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        refuse_destructive_cli(raw)
    except DestructiveRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    args = build_parser().parse_args(raw)
    if args.apply and args.dry_run:
        sys.stderr.write("error: pass only one of --apply and --dry-run\n")
        return 2
    db_path = Path(args.db)
    if db_path.name == SOR_BASENAME and not args.allow_mailroom_sqlite:
        sys.stderr.write(
            "error: refuse: basename mailroom.sqlite "
            "(pass --allow-mailroom-sqlite to override)\n"
        )
        return 2
    if not db_path.is_file():
        sys.stderr.write("error: database not found\n")
        return 2
    if args.source == "jsonl" and not args.jsonl:
        sys.stderr.write("error: jsonl path is required\n")
        return 2
    environ = os.environ if env is None else env
    host = _env_text(environ, args.host, "MAILROOM_IMAP_HOST")
    user = _env_text(environ, args.user, "MAILROOM_IMAP_USER")
    password = _env_text(environ, args.password, "MAILROOM_IMAP_PASSWORD")
    mailbox = _env_text(environ, args.mailbox, "MAILROOM_IMAP_MAILBOX") or "INBOX"
    port = args.port
    if port is None:
        raw_port = environ.get("MAILROOM_IMAP_PORT")
        if raw_port:
            try:
                port = int(raw_port)
            except ValueError:
                sys.stderr.write("error: imap port is invalid\n")
                return 2
        else:
            port = 143
    try:
        report = fill_metadata(
            db_path,
            source=args.source,
            jsonl_path=args.jsonl,
            apply=bool(args.apply),
            store_filenames=bool(args.store_filenames),
            allow_mailroom_sqlite=bool(args.allow_mailroom_sqlite),
            argv=raw,
            host=host,
            user=user,
            password=password,
            port=port,
            mailbox=mailbox,
            max_messages=args.max_messages,
            max_parts=args.max_parts,
            timeout_s=args.timeout,
            max_record_bytes=args.max_record_bytes,
        )
    except (FillRefuse, SorWriterRefuse, DestructiveRefuse) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except sqlite3.Error as exc:
        sys.stderr.write("error: sqlite fill failed (%s)\n" % type(exc).__name__)
        return 2
    sys.stdout.write(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
