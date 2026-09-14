#!/usr/bin/env python3
"""KOO-84 Heavy-09: beginner-guide polish + README About lede. Docs only.

Fail-closed presence: docs/beginner-guide.md exists, README leads with About,
relative links under docs/ stay sibling-relative. No network, no IMAP,
no MailArchive / live sqlite, no rem-legacy writer.
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
PRODUCT_LINE = (
    "ask_mail with citations is the product; vectors/FTS/IMAP are infrastructure."
)
RELATIVE_LINKS = (
    "[ask_mail.md](ask_mail.md)",
    "[sor-health.md](sor-health.md)",
    "[MAILROOM.md](MAILROOM.md)",
    "[soft-delete.md](soft-delete.md)",
    "[tombstone.md](tombstone.md)",
    "[embed-backfill.md](embed-backfill.md)",
    "[att0-constraints.md](att0-constraints.md)",
    "[unified-search-design.md](unified-search-design.md)",
    "[ops-terminal.md](ops-terminal.md)",
    "[../scripts/README.mailroom-daily.md](../scripts/README.mailroom-daily.md)",
)


class BeginnerGuideDocsTests(unittest.TestCase):
    def test_beginner_guide_exists(self):
        self.assertTrue(GUIDE.is_file(), msg="docs/beginner-guide.md must exist")
        text = GUIDE.read_text(encoding="utf-8")
        self.assertIn("Mailroom — beginner guide", text)
        self.assertIn(ABOUT, text)
        self.assertIn("## About (short)", text)
        self.assertIn("**ask_mail with citations is the product.**", text)
        self.assertIn("Vectors, FTS, and IMAP are infrastructure.", text)
        self.assertIn("design-only", text)
        self.assertIn("qwen3-embedding:8b", text)
        self.assertIn("mailroom-copy.sqlite", text)
        self.assertIn("One writer at a time", text)
        self.assertIn("Body embeddings exist", text)
        self.assertIn("HARD DECKs", text)
        self.assertIn("ZERO PII", text)
        self.assertIn("Rem untouched", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_beginner_guide_relative_links_are_docs_sibling(self):
        text = GUIDE.read_text(encoding="utf-8")
        for link in RELATIVE_LINKS:
            self.assertIn(link, text, msg="missing sibling-relative link %s" % link)
        self.assertNotIn("[docs/ask_mail.md]", text)
        self.assertNotIn("](docs/ask_mail.md)", text)
        self.assertNotIn("](docs/sor-health.md)", text)
        self.assertNotIn("](docs/MAILROOM.md)", text)

    def test_readme_mentions_beginner_guide(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("beginner-guide", text)
        self.assertIn("docs/beginner-guide.md", text)
        self.assertIn(ABOUT, text)
        self.assertIn(PRODUCT_LINE, text)
        self.assertIn("HARD DECKs stay **below** this About lede", text)
        about_at = text.find(ABOUT)
        guide_at = text.find("docs/beginner-guide.md")
        decks_at = text.find("## Mini daily RAG")
        self.assertGreaterEqual(about_at, 0)
        self.assertGreater(guide_at, about_at)
        self.assertGreater(decks_at, guide_at)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)


if __name__ == "__main__":
    unittest.main()
