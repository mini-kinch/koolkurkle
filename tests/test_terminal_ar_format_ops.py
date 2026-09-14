#!/usr/bin/env python3
"""KOO-18 Terminal AR format: one machine, one command, loud banner.

Docs/tests contract only. No live SoR DB, no MailArchive, no
Keychain reads, no rem-legacy writer, no live machine SSH.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Terminal AR format"


class TerminalArFormatOpsTests(unittest.TestCase):
    def test_contract_locks_terminal_ar_format_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("One host banner", text)
        self.assertIn("loud **MBP** or **Mini** banner", raw)
        self.assertIn("one command fence per copy button", text.lower())
        self.assertIn("Title equals body", raw)
        self.assertIn("title=body", raw)
        self.assertIn("No stacked interactive prompts in one paste", raw)
        self.assertIn("No multi-line paste that includes interactive read", raw)
        self.assertIn("Fail closed: if a card cannot follow this format, do not issue it", text)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("Keychain", raw)
        self.assertIn("rem-legacy", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_gate(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Terminal AR format", raw)
        self.assertIn("loud MBP or Mini", text)
        self.assertIn("one command per copy button", text)
        self.assertIn("title equals body", text)
        self.assertIn("no stacked interactive prompts in one paste", text)
        self.assertIn("no multi-line paste that includes interactive read", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_without_format_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("Fail closed: if a card cannot follow this format, do not issue it", text)
        self.assertIn("one command fence per copy button", text.lower())
        self.assertIn("title=body", raw)
        self.assertNotIn("title may differ from body", text.lower())
        self.assertNotIn("stack interactive prompts in one paste", text.lower().replace("no stacked interactive prompts in one paste", ""))
        self.assertNotIn("multi-line paste that includes interactive read is ok", text.lower())
        self.assertNotIn("two machines in one terminal ar", text.lower())
        self.assertNotIn("assume the focused window", text.lower())


if __name__ == "__main__":
    unittest.main()
