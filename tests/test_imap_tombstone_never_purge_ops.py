#!/usr/bin/env python3
"""KOO-43 imap_tombstone never STORE Deleted / EXPUNGE / Trash-purge.

Docs/tests fail-closed. No live IMAP. No rem-legacy writer.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
CHILD = ROOT / "scripts" / "imap_tombstone.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## IMAP tombstone never STORE Deleted / EXPUNGE"


class ImapTombstoneNeverPurgeOpsTests(unittest.TestCase):
    def test_contract_locks_local_tombstone_refuse_verbs(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("local `present_on_server` only", text)
        self.assertIn("STORE \\Deleted", raw)
        self.assertIn("EXPUNGE", raw)
        self.assertIn("Trash-purge", raw)
        self.assertIn("Refuse those verbs", raw)
        self.assertIn("Do not implement live IMAP delete here", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run live IMAP", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("IMAP tombstone never STORE Deleted / EXPUNGE", raw)
        self.assertIn("local present_on_server only", text)
        self.assertIn("Trash-purge", raw)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_tombstone_docs_and_child_refuse_imap_verbs(self):
        tomb = TOMBSTONE.read_text(encoding="utf-8")
        mail = MAILROOM.read_text(encoding="utf-8")
        child = CHILD.read_text(encoding="utf-8")
        for text in (tomb, mail, child):
            self.assertIn("present_on_server", text)
            self.assertIn("Deleted", text)
            self.assertIn("EXPUNGE", text)
            self.assertIn("Trash-purge", text)
            self.assertIn("Refuse those verbs", text)
            self.assertNotIn("imaplib", text)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay)

    def test_fail_closed_no_live_imap_purge(self):
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        self.assertIn("refuse those verbs", low)
        self.assertNotIn("imap store \\deleted is allowed", low)
        self.assertNotIn("expunge the server copy", low)
        self.assertNotIn("trash-purge is allowed", low)
        self.assertNotIn("this gate runs live imap", low)
        self.assertNotIn("this gate starts rem-legacy", low)


if __name__ == "__main__":
    unittest.main()
