#!/usr/bin/env python3
"""KOO-51: canonical soft-delete one-pager under docs/. Docs only."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ONEPAGER = ROOT / "docs" / "soft-delete.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
OPS = ROOT / "docs" / "ops-terminal.md"
ASK = ROOT / "docs" / "ask_mail.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
STALE = (
    "soft-delete is standby",
    "soft-delete remains standby",
    "history default TBD",
)


class SoftDeleteOnePagerTests(unittest.TestCase):
    def test_canonical_one_pager_lands(self):
        self.assertTrue(ONEPAGER.is_file())
        text = ONEPAGER.read_text(encoding="utf-8")
        self.assertIn("Soft-delete one-pager", text)
        self.assertIn("§5 SQL maintenance denylist", text)
        self.assertIn("DELETE FROM messages", text)
        self.assertIn("DROP TABLE messages", text)
        self.assertIn("TRUNCATE", text)
        self.assertIn("present_on_server", text)
        self.assertIn("Deleted-folder ≠ present=0", text)
        self.assertIn("DECIDED", text)
        self.assertIn("not a standby contract", text)
        self.assertIn("icloud_mail_all.jsonl", text)
        self.assertIn("--live-mailboxes", text)
        self.assertIn("--trash-live", text)
        self.assertIn("Q2", text)
        self.assertIn("deferred", text)
        self.assertIn("--embed-live-only", text)
        self.assertIn("MCP stub", text)
        self.assertIn("MBP", text)
        self.assertIn("Mini", text)
        self.assertIn("MAILROOM.md", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        low = text.lower()
        for phrase in STALE:
            self.assertNotIn(phrase, low)

    def test_mailroom_links_canonical_one_pager(self):
        text = MAILROOM.read_text(encoding="utf-8")
        self.assertIn("soft-delete.md", text)
        self.assertIn("§5 Soft-delete (DECIDED)", text)
        self.assertIn("canonical", text.lower())

    def test_tombstone_and_ops_point_at_one_pager(self):
        for path in (TOMBSTONE, OPS, ASK):
            text = path.read_text(encoding="utf-8")
            self.assertIn("soft-delete.md", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


if __name__ == "__main__":
    unittest.main()
