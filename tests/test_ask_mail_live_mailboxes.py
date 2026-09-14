#!/usr/bin/env python3
"""KOO-52: live_mailboxes + trash_live opt-in. Temp DB, read-side SELECT."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_mail  # noqa: E402
import semantic_search as ss  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


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
    folder: str,
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


class LiveMailboxFilterTests(unittest.TestCase):
    def test_live_mailboxes_filters_folders(self):
        conn = _conn()
        _add(conn, "inbox-1", present=1, folder="INBOX")
        _add(conn, "sent-1", present=1, folder="Sent")
        _add(conn, "del-1", present=1, folder="Deleted")
        _add(conn, "gone-1", present=0, folder="INBOX")
        hist = ss.fts_search(conn, "SDGE bill", k=10, live=False)
        self.assertEqual({h["message_id"] for h in hist}, {"inbox-1", "sent-1", "del-1", "gone-1"})
        boxed = ss.fts_search(
            conn, "SDGE bill", k=10, live=True, live_mailboxes="INBOX,Sent"
        )
        self.assertEqual({h["message_id"] for h in boxed}, {"inbox-1", "sent-1"})

    def test_trash_live_opt_in_adds_deleted(self):
        conn = _conn()
        _add(conn, "inbox-1", present=1, folder="INBOX")
        _add(conn, "del-1", present=1, folder="Deleted")
        no_trash = ss.fts_search(
            conn, "SDGE bill", k=10, live=True, live_mailboxes="INBOX"
        )
        self.assertEqual([h["message_id"] for h in no_trash], ["inbox-1"])
        with_trash = ss.fts_search(
            conn,
            "SDGE bill",
            k=10,
            live=True,
            live_mailboxes="INBOX",
            trash_live=True,
        )
        self.assertEqual({h["message_id"] for h in with_trash}, {"inbox-1", "del-1"})

    def test_q2_deferred_bare_live_unchanged(self):
        conn = _conn()
        _add(conn, "inbox-1", present=1, folder="INBOX")
        _add(conn, "del-1", present=1, folder="Deleted")
        live = ss.fts_search(conn, "SDGE bill", k=10, live=True)
        self.assertEqual({h["message_id"] for h in live}, {"inbox-1", "del-1"})
        trash_alone = ss.fts_search(
            conn, "SDGE bill", k=10, live=True, trash_live=True
        )
        self.assertEqual({h["message_id"] for h in trash_alone}, {"inbox-1", "del-1"})

    def test_cli_flags_default_off(self):
        parser = ask_mail.build_parser()
        option_strings = {
            opt
            for action in parser._actions
            for opt in (action.option_strings or ())
        }
        self.assertIn("--live-mailboxes", option_strings)
        self.assertIn("--trash-live", option_strings)
        args = parser.parse_args(["invoice"])
        cfg = ask_mail._cli_config(args)
        self.assertFalse(cfg["live"])
        self.assertIsNone(cfg["live_mailboxes"])
        self.assertFalse(cfg["trash_live"])
        flagged = parser.parse_args(
            ["--live", "--live-mailboxes", "INBOX", "--trash-live", "invoice"]
        )
        live_cfg = ask_mail._cli_config(flagged)
        self.assertTrue(live_cfg["live"])
        self.assertEqual(live_cfg["live_mailboxes"], "INBOX")
        self.assertTrue(live_cfg["trash_live"])

    def test_retrieve_hits_passes_filters(self):
        seen: dict[str, object] = {}

        def _fn(query, **kwargs):
            seen.update(kwargs)
            return []

        ask_mail.retrieve_hits(
            "invoice",
            db=Path("/tmp/mailroom-copy.sqlite"),
            k=8,
            lane=None,
            after=None,
            before=None,
            rerank=False,
            fts_only=True,
            retrieve_fn=_fn,
            retrieve_kwargs=None,
            live=True,
            live_mailboxes="INBOX",
            trash_live=True,
        )
        self.assertTrue(seen.get("live"))
        self.assertEqual(seen.get("live_mailboxes"), "INBOX")
        self.assertTrue(seen.get("trash_live"))

    def test_ask_mail_cli_stays_large(self):
        cli = SCRIPTS / "ask_mail.py"
        text = cli.read_text(encoding="utf-8")
        self.assertGreater(cli.stat().st_size, 10000)
        self.assertIn("live_mailboxes", text)
        self.assertIn("trash_live", text)
        self.assertNotIn("MCP stub", text)
        self.assertNotIn("LOADED_FROM_MCP_PUSH_ASK_JSON", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
