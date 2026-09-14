#!/usr/bin/env python3
"""KOO-49: fail-closed SQL denylist. No SoR open, no live writers."""

from __future__ import annotations

import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refuse_sql_maintenance as rsm  # noqa: E402
import sqlite_pragmas  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
DENIED = (
    "DELETE FROM messages",
    "DELETE FROM messages WHERE id = 1",
    'DELETE FROM "messages"',
    "DELETE FROM main.messages",
    "DROP TABLE messages",
    "DROP TABLE IF EXISTS messages",
    "DROP TABLE IF EXISTS `messages`",
    "TRUNCATE TABLE messages",
    "TRUNCATE messages",
    "TRUNCATE",
)
ALLOWED = (
    "SELECT * FROM messages",
    "DELETE FROM messages_ids WHERE id = ?",
    "DELETE FROM messages_fts WHERE id = ?",
    "DELETE FROM message_embeddings WHERE message_id = ?",
    "DROP TABLE messages_ids",
    "DROP TABLE IF EXISTS messages_fts",
    "DROP TABLE IF EXISTS message_embeddings",
    "PRAGMA integrity_check",
)


class SqlMaintenanceDenylistTests(unittest.TestCase):
    def test_refuses_delete_drop_truncate_messages(self):
        for sql in DENIED:
            verb = rsm.find_denied_sql(sql)
            self.assertIsNotNone(verb, msg=sql)
            with self.assertRaises(rsm.SqlMaintenanceRefuse) as ctx:
                rsm.refuse_sql_maintenance(sql)
            msg = str(ctx.exception)
            self.assertIn(rsm.REFUSE_PREFIX, msg)
            self.assertIn("present_on_server", msg)
            self.assertIn("soft-delete.md", msg)
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, msg)

    def test_allows_sibling_tables_and_select(self):
        for sql in ALLOWED:
            self.assertIsNone(rsm.find_denied_sql(sql), msg=sql)
            rsm.refuse_sql_maintenance(sql)

    def test_comments_cannot_bypass(self):
        with self.assertRaises(rsm.SqlMaintenanceRefuse):
            rsm.refuse_sql_maintenance("/* safe */ DELETE FROM messages")
        with self.assertRaises(rsm.SqlMaintenanceRefuse):
            rsm.refuse_sql_maintenance("TRUNCATE -- comment\nTABLE messages")

    def test_execute_sql_refuses_without_opening_sor(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages (id TEXT)")
        conn.execute("CREATE TABLE messages_ids (id TEXT)")
        with self.assertRaises(rsm.SqlMaintenanceRefuse):
            rsm.execute_sql(conn, "DELETE FROM messages")
        with self.assertRaises(rsm.SqlMaintenanceRefuse):
            rsm.execute_sql(conn, "DROP TABLE messages")
        with self.assertRaises(rsm.SqlMaintenanceRefuse):
            rsm.execute_sql(conn, "TRUNCATE TABLE messages")
        rsm.execute_sql(conn, "DELETE FROM messages_ids WHERE id = ?", ("x",))
        row = rsm.execute_sql(conn, "SELECT COUNT(*) FROM messages").fetchone()
        self.assertEqual(row[0], 0)

    def test_pragma_helper_refuses_denied_sql(self):
        conn = sqlite3.connect(":memory:")
        with self.assertRaises(rsm.SqlMaintenanceRefuse):
            sqlite_pragmas._exec_pragma(conn, "DELETE FROM messages")
        sqlite_pragmas.apply_reader_pragmas(conn)

    def test_cli_exits_2(self):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = rsm.main(["DELETE FROM messages"])
        self.assertEqual(rc, 2)
        self.assertIn(rsm.REFUSE_PREFIX, err.getvalue())
        self.assertNotIn("ok:", out.getvalue())
        with redirect_stderr(io.StringIO()), redirect_stdout(out):
            self.assertEqual(rsm.main(["SELECT id FROM messages"]), 0)

    def test_source_has_no_pii(self):
        text = (SCRIPTS / "refuse_sql_maintenance.py").read_text(encoding="utf-8")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
