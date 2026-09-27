#!/usr/bin/env python3
"""ATT-0 metadata fill. Synthetic fixtures only.

No network. No live mailbox. No system-of-record file.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import attachments.bodystructure as bodystructure  # noqa: E402
import attachments.meta_fill as meta  # noqa: E402
import hermetic_binaries  # noqa: E402
import imap_bodystructure_double as imap_double  # noqa: E402
import attachments.migrate_att0_schema as mig  # noqa: E402
import attachments.mime_meta as mime_meta  # noqa: E402
import imap_keychain  # noqa: E402
from refuse_destructive import DestructiveRefuse  # noqa: E402
from sor_writer_gate import SorWriterRefuse  # noqa: E402

DESIGN = ROOT / "docs" / "attachments" / "ATT-0-design.md"
PKG = SCRIPTS / "attachments"
MARKER = "BODYMARKER-DO-NOT-STORE"
SECRET = "example-secret-token"
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PDF_RAW = b"%PDF-1.4\n" + MARKER.encode("ascii") + b"\n"
EXPECTED_MIXED = [
    ("0", "multipart/mixed", None, None),
    ("1", "multipart/alternative", None, None),
    ("1.1", "text/plain", None, None),
    ("1.2", "text/html", None, None),
    ("2", "image/png", "inline", "pixel.png"),
    ("3", "application/pdf", "attachment", "note.pdf"),
    ("4", "message/rfc822", "attachment", "forwarded.eml"),
    ("4.1", "text/plain", None, None),
]
MIXED_BS = """(
  (
    ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 28 1 NIL NIL NIL NIL)
    ("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 32 1 NIL NIL NIL NIL)
    "ALTERNATIVE" ("BOUNDARY" "altbound") NIL NIL NIL
  )
  ("IMAGE" "PNG" ("NAME" "pixel.png") NIL NIL "BASE64" 68 NIL ("INLINE" ("FILENAME" "pixel.png")) NIL NIL)
  ("APPLICATION" "PDF" ("NAME" "note.pdf") NIL NIL "BASE64" 40 NIL ("ATTACHMENT" ("FILENAME" "note.pdf")) NIL NIL)
  ("MESSAGE" "RFC822" NIL NIL NIL "7BIT" 80 ("Mon, 1 Jan 2024 00:00:00 +0000" "inner" (("Inner" NIL "inner" "example.com")) NIL NIL NIL NIL NIL NIL "<m@example.com>") ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 24 1 NIL NIL NIL NIL) 4 NIL ("ATTACHMENT" ("FILENAME" "forwarded.eml")) NIL NIL)
  "MIXED" ("BOUNDARY" "mixbound") NIL NIL NIL
)"""
PLAIN_BS = '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 20 1)'


def _mixed_rfc822() -> bytes:
    pdf_b64 = base64.b64encode(PDF_RAW).decode("ascii")
    text = (
        "MIME-Version: 1.0\n"
        "From: sender@example.com\n"
        "To: reader@example.com\n"
        "Subject: synthetic mixed\n"
        'Content-Type: multipart/mixed; boundary="mixbound"\n'
        "\n"
        "--mixbound\n"
        'Content-Type: multipart/alternative; boundary="altbound"\n'
        "\n"
        "--altbound\n"
        'Content-Type: text/plain; charset="utf-8"\n'
        "\n"
        "%s plain\n"
        "\n"
        "--altbound\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "\n"
        "<html>%s</html>\n"
        "\n"
        "--altbound--\n"
        "--mixbound\n"
        "Content-Type: image/png\n"
        'Content-Disposition: inline; filename="pixel.png"\n'
        "Content-Transfer-Encoding: base64\n"
        "\n"
        "%s\n"
        "\n"
        "--mixbound\n"
        "Content-Type: application/pdf\n"
        'Content-Disposition: attachment; filename="note.pdf"\n'
        "Content-Transfer-Encoding: base64\n"
        "\n"
        "%s\n"
        "\n"
        "--mixbound\n"
        "Content-Type: message/rfc822\n"
        'Content-Disposition: attachment; filename="forwarded.eml"\n'
        "\n"
        "From: inner@example.com\n"
        "To: other@example.com\n"
        "Subject: forwarded\n"
        "MIME-Version: 1.0\n"
        'Content-Type: text/plain; charset="utf-8"\n'
        "\n"
        "%s inner\n"
        "\n"
        "--mixbound--\n"
    ) % (MARKER, MARKER, PNG_B64, pdf_b64, MARKER)
    raw = text.encode("utf-8")
    if MARKER.encode("ascii") not in raw:
        raise AssertionError("fixture lost the marker")
    return raw


def _plain_rfc822() -> bytes:
    text = (
        "MIME-Version: 1.0\n"
        "From: sender@example.com\n"
        "To: reader@example.com\n"
        "Subject: synthetic plain\n"
        'Content-Type: text/plain; charset="utf-8"\n'
        "\n"
        "%s only\n"
    ) % MARKER
    return text.encode("utf-8")


def _dump(mixed: bytes, plain: bytes) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    rec1 = json.dumps({"rfc822": mixed.decode("utf-8")}).encode("utf-8") + b"\n"
    rec2 = plain if plain.endswith(b"\n") else plain + b"\n"
    if rec2.lstrip().startswith(b"{"):
        raise AssertionError("plain record must be raw rfc822")
    return rec1 + rec2, (0, len(rec1)), (len(rec1), len(rec2))


def _seed(db: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE messages (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              folder TEXT,
              uid TEXT,
              jsonl_offset INTEGER,
              jsonl_len INTEGER,
              has_attachments INTEGER DEFAULT 0,
              subject TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE message_embeddings (
              message_id TEXT PRIMARY KEY,
              embedding BLOB,
              note TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO message_embeddings (message_id, embedding, note) "
            "VALUES ('keep', X'deadbeef', 'live')"
        )
        for row in rows:
            if len(row) == 6:
                row = tuple(row) + (None,)
            conn.execute(
                "INSERT INTO messages "
                "(id, source, uid, jsonl_offset, jsonl_len, subject, folder) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    mig.migrate_database(db, cmdlines=[], lock_held=False)


def _embed(db: Path):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT message_id, embedding, note FROM message_embeddings"
        ).fetchall()
    finally:
        conn.close()


def _cells(db: Path) -> list:
    conn = sqlite3.connect(str(db))
    try:
        names = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        values = []
        for name in names:
            if name.startswith("sqlite_"):
                continue
            for row in conn.execute('SELECT * FROM "%s"' % name.replace('"', "")):
                values.extend(row)
        return values
    finally:
        conn.close()


def _text_blob(db: Path) -> str:
    chunks = []
    for value in _cells(db):
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, (bytes, bytearray)):
            chunks.append(bytes(value).decode("latin1"))
    return "\n".join(chunks)


def _counts(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    try:
        flags = conn.execute(
            "SELECT id, has_attachments, subject FROM messages ORDER BY id"
        ).fetchall()
        attachments = conn.execute(
            "SELECT message_id, part_id, filename, mime, size, sha256, status, "
            "content_disposition FROM attachments ORDER BY message_id, part_id"
        ).fetchall()
        scans = conn.execute(
            "SELECT message_id, source, part_count, has_attachments "
            "FROM attachment_meta_scans ORDER BY message_id"
        ).fetchall()
        extracts = conn.execute("SELECT COUNT(*) FROM attachment_extracts").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM attachment_chunks").fetchone()[0]
        return {
            "flags": flags,
            "attachments": attachments,
            "scans": scans,
            "extracts": extracts,
            "chunks": chunks,
        }
    finally:
        conn.close()


class _StubImap:
    def __init__(self, structures):
        self.structures = structures
        self.uids = []
        self.events = []
        self.mailbox = None
        self.uidvalidity_by_folder = "1"
        self.uidvalidity = None

    def select(self, mailbox, readonly=True):
        self.events.append(("select", mailbox, readonly))
        self.mailbox = mailbox
        source = self.uidvalidity_by_folder
        if isinstance(source, dict):
            chosen = source.get(self.mailbox, "1")
        else:
            chosen = source
        self.uidvalidity = int(chosen)
        return "OK", [b"1"]

    def response(self, code):
        """Same shape as imaplib.IMAP4.response, which pops the untagged value.

        Real imaplib returns ``('UIDVALIDITY', [b'42'])``, or
        ``('UIDVALIDITY', [None])`` when the server did not send one.
        The type is the response code, not ``OK``.
        """
        name = str(code).upper()
        if name == "UIDVALIDITY" and self.uidvalidity is not None:
            return "UIDVALIDITY", [str(int(self.uidvalidity)).encode("ascii")]
        return name, [None]

    def fetch_bodystructure(self, uid):
        self.uids.append(uid)
        self.events.append(("fetch", self.mailbox, str(uid)))
        key = (self.mailbox, str(uid))
        if key in self.structures:
            return self.structures[key]
        if str(uid) in self.structures:
            return self.structures[str(uid)]
        raise ValueError("unknown uid")


def _imap_speak(conn, uidvalidity):
    """One IMAP session. UIDVALIDITY is an untagged OK response code, or omitted."""
    try:
        conn.sendall(b"* OK ready\r\n")
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                if not line:
                    continue
                tag, _, rest = line.partition(b" ")
                cmd = rest.split(b" ", 1)[0].upper()
                if cmd == b"CAPABILITY":
                    conn.sendall(b"* CAPABILITY IMAP4rev1\r\n")
                    conn.sendall(tag + b" OK CAPABILITY completed\r\n")
                elif cmd == b"LOGIN":
                    conn.sendall(tag + b" OK LOGIN completed\r\n")
                elif cmd == b"SELECT":
                    conn.sendall(b"* 1 EXISTS\r\n")
                    conn.sendall(b"* 0 RECENT\r\n")
                    if uidvalidity is not None:
                        token = str(int(uidvalidity)).encode("ascii")
                        conn.sendall(b"* OK [UIDVALIDITY " + token + b"] UIDs valid\r\n")
                    conn.sendall(tag + b" OK [READ-WRITE] SELECT completed\r\n")
                elif cmd == b"EXAMINE":
                    conn.sendall(b"* 1 EXISTS\r\n")
                    conn.sendall(b"* 0 RECENT\r\n")
                    if uidvalidity is not None:
                        token = str(int(uidvalidity)).encode("ascii")
                        conn.sendall(b"* OK [UIDVALIDITY " + token + b"] UIDs valid\r\n")
                    conn.sendall(tag + b" OK [READ-ONLY] EXAMINE completed\r\n")
                elif cmd == b"LOGOUT":
                    conn.sendall(b"* BYE logging out\r\n")
                    conn.sendall(tag + b" OK LOGOUT completed\r\n")
                    return
                else:
                    conn.sendall(tag + b" BAD unknown\r\n")
    except Exception:
        return


def _serve_imap(uidvalidity):
    """Local IMAP4 server on 127.0.0.1. Returns ``(port, stop, thread)``."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                conn, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(5)
            try:
                _imap_speak(conn, uidvalidity)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            listener.close()
        except OSError:
            pass

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return port, stop, thread


class ParserTests(unittest.TestCase):
    def test_mixed_bodystructure_tree(self):
        parts = bodystructure.parts_from_bodystructure(MIXED_BS)
        self.assertEqual([part.identity() for part in parts], EXPECTED_MIXED)
        by_id = {part.part_id: part for part in parts}
        self.assertIsNone(by_id["0"].size)
        self.assertIsNone(by_id["1"].size)
        self.assertEqual(by_id["2"].size, 68)
        self.assertEqual(by_id["3"].size, 40)
        self.assertEqual(by_id["4"].size, 80)
        self.assertTrue(mime_meta.is_attachment(by_id["2"]))
        self.assertTrue(mime_meta.is_attachment(by_id["3"]))
        self.assertTrue(mime_meta.is_attachment(by_id["4"]))
        self.assertFalse(mime_meta.is_attachment(by_id["1.1"]))
        self.assertFalse(mime_meta.is_attachment(by_id["1.2"]))
        self.assertFalse(mime_meta.is_attachment(by_id["0"]))
        self.assertEqual(mime_meta.has_attachments_flag(parts), 1)
        for part in parts:
            for value in part.identity():
                self.assertNotIn(MARKER, value or "")

    def test_plain_bodystructure_is_not_an_attachment(self):
        parts = bodystructure.parts_from_bodystructure(PLAIN_BS)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].identity(), ("1", "text/plain", None, None))
        self.assertEqual(parts[0].size, 20)
        self.assertEqual(mime_meta.has_attachments_flag(parts), 0)

    def test_literal_filename_and_fetch_slice(self):
        raw = (
            '("APPLICATION" "PDF" NIL NIL NIL "BASE64" 4 NIL '
            '("ATTACHMENT" ("FILENAME" {8}\r\nnote.pdf)))'
        )
        parts = bodystructure.parts_from_bodystructure(raw)
        self.assertEqual(parts[0].filename, "note.pdf")
        self.assertEqual(parts[0].content_disposition, "attachment")
        self.assertEqual(parts[0].size, 4)
        fetched = bodystructure.bodystructure_from_fetch(
            [b'15 (UID 15 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 3 1))']
        )
        parsed = bodystructure.parts_from_bodystructure(fetched)
        self.assertEqual(parsed[0].mime, "text/plain")
        self.assertEqual(parsed[0].part_id, "1")
        self.assertEqual(parsed[0].size, 3)

    def test_present_literal_octets_parse_to_note_pdf(self):
        """NOT a curl capture.

        Hand-built RFC 3501 response with the literal octets present:
        BODYSTRUCTURE (... {8}\\r\\nnote.pdf ...). Curl 8.5.x omits those
        octets; this fixture keeps them so a curl that returns the octets
        still parses to the filename note.pdf.
        """
        import imap_curl as imap_curl

        self.assertIn(
            "NOT a curl capture",
            self.test_present_literal_octets_parse_to_note_pdf.__doc__,
        )
        raw = (
            '* 1 FETCH (UID 9 BODYSTRUCTURE ("APPLICATION" "PDF" NIL NIL NIL '
            '"BASE64" 4 NIL ("ATTACHMENT" ("FILENAME" {8}\r\nnote.pdf)) NIL NIL))\r\n'
        )
        self.assertIn("BODYSTRUCTURE (", raw)
        self.assertIn("{8}\r\nnote.pdf", raw)
        found, issues = imap_curl.parse_fetch_structures(raw)
        self.assertEqual(issues, {})
        self.assertIn("9", found)
        parts = bodystructure.parts_from_bodystructure(found["9"])
        self.assertEqual(parts[0].filename, "note.pdf")

    def test_truncated_literal_is_a_parse_error(self):
        short = '("NAME" {8}\r\nnote'
        with self.assertRaises(bodystructure.ParseError) as ctx:
            bodystructure.parse_sexp(short)
        self.assertIn("literal short", str(ctx.exception))
        with self.assertRaises(bodystructure.ParseError) as fetched:
            bodystructure.bodystructure_from_fetch(
                "* 1 FETCH (UID 9 BODYSTRUCTURE " + short + ")"
            )
        self.assertIn("literal short", str(fetched.exception))

    def test_envelope_display_name_is_not_a_filename(self):
        text = (
            '("MESSAGE" "RFC822" NIL NIL NIL "7BIT" 10 '
            '(("ATTACHMENT" NIL "a" "example.com") NIL NIL NIL NIL NIL NIL NIL NIL) '
            '("TEXT" "PLAIN" NIL NIL NIL "7BIT" 2 1) 1)'
        )
        parts = bodystructure.parts_from_bodystructure(text)
        outer = parts[0]
        self.assertEqual(outer.mime, "message/rfc822")
        self.assertIsNone(outer.filename)
        self.assertIsNone(outer.content_disposition)
        self.assertEqual(parts[1].part_id, "1.1")
        self.assertEqual(parts[1].mime, "text/plain")


