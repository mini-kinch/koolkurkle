#!/usr/bin/env python3
"""KOO-83: beginner guide + README About lead. Docs only.

Fail-closed presence: docs/beginner-guide.md exists and README links it.
No network, no IMAP, no MailArchive / live sqlite, no rem-legacy writer.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "beginner-guide.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
ABOUT = (
    "**koolkurkle** is a **local** personal-mail search and assistant toolkit."
)


class BeginnerGuideDocsTests(unittest.TestCase):
    def test_beginner_guide_exists(self):
        self.assertTrue(GUIDE.is_file(), msg="docs/beginner-guide.md must exist")
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIn("Mailroom — beginner guide", text)
        self.assertIn(ABOUT, text)
        self.assertIn("ZERO PII", text)
        self.assertIn("Rem untouched", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_readme_mentions_beginner_guide(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("beginner-guide", text)
        self.assertIn("docs/beginner-guide.md", text)
        self.assertIn(ABOUT, text)
        self.assertIn(
            "ask_mail with citations is the product; vectors/FTS/IMAP are infrastructure.",
            text,
        )
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)


if __name__ == "__main__":
    unittest.main()
