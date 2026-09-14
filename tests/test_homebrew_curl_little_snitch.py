#!/usr/bin/env python3
"""KOO-53: Homebrew curl Little Snitch allow. No live IMAP."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import imap_fetch_bodies_fts as bodies  # noqa: E402

OPS = ROOT / "docs" / "ops-terminal.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
README = ROOT / "README.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HOMEBREW = "/opt/homebrew/opt/curl/bin/curl"
APPLE = "/usr/bin/curl"
NOTE = "Apple /usr/bin/curl Little Snitch allow does not cover Homebrew curl"


class HomebrewCurlLittleSnitchTests(unittest.TestCase):
    def test_checklist_names_separate_allow(self):
        items = bodies.little_snitch_brew_curl_checklist()
        blob = " ".join(items)
        self.assertIn(NOTE, items)
        self.assertIn(HOMEBREW, blob)
        self.assertIn(APPLE, blob)
        self.assertIn("Little Snitch allow", blob)
        self.assertIn("No live IMAP", blob)
        self.assertIn("No Keychain", blob)
        self.assertEqual(bodies.LITTLE_SNITCH_APPLE_CURL_NOTE, NOTE)

    def test_docs_operator_checklist(self):
        for path in (OPS, MAILROOM, README, DAILY):
            text = path.read_text(encoding="utf-8")
            self.assertIn(NOTE, text, msg=path.name)
            self.assertIn(HOMEBREW, text, msg=path.name)
            self.assertIn("Little Snitch", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

    def test_apple_allow_is_not_brew_allow(self):
        src = (SCRIPTS / "imap_fetch_bodies_fts.py").read_text(encoding="utf-8")
        self.assertIn(NOTE, src)
        self.assertIn(HOMEBREW, src)
        self.assertIn("needs its own Little Snitch allow", src)
        self.assertNotIn("live IMAP from this gate is ok", src.lower())


if __name__ == "__main__":
    unittest.main()