class MimeWalkTests(unittest.TestCase):
    def test_nested_message_matches_the_catalog_tree(self):
        parts = mime_meta.parts_from_rfc822(_mixed_rfc822())
        self.assertEqual([part.identity() for part in parts], EXPECTED_MIXED)
        by_id = {part.part_id: part for part in parts}
        self.assertEqual(by_id["2"].size, len(base64.b64decode(PNG_B64)))
        self.assertEqual(by_id["3"].size, len(PDF_RAW))
        self.assertGreater(by_id["1.1"].size or 0, 0)
        self.assertIsNone(by_id["0"].size)
        self.assertEqual(mime_meta.has_attachments_flag(parts), 1)
        for part in parts:
            joined = " ".join(str(item) for item in part.identity() if item)
            self.assertNotIn(MARKER, joined)
            self.assertNotIn("%PDF", joined)

    def test_plain_message_has_no_attachment(self):
        parts = mime_meta.parts_from_rfc822(_plain_rfc822())
        self.assertEqual([part.identity() for part in parts], [("1", "text/plain", None, None)])
        self.assertEqual(mime_meta.has_attachments_flag(parts), 0)
        self.assertNotIn(MARKER, parts[0].mime)


class FillTests(unittest.TestCase):
    def _jsonl_db(self, tmp: str):
        root = Path(tmp)
        mixed = _mixed_rfc822()
        plain = _plain_rfc822()
        blob, first, second = _dump(mixed, plain)
        dump = root / "archive-example.jsonl"
        dump.write_bytes(blob)
        db = root / "mailroom-copy.sqlite"
        _seed(
            db,
            [
                ("ex-mixed", "jsonl-import", None, first[0], first[1], "synthetic-mixed", None),
                ("ex-plain", "jsonl-import", None, second[0], second[1], "synthetic-plain", None),
            ],
        )
        return db, dump

    def _imap_db(self, tmp: str):
        db = Path(tmp) / "mailroom-copy.sqlite"
        _seed(
            db,
            [
                ("ex-mixed", "imap-live", "1001", None, None, "synthetic-mixed", "INBOX"),
                ("ex-plain", "imap-live", "1002", None, None, "synthetic-plain", "INBOX"),
            ],
        )
        stub = _StubImap({"1001": MIXED_BS, "1002": PLAIN_BS})
        return db, stub

    def test_jsonl_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)
            before = _counts(db)
            embeds = _embed(db)

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            with mock.patch("socket.create_connection", boom), mock.patch(
                "urllib.request.urlopen", boom
            ):
                report = meta.fill_metadata(
                    db,
                    source="jsonl",
                    jsonl_path=dump,
                    apply=False,
                    cmdlines=[],
                    lock_held=True,
                )
            self.assertEqual(_counts(db), before)
            self.assertEqual(_embed(db), embeds)
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["db_basename"], "mailroom-copy.sqlite")
            self.assertNotIn(tmp, json.dumps(report))
            self.assertEqual(report["messages"], 2)
            self.assertEqual(report["parts"], 9)
            self.assertEqual(report["has_attachments"], 1)
            self.assertEqual(report["filenames"], 0)
            self.assertEqual(report["bytes_stored"], 0)
            self.assertEqual(report["scanned"], 0)
            self.assertEqual(report["stopped"], "")
            self.assertNotIn(SECRET, meta.format_report(report))
            self.assertNotIn(MARKER, _text_blob(db))

    def test_jsonl_apply_is_idempotent_and_leaves_filename_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            with mock.patch("socket.create_connection", boom):
                first = meta.fill_metadata(
                    db,
                    source="jsonl",
                    jsonl_path=dump,
                    apply=True,
                    store_filenames=False,
                    cmdlines=[],
                    lock_held=False,
                    now=lambda: "2026-01-01T00:00:00Z",
                )
                mid = _counts(db)
                second = meta.fill_metadata(
                    db,
                    source="jsonl",
                    jsonl_path=dump,
                    apply=True,
                    store_filenames=True,
                    cmdlines=[],
                    lock_held=False,
                    now=lambda: "2026-01-02T00:00:00Z",
                )
            self.assertEqual(first["messages"], 2)
            self.assertEqual(first["filenames"], 0)
            self.assertEqual(first["bytes_stored"], 0)
            self.assertEqual(second["messages"], 0)
            self.assertEqual(second["scanned"], 2)
            self.assertEqual(second["parts"], 0)
            self.assertEqual(_counts(db), mid)
            flags = {row[0]: row[1] for row in mid["flags"]}
            self.assertEqual(flags["ex-mixed"], 1)
            self.assertEqual(flags["ex-plain"], 0)
            subjects = {row[0]: row[2] for row in mid["flags"]}
            self.assertEqual(subjects["ex-mixed"], "synthetic-mixed")
            self.assertEqual(subjects["ex-plain"], "synthetic-plain")
            self.assertEqual(mid["extracts"], 0)
            self.assertEqual(mid["chunks"], 0)
            names = [row[2] for row in mid["attachments"]]
            self.assertTrue(all(name is None for name in names))
            self.assertTrue(all(row[5] is None and row[6] == "meta" for row in mid["attachments"]))
            part_ids = [row[1] for row in mid["attachments"] if row[0] == "ex-mixed"]
            self.assertEqual(
                part_ids,
                ["0", "1", "1.1", "1.2", "2", "3", "4", "4.1"],
            )
            blob = _text_blob(db)
            self.assertNotIn(MARKER, blob)
            self.assertNotIn(SECRET, blob)
            self.assertNotIn("%PDF", blob)
            self.assertNotIn(PNG_B64, blob)
            scans = {row[0]: row for row in mid["scans"]}
            self.assertEqual(scans["ex-plain"][2], 1)
            self.assertEqual(scans["ex-plain"][3], 0)
            self.assertEqual(scans["ex-mixed"][3], 1)
            self.assertEqual(_embed(db)[0][2], "live")

    def test_store_filenames_keeps_the_raw_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)
            meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                store_filenames=True,
                cmdlines=[],
                lock_held=False,
            )
            conn = sqlite3.connect(str(db))
            try:
                names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT filename FROM attachments WHERE filename IS NOT NULL"
                    )
                }
            finally:
                conn.close()
            self.assertEqual(names, {"pixel.png", "note.pdf", "forwarded.eml"})
            self.assertNotIn(MARKER, _text_blob(db))

    def test_imap_stub_matches_jsonl_tree_and_sets_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, stub = self._imap_db(tmp)

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            with mock.patch("socket.create_connection", boom):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    imap_client=stub,
                    store_filenames=False,
                    cmdlines=[],
                    lock_held=False,
                )
            self.assertEqual(stub.uids, ["1001", "1002"])
            self.assertEqual(
                stub.events,
                [
                    ("select", "INBOX", True),
                    ("fetch", "INBOX", "1001"),
                    ("fetch", "INBOX", "1002"),
                ],
            )
            self.assertEqual(report["has_attachments"], 1)
            self.assertEqual(report["filenames"], 0)
            self.assertEqual(report["bytes_stored"], 0)
            self.assertNotIn(SECRET, meta.format_report(report))
            conn = sqlite3.connect(str(db))
            try:
                rows = conn.execute(
                    "SELECT part_id, mime, filename, content_disposition "
                    "FROM attachments WHERE message_id='ex-mixed' ORDER BY part_id"
                ).fetchall()
                flag = conn.execute(
                    "SELECT has_attachments FROM messages WHERE id='ex-plain'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(
                [(row[0], row[1], row[3]) for row in rows],
                [(item[0], item[1], item[2]) for item in EXPECTED_MIXED],
            )
            self.assertTrue(all(row[2] is None for row in rows))
            self.assertEqual(flag, 0)
            self.assertNotIn(MARKER, _text_blob(db))
            self.assertNotIn(SECRET, _text_blob(db))

    def test_resume_after_max_messages_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)
            first = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                max_messages=1,
                cmdlines=[],
                lock_held=False,
                now=lambda: "2026-01-01T00:00:00Z",
            )
            self.assertEqual(first["stopped"], "max_messages")
            self.assertEqual(first["messages"], 1)
            conn = sqlite3.connect(str(db))
            try:
                done = {
                    row[0]
                    for row in conn.execute(
                        "SELECT message_id FROM attachment_meta_scans"
                    )
                }
            finally:
                conn.close()
            self.assertEqual(done, {"ex-mixed"})
            seq = [0, 1000]

            def clock():
                return seq.pop(0)

            second = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                timeout_s=30,
                clock=clock,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(second["stopped"], "timeout")
            self.assertEqual(second["messages"], 0)
            self.assertEqual(second["scanned"], 1)
            third = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(third["messages"], 1)
            self.assertEqual(third["scanned"], 1)
            self.assertEqual(third["stopped"], "")
            conn = sqlite3.connect(str(db))
            try:
                both = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(both, 2)

    def test_caps_do_not_mark_unparsed_rows_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)
            capped = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                max_record_bytes=8,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(capped["capped"], 2)
            self.assertEqual(capped["skipped"], 0)
            self.assertEqual(capped["messages"], 0)
            self.assertIn("capped: 2", meta.format_report(capped))
            self.assertNotIn("skipped=2", meta.format_report(capped))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
                parts = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)
            self.assertEqual(parts, 0)
            bad = Path(tmp) / "mailroom-daily-copy.sqlite"
            _seed(
                bad,
                [("ex-bad", "jsonl-import", None, 999999, 10, "synthetic-bad")],
            )
            (Path(tmp) / "short.jsonl").write_bytes(b"not-json\n")
            # point the bad row at a real small file but a past-eof offset
            short = Path(tmp) / "short.jsonl"
            errored = meta.fill_metadata(
                bad,
                source="jsonl",
                jsonl_path=short,
                apply=True,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(errored["errors"], 1)
            self.assertEqual(errored["messages"], 0)
            conn = sqlite3.connect(str(bad))
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attachment_meta_scans"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

    def test_max_parts_uses_only_the_stored_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)
            report = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                max_parts=4,
                max_messages=1,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["has_attachments"], 1)
            self.assertEqual(report["parts_truncated"], 1)
            self.assertEqual(report["capped"], 0)
            self.assertEqual(report["parts"], 4)
            conn = sqlite3.connect(str(db))
            try:
                ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT part_id FROM attachments ORDER BY rowid"
                    )
                ]
                flag = conn.execute(
                    "SELECT has_attachments FROM messages WHERE id='ex-mixed'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(ids, ["0", "1", "1.1", "1.2"])
            self.assertEqual(flag, 1)

    def test_missing_uid_is_an_error_and_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(db, [("ex-missing", "imap-live", None, None, None, "synthetic")])
            report = meta.fill_metadata(
                db,
                source="imap",
                apply=True,
                imap_client=_StubImap({}),
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["messages"], 0)
            conn = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attachment_meta_scans"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

    def test_refuses_mailroom_sqlite_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "mailroom.sqlite"
            with self.assertRaises(meta.FillRefuse) as ctx:
                meta.fill_metadata(
                    missing,
                    source="jsonl",
                    jsonl_path=Path(tmp) / "archive-example.jsonl",
                    cmdlines=[],
                    lock_held=False,
                )
            self.assertIn("mailroom.sqlite", str(ctx.exception))
            self.assertFalse(missing.exists())
            existing = Path(tmp) / "nested" / "mailroom.sqlite"
            existing.parent.mkdir()
            sqlite3.connect(str(existing)).close()
            digest = existing.read_bytes()
            with self.assertRaises(meta.FillRefuse):
                meta.fill_metadata(existing, source="imap", cmdlines=[], lock_held=False)
            self.assertEqual(existing.read_bytes(), digest)

    def test_flag_still_uses_the_writer_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom.sqlite"
            sqlite3.connect(str(db)).close()
            digest = db.read_bytes()
            with self.assertRaises(SorWriterRefuse):
                meta.fill_metadata(
                    db,
                    source="imap",
                    allow_mailroom_sqlite=True,
                    lock_held=True,
                    cmdlines=[],
                )
            self.assertEqual(db.read_bytes(), digest)

    def test_destructive_verb_and_missing_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            sqlite3.connect(str(db)).close()
            with self.assertRaises(DestructiveRefuse):
                meta.fill_metadata(db, source="jsonl", argv=["--purge"])
            bare = Path(tmp) / "bare.sqlite"
            (Path(tmp) / "nope.jsonl").write_bytes(b"\n")
            conn = sqlite3.connect(str(bare))
            try:
                conn.execute(
                    "CREATE TABLE messages (id TEXT PRIMARY KEY, source TEXT)"
                )
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(meta.FillRefuse) as ctx:
                meta.fill_metadata(
                    bare,
                    source="jsonl",
                    jsonl_path=Path(tmp) / "nope.jsonl",
                    cmdlines=[],
                    lock_held=False,
                )
            self.assertIn("has_attachments", str(ctx.exception))
            conn = sqlite3.connect(str(bare))
            try:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master")
                }
            finally:
                conn.close()
            self.assertNotIn("attachment_meta_scans", names)
            self.assertNotIn("attachments", names)

    def test_timeout_zero_does_not_open_a_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, stub = self._imap_db(tmp)

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            with mock.patch("socket.create_connection", boom):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    imap_client=stub,
                    timeout_s=0,
                    cmdlines=[],
                    lock_held=False,
                )
            self.assertEqual(report["stopped"], "")
            self.assertEqual(report["messages"], 2)
            self.assertEqual(stub.uids, ["1001", "1002"])
            self.assertFalse(report["partial"])
            conn = sqlite3.connect(str(db))
            try:
                stored = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(stored, len(EXPECTED_MIXED) + 1)

    def test_client_fetches_structure_only_over_ssl(self):
        seen = {}
        plain_calls = []

        class PlainIMAP:
            def __init__(self, *args, **kwargs):
                plain_calls.append((args, kwargs))
                raise AssertionError("plain IMAP4")

        class FakeSSL:
            def __init__(self, host, port, timeout=None, ssl_context=None):
                seen["init"] = (host, port, timeout, ssl_context)

            def login(self, user, password):
                seen["login"] = (user, password)
                return "OK", [b"ok"]

            def select(self, mailbox, readonly=False):
                seen["select"] = (mailbox, readonly)
                return "OK", [b"1"]

            def response(self, code):
                if str(code).upper() != "UIDVALIDITY":
                    return str(code).upper(), [None]
                return "UIDVALIDITY", [b"15"]

            def uid(self, command, uid, item):
                seen["uid"] = (command, uid, item)
                return "OK", [
                    b'9 (BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))'
                ]

            def logout(self):
                seen["logout"] = True
                return "BYE", [b""]

        def boom(*_args, **_kwargs):
            raise AssertionError("network is forbidden")

        with mock.patch("socket.create_connection", boom), mock.patch(
            "imap_bodystructure_double.imaplib.IMAP4", PlainIMAP
        ), mock.patch("imap_bodystructure_double.imaplib.IMAP4_SSL", FakeSSL), mock.patch(
            "imap_bodystructure_double.read_imap_app_password", return_value=SECRET
        ):
            client = imap_double.ImapBodystructureClient(
                "imap.example.com",
                "user@example.com",
                timeout=5,
            )
            with client:
                client.select("INBOX", readonly=True)
                text = client.fetch_bodystructure("9")
        self.assertEqual(plain_calls, [])
        self.assertEqual(seen["init"][:3], ("imap.example.com", 993, 5))
        self.assertEqual(seen["init"][3].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(seen["init"][3].check_hostname)
        self.assertEqual(seen["uid"], ("FETCH", "9", "(BODYSTRUCTURE)"))
        self.assertEqual(seen["select"], ('"INBOX"', True))
        self.assertEqual(seen["login"][1], SECRET)
        self.assertNotIn(SECRET, text)
        self.assertTrue(seen["logout"])
        source = (PKG / "meta_fill.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith("import imaplib") or stripped.startswith("from imaplib"),
                line,
            )
        self.assertNotIn("IMAP4_SSL", source)
        double_src = Path(imap_double.__file__).read_text(encoding="utf-8")
        self.assertIn("IMAP4_SSL", double_src)
        self.assertNotIn("BODY[]", source)
        self.assertNotIn("BODY.PEEK", source)
        self.assertNotIn("MAILROOM_IMAP_PASSWORD", source)
        self.assertNotIn('"--password"', source)
        self.assertNotIn("'--password'", source)
        dests = {action.dest for action in meta.build_parser()._actions}
        self.assertNotIn("password", dests)
        self.assertNotIn("port", dests)

    def test_same_uid_in_two_folders_selects_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-archive", "imap-live", "42", None, None, "synthetic-archive", "Archive"),
                    ("ex-inbox", "imap-live", "42", None, None, "synthetic-inbox", "INBOX"),
                ],
            )
            stub = _StubImap(
                {
                    ("Archive", "42"): MIXED_BS,
                    ("INBOX", "42"): PLAIN_BS,
                }
            )

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            with mock.patch("socket.create_connection", boom):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    imap_client=stub,
                    cmdlines=[],
                    lock_held=False,
                )
            self.assertEqual(
                stub.events,
                [
                    ("select", "Archive", True),
                    ("fetch", "Archive", "42"),
                    ("select", "INBOX", True),
                    ("fetch", "INBOX", "42"),
                ],
            )
            self.assertEqual(report["messages"], 2)
            self.assertEqual(report["eligible"], 2)
            self.assertEqual(report["has_attachments"], 1)
            self.assertFalse(report["partial"])
            self.assertEqual(report["bytes_stored"], 0)
            conn = sqlite3.connect(str(db))
            try:
                flags = dict(
                    conn.execute(
                        "SELECT id, has_attachments FROM messages ORDER BY id"
                    ).fetchall()
                )
                counts = dict(
                    conn.execute(
                        "SELECT message_id, COUNT(*) FROM attachments GROUP BY message_id"
                    ).fetchall()
                )
            finally:
                conn.close()
            self.assertEqual(flags["ex-archive"], 1)
            self.assertEqual(flags["ex-inbox"], 0)
            self.assertGreater(counts["ex-archive"], counts["ex-inbox"])
            self.assertEqual(counts["ex-inbox"], 1)

    def test_mailbox_filter_does_not_fetch_other_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-archive", "imap-live", "42", None, None, "synthetic-archive", "Archive"),
                    ("ex-inbox", "imap-live", "42", None, None, "synthetic-inbox", "INBOX"),
                ],
            )
            stub = _StubImap({("INBOX", "42"): PLAIN_BS, ("Archive", "42"): MIXED_BS})
            report = meta.fill_metadata(
                db,
                source="imap",
                apply=True,
                imap_client=stub,
                mailbox="INBOX",
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(
                stub.events,
                [("select", "INBOX", True), ("fetch", "INBOX", "42")],
            )
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["eligible"], 1)
            self.assertEqual(report["skipped"], 1)
            self.assertEqual(report["capped"], 0)
            self.assertIn("capped: 0", meta.format_report(report))
            self.assertFalse(report["partial"])
            conn = sqlite3.connect(str(db))
            try:
                done = {
                    row[0]
                    for row in conn.execute("SELECT message_id FROM attachment_meta_scans")
                }
            finally:
                conn.close()
            self.assertEqual(done, {"ex-inbox"})

    def test_default_caps_print_a_partial_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            rows = [
                ("m%03d" % i, "imap-live", str(i), None, None, "synthetic", "INBOX")
                for i in range(201)
            ]
            _seed(db, rows)
            stub = _StubImap({str(i): PLAIN_BS for i in range(201)})
            report = meta.fill_metadata(
                db,
                source="imap",
                apply=False,
                imap_client=stub,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(meta._DEFAULT_MAX_MESSAGES, 200)
            self.assertEqual(meta._DEFAULT_TIMEOUT_S, 30)
            self.assertEqual(report["messages"], 200)
            self.assertEqual(report["eligible"], 201)
            self.assertTrue(report["partial"])
            self.assertEqual(report["stopped"], "max_messages")
            banner = "PARTIAL: scanned 200 of 201 (limit max_messages=200)"
            self.assertEqual(report["partial_banner"], banner)
            text = meta.format_report(report)
            self.assertTrue(text.startswith(banner + "\n"))
            summary = json.loads(text.split("summary_json=", 1)[1])
            self.assertTrue(summary["partial"])
            self.assertEqual(summary["bytes_stored"], 0)
            conn = sqlite3.connect(str(db))
            try:
                stored = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(stored, 0)
            seq = [0, 0, 1000]

            def clock():
                return seq.pop(0)

            sub = Path(tmp) / "jsonl-case"
            sub.mkdir()
            db2, dump = self._jsonl_db(str(sub))
            timed = meta.fill_metadata(
                db2,
                source="jsonl",
                jsonl_path=dump,
                apply=False,
                timeout_s=30,
                clock=clock,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(timed["stopped"], "timeout")
            self.assertEqual(timed["messages"], 1)
            self.assertTrue(timed["partial"])
            self.assertEqual(
                timed["partial_banner"],
                "PARTIAL: scanned 1 of 2 (limit timeout=30)",
            )
            finished = meta.fill_metadata(
                db2,
                source="jsonl",
                jsonl_path=dump,
                apply=False,
                cmdlines=[],
                lock_held=False,
            )
            self.assertFalse(finished["partial"])
            self.assertEqual(finished["partial_banner"], "")
            self.assertNotIn("PARTIAL:", meta.format_report(finished))

    def test_record_over_2mb_is_processed_and_skips_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = b"A" * 2_100_000
            raw = (
                b"MIME-Version: 1.0\r\n"
                b"From: sender@example.com\r\n"
                b"To: reader@example.com\r\n"
                b"Subject: synthetic large\r\n"
                b"Content-Type: text/plain\r\n"
                b"\r\n" + body + b"\r\n"
            )
            rec = json.dumps({"rfc822": raw.decode("ascii")}).encode("utf-8") + b"\n"
            self.assertGreater(len(rec), 2_000_000)
            self.assertLess(len(rec), meta._DEFAULT_MAX_RECORD_BYTES)
            dump = root / "archive-example.jsonl"
            dump.write_bytes(rec)
            db = root / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-large", "jsonl-import", None, 0, len(rec), "synthetic-large", None)],
            )
            report = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(meta._DEFAULT_MAX_RECORD_BYTES, 64 * 1024 * 1024)
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["skipped"], 0)
            self.assertEqual(report["parts"], 1)
            self.assertEqual(report["bytes_stored"], 0)
            huge = root / "mailroom-daily-copy.sqlite"
            _seed(
                huge,
                [
                    (
                        "ex-huge",
                        "jsonl-import",
                        None,
                        0,
                        meta._DEFAULT_MAX_RECORD_BYTES + 1,
                        "synthetic-huge",
                        None,
                    )
                ],
            )
            (root / "tiny.jsonl").write_bytes(b"\n")
            skipped = meta.fill_metadata(
                huge,
                source="jsonl",
                jsonl_path=root / "tiny.jsonl",
                apply=True,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(skipped["capped"], 1)
            self.assertEqual(skipped["skipped"], 0)
            self.assertEqual(skipped["messages"], 0)
            self.assertIn("capped: 1", meta.format_report(skipped))
            conn = sqlite3.connect(str(huge))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)

    def test_select_rows_loops_folders_and_mailbox_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-deleted", "imap-live", "1", None, None, "synthetic", "Deleted"),
                    ("ex-inbox", "imap-live", "1", None, None, "synthetic", "INBOX"),
                    ("ex-blank", "imap-live", "1", None, None, "synthetic", None),
                ],
            )
            conn = sqlite3.connect(str(db))
            try:
                _scanned, groups, skipped = meta._select_rows(conn, "imap", None)
                self.assertEqual(skipped, 0)
                self.assertEqual(
                    [name for name, _rows in groups],
                    [None, "Deleted", "INBOX"],
                )
                self.assertTrue(
                    all(
                        row[5] == name or (name is None and not (row[5] or "").strip())
                        for name, rows in groups
                        for row in rows
                    )
                )
                _scanned, one, skipped = meta._select_rows(conn, "imap", "INBOX")
            finally:
                conn.close()
            self.assertEqual([name for name, _rows in one], ["INBOX"])
            self.assertEqual([row[0] for _name, rows in one for row in rows], ["ex-inbox"])
            self.assertEqual(skipped, 2)
            source = (PKG / "meta_fill.py").read_text(encoding="utf-8")
            select = source.split("def _select_rows", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("folder = ?", select)
            self.assertNotIn("_group_imap_rows", source)

    def test_frozen_dump_raw_key_is_the_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = _plain_rfc822()
            other = b"Subject: not-the-body\r\n\r\nnope\r\n"
            rec = (
                json.dumps({"rfc822": other.decode("ascii"), "raw": plain.decode("utf-8")})
                .encode("utf-8")
                + b"\n"
            )
            dump = root / "archive-example.jsonl"
            dump.write_bytes(rec)
            db = root / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-raw", "jsonl-import", None, 0, len(rec), "synthetic-raw", None)],
            )
            report = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                max_messages=0,
                timeout_s=0,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["capped"], 0)
            self.assertEqual(report["has_attachments"], 0)
            self.assertEqual(report["stopped"], "")
            conn = sqlite3.connect(str(db))
            try:
                subject_parts = conn.execute(
                    "SELECT mime, size FROM attachments WHERE message_id='ex-raw'"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(subject_parts), 1)
            self.assertEqual(subject_parts[0][0], "text/plain")
            self.assertNotEqual(subject_parts[0][1], len(other))

    def test_parse_byte_size_accepts_human_sizes_and_plain_bytes(self):
        self.assertEqual(meta.parse_byte_size("64MB"), 64 * 1024 * 1024)
        self.assertEqual(meta.parse_byte_size("64MiB"), 64 * 1024 * 1024)
        self.assertEqual(meta.parse_byte_size("67108864"), 67108864)
        self.assertEqual(meta.parse_byte_size("1KB"), 1024)
        with self.assertRaises(argparse.ArgumentTypeError):
            meta.parse_byte_size("64TB")
        parser = meta.build_parser()
        args = parser.parse_args(
            [
                "--db",
                "x",
                "--source",
                "jsonl",
                "--max-messages",
                "0",
                "--timeout",
                "0",
                "--max-record-bytes",
                "64MB",
            ]
        )
        self.assertEqual(args.max_messages, 0)
        self.assertEqual(args.timeout, 0.0)
        self.assertEqual(args.max_record_bytes, 64 * 1024 * 1024)
        help_text = parser.format_help()
        self.assertIn("full pass", help_text)
        self.assertIn("--max-messages 0", help_text)
        self.assertIn("--timeout 0", help_text)
        self.assertIn("64MB", help_text)
        self.assertIn("capped:", help_text)
        self.assertIn("PARTIAL", help_text)

    def test_sixty_mib_raw_record_peak_is_traced(self):
        body_len = 60 * 1024 * 1024
        prefix = (
            b'{"raw":"MIME-Version: 1.0\\r\\n'
            b"Content-Type: text/plain\\r\\n"
            b"\\r\\n"
        )
        suffix = b'"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "archive-example.jsonl"
            chunk = b"A" * (1024 * 1024)
            with dump.open("wb") as fh:
                fh.write(prefix)
                remaining = body_len
                while remaining:
                    n = chunk if remaining >= len(chunk) else chunk[:remaining]
                    fh.write(n)
                    remaining -= len(n)
                fh.write(suffix)
            length = dump.stat().st_size
            self.assertLess(length, meta._DEFAULT_MAX_RECORD_BYTES)
            db = root / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-60", "jsonl-import", None, 0, length, "synthetic-60", None)],
            )
            tracemalloc.start()
            try:
                report = meta.fill_metadata(
                    db,
                    source="jsonl",
                    jsonl_path=dump,
                    apply=False,
                    max_messages=0,
                    timeout_s=0,
                    cmdlines=[],
                    lock_held=False,
                )
                _current, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["capped"], 0)
            self.assertEqual(report["skipped"], 0)
            self.assertEqual(report["has_attachments"], 0)
            self.assertEqual(report["parts"], 1)
            self.assertEqual(report["bytes_stored"], 0)
            # CPython 3.12.3 tracemalloc peak for this fixture. The email
            # parser, not the JSON reader, holds the extra copies.
            self.assertAlmostEqual(peak, 699503008, delta=16 * 1024 * 1024)
            self.assertGreater(peak, body_len)

    def test_clone_dry_run_needs_no_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clone = root / "clone"
            (clone / "scripts" / "attachments").mkdir(parents=True)
            for name in (
                "meta_fill.py",
                "bodystructure.py",
                "mime_meta.py",
                "__init__.py",
            ):
                shutil.copy(PKG / name, clone / "scripts" / "attachments" / name)
            for name in (
                "imap_keychain.py",
                "refuse_destructive.py",
                "sor_writer_gate.py",
                "with_writer_lock.py",
            ):
                shutil.copy(SCRIPTS / name, clone / "scripts" / name)
            plain = _plain_rfc822()
            rec = json.dumps({"raw": plain.decode("utf-8")}).encode("utf-8") + b"\n"
            dump = root / "archive.jsonl"
            dump.write_bytes(rec)
            db = root / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-raw", "jsonl-import", None, 0, len(rec), "synthetic-raw", None)],
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = "scripts:scripts/attachments"
            env.pop("PYTHONHOME", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/attachments/meta_fill.py",
                    "--dry-run",
                    "--db",
                    str(db),
                    "--source",
                    "jsonl",
                    "--jsonl",
                    str(dump),
                    "--max-messages",
                    "0",
                    "--timeout",
                    "0",
                    "--max-record-bytes",
                    "64MB",
                ],
                cwd=str(clone),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("dry_run=1", proc.stdout)
            self.assertIn("messages=1", proc.stdout)
            self.assertIn("capped: 0", proc.stdout)
            self.assertIn("bytes_stored=0", proc.stdout)
            self.assertNotIn("PARTIAL:", proc.stdout)

    def test_ssl_context_is_verified_and_passed_to_the_factory(self):
        context = ssl.create_default_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        seen = {}

        class Factory:
            def __init__(self, host, port, timeout=None, ssl_context=None):
                seen["call"] = (host, port, timeout, ssl_context)

            def login(self, user, password):
                return "OK", [b"ok"]

            def logout(self):
                return "BYE", [b""]

        with mock.patch(
            "imap_bodystructure_double.ssl.create_default_context", return_value=context
        ):
            client = imap_double.ImapBodystructureClient(
                "imap.example.com",
                "user@example.com",
                timeout=5,
                imap_factory=Factory,
                password_fn=lambda: "example-secret",
            )
            with client:
                pass
        self.assertEqual(seen["call"][:3], ("imap.example.com", 993, 5))
        self.assertIs(seen["call"][3], context)
        self.assertEqual(seen["call"][3].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(seen["call"][3].check_hostname)

    def test_select_quotes_spaces_quotes_and_backslashes(self):
        class Recorder:
            def __init__(self):
                self.sent = []

            def select(self, mailbox, readonly=False):
                self.sent.append((mailbox, readonly))
                return "OK", [b"1"]

            def response(self, code):
                if str(code).upper() != "UIDVALIDITY":
                    return str(code).upper(), [None]
                return "UIDVALIDITY", [b"7"]

        client = imap_double.ImapBodystructureClient(
            "imap.example.com",
            "user@example.com",
            password_fn=lambda: "example-secret",
        )
        client._conn = Recorder()
        client.select("Deleted Messages")
        client.select('Say "hi"')
        client.select("a\\b")
        self.assertEqual(
            client._conn.sent,
            [
                ('"Deleted Messages"', True),
                ('"Say \\"hi\\""', True),
                ('"a\\\\b"', True),
            ],
        )
        self.assertEqual(client.uidvalidity, 7)

    def test_uidvalidity_comes_from_real_imaplib(self):
        """UIDVALIDITY is parsed from imaplib, not from a hand-written tuple.

        A stub that returns ``('OK', [b'42'])`` used to pass. Real
        ``IMAP4.response('UIDVALIDITY')`` returns ``('UIDVALIDITY', [b'42'])``
        and ``('UIDVALIDITY', [None])`` after the value is popped or absent.
        The old ``typ == 'OK'`` check raises ``imap uidvalidity missing`` here.
        """
        import imaplib

        port, stop, thread = _serve_imap(42)

        def factory(host, port_arg, timeout=None, ssl_context=None):
            self.assertEqual(port_arg, 993)
            return imaplib.IMAP4("127.0.0.1", port, timeout=5)

        try:
            probe = imaplib.IMAP4("127.0.0.1", port, timeout=5)
            probe.login("user@example.com", "example-secret")
            typ, _data = probe.select('"INBOX"', readonly=True)
            self.assertEqual(typ, "OK")
            got_typ, got_data = probe.response("UIDVALIDITY")
            self.assertEqual(got_typ, "UIDVALIDITY")
            self.assertEqual(got_data, [b"42"])
            again_typ, again_data = probe.response("UIDVALIDITY")
            self.assertEqual((again_typ, again_data), ("UIDVALIDITY", [None]))
            probe.logout()

            client = imap_double.ImapBodystructureClient(
                "imap.example.com",
                "user@example.com",
                timeout=5,
                imap_factory=factory,
                password_fn=lambda: "example-secret",
            )
            with client:
                client.select("INBOX")
            self.assertEqual(client.uidvalidity, 42)
        finally:
            stop.set()
            thread.join(timeout=3)

        missing_port, missing_stop, missing_thread = _serve_imap(None)
        try:
            client = imap_double.ImapBodystructureClient(
                "imap.example.com",
                "user@example.com",
                timeout=5,
                imap_factory=lambda host, port_arg, timeout=None, ssl_context=None: (
                    imaplib.IMAP4("127.0.0.1", missing_port, timeout=5)
                ),
                password_fn=lambda: "example-secret",
            )
            with client:
                with self.assertRaises(RuntimeError) as ctx:
                    client.select("INBOX")
            self.assertIn("uidvalidity", str(ctx.exception))
        finally:
            missing_stop.set()
            missing_thread.join(timeout=3)

    def test_default_context_rejects_a_self_signed_certificate(self):
        """meta_fill's create_default_context must reject a local self-signed cert."""
        import imaplib

        if shutil.which("openssl") is None:
            self.skipTest("openssl is not available")
        with tempfile.TemporaryDirectory() as tmp:
            key = str(Path(tmp) / "key.pem")
            cert = str(Path(tmp) / "cert.pem")
            proc = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    key,
                    "-out",
                    cert,
                    "-days",
                    "1",
                    "-nodes",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not Path(cert).is_file():
                self.skipTest("could not generate a throwaway certificate")

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(5)
            local_port = listener.getsockname()[1]
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.load_cert_chain(cert, key)

            def serve():
                try:
                    conn, _addr = listener.accept()
                except OSError:
                    return
                try:
                    wrapped = server_ctx.wrap_socket(conn, server_side=True)
                    wrapped.close()
                except ssl.SSLError:
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            seen = {}

            def factory(host, port_arg, timeout=None, ssl_context=None):
                seen["call"] = (host, port_arg, timeout, ssl_context)
                return imaplib.IMAP4_SSL(
                    "127.0.0.1",
                    local_port,
                    timeout=5,
                    ssl_context=ssl_context,
                )

            created = []
            real_context = ssl.create_default_context

            def spy_context():
                ctx = real_context()
                created.append(ctx)
                return ctx

            try:
                with mock.patch(
                    "imap_bodystructure_double.ssl.create_default_context", spy_context
                ):
                    client = imap_double.ImapBodystructureClient(
                        "imap.example.com",
                        "user@example.com",
                        timeout=5,
                        imap_factory=factory,
                        password_fn=lambda: "example-secret",
                    )
                    with self.assertRaises(ssl.SSLCertVerificationError) as ctx:
                        with client:
                            pass
                self.assertIn("CERTIFICATE_VERIFY_FAILED", str(ctx.exception))
                self.assertEqual(seen["call"][:3], ("imap.example.com", 993, 5))
                self.assertIs(seen["call"][3], created[0])
                self.assertEqual(created[0].verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(created[0].check_hostname)
            finally:
                thread.join(timeout=5)
                try:
                    listener.close()
                except OSError:
                    pass

    def test_max_parts_zero_is_not_a_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, dump = self._jsonl_db(tmp)
            report = meta.fill_metadata(
                db,
                source="jsonl",
                jsonl_path=dump,
                apply=True,
                max_parts=0,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(report["capped"], 0)
            self.assertEqual(report["parts_truncated"], 0)
            self.assertEqual(report["messages"], 2)
            self.assertEqual(report["has_attachments"], 1)
            self.assertEqual(report["parts"], len(EXPECTED_MIXED) + 1)
            self.assertIn("parts_truncated=0", meta.format_report(report))

    def test_uidvalidity_mismatch_does_not_write_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    (
                        "ex-deleted",
                        "imap-live",
                        "7",
                        None,
                        None,
                        "synthetic-deleted",
                        "Deleted Messages",
                    ),
                    ("ex-inbox", "imap-live", "7", None, None, "synthetic-inbox", "INBOX"),
                    (
                        "ex-inbox-2",
                        "imap-live",
                        "8",
                        None,
                        None,
                        "synthetic-inbox-2",
                        "INBOX",
                    ),
                ],
            )
            stub = _StubImap(
                {
                    ("Deleted Messages", "7"): PLAIN_BS,
                    ("INBOX", "7"): PLAIN_BS,
                    ("INBOX", "8"): PLAIN_BS,
                }
            )
            stub.uidvalidity_by_folder = {"Deleted Messages": "10", "INBOX": "20"}
            first = meta.fill_metadata(
                db,
                source="imap",
                apply=True,
                imap_client=stub,
                cmdlines=[],
                lock_held=False,
                max_messages=0,
                timeout_s=0,
            )
            self.assertEqual(first["messages"], 3)
            self.assertEqual(first["uidvalidity_mismatch"], 0)
            self.assertFalse(first["partial"])
            self.assertEqual(stub.events[0], ("select", "Deleted Messages", True))
            conn = sqlite3.connect(str(db))
            try:
                conn.execute("DELETE FROM attachment_meta_scans")
                conn.execute("DELETE FROM attachments")
                conn.execute(
                    "UPDATE messages SET has_attachments=0"
                )
                conn.commit()
                stored = dict(
                    conn.execute(
                        "SELECT folder, uidvalidity FROM attachment_folder_uidvalidity"
                    ).fetchall()
                )
            finally:
                conn.close()
            self.assertEqual(stored, {"Deleted Messages": 10, "INBOX": 20})
            stub.events.clear()
            stub.uidvalidity_by_folder = {"Deleted Messages": "10", "INBOX": "21"}
            second = meta.fill_metadata(
                db,
                source="imap",
                apply=True,
                imap_client=stub,
                cmdlines=[],
                lock_held=False,
                max_messages=0,
                timeout_s=0,
            )
            self.assertEqual(second["uidvalidity_mismatch"], 2)
            self.assertEqual(second["messages"], 1)
            self.assertEqual(second["eligible"], 3)
            self.assertTrue(second["partial"])
            self.assertEqual(
                second["partial_banner"],
                "PARTIAL: scanned 1 of 3 (uidvalidity_mismatch=2)",
            )
            text = meta.format_report(second)
            self.assertTrue(
                text.startswith("PARTIAL: scanned 1 of 3 (uidvalidity_mismatch=2)\n")
            )
            self.assertIn("uidvalidity_mismatch=2", text)
            conn = sqlite3.connect(str(db))
            try:
                done = {
                    row[0]
                    for row in conn.execute(
                        "SELECT message_id FROM attachment_meta_scans"
                    )
                }
                kept = dict(
                    conn.execute(
                        "SELECT folder, uidvalidity FROM attachment_folder_uidvalidity"
                    ).fetchall()
                )
            finally:
                conn.close()
            self.assertEqual(done, {"ex-deleted"})
            self.assertEqual(kept["INBOX"], 20)
            self.assertNotIn("ex-inbox", done)
            self.assertNotIn("ex-inbox-2", done)

    def test_keychain_env_cannot_redirect_the_password_fetch(self):
        calls = []

        def runner(binary, service):
            calls.append((binary, service))
            if service == "mailroom.imap.app-password":
                return 1, ""
            if service == "mailroom.icloud.app-password":
                return 0, "legacy-secret"
            return 0, "redirected-secret"

        env = {
            "MAILROOM_SECURITY_BIN": "/tmp/not-security",
            "MAILROOM_KEYCHAIN_ITEM": "other-item",
            "IMAP_APP_PASSWORD": "env-secret",
            "MAILROOM_IMAP_PASSWORD": "env-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            password = imap_keychain.read_imap_app_password(runner=runner)
        self.assertEqual(
            calls,
            [
                ("/usr/bin/security", "mailroom.imap.app-password"),
                ("/usr/bin/security", "mailroom.icloud.app-password"),
            ],
        )
        self.assertLess(
            calls.index(("/usr/bin/security", "mailroom.imap.app-password")),
            calls.index(("/usr/bin/security", "mailroom.icloud.app-password")),
        )
        self.assertEqual(password, "legacy-secret")
        source = (SCRIPTS / "imap_keychain.py").read_text(encoding="utf-8")
        self.assertNotIn("MAILROOM_SECURITY_BIN", source)
        self.assertNotIn("MAILROOM_KEYCHAIN_ITEM", source)
        self.assertNotIn("os.environ", source)

    def test_mailroom_security_bin_env_does_not_change_argv(self):
        """MAILROOM_SECURITY_BIN is not a binary override. Argv stays absolute."""
        seen = []

        def fake_run(args, check=False, capture_output=False, text=False):
            seen.append(list(args))
            proc = mock.Mock()
            proc.returncode = 0
            proc.stdout = "keychain-secret\n"
            proc.stderr = ""
            return proc

        env = {"MAILROOM_SECURITY_BIN": "/tmp/not-security"}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "imap_keychain.subprocess.run", fake_run
        ):
            password = imap_keychain.read_imap_app_password()
        self.assertEqual(password, "keychain-secret")
        self.assertEqual(
            seen,
            [
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    "mailroom.imap.app-password",
                    "-w",
                ]
            ],
        )

    def test_primary_argv_matches_main_and_precedes_legacy(self):
        """Primary argv is main's list. Legacy is the next call only."""
        seen = []

        def fake_run(args, check=False, capture_output=False, text=False):
            seen.append(list(args))
            proc = mock.Mock()
            proc.stderr = ""
            if len(seen) == 1:
                proc.returncode = 1
                proc.stdout = ""
            else:
                proc.returncode = 0
                proc.stdout = "legacy-secret\n"
            return proc

        primary = [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            "mailroom.imap.app-password",
            "-w",
        ]
        legacy = [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            "mailroom.icloud.app-password",
            "-w",
        ]
        with mock.patch("imap_keychain.subprocess.run", fake_run):
            password = imap_keychain.read_imap_app_password()
        self.assertEqual(seen, [primary, legacy])
        self.assertEqual(password, "legacy-secret")

    def test_empty_imap_user_does_not_call_security(self):
        import imap_curl

        def boom(*_args, **_kwargs):
            raise AssertionError("security was called")

        with mock.patch("imap_keychain.subprocess.run", boom):
            for user in ("", None, "   "):
                client = imap_curl.CurlImapsClient(
                    "imap.example.invalid", user, password_fn=None
                )
                with self.assertRaises(imap_curl.CurlImapError) as ctx:
                    client.__enter__()
                self.assertIn("imap user is required", str(ctx.exception))
                self.assertNotIn("secret", str(ctx.exception))


