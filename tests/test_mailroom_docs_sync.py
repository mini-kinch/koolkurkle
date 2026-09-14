#!/usr/bin/env python3
"""KOO-38 MAILROOM.md soft-delete / history docs sync. No network."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAILROOM = ROOT / "docs" / "MAILROOM.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
OPS = ROOT / "docs" / "ops-terminal.md"
ASK = ROOT / "docs" / "ask_mail.md"
README = ROOT / "README.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
STALE = (
    "soft-delete standby",
    "history default TBD",
    "history default is standby",
    "live default",
)


class MailroomDocsSyncTests(unittest.TestCase):
    def test_mailroom_locks_decided_language(self):
        text = MAILROOM.read_text(encoding="utf-8")
        self.assertIn("§5 Soft-delete (DECIDED)", text)
        self.assertIn("not soft-delete standby", text)
        self.assertIn("History default (Q1 DECIDED)", text)
        self.assertIn("Deleted-folder ≠ present=0", text)
        self.assertIn("present_on_server", text)
        self.assertIn("purge", text)
        self.assertIn("expunge", text)
        self.assertIn("empty-trash", text)
        self.assertIn("delete-gone", text)
        self.assertIn("drop-messages", text)
        self.assertIn("additive SELECT", text)
        self.assertIn("`--live`", text)
        self.assertIn("opt-in", text)
        self.assertIn("/opt/homebrew/opt/curl/bin/curl", text)
        self.assertIn("mailroom.imap.app-password", text)
        self.assertIn("copy-only until pr-5", text.lower())
        self.assertIn("mailroom-copy.sqlite", text)
        self.assertIn("do not restart rem", text.lower())
        self.assertIn("quote-strip", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        low = text.lower()
        for phrase in STALE:
            self.assertNotIn(phrase, low)

    def test_ops_docs_sync_and_retire_standby(self):
        for path in (TOMBSTONE, OPS, ASK, README, DAILY):
            text = path.read_text(encoding="utf-8")
            low = text.lower()
            self.assertIn("history", low, msg=path.name)
            self.assertIn("opt-in", low, msg=path.name)
            for phrase in STALE:
                self.assertNotIn(phrase, low, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        tomb = TOMBSTONE.read_text(encoding="utf-8")
        self.assertIn("Deleted-folder ≠ present=0", tomb)
        self.assertIn("DECIDED", tomb)
        self.assertIn("MAILROOM.md", tomb)


if __name__ == "__main__":
    unittest.main()
