#!/usr/bin/env python3
"""KOO-17 SWITCH TO Mini/MBP before machine-specific Terminal AR.

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
HEADING = "## SWITCH TO Mini/MBP before machine-specific Terminal AR"


class SwitchToMiniMbpBeforeTerminalArTests(unittest.TestCase):
    def test_contract_locks_switch_to_machine_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("Before a machine-specific Terminal AR", text)
        self.assertIn("last user input", text)
        self.assertIn("**Sent-from-machine**", raw)
        self.assertIn("**hostname** proof only", raw)
        self.assertIn("cannot see the focused Terminal window", text)
        self.assertIn("**SWITCH TO MBP**", raw)
        self.assertIn("**SWITCH TO Mini**", raw)
        self.assertIn("explicitly before or with the AR", text)
        self.assertIn("Fail closed: no host proof, no machine-specific card", text)
        self.assertIn("One machine per Terminal AR", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
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
        self.assertIn("SWITCH TO Mini/MBP", raw)
        self.assertIn("machine-specific Terminal AR", text)
        self.assertIn("Sent-from-machine", text)
        self.assertIn("hostname proof only", text)
        self.assertIn("one machine per AR", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_without_host_proof_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("Fail closed: no host proof, no machine-specific card", text)
        self.assertIn("hostname** proof only", raw)
        self.assertIn("cannot see the focused Terminal window", text)
        self.assertNotIn("assume the focused window", text.lower())
        self.assertNotIn("guess the host", text.lower())
        self.assertNotIn("skip switch to when mismatch", text.lower())
        self.assertNotIn("two machines in one terminal ar", text.lower())


if __name__ == "__main__":
    unittest.main()