class CliTests(unittest.TestCase):
    def test_negative_smoke_and_mailroom_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            absent = root / "absent.sqlite"
            nested = root / "no-such-dir" / "absent.sqlite"
            sor = root / "mailroom.sqlite"
            sqlite3.connect(str(sor)).close()
            digest = sor.read_bytes()
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = meta.main(["--db", str(absent), "--source", "jsonl"])
            self.assertEqual(rc, 2)
            self.assertIn("database not found", err.getvalue())
            self.assertFalse(absent.exists())
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = meta.main(["--db", str(nested), "--source", "imap"])
            self.assertEqual(rc, 2)
            self.assertFalse(nested.exists())
            self.assertFalse(nested.parent.exists())
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = meta.main(["--db", str(sor), "--source", "imap", "--dry-run"])
            self.assertEqual(rc, 2)
            self.assertIn("mailroom.sqlite", err.getvalue())
            self.assertEqual(sor.read_bytes(), digest)
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = meta.main(
                    ["--db", str(sor), "--source", "jsonl", "--jsonl", "x", "--purge"]
                )
            self.assertEqual(rc, 2)
            self.assertIn("refuse", err.getvalue())
            self.assertEqual(sor.read_bytes(), digest)
            copy = root / "mailroom-copy.sqlite"
            sqlite3.connect(str(copy)).close()
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = meta.main(
                    [
                        "--db",
                        str(copy),
                        "--source",
                        "jsonl",
                        "--apply",
                        "--dry-run",
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertIn("only one", err.getvalue())
            with mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    meta.main([])
            self.assertEqual(ctx.exception.code, 2)
            with mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    meta.main(["--db", str(copy)])
            self.assertEqual(ctx.exception.code, 2)
            with mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    meta.main(
                        ["--db", str(copy), "--source", "imap", "--password", "nope"]
                    )
            self.assertEqual(ctx.exception.code, 2)
            self.assertFalse((root / "nope").exists())

    def test_cli_dry_run_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mixed = _mixed_rfc822()
            plain = _plain_rfc822()
            blob, first, second = _dump(mixed, plain)
            dump = root / "archive-example.jsonl"
            dump.write_bytes(blob)
            db = root / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-mixed", "jsonl-import", None, first[0], first[1], "synthetic-mixed"),
                    ("ex-plain", "jsonl-import", None, second[0], second[1], "synthetic-plain"),
                ],
            )
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                rc = meta.main(
                    ["--db", str(db), "--source", "jsonl", "--jsonl", str(dump)]
                )
            self.assertEqual(rc, 0, err.getvalue())
            text = out.getvalue()
            self.assertIn("dry_run=1", text)
            self.assertIn("bytes_stored=0", text)
            self.assertIn("db_basename=mailroom-copy.sqlite", text)
            self.assertNotIn(str(root), text)
            self.assertNotIn(MARKER, text)
            conn = sqlite3.connect(str(db))
            try:
                stored = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(stored, 0)
            out = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
                rc = meta.main(
                    [
                        "--db",
                        str(db),
                        "--source",
                        "jsonl",
                        "--jsonl",
                        str(dump),
                        "--apply",
                        "--store-filenames",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("dry_run=0", out.getvalue())
            self.assertIn("bytes_stored=0", out.getvalue())
            conn = sqlite3.connect(str(db))
            try:
                names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT filename FROM attachments WHERE filename IS NOT NULL"
                    )
                }
                flag = conn.execute(
                    "SELECT has_attachments FROM messages WHERE id='ex-mixed'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(names, {"note.pdf", "pixel.png", "forwarded.eml"})
            self.assertEqual(flag, 1)
            self.assertNotIn(MARKER, _text_blob(db))

    def test_cli_imap_uses_ssl_and_keychain_not_a_password_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-archive", "imap-live", "42", None, None, "synthetic-archive", "Archive"),
                    ("ex-inbox", "imap-live", "42", None, None, "synthetic-inbox", "INBOX"),
                ],
            )
            plain_calls = []
            seen = {"curls": []}

            def boom_imap(*_args, **_kwargs):
                plain_calls.append("imaplib")
                raise AssertionError("production opened imaplib")

            def boom_socket(*_args, **_kwargs):
                plain_calls.append("socket")
                raise AssertionError("production opened a socket")

            def fake_security(args, check=False, capture_output=False, text=False):
                seen["security"] = list(args)
                proc = mock.Mock()
                proc.returncode = 0
                proc.stdout = "keychain-secret\n"
                proc.stderr = ""
                return proc

            def fake_curl(argv, config_text, env, timeout):
                seen["curls"].append(
                    {"argv": list(argv), "config": config_text, "env": dict(env)}
                )
                payload = MIXED_BS if "Archive" in config_text else PLAIN_BS
                body = (
                    "* OK [UIDVALIDITY 8] UIDs valid\n"
                    "* 1 FETCH (UID 42 BODYSTRUCTURE %s)\n" % " ".join(payload.split())
                )
                return 0, body, ""

            out = io.StringIO()
            err = io.StringIO()
            env = {
                "MAILROOM_IMAP_HOST": "imap.example.com",
                "MAILROOM_IMAP_USER": "user@example.com",
                "IMAP_APP_PASSWORD": "env-secret",
                "MAILROOM_IMAP_PASSWORD": "env-secret",
                "MAILROOM_IMAP_PORT": "143",
                "CURL_BIN": "/tmp/not-curl",
            }
            with mock.patch("socket.create_connection", boom_socket), mock.patch(
                "sys.stderr", err
            ):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--apply"],
                    env={},
                )
            self.assertEqual(rc, 2)
            self.assertIn("imap host is required", err.getvalue())
            self.assertNotIn("env-secret", err.getvalue())
            self.assertNotIn("keychain-secret", err.getvalue())
            with mock.patch("socket.create_connection", boom_socket), mock.patch(
                "imaplib.IMAP4_SSL", boom_imap
            ), mock.patch(
                "imap_keychain.subprocess.run", fake_security
            ), mock.patch(
                "imap_curl.run_subprocess", fake_curl
            ), mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--apply"],
                    env=env,
                )
            self.assertEqual(rc, 0, out.getvalue())
            self.assertEqual(plain_calls, [])
            self.assertEqual(
                seen["security"],
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    "mailroom.imap.app-password",
                    "-w",
                ],
            )
            self.assertEqual(len(seen["curls"]), 2)
            first = seen["curls"][0]
            self.assertEqual(first["argv"][0], "/usr/bin/curl")
            self.assertEqual(first["argv"][first["argv"].index("-K") + 1], "-")
            self.assertNotIn("-v", first["argv"])
            self.assertNotIn("--verbose", first["argv"])
            self.assertNotIn("--trace", first["argv"])
            self.assertNotIn("keychain-secret", first["argv"])
            self.assertNotIn("env-secret", first["argv"])
            self.assertNotIn("keychain-secret", first["env"].values())
            self.assertNotIn("env-secret", first["env"].values())
            self.assertIn('user = "user@example.com:keychain-secret"', first["config"])
            self.assertNotIn("env-secret", out.getvalue())
            self.assertNotIn("keychain-secret", out.getvalue())
            self.assertNotIn("env-secret", _text_blob(db))
            self.assertNotIn("keychain-secret", _text_blob(db))
            self.assertIn("bytes_stored=0", out.getvalue())
            conn = sqlite3.connect(str(db))
            try:
                flags = dict(
                    conn.execute("SELECT id, has_attachments FROM messages").fetchall()
                )
            finally:
                conn.close()
            self.assertEqual(flags["ex-archive"], 1)
            self.assertEqual(flags["ex-inbox"], 0)


