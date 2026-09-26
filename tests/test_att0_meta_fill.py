#!/usr/bin/env python3
"""ATT-0 metadata fill. Synthetic fixtures only.

No network. No live mailbox. No system-of-record file.
"""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import sys
import tempfile
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
            conn.execute(
                "INSERT INTO messages "
                "(id, source, uid, jsonl_offset, jsonl_len, subject) "
                "VALUES (?, ?, ?, ?, ?, ?)",
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

    def fetch_bodystructure(self, uid):
        self.uids.append(uid)
        if uid not in self.structures:
            raise ValueError("unknown uid")
        return self.structures[uid]


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
                ("ex-mixed", "jsonl-import", None, first[0], first[1], "synthetic-mixed"),
                ("ex-plain", "jsonl-import", None, second[0], second[1], "synthetic-plain"),
            ],
        )
        return db, dump

    def _imap_db(self, tmp: str):
        db = Path(tmp) / "mailroom-copy.sqlite"
        _seed(
            db,
            [
                ("ex-mixed", "imap-live", "1001", None, None, "synthetic-mixed"),
                ("ex-plain", "imap-live", "1002", None, None, "synthetic-plain"),
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
                    password=SECRET,
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
                    password=SECRET,
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
                    password=SECRET,
                )
            self.assertEqual(stub.uids, ["1001", "1002"])
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
            self.assertEqual(capped["messages"], 0)
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
            self.assertEqual(report["has_attachments"], 0)
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
            self.assertEqual(flag, 0)

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
            self.assertEqual(report["stopped"], "timeout")
            self.assertEqual(report["messages"], 0)
            self.assertEqual(stub.uids, [])
            conn = sqlite3.connect(str(db))
            try:
                stored = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(stored, 0)

    def test_client_fetches_structure_only(self):
        seen = {}

        class FakeIMAP:
            def __init__(self, host, port, timeout=None):
                seen["init"] = (host, port, timeout)

            def login(self, user, password):
                seen["login"] = (user, password)
                return "OK", [b"ok"]

            def select(self, mailbox, readonly=False):
                seen["select"] = (mailbox, readonly)
                return "OK", [b"1"]

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

        with mock.patch("socket.create_connection", boom):
            client = meta.ImapBodystructureClient(
                "imap.example.com",
                "user@example.com",
                SECRET,
                port=143,
                mailbox="INBOX",
                timeout=5,
                imap_factory=FakeIMAP,
            )
            with client:
                text = client.fetch_bodystructure("9")
        self.assertEqual(seen["uid"], ("FETCH", "9", "(BODYSTRUCTURE)"))
        self.assertEqual(seen["select"], ("INBOX", True))
        self.assertEqual(seen["login"][1], SECRET)
        self.assertNotIn(SECRET, text)
        self.assertTrue(seen["logout"])
        source = (PKG / "meta_fill.py").read_text(encoding="utf-8")
        self.assertIn("imaplib.IMAP4", source)
        self.assertNotIn("IMAP4_SSL", source)
        self.assertNotIn("BODY[]", source)
        self.assertNotIn("BODY.PEEK", source)

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

    def test_cli_imap_env_does_not_print_the_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(db, [("ex-plain", "imap-live", "1002", None, None, "synthetic-plain")])
            seen = {}

            class Recording:
                def __init__(self, host, user, password, port=143, mailbox="INBOX", timeout=30):
                    seen["creds"] = (host, user, password, port, mailbox)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def fetch_bodystructure(self, uid):
                    return PLAIN_BS

            def boom(*_args, **_kwargs):
                raise AssertionError("network is forbidden")

            out = io.StringIO()
            err = io.StringIO()
            env = {
                "MAILROOM_IMAP_HOST": "imap.example.com",
                "MAILROOM_IMAP_USER": "user@example.com",
                "MAILROOM_IMAP_PASSWORD": SECRET,
                "MAILROOM_IMAP_PORT": "143",
            }
            with mock.patch("socket.create_connection", boom), mock.patch("sys.stderr", err):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--apply"],
                    env={},
                )
            self.assertEqual(rc, 2)
            self.assertIn("imap host is required", err.getvalue())
            self.assertNotIn(SECRET, err.getvalue())
            conn = sqlite3.connect(str(db))
            try:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM attachment_meta_scans"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(pending, 0)
            with mock.patch("socket.create_connection", boom), mock.patch(
                "attachments.meta_fill.ImapBodystructureClient", Recording
            ), mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--apply"],
                    env=env,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(seen["creds"][0], "imap.example.com")
            self.assertEqual(seen["creds"][2], SECRET)
            self.assertNotIn(SECRET, out.getvalue())
            self.assertNotIn(SECRET, _text_blob(db))
            self.assertIn("bytes_stored=0", out.getvalue())


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
            def __init__(self, host, port, timeout=None):
                return None

            def login(self, user, password):
                raise RuntimeError("rejected %s" % password)

            def logout(self):
                return "BYE", [b""]

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            _seed(db, [("ex-plain", "imap-live", "1002", None, None, "synthetic-plain")])
            with mock.patch("attachments.meta_fill.imaplib.IMAP4", BadIMAP):
                with self.assertRaises(meta.FillRefuse) as ctx:
                    meta.fill_metadata(
                        db,
                        source="imap",
                        apply=True,
                        host="imap.example.com",
                        user="user@example.com",
                        password=SECRET,
                        cmdlines=[],
                        lock_held=False,
                    )
            self.assertNotIn(SECRET, str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)
            self.assertNotIn(SECRET, _text_blob(db))


if __name__ == "__main__":
    unittest.main()
