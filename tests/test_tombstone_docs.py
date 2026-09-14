#!/usr/bin/env python3
"""Doc-contract for tombstone / never-purge SoR. No network, no IMAP."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOMBSTONE = ROOT / "docs" / "tombstone.md"
OPS = ROOT / "docs" / "ops-terminal.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
README = ROOT / "README.md"
HEALTH = ROOT / "docs" / "sor-health.md"
CHILD = ROOT / "scripts" / "imap_tombstone.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class TombstoneNeverPurgeDocTests(unittest.TestCase):
    def test_contract_locks_never_purge_language(self):
        text = TOMBSTONE.read_text(encoding="utf-8")
        self.assertIn("never-purge", text)
        self.assertIn("Never physically delete iCloud or server mail", text)
        self.assertIn("Local tombstone only", text)
        self.assertIn("present_on_server", text)
        self.assertIn("STORE \\Deleted", text)
        self.assertIn("EXPUNGE", text)
        self.assertIn("imap_tombstone.py", text)
        self.assertIn("bind_copy_db", text)
        self.assertIn("Do not", text)
        self.assertIn("implement live IMAP delete here", text)
        self.assertIn("ops-terminal.md", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_child_comment_matches_contract(self):
        text = CHILD.read_text(encoding="utf-8")
        self.assertIn("Never physically delete iCloud or server mail", text)
        self.assertIn("Local tombstone only", text)
        self.assertIn("present_on_server", text)
        self.assertIn("EXPUNGE", text)
        self.assertIn("No IMAP sockets", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)

    def test_operators_can_find_the_contract(self):
        for path in (OPS, DAILY, README, HEALTH):
            text = path.read_text(encoding="utf-8")
            self.assertIn("tombstone.md", text, msg=path.name)
            self.assertIn("local tombstone only", text.lower(), msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


if __name__ == "__main__":
    unittest.main()