class DesignAndBoundaryTests(unittest.TestCase):
    def test_design_documents_the_fill_and_the_approval(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("jsonl_offset", text)
        self.assertIn("jsonl_len", text)
        self.assertIn("BODYSTRUCTURE", text)
        self.assertIn("imap-live", text)
        self.assertIn("--store-filenames", text)
        self.assertIn("--dry-run", text)
        self.assertIn("separate approval", text)
        self.assertIn("~65.5k", text)
        self.assertIn("2.1k", text)
        self.assertIn("63.4k", text)
        self.assertIn("filename", text.lower())
        self.assertIn("NULL", text)
        self.assertIn("bytes_stored", text)
        self.assertIn("IMAP4_SSL", text)
        self.assertIn("993", text)
        self.assertIn("PARTIAL", text)
        self.assertIn("attachments_pr1_empty", text)
        self.assertIn("peak memory", text.lower())
        self.assertIn("64 MiB", text)
        self.assertIn("mailroom.imap.app-password", text)
        self.assertIn("messages.folder", text)
        self.assertIn("skipped", text)
        self.assertIn("capped:", text)
        self.assertIn("full pass", text)
        self.assertIn("--max-messages 0", text)
        self.assertIn("--timeout 0", text)
        self.assertIn("64MB", text)
        self.assertIn("Deleted 980", text)
        self.assertIn("Junk 509", text)
        self.assertIn("INBOX 422", text)
        self.assertIn("Newsletters 177", text)
        self.assertIn("Sent 34", text)
        self.assertIn("PYTHONPATH=scripts:scripts/attachments", text)
        self.assertIn("`raw`", text)
        self.assertIn("does not create or alter", text)
        self.assertIn("not at ingest", text)
        self.assertIn("must be migrated again", text)
        self.assertIn("uidvalidity_mismatch=N", text)
        self.assertIn("67108864", text)
        self.assertIn("128MB", text)
        self.assertNotIn("raise --max-record-bytes to 64MB", text)
        for name in ("meta_fill.py", "bodystructure.py", "mime_meta.py"):
            source = (PKG / name).read_text(encoding="utf-8")
            self.assertNotIn("BODY[]", source, name)
            self.assertNotIn("BODY.PEEK", source, name)
            self.assertNotIn("import subprocess", source, name)
            self.assertNotIn("os.system", source, name)
            self.assertNotIn("ollama", source.lower(), name)
            self.assertNotIn("embed_lib", source, name)
            self.assertNotIn("11434", source, name)
            self.assertNotIn("DROP TABLE", source.upper(), name)
            self.assertNotIn("ALTER TABLE", source.upper(), name)
            self.assertNotIn("ask_mail", source, name)

    def test_login_error_from_the_client_is_redacted(self):
        calls = []

        def fake_curl(argv, config_text, env, timeout):
            calls.append(list(argv))
            return 67, "", "Login denied for %s" % SECRET

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "1002", None, None, "synthetic-plain", "INBOX")],
            )
            plain = mock.Mock(side_effect=AssertionError("plain IMAP4"))
            with mock.patch("imaplib.IMAP4", plain), mock.patch(
                "imaplib.IMAP4_SSL", plain
            ), mock.patch(
                "attachments.meta_fill.read_imap_app_password", return_value=SECRET
            ), mock.patch("imap_curl.run_subprocess", fake_curl):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.com",
                    user="user@example.com",
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=0,
                )
            self.assertFalse(plain.called)
            self.assertEqual(len(calls), 1)
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["messages"], 0)
            self.assertNotIn(SECRET, str(report))
            self.assertNotIn("Login denied", str(report))
            self.assertTrue(
                any("67" in item for item in report["curl_failures"])
            )
            self.assertNotIn(SECRET, _text_blob(db))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)


