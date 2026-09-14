#!/usr/bin/env python3
"""KOO-15 MBP SoR vs Mini copy-only ops contract.

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


class MbpSorMiniCopyOnlyContractTests(unittest.TestCase):
    def test_contract_locks_mbp_sor_mini_copy_only_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("MBP SoR vs Mini copy-only", raw)
        self.assertIn("**MBP** is the live Source of Record", raw)
        self.assertIn("`mailroom.sqlite`", raw)
        self.assertIn("**Mini** is copy-only until PR-5", raw)
        self.assertIn("No Mini writers against SoR", raw)
        self.assertIn("PR-5 cutover is still gated on rem-legacy", text)
        self.assertIn("EXIT 0", raw)
        self.assertIn("mailroom-copy.sqlite", raw)
        self.assertIn("mailroom-daily-copy.sqlite", raw)
        self.assertIn("hard refuse", text)
        self.assertIn("db_mode=refused", raw)
        self.assertIn("Do not start a Mini writer against SoR", raw)
        self.assertIn("Do not promote Mini", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not open MailArchive or live sqlite", text)
        self.assertIn("read Keychain", text)
        self.assertIn("SSH a live machine", text)
        self.assertIn("change rem-legacy", text)
        self.assertNotIn("Mini writers against SoR are allowed", text)
        self.assertNotIn("promote Mini before rem-legacy EXIT 0", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("MBP SoR vs Mini copy-only", raw)
        self.assertIn("MBP is the live Source of Record", text)
        self.assertIn("mailroom.sqlite", raw)
        self.assertIn("Mini is copy-only", text)
        self.assertIn("no Mini writers against SoR", text)
        self.assertIn("PR-5 cutover still gated on rem-legacy EXIT 0", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_mini_is_not_sor_writer(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("No Mini writers against SoR", raw)
        self.assertIn("copy-only until PR-5", text)
        self.assertIn("gated on rem-legacy", text)
        self.assertNotIn("Mini may write SoR", text)
        self.assertNotIn("Mini is the live Source of Record", text)
        self.assertNotIn("cutover is not gated", text.lower())


if __name__ == "__main__":
    unittest.main()
