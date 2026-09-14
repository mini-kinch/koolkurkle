#!/usr/bin/env python3
"""KOO-40 --live additive SELECT (present_on_server). Temp DB only."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import semantic_search as ss  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE messages (
          id TEXT PRIMARY KEY,
          date_utc TEXT,
          from_addr TEXT,
          subject TEXT,
          snippet TEXT,
          lane TEXT,
          folder TEXT,
          present_on_server INTEGER,
          thread_id TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
          id UNINDEXED, subject, body, from_addr, tokenize='porter unicode61'
        );
        """
    )
    return conn


def _add(
    conn: sqlite3.Connection,
    mid: str,
    *,
    present: int,
    folder: str = "INBOX",
    subject: str = "SDGE bill",
    body: str = "electric invoice",
) -> None:
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
        (mid, "2026-09-01T00:00:00Z", "a@example.com", subject, body, "inbox", folder, present, mid),
    )
    conn.execute(
        "INSERT INTO messages_fts VALUES (?,?,?,?)",
        (mid, subject, body, "a@example.com"),
    )
    conn.commit()


class LiveSelectFilterTests(unittest.TestCase):
    def test_history_default_includes_tombstoned(self):
        conn = _conn()
        _add(conn, "live-1", present=1, folder="INBOX")
        _add(conn, "gone-1", present=0, folder="INBOX")
        _add(conn, "deleted-folder", present=1, folder="Deleted")
        hist = ss.fts_search(conn, "SDGE bill", k=10, live=False)
        ids = {h["message_id"] for h in hist}
        self.assertEqual(ids, {"live-1", "gone-1", "deleted-folder"})
        live = ss.fts_search(conn, "SDGE bill", k=10, live=True)
        live_ids = {h["message_id"] for h in live}
        self.assertEqual(live_ids, {"live-1", "deleted-folder"})
        self.assertNotIn("gone-1", live_ids)

    def test_deleted_folder_is_not_present_zero(self):
        conn = _conn()
        _add(conn, "in-deleted", present=1, folder="Deleted")
        self.assertTrue(ss.message_present_on_server(conn, "in-deleted"))
        hits = ss.fts_search(conn, "SDGE bill", k=5, live=True)
        self.assertEqual([h["message_id"] for h in hits], ["in-deleted"])

    def test_missing_column_fail_open(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE messages (
              id TEXT PRIMARY KEY,
              date_utc TEXT,
              from_addr TEXT,
              subject TEXT,
              snippet TEXT,
              lane TEXT,
              thread_id TEXT
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(
              id UNINDEXED, subject, body, from_addr, tokenize='porter unicode61'
            );
            INSERT INTO messages VALUES
              ('m1','2026-09-01T00:00:00Z','a@example.com','SDGE bill','x','inbox','t');
            INSERT INTO messages_fts VALUES ('m1','SDGE bill','electric invoice','a@example.com');
            """
        )
        hits = ss.fts_search(conn, "SDGE bill", k=5, live=True)
        self.assertEqual([h["message_id"] for h in hits], ["m1"])
        self.assertTrue(ss.message_present_on_server(conn, "m1"))


if __name__ == "__main__":
    unittest.main()
