#!/usr/bin/env python3
"""Metadata-only attachment catalog fill. Never stores part bytes.

Option 1 (``--source imap``): rows with ``source='imap-live'``. UIDs are
unique per folder, so rows are grouped by ``messages.folder`` and each
folder is examined before its UID FETCH of ``(BODYSTRUCTURE)``. The
production transport is pinned ``/usr/bin/curl`` ``imaps://`` (port 993).
It does not construct ``imaplib`` or a Python socket. ``CURL_BIN`` is
not read and Homebrew curl is not a fallback. Per folder, one curl
process uses the base URL ``imaps://host:993/`` (no mailbox, so curl
does not SELECT). Transfers are joined by ``next``. ``--fail-early``
stops that process on the first transfer error. The first is
``EXAMINE`` of the quoted mailbox (``"Deleted Messages"``). Later
transfers batch UIDs (``UID FETCH 1:50,77 (BODYSTRUCTURE)``) and do not
change the seen flag. Stdout is read as bytes so CRLF stays intact.
There is no ``--dump-header``. UIDVALIDITY is the first untagged
``* OK [UIDVALIDITY n]`` in the EXAMINE reply, before any FETCH, and
is stored per folder on the first ``--apply`` fill, not at ingest. A
mismatch is counted in ``uidvalidity_mismatch`` (one per row, not per
folder) and those rows are not written. The ``PARTIAL:`` banner
includes ``uidvalidity_mismatch=N``. A non-zero curl status (including
21, 7, 28, 60, and 67), an authentication failure, or an Errno 9 /
bad-file-descriptor error counts that folder's UIDs as errors, records
the classified message, and continues with later folders. It does not
retry, and the command still writes the report. Nothing from that
failure is marked scanned. A second connection fails closed. After
each folder, stderr gets ``HH:MM PT | folder n/N | rc=N`` (index only,
no folder name) and is flushed. The password is read from macOS Keychain
(``scripts/imap_keychain.py``). The binary is pinned to
``/usr/bin/security`` and the item is ``mailroom.imap.app-password``
with one legacy fallback. Curl receives it only on stdin (``-K -``,
``user = "..."``), never in argv, the environment, or a file. TLS
verification stays on (no ``-k``). There is no password option and no
password environment variable. This module does not import the Python
IMAP client. The old SSL double lives in
``tests/imap_bodystructure_double.py`` and is not imported here.
Option 2 (``--source jsonl``): other rows that already have
``jsonl_offset``. Seeks that offset and reads ``jsonl_len`` bytes, then
parses MIME headers.

Default is ``--dry-run`` (counts only). ``--apply`` writes. Filename text
is stored only with ``--store-filenames`` (default off); otherwise the
filename column stays NULL. Refuses basename ``mailroom.sqlite`` unless
``--allow-mailroom-sqlite``, then still calls the writer gate.

``--max-messages`` (default 200) and ``--timeout`` (default 30 seconds)
can stop early. The summary then starts with a ``PARTIAL:`` banner and
sets ``partial`` in the JSON summary. A full pass is
``--max-messages 0 --timeout 0`` (0 means no cap).
``--max-record-bytes`` defaults to 64 MiB (67108864). Pass a larger
value, such as ``128MB``, to raise that cap.
``--max-parts 0`` means no part cap and is not ``capped``. A truncated
tree increments ``parts_truncated``; ``has_attachments`` still reflects
the full tree. ``--max-record-bytes`` accepts plain bytes or a human
size. Default is 64 MiB, so records over 2 MB are counted. A row over
that byte cap is reported as ``capped: N``, separate from ``scanned``
and ``skipped``. JSONL ``raw`` (frozen dump) or ``rfc822`` is streamed.

Does not create the ATT-0 schema and does not reshape ``messages``.
A live mailbox or the system of record needs a separate approval.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from imap_keychain import read_imap_app_password  # noqa: E402
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

_IMAP_SOURCE = "imap-live"
_DEFAULT_MAX_MESSAGES = 200
_DEFAULT_MAX_PARTS = 100
_DEFAULT_TIMEOUT_S = 30
_DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024


class FillRefuse(RuntimeError):
    """Metadata fill refuse. Never includes secrets or filesystem paths."""


class _Oversized(Exception):
    """Record longer than the byte cap. Not marked scanned."""


def _load_imap_curl():
    """Import the curl transport without loading it for a JSONL-only run."""
    import imap_curl

    return imap_curl


class _CurlProductionClient:
    """CLI / fill_metadata IMAP path. Curl only. Never constructs imaplib."""

    def __init__(self, host, user, timeout, password_fn, port=993, cacert=None) -> None:
        mod = _load_imap_curl()
        self._mod = mod
        fn = read_imap_app_password if password_fn is None else password_fn
        try:
            self._inner = mod.CurlImapsClient(
                host,
                user,
                timeout=timeout,
                password_fn=fn,
                port=int(port),
                cacert=cacert,
            )
        except mod.CurlImapError as exc:
            raise FillRefuse(str(exc)) from None
        self.mailbox = None
        self.uidvalidity = None

    def __enter__(self) -> "_CurlProductionClient":
        try:
            self._inner.__enter__()
        except self._mod.CurlImapError as exc:
            raise FillRefuse(str(exc)) from None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._inner.__exit__(exc_type, exc, tb)

    def select(self, mailbox: str, readonly: bool = True) -> None:
        try:
            self._inner.select(mailbox, readonly=readonly)
        except self._mod.CurlImapError as exc:
            raise FillRefuse(str(exc)) from None
        self.mailbox = self._inner.mailbox
        self.uidvalidity = self._inner.uidvalidity

    def open_folder(self, mailbox: str, uids) -> None:
        """One curl for this folder. A failed batch is not split into UIDs.

        ``CurlImapError`` propagates with its curl status so the fill can
        count this folder's UIDs as errors and continue. It is not retried.
        """
        self._inner.open_folder(mailbox, uids)
        self.mailbox = self._inner.mailbox
        self.uidvalidity = self._inner.uidvalidity

    def fetch_bodystructure(self, uid: str) -> str:
        try:
            return self._inner.fetch_bodystructure(uid)
        except self._mod.CurlImapError as exc:
            raise FillRefuse(str(exc)) from None


def _progress_rc(exc: BaseException) -> int:
    rc = getattr(exc, "rc", None)
    if isinstance(rc, int):
        return rc
    return 1


def _write_folder_progress(index: int, total: int, rc: int) -> None:
    """Stall-watcher line. Folder index only, flushed, Pacific time."""
    now = datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
    sys.stderr.write(
        "%02d:%02d PT | folder %s/%s | rc=%s\n"
        % (now.hour, now.minute, int(index), int(total), int(rc))
    )
    sys.stderr.flush()


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
    if not _table_exists(conn, "attachment_folder_uidvalidity"):
        raise FillRefuse("attachment_folder_uidvalidity is missing; migrate first")
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


_SIZE_RE = re.compile(r"^(\d+)\s*([kmg]i?b?)?$", re.IGNORECASE)
_SIZE_MULT = {
    "": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024 ** 2,
    "mb": 1024 ** 2,
    "mib": 1024 ** 2,
    "g": 1024 ** 3,
    "gb": 1024 ** 3,
    "gib": 1024 ** 3,
}
_READ_CHUNK = 1024 * 1024
_JSON_BODY_KEYS = ("raw", "rfc822")


def parse_byte_size(text: str) -> int:
    """Plain byte count or a human size. ``MB`` and ``MiB`` are 1024*1024."""
    raw = str(text).strip().replace("_", "")
    match = _SIZE_RE.fullmatch(raw)
    if match is None:
        raise argparse.ArgumentTypeError("invalid size %r" % text)
    number = int(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit not in _SIZE_MULT:
        raise argparse.ArgumentTypeError("invalid size %r" % text)
    return number * _SIZE_MULT[unit]


class _SliceReader:
    """Read at most ``length`` bytes from ``fh`` in chunks. Seek is already done."""

    def __init__(self, fh, length: int) -> None:
        self._fh = fh
        self.left = int(length)
        self._buf = b""
        self._i = 0
        self.eof_early = False

    def _pull(self) -> bool:
        if self._i < len(self._buf):
            return True
        if self.left <= 0:
            self._buf = b""
            self._i = 0
            return False
        n = _READ_CHUNK if self.left > _READ_CHUNK else self.left
        data = self._fh.read(n)
        if not data:
            self.left = 0
            self._buf = b""
            self._i = 0
            self.eof_early = True
            return False
        if len(data) < n:
            self.eof_early = True
        self.left -= len(data)
        self._buf = data
        self._i = 0
        return True

    def get(self) -> int | None:
        if not self._pull():
            return None
        value = self._buf[self._i]
        self._i += 1
        return value

    def read_all(self) -> bytes:
        """Return every byte of the slice, including bytes already buffered."""
        parts = []
        if self._i < len(self._buf):
            parts.append(self._buf[self._i :])
            self._i = len(self._buf)
        while self.left > 0:
            n = _READ_CHUNK if self.left > _READ_CHUNK else self.left
            data = self._fh.read(n)
            if not data:
                self.eof_early = True
                break
            if len(data) < n:
                self.eof_early = True
            self.left -= len(data)
            parts.append(data)
        return b"".join(parts)


class _Pushback:
    """One-byte pushback over a slice reader."""

    def __init__(self, reader: _SliceReader) -> None:
        self._reader = reader
        self._held: int | None = None

    def get(self) -> int | None:
        if self._held is not None:
            value = self._held
            self._held = None
            return value
        return self._reader.get()

    def push(self, value: int) -> None:
        if self._held is not None:
            raise RuntimeError("pushback full")
        self._held = value


def _skip_ws_pb(pb: _Pushback) -> int | None:
    while True:
        ch = pb.get()
        if ch is None:
            return None
        if ch not in (32, 9, 10, 13):
            return ch


def _decode_json_string_pb(pb: _Pushback) -> bytearray:
    out = bytearray()
    while True:
        ch = pb.get()
        if ch is None:
            raise ValueError("truncated json string")
        if ch == 34:
            return out
        if ch != 92:
            out.append(ch)
            continue
        esc = pb.get()
        if esc is None:
            raise ValueError("truncated json string")
        mapped = {
            34: 34,
            92: 92,
            47: 47,
            98: 8,
            102: 12,
            110: 10,
            114: 13,
            116: 9,
        }
        if esc in mapped:
            out.append(mapped[esc])
            continue
        if esc != 117:
            raise ValueError("bad json string")
        hex_digits = bytearray()
        for _ in range(4):
            digit = pb.get()
            if digit is None:
                raise ValueError("truncated json string")
            hex_digits.append(digit)
        try:
            code = int(hex_digits.decode("ascii"), 16)
        except ValueError as exc:
            raise ValueError("bad json string") from exc
        if 0xD800 <= code <= 0xDBFF:
            if pb.get() != 92 or pb.get() != 117:
                raise ValueError("bad json string")
            low_digits = bytearray()
            for _ in range(4):
                digit = pb.get()
                if digit is None:
                    raise ValueError("truncated json string")
                low_digits.append(digit)
            try:
                low = int(low_digits.decode("ascii"), 16)
            except ValueError as exc:
                raise ValueError("bad json string") from exc
            if not 0xDC00 <= low <= 0xDFFF:
                raise ValueError("bad json string")
            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
        out.extend(chr(code).encode("utf-8", "replace"))


def _skip_json_value_pb(pb: _Pushback, first: int) -> None:
    if first == 34:
        _decode_json_string_pb(pb)
        return
    if first == 123:
        _parse_json_object(pb, capture=False)
        return
    if first == 91:
        ch = _skip_ws_pb(pb)
        if ch is None:
            raise ValueError("truncated json record")
        if ch == 93:
            return
        while True:
            _skip_json_value_pb(pb, ch)
            nxt = _skip_ws_pb(pb)
            if nxt == 93:
                return
            if nxt != 44:
                raise ValueError("jsonl record is not an object")
            ch = _skip_ws_pb(pb)
            if ch is None:
                raise ValueError("truncated json record")
    while True:
        ch = pb.get()
        if ch is None:
            return
        if ch in (44, 125, 93):
            pb.push(ch)
            return
        if ch in (32, 9, 10, 13):
            pb.push(ch)
            return


def _parse_json_object(pb: _Pushback, capture: bool) -> bytearray | None:
    """``pb`` is positioned just after ``{``. Return the body string if capturing."""
    found_raw: bytearray | None = None
    found_rfc822: bytearray | None = None
    ch = _skip_ws_pb(pb)
    if ch is None:
        raise ValueError("truncated json record")
    if ch == 125:
        if not capture:
            return None
        raise ValueError("jsonl record has no raw text")
    while True:
        if ch != 34:
            raise ValueError("jsonl record is not an object")
        key = _decode_json_string_pb(pb).decode("utf-8", "replace")
        colon = _skip_ws_pb(pb)
        if colon != 58:
            raise ValueError("jsonl record is not an object")
        val = _skip_ws_pb(pb)
        if val is None:
            raise ValueError("truncated json record")
        if capture and key in _JSON_BODY_KEYS and val == 34:
            body = _decode_json_string_pb(pb)
            if key == "raw":
                found_raw = body
            elif found_rfc822 is None:
                found_rfc822 = body
        else:
            _skip_json_value_pb(pb, val)
        if found_raw is not None:
            return found_raw
        nxt = _skip_ws_pb(pb)
        if nxt == 125:
            break
        if nxt != 44:
            raise ValueError("jsonl record is not an object")
        ch = _skip_ws_pb(pb)
        if ch is None:
            raise ValueError("truncated json record")
    if not capture:
        return None
    if found_raw is not None:
        return found_raw
    if found_rfc822 is not None:
        return found_rfc822
    raise ValueError("jsonl record has no raw text")


def _read_rfc822(fh, offset: int, length: int) -> bytes | bytearray:
    """Stream one jsonl slice. Frozen dumps store the message under ``raw``.

    A non-JSON slice is raw RFC822. A JSON object uses ``raw`` when that
    key is present, otherwise ``rfc822``. The file is read in 1 MiB chunks.
    The decoded message is the large buffer; the JSON wrapper is not kept.
    """
    fh.seek(offset)
    reader = _SliceReader(fh, length)
    pb = _Pushback(reader)
    first = _skip_ws_pb(pb)
    if first is None:
        raise ValueError("short read")
    if first != 123:
        fh.seek(offset)
        whole = _SliceReader(fh, length)
        blob = whole.read_all()
        if whole.eof_early or len(blob) != length:
            raise ValueError("short read")
        return blob
    body = _parse_json_object(pb, capture=True)
    if body is None:
        raise ValueError("jsonl record has no raw text")
    if reader.eof_early:
        raise ValueError("short read")
    return body


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
        "eligible": 0,
        "skipped": 0,
        "partial": False,
        "partial_banner": "",
        "parts_truncated": 0,
        "uidvalidity_mismatch": 0,
        "curl_failures": [],
    }


_IMAP_NOT_SCANNED = "id NOT IN (SELECT message_id FROM attachment_meta_scans)"
_IMAP_FOLDER_KEY = (
    "CASE WHEN folder IS NULL OR TRIM(folder) = '' THEN NULL ELSE folder END"
)


def _select_rows(conn: sqlite3.Connection, source: str, mailbox: str | None = None):
    """Return ``(already_scanned, groups, skipped_other)``.

    IMAP UIDs are unique per folder. A full pass loops distinct folders and
    runs ``AND folder=?`` for each before that folder is selected. ``mailbox``
    is one such query. ``skipped_other`` counts unscanned imap-live rows whose
    folder is null, blank, or not ``mailbox``. JSONL is a single group and
    ``skipped_other`` is 0.
    """
    cols = _columns(conn, "messages")
    if source == "imap":
        if "folder" not in cols:
            raise FillRefuse("messages.folder is missing")
        if mailbox is not None:
            scanned = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE source = ? AND folder = ? "
                "AND id IN (SELECT message_id FROM attachment_meta_scans)",
                (_IMAP_SOURCE, mailbox),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, uid, jsonl_offset, jsonl_len, source, folder "
                "FROM messages WHERE source = ? AND folder = ? AND %s "
                "ORDER BY id" % _IMAP_NOT_SCANNED,
                (_IMAP_SOURCE, mailbox),
            ).fetchall()
            skipped = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE source = ? AND %s AND "
                "(folder IS NULL OR TRIM(folder) = '' OR folder != ?)"
                % _IMAP_NOT_SCANNED,
                (_IMAP_SOURCE, mailbox),
            ).fetchone()[0]
            groups = [(mailbox, rows)] if rows else []
            return int(scanned), groups, int(skipped)
        scanned = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE source = ? AND id IN "
            "(SELECT message_id FROM attachment_meta_scans)",
            (_IMAP_SOURCE,),
        ).fetchone()[0]
        folder_rows = conn.execute(
            "SELECT DISTINCT %s AS folder_key FROM messages "
            "WHERE source = ? AND %s ORDER BY folder_key"
            % (_IMAP_FOLDER_KEY, _IMAP_NOT_SCANNED),
            (_IMAP_SOURCE,),
        ).fetchall()
        groups = []
        for (folder_key,) in folder_rows:
            name = _folder_name(folder_key)
            if name is None:
                clause = "(folder IS NULL OR TRIM(folder) = '')"
                params: tuple = (_IMAP_SOURCE,)
            else:
                clause = "folder = ?"
                params = (_IMAP_SOURCE, name)
            rows = conn.execute(
                "SELECT id, uid, jsonl_offset, jsonl_len, source, folder "
                "FROM messages WHERE source = ? AND %s AND %s ORDER BY id"
                % (clause, _IMAP_NOT_SCANNED),
                params,
            ).fetchall()
            if rows:
                groups.append((name, rows))
        return int(scanned), groups, 0
    if source == "jsonl":
        where = "source != ? AND jsonl_offset IS NOT NULL"
        params = (_IMAP_SOURCE,)
        scanned = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE %s AND id IN "
            "(SELECT message_id FROM attachment_meta_scans)" % where,
            params,
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, uid, jsonl_offset, jsonl_len, source, NULL FROM messages "
            "WHERE %s AND id NOT IN (SELECT message_id FROM attachment_meta_scans) "
            "ORDER BY jsonl_offset, id" % where,
            params,
        ).fetchall()
        return int(scanned), [(None, rows)], 0
    raise FillRefuse("source must be imap or jsonl")


def _folder_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.strip() == "":
        return None
    return text


def _visited(report: dict[str, Any]) -> int:
    return (
        int(report["messages"])
        + int(report["capped"])
        + int(report["errors"])
        + int(report["skipped"])
    )


def _limit_num(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _note_partial(report: dict[str, Any], limit_text: str) -> None:
    visited = _visited(report)
    eligible = int(report.get("eligible") or 0)
    if eligible > visited:
        report["partial"] = True
        report["partial_banner"] = "PARTIAL: scanned %s of %s (limit %s)" % (
            visited,
            eligible,
            limit_text,
        )
    else:
        report["partial"] = False
        report["partial_banner"] = ""
    _attach_uidvalidity_banner(report)


def _attach_uidvalidity_banner(report: dict[str, Any]) -> None:
    """Put mismatched row counts on the PARTIAL banner."""
    count = int(report.get("uidvalidity_mismatch") or 0)
    if count <= 0:
        return
    tag = "uidvalidity_mismatch=%s" % count
    banner = str(report.get("partial_banner") or "")
    if banner:
        if tag not in banner:
            report["partial_banner"] = "%s %s" % (banner, tag)
        report["partial"] = True
        return
    report["partial"] = True
    report["partial_banner"] = "PARTIAL: scanned %s of %s (%s)" % (
        _visited(report),
        int(report.get("eligible") or 0),
        tag,
    )


def _stop_for_limits(report, *, start, tick, timeout_s, max_messages) -> bool:
    """Stop only when a positive cap is set. ``0`` means unlimited."""
    if timeout_s > 0 and tick() - start > timeout_s:
        report["stopped"] = "timeout"
        _note_partial(report, "timeout=%s" % _limit_num(timeout_s))
        return True
    if max_messages > 0 and _visited(report) >= max_messages:
        report["stopped"] = "max_messages"
        _note_partial(report, "max_messages=%s" % max_messages)
        return True
    return False


def _record_message(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    message_id: str,
    source_label: str,
    parts: list[MimePart],
    store_filenames: bool,
    now: Callable[[], str],
    attachment_flag: int | None = None,
) -> tuple[int, int, int]:
    flag = (
        has_attachments_flag(parts)
        if attachment_flag is None
        else int(attachment_flag)
    )
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
    _message_id, uid, offset, length, _row_source = row[:5]
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
    raw = _read_rfc822(fh, offset_i, length_i)
    parts = parts_from_rfc822(raw)
    del raw
    return parts


def _note_uidvalidity(conn: sqlite3.Connection, folder: str, validity: int, apply: bool) -> bool:
    """Return True when the stored UIDVALIDITY does not match.

    A missing row is recorded on apply and is not a mismatch. Dry-run
    does not insert. A mismatch does not update the stored value.
    """
    row = conn.execute(
        "SELECT uidvalidity FROM attachment_folder_uidvalidity WHERE folder=?",
        (folder,),
    ).fetchone()
    if row is None:
        if apply:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO attachment_folder_uidvalidity (folder, uidvalidity) "
                "VALUES (?, ?)",
                (folder, int(validity)),
            )
            conn.commit()
        return False
    return int(row[0]) != int(validity)


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
    password_fn: Callable[[], str] | None = None,
    mailbox: str | None = None,
    imap_port: int = 993,
    cacert: str | None = None,
    max_messages: int = _DEFAULT_MAX_MESSAGES,
    max_parts: int = _DEFAULT_MAX_PARTS,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_record_bytes: int = _DEFAULT_MAX_RECORD_BYTES,
    clock: Callable[[], float] | None = None,
    now: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Count or write attachment metadata. ``bytes_stored`` is always 0.

    ``imap_client`` skips curl and Keychain. When it is omitted and the
    source is imap, the CLI builds ``_CurlProductionClient`` (pinned
    ``/usr/bin/curl imaps://``, one process per folder). That path does
    not import imaplib.
    ``mailbox`` limits
    IMAP rows to that folder. Omit it to examine every folder that still
    has unscanned rows. ``imap_port`` and ``cacert`` are test injections
    for a local fake IMAPS server. The CLI does not pass them. Production
    uses port 993 and does not pass ``--cacert``.
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
    login_failed = False
    client = imap_client
    skipped_other = 0
    try:
        _require_schema(conn)
        scanned, groups, skipped_other = _select_rows(
            conn, source, mailbox if source == "imap" else None
        )
        report["scanned"] = scanned
        report["eligible"] = sum(len(group) for _name, group in groups)
        if source == "jsonl" and report["eligible"]:
            try:
                fh = open(dump, "rb")
            except OSError:
                raise FillRefuse("jsonl read failed") from None
        if source == "imap" and report["eligible"] and client is None:
            if not host:
                raise FillRefuse("imap host is required")
            if not user:
                raise FillRefuse("imap user is required")
            client = _CurlProductionClient(
                host,
                user,
                timeout=timeout_s if timeout_s > 0 else _DEFAULT_TIMEOUT_S,
                password_fn=password_fn,
                port=imap_port,
                cacert=cacert,
            )
            try:
                client.__enter__()
            except FillRefuse as exc:
                report["errors"] = report["eligible"]
                report["curl_failures"].append(str(exc))
                login_failed = True
            except Exception:
                report["errors"] = report["eligible"]
                report["curl_failures"].append("imap login failed")
                login_failed = True
            else:
                opened_client = True
        start = tick()
        folder_total = len(groups)
        if not login_failed:
          for folder_index, (folder_name, group) in enumerate(groups, start=1):
            if report["stopped"]:
                break
            if source == "imap":
                if not folder_name:
                    _write_folder_progress(folder_index, folder_total, 0)
                    for _row in group:
                        if _stop_for_limits(
                            report,
                            start=start,
                            tick=tick,
                            timeout_s=timeout_s,
                            max_messages=max_messages,
                        ):
                            break
                        report["errors"] += 1
                    continue
                try:
                    if hasattr(client, "open_folder"):
                        client.open_folder(
                            folder_name,
                            [
                                str(row[1]).strip()
                                for row in group
                                if row[1] is not None and str(row[1]).strip()
                            ],
                        )
                    else:
                        client.select(folder_name, readonly=True)
                    if client.uidvalidity is None:
                        raise RuntimeError("imap uidvalidity missing")
                    if _note_uidvalidity(
                        conn, folder_name, int(client.uidvalidity), apply
                    ):
                        report["uidvalidity_mismatch"] += len(group)
                        _write_folder_progress(folder_index, folder_total, 0)
                        continue
                except Exception as exc:
                    if type(exc).__name__ in ("FillRefuse", "CurlImapError"):
                        report["curl_failures"].append(str(exc))
                    _write_folder_progress(
                        folder_index, folder_total, _progress_rc(exc)
                    )
                    for _row in group:
                        if _stop_for_limits(
                            report,
                            start=start,
                            tick=tick,
                            timeout_s=timeout_s,
                            max_messages=max_messages,
                        ):
                            break
                        report["errors"] += 1
                    continue
                _write_folder_progress(folder_index, folder_total, 0)
            for row in group:
                if _stop_for_limits(
                    report,
                    start=start,
                    tick=tick,
                    timeout_s=timeout_s,
                    max_messages=max_messages,
                ):
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
                full_flag = has_attachments_flag(parts)
                if max_parts > 0 and len(parts) > max_parts:
                    parts = parts[:max_parts]
                    report["parts_truncated"] += 1
                try:
                    flag, names, count = _record_message(
                        conn,
                        apply=apply,
                        message_id=message_id,
                        source_label=_IMAP_SOURCE if source == "imap" else row_source,
                        parts=parts,
                        store_filenames=store_filenames,
                        now=stamp,
                        attachment_flag=full_flag,
                    )
                except sqlite3.Error:
                    report["errors"] += 1
                    continue
                report["messages"] += 1
                report["parts"] += count
                report["has_attachments"] += flag
                report["filenames"] += names
            else:
                continue
            break
    finally:
        if opened_client and client is not None:
            client.__exit__(None, None, None)
        if fh is not None:
            fh.close()
        conn.close()
    report["skipped"] = skipped_other
    report["bytes_stored"] = 0
    _attach_uidvalidity_banner(report)
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = []
    banner = report.get("partial_banner") or ""
    if report.get("partial") and banner:
        lines.append(str(banner))
    lines.extend(
        [
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
            "eligible=%s" % report.get("eligible"),
            "stopped=%s" % report.get("stopped"),
            "capped=%s" % report.get("capped"),
            "skipped=%s" % report.get("skipped"),
            "errors=%s" % report.get("errors"),
            "partial=%s" % (1 if report.get("partial") else 0),
            "parts_truncated=%s" % report.get("parts_truncated"),
            "uidvalidity_mismatch=%s" % report.get("uidvalidity_mismatch"),
            "capped: %s" % report.get("capped"),
        ]
    )
    lines.append("summary_json=%s" % json.dumps(report, sort_keys=True))
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ATT-0 metadata-only attachment fill. Default is dry-run counts. "
            "Refuses basename mailroom.sqlite unless --allow-mailroom-sqlite. "
            "Does not store part bytes. "
            "A full pass is --max-messages 0 --timeout 0. "
            "--max-record-bytes defaults to 64MB; pass a larger value "
            "such as 128MB to raise it. "
            "The summary prints capped: N separate from scanned and skipped. "
            "Defaults still print a PARTIAL banner when rows remain."
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
    parser.add_argument(
        "--max-messages",
        type=int,
        default=_DEFAULT_MAX_MESSAGES,
        help=(
            "Stop after this many eligible rows (default 200). "
            "0 means no message cap. A full pass uses --max-messages 0. "
            "A stopped run still prints a PARTIAL banner."
        ),
    )
    parser.add_argument("--max-parts", type=int, default=_DEFAULT_MAX_PARTS)
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_S,
        help=(
            "Stop after this many seconds (default 30). "
            "0 means no time cap. A full pass uses --timeout 0. "
            "A stopped run still prints a PARTIAL banner."
        ),
    )
    parser.add_argument(
        "--max-record-bytes",
        type=parse_byte_size,
        default=_DEFAULT_MAX_RECORD_BYTES,
        help=(
            "Do not parse a JSONL record longer than this. "
            "Plain bytes or a human size such as 64MB (1024*1024). "
            "Default 67108864 (64MB). Those rows are capped: N, not scanned."
        ),
    )
    parser.add_argument("--host", help="IMAP host. Else MAILROOM_IMAP_HOST.")
    parser.add_argument("--user", help="IMAP user. Else MAILROOM_IMAP_USER.")
    parser.add_argument(
        "--mailbox",
        help=(
            "Limit IMAP rows to this folder and EXAMINE only that folder. "
            "Omit to EXAMINE every folder that still has unscanned rows."
        ),
    )
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
    mailbox = _env_text(environ, args.mailbox, "MAILROOM_IMAP_MAILBOX")

    def _password_fn() -> str:
        return read_imap_app_password()

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
            password_fn=_password_fn,
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
