#!/usr/bin/env python3
"""ATT-0 attachment schema, extract, chunk, and FTS citations.

Synthetic fixtures only. No network. No Ollama. No live SoR.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.chunk as chunk_mod  # noqa: E402
import attachments.extract as extract_mod  # noqa: E402
import attachments.migrate_att0_schema as mig  # noqa: E402
import attachments.search as search_mod  # noqa: E402
import sor_writer_gate as gate  # noqa: E402
from refuse_destructive import DestructiveRefuse  # noqa: E402
from sor_writer_gate import SorWriterRefuse  # noqa: E402

LIVE_CALLER_REFUSE = (
    "refuse: resolved path is the live SoR "
    "(pass --allow-mailroom-sqlite if you know this resolved path is the live SoR)"
)
_QUIET_ENV = (
    "MAILROOM_WRITE_LOCK",
    "SOR_FORCE_LIVE_CHECKS",
    "MAILROOM_WRITER_LOCK_TOKEN",
    "MAILROOM_WRITER_LOCK_PID",
    "MAILROOM_WRITER_LOCK_PURPOSE",
)

DESIGN = ROOT / "docs" / "attachments" / "ATT-0-design.md"
HEAVY = ROOT / "docs" / "att0-constraints.md"
PKG = SCRIPTS / "attachments"
ASK = SCRIPTS / "ask_mail.py"


def _master(path: Path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY 1, 2, 3"
        ).fetchall()
    finally:
        conn.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(%s)" % table)]


def make_pdf(page_texts, *, flate=False, kids=None):
    """Minimal text-layer PDF. ``kids`` overrides page-object order."""
    blobs = {}
    page_ids = []
    for index, text in enumerate(page_texts):
        page_id = 3 + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        literal = (
            text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        )
        stream = ("BT /F1 12 Tf 72 720 Td (%s) Tj ET" % literal).encode("latin-1")
        if flate:
            raw = zlib.compress(stream)
            header = ("<< /Length %d /Filter /FlateDecode >>" % len(raw)).encode("ascii")
            body = header + b"\nstream\n" + raw + b"\nendstream"
        else:
            header = ("<< /Length %d >>" % len(stream)).encode("ascii")
            body = header + b"\nstream\n" + stream + b"\nendstream"
        blobs[content_id] = body
        blobs[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R >>"
            % content_id
        ).encode("ascii")
    order = page_ids if kids is None else list(kids)
    kid_refs = " ".join("%d 0 R" % num for num in order)
    blobs[2] = (
        "<< /Type /Pages /Count %d /Kids [%s] >>" % (len(order), kid_refs)
    ).encode("ascii")
    blobs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    parts = [b"%PDF-1.4\n"]
    for num in sorted(blobs):
        parts.append(("%d 0 obj\n" % num).encode("ascii"))
        parts.append(blobs[num])
        parts.append(b"\nendobj\n")
    parts.append(b"%%EOF\n")
    return b"".join(parts)


def make_docx(pages):
    """``pages`` is a list of paragraph lists. A page break sits between pages."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    chunks = []
    for index, paras in enumerate(pages):
        if index:
            chunks.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        for para in paras:
            chunks.append(
                "<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % escape(para)
            )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>'
        % (ns, "".join(chunks))
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def make_xlsx(sheets):
    """``sheets`` is a list of lists of cell-text rows."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    strings = []
    index_of = {}

    def shared(value):
        if value not in index_of:
            index_of[value] = len(strings)
            strings.append(value)
        return index_of[value]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for sheet_no, rows in enumerate(sheets, 1):
            row_xml = []
            for row_no, cells in enumerate(rows, 1):
                cell_xml = []
                for col, value in enumerate(cells):
                    ref = "%s%d" % (chr(ord("A") + col), row_no)
                    if isinstance(value, int):
                        cell_xml.append('<c r="%s"><v>%d</v></c>' % (ref, value))
                    else:
                        cell_xml.append(
                            '<c r="%s" t="s"><v>%d</v></c>'
                            % (ref, shared(str(value)))
                        )
                row_xml.append(
                    '<row r="%d">%s</row>' % (row_no, "".join(cell_xml))
                )
            sheet = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
                % (ns, "".join(row_xml))
            )
            zf.writestr("xl/worksheets/sheet%d.xml" % sheet_no, sheet)
        items = "".join("<si><t>%s</t></si>" % escape(item) for item in strings)
        zf.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="%s">%s</sst>' % (ns, items),
        )
        zf.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


def make_pptx(slides):
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ppt/presentation.xml", "<presentation/>")
        for index, lines in enumerate(slides, 1):
            runs = "".join("<a:t>%s</a:t>" % escape(line) for line in lines)
            slide = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sld xmlns:a="%s"><cSld>%s</cSld></sld>' % (ns, runs)
            )
            zf.writestr("ppt/slides/slide%d.xml" % index, slide)
    return buf.getvalue()


class DesignRefreshTests(unittest.TestCase):
    def test_refresh_cites_heavy_05_and_todays_setup(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("att0-constraints.md", text)
        self.assertIn("20260914-05-attachment-search-design", text)
        self.assertIn("Mac mini", text)
        self.assertIn("sole writer", text.lower())
        self.assertIn("FTS-only", text)
        self.assertIn("Ollama is down", text)
        self.assertIn("no embedding service", text.lower())
        self.assertIn("Scope B", text)
        self.assertIn("sor_writer_gate", text)
        self.assertIn("refuse_destructive", text)
        self.assertIn("mailroom.sqlite", text)
        self.assertIn("--allow-mailroom-sqlite", text)
        self.assertIn("message_embeddings", text)
        self.assertIn("scripts/ask_mail.py", text)
        heavy = HEAVY.read_text(encoding="utf-8")
        self.assertIn("attachments/ATT-0-design.md", heavy)
        self.assertIn("20260914-05-attachment-search-design", heavy)


class SchemaMigrationTests(unittest.TestCase):
    def test_sql_file_is_fts_only_and_does_not_touch_embeddings(self):
        sql = (PKG / "schema.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS attachments", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS attachment_extracts", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS attachment_chunks", sql)
        self.assertIn("attachment_chunks_fts", sql)
        self.assertIn("fts5", sql.lower())
        self.assertNotIn("message_embeddings", sql)
        self.assertNotIn("vec0", sql.lower())
        self.assertNotIn("ON DELETE CASCADE", sql.upper())
        self.assertNotIn("DROP TABLE", sql.upper())
        self.assertNotIn("ALTER TABLE", sql.upper())
        for name in mig.EXPECTED_COLUMNS:
            self.assertEqual(_columns_declared_in_sql(sql, name), mig.EXPECTED_COLUMNS[name])

    def test_idempotent_on_a_copy_and_leaves_embeddings_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            self._seed_embeddings(db)
            before = self._embed_snapshot(db)
            messages_sql = self._sql(db, "messages")
            first = mig.migrate_database(db, cmdlines=[], lock_held=True)
            second = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(first["tables"]["attachments"], "created")
            self.assertEqual(second["tables"]["attachments"], "exists")
            self.assertEqual(first["message_embeddings"], "untouched")
            stable = _master(db)
            third = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(_master(db), stable)
            self.assertEqual(third["message_embeddings"], "untouched")
            self.assertEqual(self._embed_snapshot(db), before)
            self.assertEqual(self._sql(db, "messages"), messages_sql)
            conn = sqlite3.connect(str(db))
            try:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master")
                }
                self.assertNotIn("chunk_embeddings", names)
                self.assertNotIn("chunk_embedding_meta", names)
                for table, columns in mig.EXPECTED_COLUMNS.items():
                    self.assertEqual(_cols(conn, table), columns)
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0], 0
                )
            finally:
                conn.close()
            master_after = _master(db)
            mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(_master(db), master_after)

    def test_rerun_adds_uidvalidity_table_without_rewriting_rows(self):
        """An older database is missing attachment_folder_uidvalidity.

        Migrating again adds only that table. Existing attachment and scan
        rows stay byte-for-byte the same, and a third run reports exists.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            first = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(
                first["tables"]["attachment_folder_uidvalidity"], "created"
            )
            conn = sqlite3.connect(str(db))
            try:
                conn.execute("DROP TABLE attachment_folder_uidvalidity")
                conn.execute(
                    "INSERT INTO attachments "
                    "(message_id, part_id, filename, mime, size, sha256, status) "
                    "VALUES ('msg-keep', '1', NULL, 'text/plain', 4, 'abc', 'meta')"
                )
                conn.execute(
                    "INSERT INTO attachment_meta_scans "
                    "(message_id, source, part_count, has_attachments, scanned_at) "
                    "VALUES ('msg-keep', 'jsonl', 1, 0, '2026-01-01T00:00:00Z')"
                )
                conn.commit()
                before_att = conn.execute(
                    "SELECT * FROM attachments ORDER BY attachment_id"
                ).fetchall()
                before_scan = conn.execute(
                    "SELECT * FROM attachment_meta_scans ORDER BY message_id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(before_att), 1)
            second = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(
                second["tables"]["attachment_folder_uidvalidity"], "created"
            )
            self.assertEqual(second["tables"]["attachments"], "exists")
            conn = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    _cols(conn, "attachment_folder_uidvalidity"),
                    ["folder", "uidvalidity"],
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attachment_folder_uidvalidity"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT * FROM attachments ORDER BY attachment_id"
                    ).fetchall(),
                    before_att,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT * FROM attachment_meta_scans ORDER BY message_id"
                    ).fetchall(),
                    before_scan,
                )
            finally:
                conn.close()
            third = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(
                third["tables"]["attachment_folder_uidvalidity"], "exists"
            )
            self.assertEqual(third["tables"]["attachments"], "exists")
            conn = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT * FROM attachments ORDER BY attachment_id"
                    ).fetchall(),
                    before_att,
                )
            finally:
                conn.close()

    def test_refuses_mailroom_sqlite_without_flag_and_does_not_open_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "mailroom.sqlite"
            with self.assertRaises(mig.MigrateRefuse) as ctx:
                mig.migrate_database(missing, cmdlines=[], lock_held=False)
            self.assertEqual(str(ctx.exception), LIVE_CALLER_REFUSE)
            self.assertNotIn("CONFLICT", str(ctx.exception))
            self.assertFalse(missing.exists())

            existing = Path(tmp) / "nested" / "mailroom.sqlite"
            existing.parent.mkdir()
            conn = sqlite3.connect(str(existing))
            try:
                conn.execute("CREATE TABLE message_embeddings (message_id TEXT)")
                conn.execute(
                    "INSERT INTO message_embeddings (message_id) VALUES ('keep')"
                )
                conn.commit()
            finally:
                conn.close()
            digest = _sha(existing)
            with self.assertRaises(mig.MigrateRefuse):
                mig.migrate_database(existing)
            self.assertEqual(_sha(existing), digest)

    def test_flag_still_uses_writer_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom.sqlite"
            with self.assertRaises(SorWriterRefuse) as ctx:
                mig.migrate_database(
                    db,
                    allow_mailroom_sqlite=True,
                    lock_held=True,
                    cmdlines=[],
                )
            self.assertIn("CONFLICT", str(ctx.exception))
            self.assertFalse(db.exists())

            allowed = Path(tmp) / "allowed" / "mailroom.sqlite"
            allowed.parent.mkdir()
            self._seed_embeddings(allowed)
            before = self._embed_snapshot(allowed)
            report = mig.migrate_database(
                allowed,
                allow_mailroom_sqlite=True,
                lock_held=False,
                cmdlines=[],
            )
            self.assertEqual(report["db_basename"], "mailroom.sqlite")
            self.assertEqual(report["message_embeddings"], "untouched")
            self.assertEqual(self._embed_snapshot(allowed), before)

    def test_copy_is_allowed_while_sor_lock_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-daily-copy.sqlite"
            report = mig.migrate_database(
                db,
                lock_held=True,
                cmdlines=[(4242, "rem-legacy --db /tmp/mailroom.sqlite")],
            )
            self.assertEqual(report["tables"]["attachment_chunks"], "created")

    def test_destructive_verb_refuses_before_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            with self.assertRaises(DestructiveRefuse):
                mig.migrate_database(db, argv=["--purge"])
            self.assertFalse(db.exists())

    def test_does_not_alter_preexisting_attachments_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(
                    """
                    CREATE TABLE attachments (
                      id TEXT PRIMARY KEY,
                      message_id TEXT NOT NULL,
                      filename TEXT,
                      mime_type TEXT,
                      size_bytes INTEGER,
                      content_hash TEXT,
                      path TEXT,
                      created_at TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO attachments (id, message_id, filename) "
                    "VALUES ('att-old', 'msg-old', 'old.txt')"
                )
                conn.execute(
                    "CREATE TABLE message_embeddings (message_id TEXT PRIMARY KEY, embedding BLOB)"
                )
                conn.execute(
                    "INSERT INTO message_embeddings (message_id, embedding) "
                    "VALUES ('msg-old', X'deadbeef')"
                )
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(mig.MigrateRefuse) as ctx:
                mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertIn("attachments", str(ctx.exception))
            conn = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    _cols(conn, "attachments"),
                    [
                        "id",
                        "message_id",
                        "filename",
                        "mime_type",
                        "size_bytes",
                        "content_hash",
                        "path",
                        "created_at",
                    ],
                )
                row = conn.execute(
                    "SELECT filename FROM attachments WHERE id='att-old'"
                ).fetchone()
                self.assertEqual(row[0], "old.txt")
                names = {
                    item[0]
                    for item in conn.execute("SELECT name FROM sqlite_master")
                }
                self.assertNotIn("attachment_extracts", names)
                self.assertNotIn("attachment_meta_scans", names)
                self.assertNotIn("attachments_pr1_empty", names)
                self.assertEqual(
                    conn.execute(
                        "SELECT embedding FROM message_embeddings"
                    ).fetchone()[0],
                    b"\xde\xad\xbe\xef",
                )
            finally:
                conn.close()

    def test_refused_nonempty_mismatch_preserves_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(
                    """
                    CREATE TABLE attachments (
                      id TEXT PRIMARY KEY,
                      message_id TEXT NOT NULL,
                      filename TEXT,
                      mime_type TEXT,
                      size_bytes INTEGER,
                      content_hash TEXT,
                      path TEXT,
                      created_at TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO attachments (id, message_id, filename) "
                    "VALUES ('att-old', 'msg-old', 'old.txt')"
                )
                conn.commit()
            finally:
                conn.close()
            before = db.read_bytes()
            digest = hashlib.sha256(before).hexdigest()
            with self.assertRaises(mig.MigrateRefuse) as ctx:
                mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertIn("row", str(ctx.exception))
            after = db.read_bytes()
            self.assertEqual(len(after), len(before))
            self.assertEqual(hashlib.sha256(after).hexdigest(), digest)
            self.assertEqual(after, before)

    def test_empty_legacy_attachments_is_renamed_and_meta_scans_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(
                    """
                    CREATE TABLE attachments (
                      id TEXT PRIMARY KEY,
                      message_id TEXT NOT NULL,
                      filename TEXT,
                      mime_type TEXT,
                      size_bytes INTEGER,
                      content_hash TEXT,
                      path TEXT,
                      created_at TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE TABLE message_embeddings (message_id TEXT PRIMARY KEY, embedding BLOB)"
                )
                conn.execute(
                    "INSERT INTO message_embeddings (message_id, embedding) "
                    "VALUES ('keep', X'deadbeef')"
                )
                conn.execute("PRAGMA user_version=7")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(
                mig.LEGACY_ATTACHMENTS_COLUMNS,
                [
                    "id",
                    "message_id",
                    "filename",
                    "mime_type",
                    "size_bytes",
                    "content_hash",
                    "path",
                    "created_at",
                ],
            )
            report = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(report["legacy_attachments"], "renamed_empty")
            self.assertEqual(report["tables"]["attachments"], "created")
            self.assertEqual(report["tables"]["attachment_meta_scans"], "created")
            self.assertEqual(report["user_version"], 7)
            self.assertEqual(report["message_embeddings"], "untouched")
            conn = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    _cols(conn, "attachments"), mig.EXPECTED_COLUMNS["attachments"]
                )
                self.assertEqual(
                    _cols(conn, "attachments_pr1_empty"),
                    mig.LEGACY_ATTACHMENTS_COLUMNS,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attachments_pr1_empty"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    _cols(conn, "attachment_meta_scans"),
                    mig.EXPECTED_COLUMNS["attachment_meta_scans"],
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT embedding FROM message_embeddings"
                    ).fetchone()[0],
                    b"\xde\xad\xbe\xef",
                )
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 7)
            finally:
                conn.close()
            again = mig.migrate_database(db, cmdlines=[], lock_held=False)
            self.assertEqual(again["legacy_attachments"], "unchanged")
            self.assertEqual(again["tables"]["attachments"], "exists")
            self.assertEqual(again["tables"]["attachment_meta_scans"], "exists")
            conn = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attachments_pr1_empty"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    _cols(conn, "attachments_pr1_empty"),
                    mig.LEGACY_ATTACHMENTS_COLUMNS,
                )
            finally:
                conn.close()

    def test_cli_refuse_and_copy_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "mailroom-copy.sqlite"
            sqlite3.connect(str(copy)).close()
            sor = Path(tmp) / "mailroom.sqlite"
            purged = Path(tmp) / "other-copy.sqlite"
            absent = Path(tmp) / "absent.sqlite"
            nested = Path(tmp) / "no-such-dir" / "absent.sqlite"
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                rc = mig.main(["--db", str(copy)])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertIn("db_basename=mailroom-copy.sqlite", out.getvalue())
            self.assertIn("message_embeddings=absent", out.getvalue())
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = mig.main(["--db", str(sor)])
            self.assertEqual(rc, 2)
            self.assertIn(LIVE_CALLER_REFUSE, err.getvalue())
            self.assertNotIn("basename mailroom.sqlite", err.getvalue())
            self.assertFalse(sor.exists())
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = mig.main(["--db", str(purged), "--purge"])
            self.assertEqual(rc, 2)
            self.assertIn("refuse", err.getvalue())
            self.assertFalse(purged.exists())
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                rc = mig.main(["--db", str(absent)])
            self.assertEqual(rc, 2)
            self.assertIn("database not found", err.getvalue())
            self.assertFalse(absent.exists())
            with mock.patch("sys.stderr", err):
                rc = mig.main(["--db", str(nested)])
            self.assertEqual(rc, 2)
            self.assertFalse(nested.exists())
            self.assertFalse(nested.parent.exists())
            with mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    mig.main([])
            self.assertEqual(ctx.exception.code, 2)

    def _seed_embeddings(self, db: Path) -> None:
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "CREATE TABLE messages (id TEXT PRIMARY KEY, subject TEXT)"
            )
            conn.execute(
                "INSERT INTO messages (id, subject) VALUES ('msg-keep', 'synthetic')"
            )
            conn.execute(
                "CREATE TABLE message_embeddings (message_id TEXT PRIMARY KEY, embedding BLOB)"
            )
            conn.execute(
                "INSERT INTO message_embeddings (message_id, embedding) "
                "VALUES ('msg-keep', X'deadbeef')"
            )
            conn.commit()
        finally:
            conn.close()

    def _embed_snapshot(self, db: Path):
        conn = sqlite3.connect(str(db))
        try:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='message_embeddings'"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT message_id, embedding FROM message_embeddings ORDER BY message_id"
            ).fetchall()
            subject = conn.execute("SELECT subject FROM messages").fetchone()[0]
            return sql, rows, subject
        finally:
            conn.close()

    def _sql(self, db: Path, name: str):
        conn = sqlite3.connect(str(db))
        try:
            return conn.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()[0]
        finally:
            conn.close()


def _columns_declared_in_sql(sql: str, table: str) -> list[str]:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS %s \((.*?)\)\s*;" % table,
        sql,
        re.S,
    )
    if not match:
        raise AssertionError("missing CREATE TABLE %s" % table)
    columns = []
    for line in match.group(1).splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.upper().startswith("FOREIGN"):
            continue
        columns.append(stripped.split()[0])
    return columns


@contextlib.contextmanager
def _chdir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@contextlib.contextmanager
def _quiet_writer_env(lock):
    saved = {key: os.environ.get(key) for key in _QUIET_ENV}
    os.environ["MAILROOM_WRITE_LOCK"] = str(lock)
    for key in _QUIET_ENV:
        if key != "MAILROOM_WRITE_LOCK":
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def _held_empty_lock(path):
    path.write_text("")
    handle = path.open("r+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextlib.contextmanager
def _without_force_live_checks():
    saved = os.environ.pop("SOR_FORCE_LIVE_CHECKS", None)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("SOR_FORCE_LIVE_CHECKS", None)
        else:
            os.environ["SOR_FORCE_LIVE_CHECKS"] = saved


class LiveSoRCallerGuardTests(unittest.TestCase):
    def test_help_says_the_flag_means_the_resolved_path_is_live(self):
        text = " ".join(mig.build_parser().format_help().split())
        self.assertIn("I know this resolved path is the live SoR", text)
        self.assertIn("resolved path that is the live SoR", text)
        self.assertNotIn("basename mailroom.sqlite", text)

    def _assert_caller_refuse(self, db):
        err = io.StringIO()
        with mock.patch.object(
            sqlite3, "connect", side_effect=AssertionError("sqlite connect")
        ):
            with self.assertRaises(mig.MigrateRefuse) as ctx:
                mig.migrate_database(
                    db,
                    cmdlines=[(4242, "rem-legacy")],
                    lock_held=True,
                )
            with mock.patch("sys.stderr", err):
                rc = mig.main(["--db", str(db)])
        self.assertEqual(str(ctx.exception), LIVE_CALLER_REFUSE)
        self.assertNotIn("CONFLICT", str(ctx.exception))
        self.assertEqual(rc, 2)
        self.assertEqual(err.getvalue(), "error: " + LIVE_CALLER_REFUSE + "\n")

    def test_regular_file_and_resolving_shapes_refuse_at_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "mailroom.sqlite"
            sqlite3.connect(str(live)).close()
            digest = live.read_bytes()
            alias = root / "mailroom-copy.sqlite"
            alias.symlink_to(live.name)
            self._assert_caller_refuse(live)
            self._assert_caller_refuse(alias)
            self._assert_caller_refuse(str(alias) + "/")
            self._assert_caller_refuse(root / "down" / ".." / "mailroom.sqlite")
            with _chdir(root):
                self._assert_caller_refuse("mailroom.sqlite")
            self.assertEqual(live.read_bytes(), digest)
            self.assertTrue(alias.is_symlink())

    def test_direction2_live_name_to_a_copy_passes_caller_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy = root / "mailroom-copy.sqlite"
            sqlite3.connect(str(copy)).close()
            nested = root / "nested"
            nested.mkdir()
            link = nested / "mailroom.sqlite"
            link.symlink_to(copy)
            self.assertEqual(link.name, "mailroom.sqlite")
            self.assertFalse(gate.is_live_sor(link))
            with _without_force_live_checks():
                report = mig.migrate_database(
                    link,
                    cmdlines=[(4242, "rem-legacy")],
                    lock_held=True,
                )
            self.assertEqual(report["tables"]["attachments"], "created")
            lock = root / "mailroom.write.lock"
            out = io.StringIO()
            err = io.StringIO()
            with _held_empty_lock(lock):
                with _quiet_writer_env(lock):
                    with mock.patch(
                        "sor_writer_gate.rem_process_hits",
                        return_value=[(4242, "rem-legacy")],
                    ):
                        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                            rc = mig.main(["--db", str(link)])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertIn("att0 schema migrate", out.getvalue())
            self.assertNotIn(LIVE_CALLER_REFUSE, err.getvalue())
            self.assertNotIn("CONFLICT", err.getvalue())

    def test_broken_symlink_and_missing_target_do_not_open_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / "mailroom-copy.sqlite"
            link.symlink_to("mailroom.sqlite")
            missing = root / "mailroom.sqlite"
            dotted = root / "down" / ".." / "mailroom.sqlite"
            self.assertTrue(link.is_symlink())
            self.assertFalse(missing.exists())
            self._assert_caller_refuse(link)
            self._assert_caller_refuse(missing)
            self._assert_caller_refuse(dotted)
            self.assertTrue(link.is_symlink())
            self.assertFalse(missing.exists())
            self.assertFalse(dotted.exists())
            self.assertFalse((root / "down").exists())
            copy_link = root / "nested"
            copy_link.mkdir()
            named = copy_link / "mailroom.sqlite"
            target = root / "mailroom-daily-copy.sqlite"
            named.symlink_to(target)
            err = io.StringIO()
            with mock.patch.object(
                sqlite3, "connect", side_effect=AssertionError("sqlite connect")
            ):
                with mock.patch("sys.stderr", err):
                    rc = mig.main(["--db", str(named)])
            self.assertEqual(rc, 2)
            self.assertIn("database not found", err.getvalue())
            self.assertFalse(target.exists())
            self.assertTrue(named.is_symlink())

    def test_cli_flag_skips_caller_refuse_and_reaches_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "mailroom.sqlite"
            sqlite3.connect(str(live)).close()
            lock = root / "mailroom.write.lock"
            with _quiet_writer_env(lock):
                err = io.StringIO()
                with mock.patch.object(
                    mig,
                    "refuse_if_sor_writer_conflict",
                    wraps=mig.refuse_if_sor_writer_conflict,
                ) as gate_call:
                    with mock.patch("sys.stderr", err):
                        refused = mig.main(["--db", str(live)])
                self.assertEqual(refused, 2)
                self.assertIn(LIVE_CALLER_REFUSE, err.getvalue())
                self.assertFalse(gate_call.called)
                err = io.StringIO()
                out = io.StringIO()
                with mock.patch.object(
                    mig,
                    "refuse_if_sor_writer_conflict",
                    wraps=mig.refuse_if_sor_writer_conflict,
                ) as gate_call:
                    with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                        allowed = mig.main(
                            ["--db", str(live), "--allow-mailroom-sqlite"]
                        )
            self.assertEqual(allowed, 0, err.getvalue())
            self.assertTrue(gate_call.called)
            self.assertIn("att0 schema migrate", out.getvalue())
            self.assertNotIn(LIVE_CALLER_REFUSE, err.getvalue())
            with mock.patch.object(
                mig,
                "refuse_if_sor_writer_conflict",
                wraps=mig.refuse_if_sor_writer_conflict,
            ) as gate_call:
                report = mig.migrate_database(
                    live,
                    allow_mailroom_sqlite=True,
                    cmdlines=[],
                    lock_held=False,
                )
            self.assertTrue(gate_call.called)
            self.assertEqual(report["tables"]["attachments"], "exists")

    def test_flag_with_rem_or_held_lock_is_a_gate_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "mailroom.sqlite"
            sqlite3.connect(str(live)).close()
            digest = live.read_bytes()
            with mock.patch.object(
                mig,
                "refuse_if_sor_writer_conflict",
                wraps=mig.refuse_if_sor_writer_conflict,
            ) as gate_call:
                with self.assertRaises(SorWriterRefuse) as ctx:
                    mig.migrate_database(
                        live,
                        allow_mailroom_sqlite=True,
                        lock_held=False,
                        cmdlines=[(4242, "rem-legacy")],
                    )
            self.assertTrue(gate_call.called)
            self.assertIn("rem process pid=4242", str(ctx.exception))
            self.assertNotIn(LIVE_CALLER_REFUSE, str(ctx.exception))
            with mock.patch.object(
                mig,
                "refuse_if_sor_writer_conflict",
                wraps=mig.refuse_if_sor_writer_conflict,
            ) as gate_call:
                with self.assertRaises(SorWriterRefuse) as ctx:
                    mig.migrate_database(
                        live,
                        allow_mailroom_sqlite=True,
                        lock_held=True,
                        cmdlines=[],
                    )
            self.assertTrue(gate_call.called)
            self.assertIn("writer lock held", str(ctx.exception))
            self.assertEqual(live.read_bytes(), digest)
            err = io.StringIO()
            with _quiet_writer_env(root / "absent.write.lock"):
                with mock.patch(
                    "sor_writer_gate.rem_process_hits",
                    return_value=[(4242, "rem-legacy")],
                ):
                    with mock.patch("sys.stderr", err):
                        rc = mig.main(
                            ["--db", str(live), "--allow-mailroom-sqlite"]
                        )
            self.assertEqual(rc, 2)
            self.assertIn("rem process pid=4242", err.getvalue())
            self.assertNotIn(LIVE_CALLER_REFUSE, err.getvalue())
            self.assertEqual(live.read_bytes(), digest)
            err = io.StringIO()
            lock = root / "mailroom.write.lock"
            with _held_empty_lock(lock):
                with _quiet_writer_env(lock):
                    with mock.patch("sys.stderr", err):
                        rc = mig.main(
                            ["--db", str(live), "--allow-mailroom-sqlite"]
                        )
            self.assertEqual(rc, 2)
            self.assertIn("CONFLICT", err.getvalue())
            self.assertIn("writer lock held", err.getvalue())
            self.assertNotIn(LIVE_CALLER_REFUSE, err.getvalue())
            self.assertEqual(live.read_bytes(), digest)


class ExtractTests(unittest.TestCase):
    def test_plain_markdown_csv_and_form_feed_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = root / "note.txt"
            text.write_text("alpha page\fbeta page\fgamma page\n", encoding="utf-8")
            result = extract_mod.extract_file(text)
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.extractor, "text-stdlib")
            self.assertEqual(result.page_count, 3)
            self.assertEqual([page.page for page in result.pages], [1, 2, 3])
            self.assertIn("beta page", result.pages[1].text)

            markdown = root / "note.md"
            markdown.write_text("# Note\n\nSynthetic body acmewidget\n", encoding="utf-8")
            md = extract_mod.extract_file(markdown)
            self.assertEqual(md.status, "ok")
            self.assertEqual(md.extractor, "markdown-stdlib")
            self.assertIn("acmewidget", md.text)
            self.assertEqual(md.pages[0].page, 1)

            csv_path = root / "vendors.csv"
            csv_path.write_text("vendor,amount\nExample Vendor,10\n", encoding="utf-8")
            csv_result = extract_mod.extract_file(csv_path)
            self.assertEqual(csv_result.status, "ok")
            self.assertEqual(csv_result.extractor, "csv-stdlib")
            self.assertIn("Example Vendor", csv_result.text)
            self.assertEqual(csv_result.page_count, 1)

    def test_pdf_text_layer_pages_flate_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "invoice-example.pdf"
            path.write_bytes(
                make_pdf(
                    ["alpha page synthetic", "Page (draft) beta"],
                    flate=True,
                )
            )
            result = extract_mod.extract_file(path)
            self.assertEqual(result.status, "ok", result.error)
            self.assertEqual(result.extractor, "pdf-stdlib")
            self.assertEqual(result.page_count, 2)
            self.assertIn("alpha page synthetic", result.pages[0].text)
            self.assertIn("Page (draft) beta", result.pages[1].text)
            self.assertEqual(result.pages[1].page, 2)

            ordered = root / "ordered.pdf"
            # Object 3 is the second page in the file; Kids lists object 5 first.
            ordered.write_bytes(
                make_pdf(
                    ["second-in-file", "first-in-kids"],
                    kids=[5, 3],
                )
            )
            ordered_result = extract_mod.extract_file(ordered)
            self.assertEqual(ordered_result.status, "ok", ordered_result.error)
            self.assertIn("first-in-kids", ordered_result.pages[0].text)
            self.assertIn("second-in-file", ordered_result.pages[1].text)

    def test_office_docx_xlsx_pptx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docx = root / "memo.docx"
            docx.write_bytes(
                make_docx(
                    [
                        ["Memo for Example Vendor"],
                        ["Page two acmewidget"],
                    ]
                )
            )
            doc = extract_mod.extract_file(docx)
            self.assertEqual(doc.status, "ok", doc.error)
            self.assertEqual(doc.extractor, "docx-stdlib")
            self.assertEqual(doc.page_count, 2)
            self.assertIn("Example Vendor", doc.pages[0].text)
            self.assertIn("acmewidget", doc.pages[1].text)
            self.assertEqual(doc.pages[1].page, 2)

            xlsx = root / "sheet.xlsx"
            xlsx.write_bytes(
                make_xlsx(
                    [
                        [["Example Vendor", 10]],
                        [["second sheet acmewidget"]],
                    ]
                )
            )
            sheet = extract_mod.extract_file(xlsx)
            self.assertEqual(sheet.status, "ok", sheet.error)
            self.assertEqual(sheet.extractor, "xlsx-stdlib")
            self.assertEqual(sheet.page_count, 2)
            self.assertIn("Example Vendor", sheet.pages[0].text)
            self.assertIn("10", sheet.pages[0].text)
            self.assertIn("acmewidget", sheet.pages[1].text)

            pptx = root / "deck.pptx"
            pptx.write_bytes(
                make_pptx(
                    [
                        ["Slide one"],
                        ["Slide two acmewidget"],
                    ]
                )
            )
            deck = extract_mod.extract_file(pptx)
            self.assertEqual(deck.status, "ok", deck.error)
            self.assertEqual(deck.extractor, "pptx-stdlib")
            self.assertEqual(deck.page_count, 2)
            self.assertIn("Slide one", deck.pages[0].text)
            self.assertIn("acmewidget", deck.pages[1].text)
            self.assertEqual(deck.pages[1].page, 2)

    def test_skip_ocr_images_av_archives_and_executables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_pdf = root / "scan.pdf"
            image_pdf.write_bytes(
                b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
                b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>\nendobj\n"
                b"4 0 obj\n<< /Length 14 >>\nstream\n0 0 0 rg\nendstream\nendobj\n"
                b"%%EOF\n"
            )
            scanned = extract_mod.extract_file(image_pdf)
            self.assertEqual(scanned.status, "skip_ocr")
            self.assertEqual(scanned.text, "")

            cases = {
                "pixel.png": b"\x89PNG\r\n\x1a\nSECRETTEXT",
                "clip.mp4": b"\x00\x00\x00\x18ftypmp42SECRETTEXT",
                "tone.mp3": b"ID3SECRETTEXT",
                "run.exe": b"MZSECRETTEXT",
                "renamed.txt": b"MZSECRETTEXT",
            }
            expected = {
                "pixel.png": "skip_image",
                "clip.mp4": "skip_video",
                "tone.mp3": "skip_audio",
                "run.exe": "skip_executable",
                "renamed.txt": "skip_executable",
            }
            for name, blob in cases.items():
                path = root / name
                path.write_bytes(blob)
                result = extract_mod.extract_file(path)
                self.assertEqual(result.status, expected[name], name)
                self.assertNotIn("SECRETTEXT", result.text)
                self.assertEqual(result.text, "")

            archive = root / "bundle.zip"
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("readme.txt", "SECRETTEXT inside archive")
            archive.write_bytes(buf.getvalue())
            archived = extract_mod.extract_file(archive)
            self.assertEqual(archived.status, "skip_archive")
            self.assertNotIn("SECRETTEXT", archived.text)

            encrypted = root / "locked.pdf"
            encrypted.write_bytes(
                b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R /Encrypt 9 0 R >>\nendobj\n"
                b"%%EOF\n"
            )
            locked = extract_mod.extract_file(encrypted)
            self.assertEqual(locked.status, "skip_encrypted")
            self.assertEqual(locked.text, "")

    def test_unsupported_and_missing_optional_module(self):
        self.assertIsNone(extract_mod.optional_module("no_such_att0_module_cbf1"))
        src = (PKG / "extract.py").read_text(encoding="utf-8")
        self.assertIn("ImportError", src)
        with tempfile.TemporaryDirectory() as tmp:
            rtf = Path(tmp) / "note.rtf"
            rtf.write_text("{\\rtf1 hello acmewidget}", encoding="utf-8")
            result = extract_mod.extract_file(rtf)
            self.assertEqual(result.status, "unsupported")
            self.assertEqual(result.text, "")
            broken = Path(tmp) / "broken.pdf"
            broken.write_bytes(b"%PDF-1.4\n%not a body\n")
            pdf = extract_mod.extract_file(broken)
            self.assertEqual(pdf.status, "unsupported")
            self.assertEqual(pdf.text, "")

    def test_caps_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = root / "big.txt"
            big.write_text("should-not-extract-acmewidget", encoding="utf-8")
            too_big = extract_mod.extract_file(big, max_bytes=8)
            self.assertEqual(too_big.status, "too_big")
            self.assertEqual(too_big.error, "max_bytes")
            self.assertEqual(too_big.text, "")

            pages = root / "pages.txt"
            pages.write_text("p1\fp2\fp3", encoding="utf-8")
            capped_pages = extract_mod.extract_file(pages, max_pages=1)
            self.assertEqual(capped_pages.status, "capped")
            self.assertEqual(capped_pages.error, "max_pages")
            self.assertTrue(capped_pages.truncated)
            self.assertEqual(capped_pages.text, "p1")
            self.assertEqual(capped_pages.page_count, 3)

            chars = root / "chars.txt"
            chars.write_text("0123456789ABCDEF", encoding="utf-8")
            capped_chars = extract_mod.extract_file(chars, max_chars=10)
            self.assertEqual(capped_chars.status, "capped")
            self.assertEqual(capped_chars.error, "max_chars")
            self.assertEqual(capped_chars.text, "0123456789")

            docx = root / "wide.docx"
            docx.write_bytes(make_docx([["A" * 4000]]))
            member_cap = extract_mod.extract_file(
                docx,
                max_bytes=docx.stat().st_size,
                max_chars=10 ** 6,
            )
            self.assertEqual(member_cap.status, "too_big")
            self.assertEqual(member_cap.text, "")

        calls = {"n": 0}

        def _body(*_args, **_kwargs):
            calls["n"] += 1
            raise AssertionError("body should not run")

        with mock.patch("attachments.extract.extract_file_body", _body):
            immediate = extract_mod.extract_file("unused.txt", timeout_s=0)
        self.assertEqual(immediate.status, "timeout")
        self.assertEqual(immediate.error, "timeout")
        self.assertEqual(calls["n"], 0)

        def _slow(*_args, **_kwargs):
            import time

            time.sleep(0.4)
            raise AssertionError("timeout did not fire")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slow.txt"
            path.write_text("hello", encoding="utf-8")
            with mock.patch("attachments.extract.extract_file_body", _slow):
                timed = extract_mod.extract_file(path, timeout_s=0.1)
        self.assertEqual(timed.status, "timeout")
        self.assertEqual(timed.text, "")


class ChunkTests(unittest.TestCase):
    def test_pack_page_ranges_and_long_page_overlap(self):
        packed = chunk_mod.chunk_pages(
            [(1, "a" * 10), (2, "b" * 10), (3, "c" * 10)],
            target_chars=25,
            overlap_chars=0,
        )
        self.assertEqual(len(packed), 2)
        self.assertEqual((packed[0].page_start, packed[0].page_end), (1, 2))
        self.assertIn("a" * 10, packed[0].text)
        self.assertIn("b" * 10, packed[0].text)
        self.assertEqual((packed[1].page_start, packed[1].page_end), (3, 3))
        self.assertEqual(packed[0].chunk_index, 0)
        self.assertEqual(packed[1].chunk_index, 1)

        letters = "abcdefghijklmnopqrstuvwxyz"
        splits = chunk_mod.chunk_pages(
            [(4, letters)],
            target_chars=10,
            overlap_chars=4,
        )
        self.assertGreaterEqual(len(splits), 2)
        for piece in splits:
            self.assertEqual((piece.page_start, piece.page_end), (4, 4))
        self.assertTrue(splits[1].text.startswith(splits[0].text[-4:]))

        mixed = chunk_mod.chunk_pages(
            [(1, "a" * 30), (2, "tail")],
            target_chars=20,
            overlap_chars=0,
        )
        self.assertTrue(all(piece.page_start == 1 for piece in mixed[:-1]))
        self.assertEqual((mixed[-1].page_start, mixed[-1].page_end), (2, 2))
        self.assertEqual(mixed[-1].text, "tail")

        skipped = chunk_mod.chunk_pages([(1, "  "), (2, "hello")])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].page_start, 2)
        with self.assertRaises(ValueError):
            chunk_mod.chunk_pages([(1, "abc")], target_chars=4, overlap_chars=4)


class SearchCitationTests(unittest.TestCase):
    def test_fts_returns_file_and_page_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "dir with space"
            folder.mkdir()
            db = folder / "mailroom-copy.sqlite"
            mig.migrate_database(db, cmdlines=[], lock_held=False)
            mig.migrate_database(db, cmdlines=[], lock_held=False)
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(
                    "CREATE TABLE message_embeddings (message_id TEXT PRIMARY KEY, embedding BLOB)"
                )
                conn.execute(
                    "INSERT INTO message_embeddings (message_id, embedding) "
                    "VALUES ('msg-example-1', X'aabb')"
                )
                conn.execute(
                    "INSERT INTO attachments "
                    "(message_id, part_id, filename, mime, size, sha256, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "msg-example-1",
                        "2",
                        "invoice-example.pdf",
                        "application/pdf",
                        128,
                        "ab" * 32,
                        "present",
                    ),
                )
                attachment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO attachment_extracts "
                    "(attachment_id, extractor, extractor_version, text, page_count, status, error, timings) "
                    "VALUES (?, 'pdf-stdlib', '1', '', 2, 'ok', NULL, '{\"elapsed_ms\":1}')",
                    (attachment_id,),
                )
                extract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO attachment_chunks "
                    "(extract_id, chunk_index, page_start, page_end, text) "
                    "VALUES (?, 0, 1, 1, 'cover page only')",
                    (extract_id,),
                )
                conn.execute(
                    "INSERT INTO attachment_chunks "
                    "(extract_id, chunk_index, page_start, page_end, text) "
                    "VALUES (?, 1, 2, 3, 'synthetic invoice acmewidget for Example Vendor')",
                    (extract_id,),
                )
                conn.commit()
                fts_count = conn.execute(
                    "SELECT COUNT(*) FROM attachment_chunks_fts"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(fts_count, 2)
            mig.migrate_database(db, cmdlines=[], lock_held=False)
            conn = sqlite3.connect(str(db))
            try:
                fts_after = conn.execute(
                    "SELECT COUNT(*) FROM attachment_chunks_fts"
                ).fetchone()[0]
                embed = conn.execute(
                    "SELECT embedding FROM message_embeddings"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(fts_after, 2)
            self.assertEqual(embed, b"\xaa\xbb")

            digest = _sha(db)
            hits = search_mod.search_attachments(db, "acmewidget")
            self.assertEqual(_sha(db), digest)
            self.assertEqual(len(hits), 1)
            hit = hits[0]
            self.assertEqual(hit.message_id, "msg-example-1")
            self.assertEqual(hit.filename, "invoice-example.pdf")
            self.assertEqual(hit.page_start, 2)
            self.assertEqual(hit.page_end, 3)
            self.assertIn("acmewidget", hit.snippet.lower())
            self.assertEqual(search_mod.search_attachments(db, "   "), [])
            self.assertEqual(_sha(db), digest)

            empty = folder / "empty.sqlite"
            sqlite3.connect(str(empty)).close()
            empty_digest = _sha(empty)
            with self.assertRaises(search_mod.SearchError):
                search_mod.search_attachments(empty, "acmewidget")
            self.assertEqual(_sha(empty), empty_digest)

    def test_extract_chunk_search_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "invoice-example.pdf"
            pdf.write_bytes(
                make_pdf(
                    [
                        "cover only",
                        "synthetic invoice acmewidget for Example Vendor",
                    ]
                )
            )
            extracted = extract_mod.extract_file(pdf)
            self.assertEqual(extracted.status, "ok", extracted.error)
            chunks = chunk_mod.chunk_pages(
                [(page.page, page.text) for page in extracted.pages],
                target_chars=40,
                overlap_chars=0,
            )
            matched = [item for item in chunks if "acmewidget" in item.text]
            self.assertEqual(len(matched), 1)
            self.assertEqual((matched[0].page_start, matched[0].page_end), (2, 2))

            db = root / "mailroom-copy.sqlite"
            mig.migrate_database(db, cmdlines=[], lock_held=False)
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(
                    "CREATE TABLE message_embeddings (message_id TEXT PRIMARY KEY, note TEXT)"
                )
                conn.execute(
                    "INSERT INTO message_embeddings (message_id, note) VALUES ('msg-example-1', 'live')"
                )
                conn.execute(
                    "INSERT INTO attachments "
                    "(message_id, part_id, filename, mime, size, sha256, status) "
                    "VALUES ('msg-example-1', '1', 'invoice-example.pdf', 'application/pdf', 64, ?, 'present')",
                    ("cd" * 32,),
                )
                attachment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO attachment_extracts "
                    "(attachment_id, extractor, extractor_version, text, page_count, status, error, timings) "
                    "VALUES (?, ?, ?, ?, ?, 'ok', NULL, '{}')",
                    (
                        attachment_id,
                        extracted.extractor,
                        extracted.extractor_version,
                        extracted.text,
                        extracted.page_count,
                    ),
                )
                extract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                for item in chunks:
                    conn.execute(
                        "INSERT INTO attachment_chunks "
                        "(extract_id, chunk_index, page_start, page_end, text) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            extract_id,
                            item.chunk_index,
                            item.page_start,
                            item.page_end,
                            item.text,
                        ),
                    )
                conn.commit()
                sql_before = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name='message_embeddings'"
                ).fetchone()[0]
            finally:
                conn.close()
            hits = search_mod.search_attachments(db, "acmewidget")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].message_id, "msg-example-1")
            self.assertEqual(hits[0].filename, "invoice-example.pdf")
            self.assertEqual((hits[0].page_start, hits[0].page_end), (2, 2))
            self.assertIn("acmewidget", hits[0].snippet)
            conn = sqlite3.connect(str(db))
            try:
                sql_after = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name='message_embeddings'"
                ).fetchone()[0]
                note = conn.execute(
                    "SELECT note FROM message_embeddings"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(sql_before, sql_after)
            self.assertEqual(note, "live")


class HermeticBoundaryTests(unittest.TestCase):
    def test_package_has_no_process_embed_or_network_path(self):
        # fetch_p1.py and p1_worker.py are the ATT-1/ATT-2 P1 path.
        # Extractors there run out of process, so those two files may
        # import subprocess. Every other module stays in-process.
        out_of_process = {"fetch_p1.py", "p1_worker.py"}
        for path in sorted(PKG.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name not in out_of_process:
                self.assertNotIn("import subprocess", text, path.name)
            self.assertNotIn("os.system", text, path.name)
            self.assertNotIn("os.popen", text, path.name)
            if path.name == "fetch_p1.py":
                # The worker supervisor uses Popen in a new session so a
                # timeout can SIGKILL the extractor and its grandchildren.
                self.assertIn("Popen(", text)
                self.assertIn("start_new_session=True", text)
                self.assertNotIn("shell=True", text)
            else:
                self.assertNotIn("Popen(", text, path.name)
            self.assertNotIn("ollama", text.lower(), path.name)
            self.assertNotIn("embed_lib", text, path.name)
            self.assertNotIn("11434", text, path.name)
            self.assertNotIn("DROP TABLE", text.upper(), path.name)
            if path.name == "migrate_att0_schema.py":
                alters = [
                    line.strip()
                    for line in text.splitlines()
                    if "ALTER TABLE" in line.upper()
                ]
                self.assertEqual(len(alters), 1, alters)
                self.assertIn(
                    'ALTER TABLE "attachments" RENAME TO "attachments_pr1_empty"',
                    alters[0],
                )
            else:
                self.assertNotIn("ALTER TABLE", text.upper(), path.name)
            self.assertNotIn("ask_mail", text, path.name)
        ask = ASK.read_text(encoding="utf-8")
        self.assertIn("def ", ask)
        self.assertNotIn("attachment_chunks_fts", ask)

    def test_extract_migrate_and_search_do_not_touch_network(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("network is forbidden")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            note = root / "note.txt"
            note.write_text("hello acmewidget\n", encoding="utf-8")
            with mock.patch("socket.create_connection", boom), mock.patch(
                "urllib.request.urlopen", boom
            ), mock.patch("subprocess.Popen", boom):
                extracted = extract_mod.extract_file(note)
                mig.migrate_database(db, cmdlines=[], lock_held=False)
                conn = sqlite3.connect(str(db))
                try:
                    conn.execute(
                        "INSERT INTO attachments "
                        "(message_id, part_id, filename, mime, size, sha256, status) "
                        "VALUES ('msg-example-1', '1', 'note.txt', 'text/plain', 5, ?, 'present')",
                        ("ef" * 32,),
                    )
                    attachment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute(
                        "INSERT INTO attachment_extracts "
                        "(attachment_id, extractor, extractor_version, text, page_count, status, error, timings) "
                        "VALUES (?, 'text-stdlib', '1', ?, 1, 'ok', NULL, '{}')",
                        (attachment_id, extracted.text),
                    )
                    extract_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute(
                        "INSERT INTO attachment_chunks "
                        "(extract_id, chunk_index, page_start, page_end, text) "
                        "VALUES (?, 0, 1, 1, ?)",
                        (extract_id, extracted.text),
                    )
                    conn.commit()
                finally:
                    conn.close()
                hits = search_mod.search_attachments(db, "acmewidget")
            self.assertEqual(extracted.status, "ok")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].filename, "note.txt")


if __name__ == "__main__":
    unittest.main()
