#!/usr/bin/env python3
"""KOO-60 fetch/auth error ≠ tombstone + UIDVALIDITY. No live IMAP."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import imap_fetch_error as fe  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class FetchErrorNotTombstoneTests(unittest.TestCase):
    def test_errors_and_empty_are_not_gone(self):
        self.assertFalse(
            fe.may_tombstone(fetch_ok=False, auth_ok=True, fetch_error="timeout")
        )
        self.assertFalse(
            fe.may_tombstone(fetch_ok=True, auth_ok=False, auth_error="login")
        )
        self.assertFalse(
            fe.may_tombstone(fetch_ok=True, auth_ok=True, empty_fetch=True)
        )
        self.assertTrue(
            fe.may_tombstone(
                fetch_ok=True, auth_ok=True, empty_fetch=False, listed_on_server=False
            )
        )
        empty = fe.tombstone_decision(
            fetch_ok=True, auth_ok=True, empty_fetch=True
        )
        self.assertFalse(empty["may_tombstone"])
        self.assertEqual(empty["reason"], "empty_fetch_neq_gone")

    def test_uid_uidvalidity_pair(self):
        pair = fe.persist_uid_pair("100", "9876")
        self.assertEqual(pair["uid"], "100")
        self.assertEqual(pair["uidvalidity"], "9876")
        with self.assertRaises(fe.FetchErrorTombstoneRefuse):
            fe.persist_uid_pair("100", None)
        self.assertTrue(fe.same_uid_identity("100", "9876", "100", "9876"))
        self.assertFalse(fe.same_uid_identity("100", "9876", "100", "1111"))

    def test_docs(self):
        for path in (
            ROOT / "docs" / "fetch-error-tombstone.md",
            ROOT / "docs" / "tombstone.md",
            ROOT / "docs" / "MAILROOM.md",
            ROOT / "docs" / "ops-terminal.md",
            ROOT / "README.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("tombstone", text.lower(), msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        fetch = (ROOT / "docs" / "fetch-error-tombstone.md").read_text(encoding="utf-8")
        self.assertIn("Fetch error ≠ tombstone", fetch)
        self.assertIn("Auth error ≠ tombstone", fetch)
        self.assertIn("Empty fetch ≠ gone", fetch)
        self.assertIn("UID + UIDVALIDITY", fetch)
        self.assertIn("No live IMAP", fetch)


if __name__ == "__main__":
    unittest.main()
