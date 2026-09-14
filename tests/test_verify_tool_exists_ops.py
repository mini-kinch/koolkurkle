#!/usr/bin/env python3
"""KOO-14 verify-tool-exists before Terminal AR gate.

Docs/tests contract only. No live machine SSH, no MailArchive, no
live sqlite, no Keychain reads, no rem-legacy writer.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class VerifyToolExistsBeforeTerminalArTests(unittest.TestCase):
    def test_contract_locks_verify_tool_exists_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("Verify tool exists before Terminal AR", raw)
        self.assertIn("Before a Terminal AR", text)
        self.assertIn('"run this tool"', raw)
        self.assertIn("confirm the binary or script exists", text)
        self.assertIn("named machine", text)
        self.assertIn("**MBP** vs **Mini**", raw)
        self.assertIn("Do not invent tool paths", raw)
        self.assertIn("Fail closed", raw)
        self.assertIn("no existence proof, no card", text)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("/usr/bin/<tool>", raw)
        self.assertIn("$HOME", raw)
        self.assertIn("/Users/<operator>/...", raw)
        self.assertIn("command -v <tool>", raw)
        self.assertIn("# Mini — prove the named binary exists", raw)
        self.assertIn("# MBP — prove the named binary exists", raw)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("does not", text.lower())
        self.assertIn("Keychain", raw)
        self.assertIn("rem-legacy", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_gate(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("verify-tool-exists", text)
        self.assertIn("Terminal AR", raw)
        self.assertIn("named machine MBP vs Mini", text)
        self.assertIn("do not invent tool paths", text)
        self.assertNotIn("/Users/", raw)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, raw)

    def test_fail_closed_without_existence_proof_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("Fail closed: no existence proof, no card", text)
        self.assertIn("Do not invent tool paths", raw)
        self.assertNotIn("if missing, invent", text.lower())
        self.assertNotIn("assume the tool exists", text.lower())


if __name__ == "__main__":
    unittest.main()
