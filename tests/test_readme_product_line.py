#!/usr/bin/env python3
"""KOO-63 README product line + thread-expansion cap as injection control."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import semantic_search as ss  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class ProductLineAndThreadCapTests(unittest.TestCase):
    def test_readme_product_sentence(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "ask_mail with citations is the product; vectors/FTS/IMAP are infrastructure.",
            text,
        )
        self.assertIn("Thread expansion cap as injection control", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_thread_expand_cap_is_injection_control(self):
        self.assertEqual(ss.THREAD_EXPAND_LAST_N, 3)
        self.assertEqual(ss.THREAD_EXPAND_CAP, 8)
        src = (SCRIPTS / "semantic_search.py").read_text(encoding="utf-8")
        self.assertIn("Injection control", src)
        ask = (ROOT / "docs" / "ask_mail.md").read_text(encoding="utf-8")
        self.assertIn("injection control", ask)
        self.assertIn("root + last 3", ask)
        self.assertIn("cap 8", ask)


if __name__ == "__main__":
    unittest.main()