class CurlTransportTests(unittest.TestCase):
    """Production path is /usr/bin/curl imaps://. No live mailbox."""

    def test_curl_argv_shape_keeps_the_password_out_of_argv_and_env(self):
        import imap_curl as imap_curl

        captured = []
        password = 'p"q\\r'
        env_password = "env-secret-token"

        def runner(argv, config_text, env, timeout):
            captured.append(
                {"argv": list(argv), "config": config_text, "env": dict(env)}
            )
            return (
                0,
                "* OK [UIDVALIDITY 42] UIDs valid\n"
                '* 1 FETCH (UID 9 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\n',
                "",
            )

        with mock.patch.dict(
            os.environ,
            {
                "CURL_BIN": "/opt/homebrew/opt/curl/bin/curl",
                "MAILROOM_IMAP_PASSWORD": env_password,
                "IMAP_APP_PASSWORD": env_password,
            },
            clear=False,
        ):
            client = imap_curl.CurlImapsClient(
                "imap.example.invalid",
                "user@example.invalid",
                timeout=30,
                password_fn=lambda: password,
                runner=runner,
            )
            with client:
                client.open_folder("Deleted Messages", ["9"])
        self.assertEqual(len(captured), 1)
        argv = captured[0]["argv"]
        self.assertEqual(
            argv,
            ["/usr/bin/curl", "--silent", "--show-error", "--fail-early", "-K", "-"],
        )
        self.assertEqual(argv[argv.index("-K") + 1], "-")
        self.assertNotIn("-k", argv)
        self.assertNotIn("--insecure", argv)
        self.assertNotIn("--cacert", argv)
        self.assertNotIn("-v", argv)
        self.assertNotIn("--verbose", argv)
        self.assertNotIn("--trace", argv)
        self.assertNotIn(password, argv)
        self.assertNotIn(env_password, argv)
        self.assertNotIn("homebrew", " ".join(argv))
        self.assertNotIn("CURL_BIN", captured[0]["env"])
        self.assertNotIn("IMAP_APP_PASSWORD", captured[0]["env"])
        self.assertNotIn(password, captured[0]["env"].values())
        self.assertNotIn(env_password, captured[0]["env"].values())
        config = captured[0]["config"]
        self.assertIn('user = "user@example.invalid:p\\"q\\\\r"', config)
        self.assertIn('url = "imaps://imap.example.invalid:993/"', config)
        self.assertNotIn("Deleted%20Messages", config)
        self.assertNotIn("/Deleted", config)
        self.assertIn('request = "EXAMINE \\"Deleted Messages\\""', config)
        self.assertIn('request = "UID FETCH 9 (BODYSTRUCTURE)"', config)
        blocks = imap_curl.config_blocks(config)
        self.assertEqual(len(blocks), 2)
        self.assertNotIn("dump-header", config)
        for block in blocks:
            self.assertIn("user = ", block)
            self.assertIn('connect-timeout = "30"', block)
            self.assertIn('max-time = "30"', block)
            self.assertIn("write-out", block)
            self.assertIn("num_connects", block)
        self.assertEqual(client.uidvalidity, 42)
        text = client.fetch_bodystructure("9")
        self.assertEqual(len(captured), 1)
        self.assertIn("TEXT", text)

    def test_quoted_folders_stay_out_of_the_url(self):
        import imap_curl as imap_curl

        cases = [
            ("Deleted Messages", '"Deleted Messages"'),
            ('Say "hi"', '"Say \\"hi\\""'),
            ("a\\b", '"a\\\\b"'),
        ]
        url = imap_curl.base_url("imap.example.invalid", 993)
        self.assertEqual(url, "imaps://imap.example.invalid:993/")
        for mailbox, quoted in cases:
            command = imap_curl.examine_command(mailbox)
            self.assertEqual(command, "EXAMINE " + quoted)
            block = imap_curl.transfer_block(
                user="user@example.invalid",
                password="example-secret",
                command=command,
                url=url,
                timeout_s=30,
                cacert=None,
            )
            self.assertIn("EXAMINE ", block)
            self.assertIn(quoted.replace("\\", "\\\\").replace('"', '\\"'), block)
            self.assertIn('url = "imaps://imap.example.invalid:993/"', block)
            self.assertNotIn("Deleted%20", block)
            self.assertNotIn("%20", block)
            self.assertNotIn("%22", block)
            self.assertNotIn("%5C", block)
        argv = imap_curl.curl_argv()
        self.assertEqual(argv[0], "/usr/bin/curl")
        self.assertEqual(argv[argv.index("-K") + 1], "-")
        self.assertNotIn("-k", argv)
        self.assertNotIn("--insecure", argv)
        fetch = imap_curl.fetch_command("9")
        self.assertEqual(fetch, "UID FETCH 9 (BODYSTRUCTURE)")

    @hermetic_binaries.allow_real_curl("127.0.0.1")
    def test_real_curl_parses_uidvalidity_and_bodystructure(self):
        """fill_metadata EXAMINEs, reads UIDVALIDITY, and UID FETCHes via real curl."""
        import imap_curl as imap_curl

        if not os.access("/usr/bin/curl", os.X_OK):
            self.skipTest("curl is not available")
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not available")
        with tempfile.TemporaryDirectory() as tmp:
            key = str(Path(tmp) / "key.pem")
            cert = str(Path(tmp) / "cert.pem")
            proc = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    key,
                    "-out",
                    cert,
                    "-days",
                    "1",
                    "-nodes",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not Path(cert).is_file():
                self.skipTest("could not generate a throwaway certificate")
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert, key)
            except ssl.SSLError:
                self.skipTest("TLS is not available")

            validity = {
                "Deleted Messages": 11,
                'Say "hi"': 22,
                "a\\b": 33,
            }
            needles = [
                (imap_curl.quote_imap_mailbox(name).encode("utf-8"), value)
                for name, value in validity.items()
            ]
            seen = []
            stats = {"connections": 0, "logins": 0, "selects": 0, "examines": 0}
            lock = threading.Lock()

            def handler(conn):
                buf = b""
                try:
                    conn.sendall(b"* OK IMAP4rev1 ready\r\n")
                    while True:
                        data = conn.recv(8192)
                        if not data:
                            return
                        buf += data
                        while b"\r\n" in buf:
                            line, buf = buf.split(b"\r\n", 1)
                            text = line.decode("utf-8", "replace")
                            with lock:
                                seen.append(text)
                            parts = line.split(b" ", 2)
                            tag = parts[0]
                            cmd = parts[1].upper() if len(parts) > 1 else b""
                            arg = parts[2] if len(parts) > 2 else b""
                            if cmd == b"CAPABILITY":
                                conn.sendall(
                                    b"* CAPABILITY IMAP4rev1\r\n"
                                    + tag
                                    + b" OK CAPABILITY completed\r\n"
                                )
                            elif cmd == b"LOGIN":
                                with lock:
                                    stats["logins"] += 1
                                conn.sendall(tag + b" OK LOGIN completed\r\n")
                            elif cmd == b"SELECT":
                                with lock:
                                    stats["selects"] += 1
                                number = b"11"
                                for needle, value in needles:
                                    if needle in line:
                                        number = str(value).encode("ascii")
                                        break
                                conn.sendall(
                                    b"* 1 EXISTS\r\n* OK [UIDVALIDITY "
                                    + number
                                    + b"] UIDs valid\r\n"
                                    + tag
                                    + b" OK [READ-WRITE] SELECT completed\r\n"
                                )
                            elif cmd == b"EXAMINE":
                                with lock:
                                    stats["examines"] += 1
                                number = b"11"
                                for needle, value in needles:
                                    if needle in line:
                                        number = str(value).encode("ascii")
                                        break
                                conn.sendall(
                                    b"* 1 EXISTS\r\n* OK [UIDVALIDITY "
                                    + number
                                    + b"] UIDs valid\r\n* OK [PERMANENTFLAGS ()] "
                                    b"Read-only\r\n"
                                    + tag
                                    + b" OK [READ-ONLY] EXAMINE completed\r\n"
                                )
                            elif cmd == b"UID":
                                conn.sendall(
                                    b'* 1 FETCH (UID 9 BODYSTRUCTURE ("TEXT" "PLAIN" '
                                    b'NIL NIL NIL "7BIT" 4 1))\r\n'
                                    b"* OK [UIDVALIDITY 99] not the examine value\r\n"
                                    + tag
                                    + b" OK UID FETCH completed\r\n"
                                )
                            elif cmd == b"LOGOUT":
                                conn.sendall(b"* BYE\r\n" + tag + b" OK LOGOUT\r\n")
                                return
                            else:
                                conn.sendall(tag + b" BAD\r\n")
                except Exception:
                    return
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(8)
            listener.settimeout(0.5)
            port = listener.getsockname()[1]
            stop = threading.Event()

            def loop():
                while not stop.is_set():
                    try:
                        conn, _addr = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    try:
                        wrapped = ctx.wrap_socket(conn, server_side=True)
                    except ssl.SSLError:
                        try:
                            conn.close()
                        except OSError:
                            pass
                        continue
                    with lock:
                        stats["connections"] += 1
                    handler(wrapped)

            thread = threading.Thread(target=loop, daemon=True)
            thread.start()
            calls = []
            real_run = imap_curl.run_subprocess

            def spy(argv, config_text, env, timeout):
                rc, out, err = real_run(argv, config_text, env, timeout)
                calls.append(
                    {
                        "argv": list(argv),
                        "config": config_text,
                        "env": dict(env),
                        "stdout": out,
                    }
                )
                return rc, out, err

            def boom(*_args, **_kwargs):
                raise AssertionError("fill opened imaplib or a python socket")

            rows = [
                ("ex-deleted", "imap-live", "9", None, None, "synthetic", "Deleted Messages"),
                ("ex-quote", "imap-live", "9", None, None, "synthetic", 'Say "hi"'),
                ("ex-slash", "imap-live", "9", None, None, "synthetic", "a\\b"),
            ]
            main_src = (PKG / "meta_fill.py").read_text(encoding="utf-8").split(
                "def main(", 1
            )[1]
            self.assertNotIn("cacert", main_src.split("\ndef ", 1)[0])
            try:
                bare = Path(tmp) / "bare.sqlite"
                _seed(bare, rows)
                with mock.patch("imap_curl.run_subprocess", spy), mock.patch(
                    "imaplib.IMAP4_SSL", boom
                ), mock.patch("socket.create_connection", boom):
                    denied = meta.fill_metadata(
                        bare,
                        source="imap",
                        apply=True,
                        host="127.0.0.1",
                        user="user@example.invalid",
                        password_fn=lambda: "example-secret",
                        imap_port=port,
                        cmdlines=[],
                        lock_held=False,
                        max_messages=0,
                        timeout_s=15,
                    )
                self.assertNotIn("example-secret", str(denied))
                self.assertEqual(denied["errors"], 3)
                self.assertEqual(denied["messages"], 0)
                self.assertEqual(len(denied["curl_failures"]), 3)
                self.assertTrue(all("60" in item for item in denied["curl_failures"]))
                self.assertTrue(all("certificate" in item for item in denied["curl_failures"]))
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[0]["argv"][0], "/usr/bin/curl")
                self.assertNotIn("--cacert", calls[0]["argv"])
                self.assertNotIn("-k", calls[0]["argv"])
                self.assertNotIn("--insecure", calls[0]["argv"])
                self.assertNotIn("example-secret", calls[0]["argv"])
                self.assertNotIn("example-secret", calls[0]["env"].values())
                conn = sqlite3.connect(str(bare))
                try:
                    scans = conn.execute(
                        "SELECT COUNT(*) FROM attachment_meta_scans"
                    ).fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(scans, 0)
                calls.clear()
                seen.clear()
                for key in stats:
                    stats[key] = 0

                db = Path(tmp) / "mailroom-copy.sqlite"
                _seed(db, rows)
                with mock.patch("imap_curl.run_subprocess", spy), mock.patch(
                    "imaplib.IMAP4_SSL", boom
                ), mock.patch("socket.create_connection", boom):
                    report = meta.fill_metadata(
                        db,
                        source="imap",
                        apply=True,
                        host="127.0.0.1",
                        user="user@example.invalid",
                        password_fn=lambda: "example-secret",
                        imap_port=port,
                        cacert=cert,
                        cmdlines=[],
                        lock_held=False,
                        max_messages=0,
                        timeout_s=15,
                    )
                self.assertEqual(report["messages"], 3)
                self.assertEqual(report["parts"], 3)
                self.assertEqual(report["bytes_stored"], 0)
                self.assertEqual(report["uidvalidity_mismatch"], 0)
                self.assertEqual(report["errors"], 0)
                self.assertEqual(len(calls), 3)
                commands = []
                for item in calls:
                    self.assertEqual(item["argv"][0], "/usr/bin/curl")
                    self.assertEqual(item["argv"][item["argv"].index("-K") + 1], "-")
                    self.assertNotIn("--cacert", item["argv"])
                    self.assertNotIn("-k", item["argv"])
                    self.assertNotIn("--insecure", item["argv"])
                    self.assertNotIn("-v", item["argv"])
                    self.assertNotIn("--verbose", item["argv"])
                    self.assertNotIn("--trace", item["argv"])
                    self.assertNotIn("example-secret", item["argv"])
                    self.assertNotIn("example-secret", item["env"].values())
                    self.assertNotIn("Deleted%20Messages", item["config"])
                    self.assertIn(
                        'url = "imaps://127.0.0.1:%s/"' % port, item["config"]
                    )
                    blocks = [
                        block
                        for block in item["config"].split("\nnext\n")
                        if block.strip()
                    ]
                    self.assertGreaterEqual(len(blocks), 2)
                    for block in blocks:
                        self.assertIn(
                            'user = "user@example.invalid:example-secret"', block
                        )
                        self.assertIn("connect-timeout", block)
                        self.assertIn("max-time", block)
                        self.assertIn("cacert", block)
                        self.assertIn(cert, block)
                        self.assertIn("write-out", block)
                        self.assertIn("user = ", block)
                    self.assertNotIn("dump-header", item["config"])
                    self.assertEqual(item["config"].count("\nnext\n"), len(blocks) - 1)
                    for line in item["config"].splitlines():
                        if line.startswith("request = "):
                            commands.append(line)
                    self.assertIn("\r\n", item["stdout"])
                    self.assertIn('("TEXT" "PLAIN"', item["stdout"])
                    self.assertNotIn("dump-header", item["stdout"])
                joined = "\n".join(commands)
                self.assertIn('EXAMINE \\"Deleted Messages\\"', joined)
                self.assertIn("EXAMINE", joined)
                self.assertEqual(joined.count("UID FETCH 9 (BODYSTRUCTURE)"), 3)
                transcript = "\n".join(seen)
                self.assertEqual(stats["connections"], 3)
                self.assertEqual(stats["logins"], 3)
                self.assertEqual(stats["examines"], 3)
                self.assertEqual(stats["selects"], 0)
                self.assertEqual(len(calls), 3)
                self.assertIn('EXAMINE "Deleted Messages"', transcript)
                self.assertNotIn("SELECT", transcript)
                self.assertNotIn("READ-WRITE", transcript)
                self.assertIn('EXAMINE "Say \\"hi\\""', transcript)
                self.assertIn('EXAMINE "a\\\\b"', transcript)
                self.assertIn("UID FETCH 9 (BODYSTRUCTURE)", transcript)
                conn = sqlite3.connect(str(db))
                try:
                    stored = dict(
                        conn.execute(
                            "SELECT folder, uidvalidity FROM attachment_folder_uidvalidity"
                        ).fetchall()
                    )
                    parts = conn.execute(
                        "SELECT message_id, mime, size FROM attachments ORDER BY message_id"
                    ).fetchall()
                    scans = conn.execute(
                        "SELECT COUNT(*) FROM attachment_meta_scans"
                    ).fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(
                    stored,
                    {"Deleted Messages": 11, 'Say "hi"': 22, "a\\b": 33},
                )
                self.assertEqual(scans, 3)
                self.assertEqual(len(parts), 3)
                self.assertTrue(all(row[1] == "text/plain" and row[2] == 4 for row in parts))
            finally:
                stop.set()
                try:
                    listener.close()
                except OSError:
                    pass
                thread.join(timeout=3)

    def test_production_fill_never_constructs_imaplib_or_a_socket(self):
        """CLI fill stays on curl when imaplib and sockets are poisoned.

        On dd2f47cd this fails: fill_metadata constructs imaplib.IMAP4_SSL,
        the poison raises, and the command exits 2 with
        ``error: imap login failed``.
        """
        raised = []

        def boom(*_args, **_kwargs):
            raised.append("called")
            raise AssertionError("production opened imaplib or a socket")

        def fake_security(args, check=False, capture_output=False, text=False):
            proc = mock.Mock()
            proc.returncode = 0
            proc.stdout = "keychain-secret\n"
            proc.stderr = ""
            return proc

        def fake_curl(argv, config_text, env, timeout):
            return (
                0,
                "* OK [UIDVALIDITY 5] UIDs valid\n"
                '* 1 FETCH (UID 9 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\n',
                "",
            )

        patches = [
            mock.patch("imaplib.IMAP4_SSL", boom),
            mock.patch("socket.create_connection", boom),
            mock.patch("imap_keychain.subprocess.run", fake_security),
        ]
        try:
            import imap_curl as imap_curl  # noqa: F401
        except ImportError:
            imap_curl = None
        else:
            patches.append(
                mock.patch("imap_curl.run_subprocess", fake_curl)
            )

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "9", None, None, "synthetic-plain", "INBOX")],
            )
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                active = []
                for item in patches:
                    active.append(item)
                    item.start()
                try:
                    rc = meta.main(
                        [
                            "--db",
                            str(db),
                            "--source",
                            "imap",
                            "--apply",
                            "--max-messages",
                            "0",
                            "--timeout",
                            "0",
                        ],
                        env={
                            "MAILROOM_IMAP_HOST": "imap.example.invalid",
                            "MAILROOM_IMAP_USER": "user@example.invalid",
                            "CURL_BIN": "/tmp/not-curl",
                        },
                    )
                finally:
                    for item in reversed(active):
                        item.stop()
            self.assertEqual(rc, 0, err.getvalue())
            self.assertEqual(raised, [])
            self.assertIn("messages=1", out.getvalue())
            self.assertNotIn("keychain-secret", out.getvalue())
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
                stored = conn.execute(
                    "SELECT uidvalidity FROM attachment_folder_uidvalidity WHERE folder='INBOX'"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(scans, 1)
            self.assertEqual(stored[0], 5)

    def test_curl_error_fails_closed_without_retry(self):
        import imap_curl as imap_curl

        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(list(argv))
            return 67, "", "Login denied %s\nErrno 9 Bad file descriptor" % SECRET

        client = imap_curl.CurlImapsClient(
            "imap.example.invalid",
            "user@example.invalid",
            timeout=5,
            password_fn=lambda: SECRET,
            runner=runner,
        )
        with client:
            with self.assertRaises(imap_curl.CurlImapError) as ctx:
                client.select("INBOX")
            with self.assertRaises(imap_curl.CurlImapError):
                client.fetch_bodystructure("9")
        self.assertEqual(len(calls), 1)
        self.assertIn("errno 9", str(ctx.exception))
        self.assertIn("not retrying", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

        calls.clear()

        def auth_runner(argv, config_text, env, timeout):
            calls.append(1)
            return 67, "", "Login denied"

        client = imap_curl.CurlImapsClient(
            "imap.example.invalid",
            "user@example.invalid",
            timeout=5,
            password_fn=lambda: SECRET,
            runner=auth_runner,
        )
        with client:
            with self.assertRaises(imap_curl.CurlImapError) as ctx:
                client.select("Deleted Messages")
        self.assertEqual(calls, [1])
        self.assertIn("authentication failed", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

        calls.clear()

        def rc_runner(argv, config_text, env, timeout):
            calls.append(1)
            return 56, "", "curl failed %s" % SECRET

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "9", None, None, "synthetic-plain", "INBOX")],
            )
            with mock.patch("imap_curl.run_subprocess", rc_runner), mock.patch(
                "attachments.meta_fill.read_imap_app_password", return_value=SECRET
            ):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=0,
                )
            self.assertEqual(calls, [1])
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["messages"], 0)
            self.assertEqual(
                report["curl_failures"],
                ["imap curl failed (rc 56); not retrying"],
            )
            self.assertNotIn(SECRET, str(report))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)

    def test_missing_curl_binary_fails_closed_without_fallback(self):
        """Real subprocess against a missing binary. No imaplib fallback."""
        import imap_curl as imap_curl

        calls = []

        def boom(*_args, **_kwargs):
            raise AssertionError("fill fell back to imaplib or a python socket")

        def boom_exec(argv, **kwargs):
            calls.append(list(argv))
            if list(argv)[0] != "/usr/bin/curl":
                raise AssertionError("fallback client %s" % argv[0])
            raise FileNotFoundError(2, "curl missing")

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "9", None, None, "synthetic-plain", "INBOX")],
            )
            with mock.patch.dict(
                os.environ, {"CURL_BIN": "/opt/homebrew/opt/curl/bin/curl"}, clear=False
            ), mock.patch("imap_curl.subprocess.run", boom_exec), mock.patch(
                "imaplib.IMAP4_SSL", boom
            ), mock.patch("socket.create_connection", boom):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    password_fn=lambda: "example-secret",
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=5,
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "/usr/bin/curl")
            self.assertNotIn("/opt/homebrew", " ".join(calls[0]))
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["messages"], 0)
            self.assertIn("failed closed", " ".join(report["curl_failures"]))
            self.assertNotIn("example-secret", str(report))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)

    @hermetic_binaries.allow_real_curl("127.0.0.1")
    def test_real_curl_connection_refused_fails_closed_without_retry(self):
        """Real /usr/bin/curl to a closed local port. One attempt, no fallback."""
        import imap_curl as imap_curl

        if not os.access("/usr/bin/curl", os.X_OK):
            self.skipTest("curl is not available")
        calls = []
        real_run = imap_curl.run_subprocess

        def spy(argv, config_text, env, timeout):
            calls.append(list(argv))
            return real_run(argv, config_text, env, timeout)

        def boom(*_args, **_kwargs):
            raise AssertionError("fill fell back to imaplib or a python socket")

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "9", None, None, "synthetic-plain", "INBOX")],
            )
            with mock.patch("imap_curl.run_subprocess", spy), mock.patch(
                "imaplib.IMAP4_SSL", boom
            ), mock.patch("socket.create_connection", boom):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="127.0.0.1",
                    user="user@example.invalid",
                    password_fn=lambda: "example-secret",
                    imap_port=port,
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=5,
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "/usr/bin/curl")
            failed = " ".join(report["curl_failures"])
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["messages"], 0)
            self.assertIn("connect", failed)
            self.assertIn("7", failed)
            self.assertIn("imap curl failed", failed)
            self.assertNotIn("example-secret", calls[0])
            self.assertNotIn("example-secret", str(report))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)

    def test_real_security_binary_missing_fails_closed_before_curl(self):
        """A missing security path fails closed. The real binary is never called."""
        calls = []

        def spy(argv, config_text, env, timeout):
            calls.append(list(argv))
            raise AssertionError("curl started")

        def boom(*_args, **_kwargs):
            raise AssertionError("fill fell back to imaplib or a python socket")

        missing = "/tmp/mailroom-missing-security"
        self.assertNotEqual(missing, "/usr/bin/security")
        env = {
            "IMAP_APP_PASSWORD": "env-secret",
            "MAILROOM_IMAP_PASSWORD": "env-secret",
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "9", None, None, "synthetic-plain", "INBOX")],
            )
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "imap_keychain._SECURITY_BIN", missing
            ), mock.patch("imap_curl.run_subprocess", spy), mock.patch(
                "imaplib.IMAP4_SSL", boom
            ), mock.patch("socket.create_connection", boom):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=5,
                )
            rc, out = imap_keychain._run_security(
                missing, "mailroom.imap.app-password"
            )
            self.assertNotEqual(rc, 0)
            self.assertEqual(out, "")
            self.assertEqual(calls, [])
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["messages"], 0)
            self.assertIn("keychain", " ".join(report["curl_failures"]))
            self.assertNotIn("env-secret", str(report))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, 0)

    def test_batch_is_one_curl_and_a_failed_batch_is_not_split(self):
        import imap_curl as imap_curl

        uids = [str(number) for number in range(1, 61)]
        sets = imap_curl.batch_uid_sets(uids, 50)
        self.assertEqual(sets, ["1:50", "51:60"])
        self.assertEqual(
            imap_curl.compress_uid_set([77] + list(range(1, 51))), "1:50,77"
        )
        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(config_text)
            return 21, "", "NO"

        client = imap_curl.CurlImapsClient(
            "imap.example.invalid",
            "user@example.invalid",
            timeout=5,
            password_fn=lambda: SECRET,
            runner=runner,
            batch_size=50,
        )
        with client:
            with self.assertRaises(imap_curl.CurlImapError) as ctx:
                client.open_folder("INBOX", ["1", "2", "3"])
        self.assertEqual(len(calls), 1)
        self.assertIn("UID FETCH 1:3 (BODYSTRUCTURE)", calls[0])
        self.assertEqual(calls[0].count("\nnext\n"), 1)
        self.assertIn("rc 21", str(ctx.exception))
        self.assertIn("not retrying", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_allowlist_timeouts_and_stderr_redaction(self):
        import imap_curl as imap_curl

        imap_curl.allow_command('EXAMINE "INBOX"')
        imap_curl.allow_command("UID FETCH 1:50,77 (BODYSTRUCTURE)")
        for command in (
            "SELECT INBOX",
            'SELECT "Deleted Messages"',
            "UID FETCH 1 (BODY[1])",
            "UID FETCH 1 (BODY[])",
            "UID FETCH 1 (RFC822)",
            "UID FETCH 1 (BODY.PEEK[])",
            "UID STORE 1 +FLAGS (\\Seen)",
            "EXPUNGE",
            'UID COPY 1 "INBOX"',
            'UID MOVE 1 "INBOX"',
            "DELETE INBOX",
            "UID DELETE 1",
            "CLOSE",
            "EXAMINE INBOX",
        ):
            with self.assertRaises(imap_curl.CurlImapError):
                imap_curl.allow_command(command)
        for name in ("a\nb", "a\rb", "a\x00b"):
            with self.assertRaises(imap_curl.CurlImapError) as ctx:
                imap_curl.examine_command(name)
            self.assertIn("invalid", str(ctx.exception))
            self.assertNotIn(name, str(ctx.exception))
        with self.assertRaises(imap_curl.CurlImapError):
            imap_curl.guard_curl_argv(["/opt/homebrew/opt/curl/bin/curl", "-K", "-"])
        with self.assertRaises(imap_curl.CurlImapError):
            imap_curl.guard_curl_argv(["/usr/bin/curl", "--verbose", "-K", "-"])
        with self.assertRaises(imap_curl.CurlImapError):
            imap_curl.guard_curl_argv(["/usr/bin/curl", "--trace", "-", "-K", "-"])
        redacted = imap_curl.redact_stderr(
            "Login denied %s\n" % SECRET, SECRET, "user@example.invalid"
        )
        self.assertNotIn(SECRET, redacted)
        self.assertIn("[redacted]", redacted)
        codes = {
            21: "no or bad",
            7: "connect",
            60: "certificate",
            67: "authentication failed",
            28: "timeout",
        }
        for rc, needle in codes.items():
            with self.assertRaises(imap_curl.CurlImapError) as ctx:
                imap_curl._raise_for_status(rc, "boom %s" % SECRET, SECRET, "user")
            self.assertIn(needle, str(ctx.exception))
            self.assertIn("rc %s" % rc, str(ctx.exception))
            self.assertIn("not retrying", str(ctx.exception))
            self.assertNotIn(SECRET, str(ctx.exception))
            self.assertNotIn("boom", str(ctx.exception))
            self.assertEqual(ctx.exception.rc, rc)

    def test_missing_uid_in_a_batch_reply_is_an_error(self):
        import imap_curl as imap_curl

        literal = b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1)'
        line = "* 1 FETCH (UID 1 BODYSTRUCTURE {%s}\n" % len(literal)
        doubled = (
            "* OK [UIDVALIDITY 5] UIDs valid\n"
            "* OK [UIDVALIDITY 5] UIDs valid\n"
            + line
            + line
            + literal.decode("ascii")
            + ")\n"
        )
        self.assertEqual(imap_curl.uidvalidity_from_curl_output(doubled), 5)
        found = imap_curl.structures_by_uid(doubled)
        self.assertEqual(set(found), {"1"})
        self.assertIn("TEXT", found["1"])
        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(1)
            blocks = imap_curl.config_blocks(config_text)
            for block in blocks:
                self.assertIn("user = ", block)
                self.assertIn("connect-timeout", block)
                self.assertIn("max-time", block)
            return 0, doubled, "noise %s" % SECRET

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-1", "imap-live", "1", None, None, "synthetic", "INBOX"),
                    ("ex-2", "imap-live", "2", None, None, "synthetic", "INBOX"),
                ],
            )
            with mock.patch("imap_curl.run_subprocess", runner):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    password_fn=lambda: SECRET,
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=5,
                )
            self.assertEqual(calls, [1])
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["capped"], 0)
            self.assertEqual(
                report["messages"] + report["errors"] + report["capped"],
                report["eligible"],
            )
            self.assertNotIn(SECRET, str(report))
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT message_id FROM attachment_meta_scans"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(scans, [("ex-1",)])

    def test_uidvalidity_comes_from_the_examine_reply_only(self):
        import imap_curl as imap_curl

        text = (
            "* OK [UIDVALIDITY 5] UIDs valid\r\n"
            '* 1 FETCH (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\r\n'
            "* OK [UIDVALIDITY 99] after fetch\r\n"
        )
        self.assertEqual(imap_curl.uidvalidity_from_curl_output(text), 5)
        later_only = (
            '* 1 FETCH (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\r\n'
            "* OK [UIDVALIDITY 99] after fetch\r\n"
        )
        with self.assertRaises(imap_curl.CurlImapError):
            imap_curl.uidvalidity_from_curl_output(later_only)

    def test_password_in_argv_is_refused_before_curl_starts(self):
        import imap_curl as imap_curl

        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(1)
            return 0, "", ""

        def bad_argv():
            return ["/usr/bin/curl", "--silent", "--show-error", "-K", "-", SECRET]

        client = imap_curl.CurlImapsClient(
            "imap.example.invalid",
            "user@example.invalid",
            timeout=5,
            password_fn=lambda: SECRET,
            runner=runner,
        )
        with mock.patch("imap_curl.curl_argv", bad_argv):
            with client:
                with self.assertRaises(imap_curl.CurlImapError) as ctx:
                    client.select("INBOX")
        self.assertEqual(calls, [])
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_second_connection_fails_closed(self):
        import imap_curl as imap_curl

        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(config_text)
            return (
                0,
                "* OK [UIDVALIDITY 5] UIDs valid\r\n"
                "CURL_NUM_CONNECTS:1\r\n"
                "CURL_NUM_CONNECTS:1\r\n",
                "",
            )

        client = imap_curl.CurlImapsClient(
            "imap.example.invalid",
            "user@example.invalid",
            timeout=5,
            password_fn=lambda: SECRET,
            runner=runner,
        )
        with client:
            with self.assertRaises(imap_curl.CurlImapError) as ctx:
                client.select("INBOX")
        self.assertEqual(len(calls), 1)
        self.assertIn("second connection", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_real_curl_fixture_isolates_a_dropped_literal(self):
        """Captured by: /usr/bin/curl --silent --show-error --fail-early -K - (stdin config, no dump-header). Two blocks joined by a line that is next. Each block sets silent, show-error, connect-timeout = "20", max-time = "20", write-out = "CURL_NUM_CONNECTS:%{num_connects}\\n", cacert = "cert.pem", user = "user@example.invalid:example-secret", url = "imaps://127.0.0.1:PORT/". First request = "EXAMINE \\"INBOX\\"". Second request = "UID FETCH 1,9 (BODYSTRUCTURE)". The local server sends UID 1 as a quoted structure and UID 9 as an {8} filename. The quoted UID parses. The literal UID is literal_dropped."""
        import imap_curl as mod; read_validity = mod.uidvalidity_from_curl_output; split = mod.parse_fetch_structures

        raw = (TESTS / "fixtures" / "curl_imaps_bodystructure_literal.bin").read_bytes()
        self.assertNotIn(b"note.pdf", raw)
        self.assertIn(b'("NAME" {8}', raw)
        self.assertIn(b"CURL_NUM_CONNECTS:1\n", raw)
        self.assertIn(b"CURL_NUM_CONNECTS:0\n", raw)
        self.assertNotIn(b"example-secret", raw)
        text = raw.decode("utf-8", "surrogateescape")
        self.assertIn("\r\n", text)
        self.assertEqual(read_validity(text), 11)
        found, issues = split(text)
        self.assertEqual(issues.get("9"), "literal_dropped")
        self.assertNotIn("9", found)
        sibling = bodystructure.parts_from_bodystructure(found["1"])
        self.assertEqual(sibling[0].mime, "text/plain")
        sample = (
            "* OK [UIDVALIDITY 5] UIDs valid\r\n"
            "* 1 FETCH (UID 9 BODYSTRUCTURE "
            '("APPLICATION" "PDF" ("NAME" {8}\r\n'
            "note.pdf) NIL NIL \"BASE64\" 4 NIL "
            '("ATTACHMENT" ("FILENAME" {8}\r\n'
            "note.pdf)) NIL NIL))\r\n"
            "* OK [UIDVALIDITY 99] after fetch\r\n"
        )
        self.assertEqual(read_validity(sample), 5)
        sample_found, sample_issues = split(sample)
        self.assertEqual(sample_issues, {})
        sample_parts = bodystructure.parts_from_bodystructure(sample_found["9"])
        self.assertEqual(sample_parts[0].filename, "note.pdf")

        def runner(argv, config_text, env, timeout):
            self.assertNotIn("dump-header", config_text)
            self.assertNotIn(SECRET, argv)
            return 0, text, ""

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-plain", "imap-live", "1", None, None, "synthetic", "INBOX"),
                    ("ex-lit", "imap-live", "9", None, None, "synthetic", "INBOX"),
                ],
            )
            with mock.patch("imap_curl.run_subprocess", runner):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    store_filenames=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    password_fn=lambda: SECRET,
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=5,
                )
            self.assertEqual(report["errors"], 1, report.get("curl_failures"))
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["literal_dropped"], 1)
            self.assertEqual(report["literal_truncated"], 0)
            self.assertEqual(report["bytes_stored"], 0)
            self.assertEqual(
                report["literal_folders"],
                [
                    {
                        "index": 1,
                        "literal_dropped": 1,
                        "literal_truncated": 0,
                        "dropped_uids": [9],
                        "truncated_uids": [],
                    }
                ],
            )
            rendered = meta.format_report(report)
            self.assertIn("literal_dropped=1", rendered)
            self.assertIn("literal_folder index=1", rendered)
            self.assertIn("dropped_uids=9", rendered)
            self.assertNotIn("INBOX", rendered)
            self.assertNotIn("ex-plain", rendered)
            self.assertNotIn("ex-lit", rendered)
            self.assertNotIn("note.pdf", rendered)
            self.assertNotIn(SECRET, rendered)
            conn = sqlite3.connect(str(db))
            try:
                row = conn.execute(
                    "SELECT mime, filename FROM attachments"
                ).fetchone()
                scans = conn.execute(
                    "SELECT message_id FROM attachment_meta_scans"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(row, ("text/plain", None))
            self.assertEqual(scans, [("ex-plain",)])

    def test_folder_progress_line_is_flushed_without_the_folder_name(self):
        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(config_text)
            if 'EXAMINE \\"Archive\\"' in config_text:
                return 21, "", "boom %s" % SECRET
            return (
                0,
                "* OK [UIDVALIDITY 5] UIDs valid\r\n"
                '* 1 FETCH (UID 42 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\r\n',
                "",
            )

        class _Err(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushes = 0

            def flush(self):
                self.flushes += 1
                super().flush()

        err = _Err()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-inbox", "imap-live", "42", None, None, "synthetic", "INBOX"),
                    ("ex-archive", "imap-live", "42", None, None, "synthetic", "Archive"),
                ],
            )
            with mock.patch("imap_curl.run_subprocess", runner), mock.patch(
                "sys.stderr", err
            ):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    password_fn=lambda: SECRET,
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=0,
                )
        lines = [line for line in err.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertGreaterEqual(err.flushes, 2)
        for line in lines:
            self.assertRegex(line, r"^\d{2}:\d{2} PT \| folder \d+/2 \| rc=\d+$")
        self.assertNotIn("INBOX", err.getvalue())
        self.assertNotIn("Archive", err.getvalue())
        self.assertTrue(any(line.endswith("rc=0") for line in lines))
        self.assertTrue(any(line.endswith("rc=21") for line in lines))
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["messages"], 1)
        self.assertEqual(report["errors"], 1)
        self.assertNotIn(SECRET, str(report))
        self.assertNotIn("boom", str(report))

    def test_truncated_literal_counts_as_an_error(self):
        """A short ``{n}`` literal is literal_truncated for that UID only."""
        import imap_curl as mod

        short = (
            "* OK [UIDVALIDITY 5] UIDs valid\r\n"
            '* 1 FETCH (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\r\n'
            '* 2 FETCH (UID 9 BODYSTRUCTURE ("NAME" {8}\r\n'
            "note"
        )
        found, issues = mod.parse_fetch_structures(short)
        self.assertIn("1", found)
        self.assertEqual(issues.get("9"), "literal_truncated")
        self.assertNotIn("9", found)
        calls = []

        def runner(argv, config_text, env, timeout):
            calls.append(config_text)
            self.assertNotIn("dump-header", config_text)
            self.assertNotIn("SELECT", config_text)
            return 0, short, ""

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [
                    ("ex-ok", "imap-live", "1", None, None, "synthetic", "INBOX"),
                    ("ex-short", "imap-live", "9", None, None, "synthetic", "INBOX"),
                ],
            )
            with mock.patch("imap_curl.run_subprocess", runner):
                report = meta.fill_metadata(
                    db,
                    source="imap",
                    apply=True,
                    host="imap.example.invalid",
                    user="user@example.invalid",
                    password_fn=lambda: SECRET,
                    cmdlines=[],
                    lock_held=False,
                    max_messages=0,
                    timeout_s=0,
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(report["messages"], 1)
            self.assertEqual(report["capped"], 0)
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["literal_truncated"], 1)
            self.assertEqual(report["literal_dropped"], 0)
            self.assertEqual(
                report["messages"] + report["errors"] + report["capped"],
                report["eligible"],
            )
            rendered = meta.format_report(report)
            self.assertIn("literal_truncated=1", rendered)
            self.assertIn("literal_folder index=1", rendered)
            self.assertIn("truncated_uids=9", rendered)
            self.assertNotIn("INBOX", rendered)
            self.assertNotIn("ex-ok", rendered)
            self.assertNotIn("ex-short", rendered)
            self.assertNotIn(SECRET, rendered)
            conn = sqlite3.connect(str(db))
            try:
                scans = conn.execute(
                    "SELECT message_id FROM attachment_meta_scans"
                ).fetchall()
                stored = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(scans, [("ex-ok",)])
            self.assertEqual(stored, 1)

    @hermetic_binaries.allow_real_curl("127.0.0.1")
    def test_wire_harness_modes(self):
        """Real curl against a fake IMAPS server, one mode at a time.

        SELECT is answered ``[READ-WRITE]`` so a regression cannot look
        read-only. Modes: normal, missing_uid, literal_behavior,
        literal_mixed, trunc_literal, login_no, rc 7, rc 21, rc 60.
        ``literal_behavior`` is a 127.0.0.1-only run with the fake password.
        It prints ``literal_behavior=omitted`` or ``literal_behavior=present``
        for whichever behavior this curl shows. It does not require one of them.
        """
        import imap_curl as imap_curl

        if not os.access("/usr/bin/curl", os.X_OK):
            self.skipTest("curl is not available")
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not available")
        password = "example-secret"
        with tempfile.TemporaryDirectory() as tmp:
            key = str(Path(tmp) / "key.pem")
            cert = str(Path(tmp) / "cert.pem")
            proc = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    key,
                    "-out",
                    cert,
                    "-days",
                    "1",
                    "-nodes",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not Path(cert).is_file():
                self.skipTest("could not generate a throwaway certificate")
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert, key)
            except ssl.SSLError:
                self.skipTest("TLS is not available")

            mode = {"name": "normal"}
            seen = []
            stats = {"connections": 0, "logins": 0, "selects": 0, "examines": 0}
            lock = threading.Lock()

            def handler(conn):
                buf = b""
                try:
                    conn.sendall(b"* OK IMAP4rev1 ready\r\n")
                    while True:
                        try:
                            data = conn.recv(8192)
                        except socket.timeout:
                            return
                        if not data:
                            return
                        buf += data
                        while b"\r\n" in buf:
                            line, buf = buf.split(b"\r\n", 1)
                            text = line.decode("utf-8", "replace")
                            with lock:
                                seen.append(text)
                                current = mode["name"]
                            parts = line.split(b" ", 2)
                            tag = parts[0]
                            cmd = parts[1].upper() if len(parts) > 1 else b""
                            if cmd == b"CAPABILITY":
                                conn.sendall(
                                    b"* CAPABILITY IMAP4rev1\r\n"
                                    + tag
                                    + b" OK CAPABILITY completed\r\n"
                                )
                            elif cmd == b"LOGIN":
                                with lock:
                                    stats["logins"] += 1
                                if current == "login_no":
                                    conn.sendall(tag + b" NO LOGIN denied\r\n")
                                    continue
                                conn.sendall(tag + b" OK LOGIN completed\r\n")
                            elif cmd == b"SELECT":
                                with lock:
                                    stats["selects"] += 1
                                conn.sendall(
                                    b"* 1 EXISTS\r\n* OK [UIDVALIDITY 11] UIDs valid\r\n"
                                    + tag
                                    + b" OK [READ-WRITE] SELECT completed\r\n"
                                )
                            elif cmd == b"EXAMINE":
                                with lock:
                                    stats["examines"] += 1
                                if current == "rc21":
                                    conn.sendall(tag + b" NO EXAMINE denied\r\n")
                                    continue
                                conn.sendall(
                                    b"* 1 EXISTS\r\n* OK [UIDVALIDITY 11] UIDs valid\r\n"
                                    b"* OK [PERMANENTFLAGS ()] Read-only\r\n"
                                    + tag
                                    + b" OK [READ-ONLY] EXAMINE completed\r\n"
                                )
                            elif cmd == b"UID":
                                if current == "missing_uid":
                                    body = (
                                        b'* 1 FETCH (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" '
                                        b'NIL NIL NIL "7BIT" 4 1))\r\n'
                                    )
                                elif current == "literal_behavior":
                                    name = b"note.pdf"
                                    inner = (
                                        b'("APPLICATION" "PDF" NIL NIL NIL "BASE64" 4 '
                                        b'NIL ("ATTACHMENT" ("FILENAME" {8}\r\n'
                                        + name
                                        + b")) NIL NIL)"
                                    )
                                    body = (
                                        b"* 1 FETCH (UID 9 BODYSTRUCTURE "
                                        + inner
                                        + b")\r\n"
                                    )
                                elif current == "literal_mixed":
                                    name = b"note.pdf"
                                    inner = (
                                        b'("APPLICATION" "PDF" ("NAME" {8}\r\n'
                                        + name
                                        + b') NIL NIL "BASE64" 4 NIL ("ATTACHMENT" ("FILENAME" {8}\r\n'
                                        + name
                                        + b")) NIL NIL)"
                                    )
                                    quoted = (
                                        b'* 1 FETCH (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" '
                                        b'NIL NIL NIL "7BIT" 4 1))\r\n'
                                    )
                                    body = (
                                        quoted
                                        + b"* 2 FETCH (UID 9 BODYSTRUCTURE "
                                        + inner
                                        + b")\r\n"
                                    )
                                elif current == "trunc_literal":
                                    body = (
                                        b"* 1 FETCH (UID 9 BODYSTRUCTURE {400}\r\n"
                                        + (b"x" * 400)
                                    )
                                else:
                                    body = (
                                        b'* 1 FETCH (UID 9 BODYSTRUCTURE ("TEXT" "PLAIN" '
                                        b'NIL NIL NIL "7BIT" 4 1))\r\n'
                                    )
                                conn.sendall(
                                    body
                                    + b"* OK [UIDVALIDITY 99] not the examine value\r\n"
                                    + tag
                                    + b" OK UID FETCH completed\r\n"
                                )
                            elif cmd == b"LOGOUT":
                                conn.sendall(b"* BYE\r\n" + tag + b" OK LOGOUT\r\n")
                                return
                            else:
                                conn.sendall(tag + b" BAD\r\n")
                except Exception:
                    return
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(8)
            listener.settimeout(0.5)
            port = listener.getsockname()[1]
            stop = threading.Event()

            def loop():
                while not stop.is_set():
                    try:
                        conn, _addr = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    try:
                        wrapped = ctx.wrap_socket(conn, server_side=True)
                    except ssl.SSLError:
                        try:
                            conn.close()
                        except OSError:
                            pass
                        continue
                    wrapped.settimeout(20)
                    with lock:
                        stats["connections"] += 1
                    handler(wrapped)

            thread = threading.Thread(target=loop, daemon=True)
            thread.start()
            calls = []
            real_run = imap_curl.run_subprocess

            def spy(argv, config_text, env, timeout):
                rc, out, err = real_run(argv, config_text, env, timeout)
                calls.append(
                    {
                        "argv": list(argv),
                        "config": config_text,
                        "env": dict(env),
                        "stdout": out,
                        "rc": rc,
                    }
                )
                return rc, out, err

            def boom(*_args, **_kwargs):
                raise AssertionError("fill opened imaplib or a python socket")

            def reset():
                calls.clear()
                seen.clear()
                for key_name in stats:
                    stats[key_name] = 0

            def snapshot(name):
                return {
                    "mode": name,
                    "processes": len(calls),
                    "connections": stats["connections"],
                    "logins": stats["logins"],
                    "selects": stats["selects"],
                    "examines": stats["examines"],
                }

            def run_fill(rows, *, apply, cacert_path):
                db = Path(tmp) / ("wire-%s.sqlite" % mode["name"])
                if db.exists():
                    db.unlink()
                _seed(db, rows)
                with mock.patch("imap_curl.run_subprocess", spy), mock.patch(
                    "imaplib.IMAP4_SSL", boom
                ), mock.patch("socket.create_connection", boom):
                    report = meta.fill_metadata(
                        db,
                        source="imap",
                        apply=apply,
                        host="127.0.0.1",
                        user="user@example.invalid",
                        password_fn=lambda: password,
                        imap_port=port,
                        cacert=cacert_path,
                        cmdlines=[],
                        lock_held=False,
                        max_messages=0,
                        timeout_s=0,
                    )
                return db, report

            def assert_wire(name, report, *, processes, examines):
                shot = snapshot(name)
                self.assertEqual(shot["processes"], processes, shot)
                self.assertEqual(shot["selects"], 0, shot)
                self.assertEqual(shot["examines"], examines, shot)
                self.assertEqual(
                    report["messages"] + report["errors"] + report["capped"],
                    report["eligible"],
                    report,
                )
                self.assertNotIn(password, str(report))
                shot["messages"] = report["messages"]
                shot["errors"] = report["errors"]
                shot["literal_dropped"] = report.get("literal_dropped") or 0
                shot["literal_truncated"] = report.get("literal_truncated") or 0
                transcript = "\n".join(seen)
                self.assertNotIn("SELECT", transcript)
                self.assertNotIn("READ-WRITE", transcript)
                for item in calls:
                    self.assertEqual(item["argv"][0], "/usr/bin/curl")
                    self.assertNotIn("dump-header", item["config"])
                    self.assertNotIn("SELECT", item["config"])
                    self.assertNotIn(password, item["argv"])
                    self.assertNotIn(password, item["env"].values())
                    self.assertIn("-K", item["argv"])
                    blocks = [
                        block
                        for block in item["config"].split("\nnext\n")
                        if block.strip()
                    ]
                    self.assertGreaterEqual(len(blocks), 1)
                    for block in blocks:
                        self.assertIn("connect-timeout", block)
                        self.assertIn("max-time", block)
                        self.assertIn("user = ", block)
                print(
                    "WIRE %s processes=%s connections=%s logins=%s selects=%s examines=%s messages=%s errors=%s literal_dropped=%s literal_truncated=%s"
                    % (
                        name,
                        shot["processes"],
                        shot["connections"],
                        shot["logins"],
                        shot["selects"],
                        shot["examines"],
                        shot["messages"],
                        shot["errors"],
                        shot["literal_dropped"],
                        shot["literal_truncated"],
                    ),
                    flush=True,
                )
                return shot

            results = []
            try:
                reset()
                mode["name"] = "normal"
                db, report = run_fill(
                    [("ex-n", "imap-live", "9", None, None, "synthetic", "INBOX")],
                    apply=False,
                    cacert_path=cert,
                )
                shot = assert_wire("normal", report, processes=1, examines=1)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(report["messages"], 1, report)
                self.assertEqual(report["errors"], 0, report)
                self.assertEqual(report["bytes_stored"], 0)
                self.assertTrue(report["dry_run"])
                conn = sqlite3.connect(str(db))
                try:
                    stored = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(stored, 0)
                self.assertIn("EXAMINE", calls[0]["config"])
                self.assertIn("\nnext\n", calls[0]["config"])
                self.assertIn("UID FETCH", calls[0]["config"])
                self.assertIn("\r\n", calls[0]["stdout"])
                results.append(shot)

                reset()
                mode["name"] = "literal_behavior"
                _db, report = run_fill(
                    [("ex-probe", "imap-live", "9", None, None, "synthetic", "INBOX")],
                    apply=True,
                    cacert_path=cert,
                )
                shot = assert_wire("literal_behavior", report, processes=1, examines=1)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(shot["selects"], 0, shot)
                stdout = calls[0]["stdout"]
                config = calls[0]["config"]
                self.assertIn("127.0.0.1", config)
                self.assertNotIn("imap.example", config)
                self.assertNotIn(password, calls[0]["argv"])
                self.assertNotIn(password, calls[0]["env"].values())
                if "note.pdf" in stdout:
                    behavior = "present"
                    self.assertEqual(report["messages"], 1, report)
                    self.assertEqual(report["errors"], 0, report)
                    self.assertEqual(report["literal_dropped"], 0, report)
                else:
                    behavior = "omitted"
                    self.assertEqual(report["literal_dropped"], 1, report)
                    self.assertEqual(report["messages"], 0, report)
                    self.assertEqual(report["errors"], 1, report)
                self.assertIn(behavior, ("omitted", "present"))
                shot["literal_behavior"] = behavior
                print("literal_behavior=%s" % behavior, flush=True)
                results.append(shot)

                reset()
                mode["name"] = "missing_uid"
                db, report = run_fill(
                    [
                        ("ex-1", "imap-live", "1", None, None, "synthetic", "INBOX"),
                        ("ex-2", "imap-live", "2", None, None, "synthetic", "INBOX"),
                    ],
                    apply=True,
                    cacert_path=cert,
                )
                shot = assert_wire("missing_uid", report, processes=1, examines=1)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(report["messages"], 1, report)
                self.assertEqual(report["errors"], 1, report)
                conn = sqlite3.connect(str(db))
                try:
                    scans = conn.execute(
                        "SELECT message_id FROM attachment_meta_scans"
                    ).fetchall()
                finally:
                    conn.close()
                self.assertEqual(scans, [("ex-1",)])
                results.append(shot)

                reset()
                mode["name"] = "literal_mixed"
                db, report = run_fill(
                    [
                        ("ex-plain", "imap-live", "1", None, None, "synthetic", "INBOX"),
                        ("ex-lit", "imap-live", "9", None, None, "synthetic", "INBOX"),
                    ],
                    apply=True,
                    cacert_path=cert,
                )
                shot = assert_wire("literal_mixed", report, processes=1, examines=1)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(report["messages"], 1, report)
                self.assertEqual(report["errors"], 1, report)
                self.assertEqual(report["literal_dropped"], 1, report)
                self.assertEqual(report["literal_truncated"], 0, report)
                self.assertEqual(report["literal_folders"][0]["index"], 1)
                self.assertEqual(report["literal_folders"][0]["dropped_uids"], [9])
                rendered = meta.format_report(report)
                self.assertIn("dropped_uids=9", rendered)
                self.assertNotIn("INBOX", rendered)
                self.assertNotIn("ex-plain", rendered)
                self.assertNotIn("ex-lit", rendered)
                self.assertNotIn("note.pdf", calls[0]["stdout"])
                results.append(shot)

                reset()
                mode["name"] = "trunc_literal"
                db, report = run_fill(
                    [("ex-trunc", "imap-live", "9", None, None, "synthetic", "INBOX")],
                    apply=True,
                    cacert_path=cert,
                )
                shot = assert_wire("trunc_literal", report, processes=1, examines=1)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(report["messages"], 0, report)
                self.assertEqual(report["errors"], 1, report)
                self.assertEqual(report["literal_truncated"], 1, report)
                self.assertEqual(report["literal_dropped"], 0, report)
                self.assertEqual(report["literal_folders"][0]["index"], 1)
                self.assertEqual(report["literal_folders"][0]["truncated_uids"], [9])
                rendered = meta.format_report(report)
                self.assertIn("truncated_uids=9", rendered)
                self.assertNotIn("INBOX", rendered)
                self.assertNotIn("ex-trunc", rendered)
                conn = sqlite3.connect(str(db))
                try:
                    scans = conn.execute(
                        "SELECT COUNT(*) FROM attachment_meta_scans"
                    ).fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(scans, 0)
                results.append(shot)

                reset()
                mode["name"] = "login_no"
                _db, report = run_fill(
                    [("ex-login", "imap-live", "9", None, None, "synthetic", "INBOX")],
                    apply=False,
                    cacert_path=cert,
                )
                shot = assert_wire("login_no", report, processes=1, examines=0)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(report["messages"], 0, report)
                self.assertEqual(report["errors"], report["eligible"], report)
                self.assertEqual(len(report["curl_failures"]), 1)
                self.assertIn("67", report["curl_failures"][0])
                self.assertIn("not retrying", report["curl_failures"][0])
                self.assertNotIn("LOGIN denied", str(report))
                results.append(shot)

                reset()
                mode["name"] = "rc21"
                _db, report = run_fill(
                    [("ex-21", "imap-live", "9", None, None, "synthetic", "INBOX")],
                    apply=False,
                    cacert_path=cert,
                )
                shot = assert_wire("rc21", report, processes=1, examines=1)
                self.assertEqual(shot["connections"], 1, shot)
                self.assertEqual(shot["logins"], 1, shot)
                self.assertEqual(report["messages"], 0, report)
                self.assertEqual(report["errors"], report["eligible"], report)
                self.assertIn("21", report["curl_failures"][0])
                self.assertIn("not retrying", report["curl_failures"][0])
                self.assertNotIn("EXAMINE denied", str(report))
                results.append(shot)

                reset()
                mode["name"] = "rc60"
                _db, report = run_fill(
                    [("ex-60", "imap-live", "9", None, None, "synthetic", "INBOX")],
                    apply=False,
                    cacert_path=None,
                )
                shot = assert_wire("rc60", report, processes=1, examines=0)
                self.assertEqual(shot["selects"], 0, shot)
                self.assertEqual(report["messages"], 0, report)
                self.assertEqual(report["errors"], report["eligible"], report)
                self.assertEqual(len(report["curl_failures"]), 1)
                self.assertIn("60", report["curl_failures"][0])
                self.assertIn("certificate", report["curl_failures"][0])
                self.assertIn("not retrying", report["curl_failures"][0])
                results.append(shot)

                closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                closed.bind(("127.0.0.1", 0))
                closed_port = closed.getsockname()[1]
                closed.close()
                reset()
                mode["name"] = "rc7"
                db = Path(tmp) / "wire-rc7.sqlite"
                _seed(
                    db,
                    [("ex-7", "imap-live", "9", None, None, "synthetic", "INBOX")],
                )
                with mock.patch("imap_curl.run_subprocess", spy), mock.patch(
                    "imaplib.IMAP4_SSL", boom
                ), mock.patch("socket.create_connection", boom):
                    report = meta.fill_metadata(
                        db,
                        source="imap",
                        apply=False,
                        host="127.0.0.1",
                        user="user@example.invalid",
                        password_fn=lambda: password,
                        imap_port=closed_port,
                        cmdlines=[],
                        lock_held=False,
                        max_messages=0,
                        timeout_s=0,
                    )
                shot = {
                    "mode": "rc7",
                    "processes": len(calls),
                    "connections": 0,
                    "logins": 0,
                    "selects": 0,
                    "examines": 0,
                    "messages": report["messages"],
                    "errors": report["errors"],
                    "literal_dropped": report.get("literal_dropped") or 0,
                    "literal_truncated": report.get("literal_truncated") or 0,
                }
                self.assertEqual(shot["processes"], 1, shot)
                self.assertEqual(report["messages"], 0, report)
                self.assertEqual(report["errors"], report["eligible"], report)
                self.assertEqual(
                    report["messages"] + report["errors"] + report["capped"],
                    report["eligible"],
                )
                self.assertIn("7", report["curl_failures"][0])
                self.assertIn("connect", report["curl_failures"][0])
                self.assertIn("not retrying", report["curl_failures"][0])
                self.assertNotIn(password, str(report))
                self.assertEqual(calls[0]["argv"][0], "/usr/bin/curl")
                print(
                    "WIRE rc7 processes=1 connections=0 logins=0 selects=0 examines=0 messages=%s errors=%s literal_dropped=%s literal_truncated=%s"
                    % (
                        shot["messages"],
                        shot["errors"],
                        shot["literal_dropped"],
                        shot["literal_truncated"],
                    ),
                    flush=True,
                )
                results.append(shot)
                lines = []
                for item in results:
                    line = (
                        "WIRE %s processes=%s connections=%s logins=%s selects=%s examines=%s messages=%s errors=%s literal_dropped=%s literal_truncated=%s"
                        % (
                            item["mode"],
                            item["processes"],
                            item["connections"],
                            item["logins"],
                            item["selects"],
                            item["examines"],
                            item["messages"],
                            item["errors"],
                            item["literal_dropped"],
                            item["literal_truncated"],
                        )
                    )
                    if item.get("literal_behavior"):
                        line += " literal_behavior=%s" % item["literal_behavior"]
                    lines.append(line)
                Path("/tmp/wire-harness-counts.txt").write_text(
                    "\n".join(lines) + "\n",
                    encoding="utf-8",
                )
            finally:
                stop.set()
                try:
                    listener.close()
                except OSError:
                    pass
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
