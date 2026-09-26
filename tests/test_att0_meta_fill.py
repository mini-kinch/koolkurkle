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
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.bodystructure as bodystructure  # noqa: E402
import attachments.meta_fill as meta  # noqa: E402
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
        if str(code).upper() != "UIDVALIDITY":
            return "OK", [None]
        if self.uidvalidity is None:
            return "OK", [None]
        return "OK", [str(self.uidvalidity).encode("ascii")]

    def fetch_bodystructure(self, uid):
        self.uids.append(uid)
        self.events.append(("fetch", self.mailbox, str(uid)))
        key = (self.mailbox, str(uid))
        if key in self.structures:
            return self.structures[key]
        if str(uid) in self.structures:
            return self.structures[str(uid)]
        raise ValueError("unknown uid")


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
                return "OK", [b"15"]

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
            "attachments.meta_fill.imaplib.IMAP4", PlainIMAP
        ), mock.patch("attachments.meta_fill.imaplib.IMAP4_SSL", FakeSSL), mock.patch(
            "attachments.meta_fill.read_imap_app_password", return_value=SECRET
        ):
            client = meta.ImapBodystructureClient(
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
        self.assertIn("IMAP4_SSL", source)
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
            "attachments.meta_fill.ssl.create_default_context", return_value=context
        ):
            client = meta.ImapBodystructureClient(
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
                return "OK", [b"7"]

        client = meta.ImapBodystructureClient(
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
                ],
            )
            stub = _StubImap(
                {
                    ("Deleted Messages", "7"): PLAIN_BS,
                    ("INBOX", "7"): PLAIN_BS,
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
            )
            self.assertEqual(first["messages"], 2)
            self.assertEqual(first["uidvalidity_mismatch"], 0)
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
            )
            self.assertEqual(second["uidvalidity_mismatch"], 1)
            self.assertEqual(second["messages"], 1)
            self.assertIn("uidvalidity_mismatch=1", meta.format_report(second))
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
        self.assertEqual(password, "legacy-secret")
        source = (SCRIPTS / "imap_keychain.py").read_text(encoding="utf-8")
        self.assertNotIn("MAILROOM_SECURITY_BIN", source)
        self.assertNotIn("MAILROOM_KEYCHAIN_ITEM", source)
        self.assertNotIn("os.environ", source)


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
            seen = {"inits": [], "events": []}

            class PlainIMAP:
                def __init__(self, *args, **kwargs):
                    plain_calls.append((args, kwargs))
                    raise AssertionError("plain IMAP4")

            class FakeSSL:
                def __init__(self, host, port, timeout=None, ssl_context=None):
                    seen["inits"].append((host, port, timeout))
                    seen["ssl_context"] = ssl_context
                    self.mailbox = None

                def login(self, user, password):
                    seen["login"] = (user, password)
                    return "OK", [b"ok"]

                def select(self, mailbox, readonly=False):
                    self.mailbox = mailbox
                    seen["events"].append(("select", mailbox, readonly))
                    return "OK", [b"1"]

                def response(self, code):
                    return "OK", [b"8"]

                def uid(self, command, uid, item):
                    logical = self.mailbox
                    if (
                        isinstance(logical, str)
                        and len(logical) >= 2
                        and logical.startswith('"')
                        and logical.endswith('"')
                    ):
                        logical = logical[1:-1]
                    seen["events"].append(("uid", logical, command, uid, item))
                    if logical == "Archive":
                        payload = MIXED_BS
                    else:
                        payload = PLAIN_BS
                    raw = b'1 (BODYSTRUCTURE %s)' % payload.encode("utf-8")
                    return "OK", [raw]

                def logout(self):
                    seen["events"].append(("logout",))
                    return "BYE", [b""]

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            def fake_security(args, check=False, capture_output=False, text=False):
                seen["security"] = list(args)
                proc = mock.Mock()
                proc.returncode = 0
                proc.stdout = "keychain-secret\n"
                proc.stderr = ""
                return proc

            out = io.StringIO()
            err = io.StringIO()
            env = {
                "MAILROOM_IMAP_HOST": "imap.example.com",
                "MAILROOM_IMAP_USER": "user@example.com",
                "IMAP_APP_PASSWORD": "env-secret",
                "MAILROOM_IMAP_PASSWORD": "env-secret",
                "MAILROOM_IMAP_PORT": "143",
            }
            with mock.patch("socket.create_connection", boom), mock.patch("sys.stderr", err):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--apply"],
                    env={},
                )
            self.assertEqual(rc, 2)
            self.assertIn("imap host is required", err.getvalue())
            self.assertNotIn("env-secret", err.getvalue())
            self.assertNotIn("keychain-secret", err.getvalue())
            with mock.patch("socket.create_connection", boom), mock.patch(
                "attachments.meta_fill.imaplib.IMAP4", PlainIMAP
            ), mock.patch(
                "attachments.meta_fill.imaplib.IMAP4_SSL", FakeSSL
            ), mock.patch(
                "imap_keychain.subprocess.run", fake_security
            ), mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--apply"],
                    env=env,
                )
            self.assertEqual(rc, 0, out.getvalue())
            self.assertEqual(plain_calls, [])
            self.assertEqual(seen["inits"], [("imap.example.com", 993, 30)])
            self.assertEqual(seen["login"], ("user@example.com", "keychain-secret"))
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
            self.assertEqual(
                seen["events"][:4],
                [
                    ("select", '"Archive"', True),
                    ("uid", "Archive", "FETCH", "42", "(BODYSTRUCTURE)"),
                    ("select", '"INBOX"', True),
                    ("uid", "INBOX", "FETCH", "42", "(BODYSTRUCTURE)"),
                ],
            )
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
        class BadIMAP:
            def __init__(self, host, port, timeout=None, ssl_context=None):
                return None

            def login(self, user, password):
                raise RuntimeError("rejected %s" % password)

            def logout(self):
                return "BYE", [b""]

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(
                db,
                [("ex-plain", "imap-live", "1002", None, None, "synthetic-plain", "INBOX")],
            )
            plain = mock.Mock(side_effect=AssertionError("plain IMAP4"))
            with mock.patch("attachments.meta_fill.imaplib.IMAP4", plain), mock.patch(
                "attachments.meta_fill.imaplib.IMAP4_SSL", BadIMAP
            ), mock.patch(
                "attachments.meta_fill.read_imap_app_password", return_value=SECRET
            ):
                with self.assertRaises(meta.FillRefuse) as ctx:
                    meta.fill_metadata(
                        db,
                        source="imap",
                        apply=True,
                        host="imap.example.com",
                        user="user@example.com",
                        cmdlines=[],
                        lock_held=False,
                    )
            self.assertFalse(plain.called)
            self.assertNotIn(SECRET, str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)
            self.assertNotIn(SECRET, _text_blob(db))


if __name__ == "__main__":
    unittest.main()
