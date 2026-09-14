#!/usr/bin/env python3
"""KOO-12 repositories() before CloudAgent / Connect≠ACL gate.

Docs-contract only. No network, no MailArchive, no live sqlite,
no rem-legacy writer, no CloudAgent tooling changes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
PARKED = "9zjf9jpv7z-glitch"  # parked/historical GitHub owner — not live SoR
LIVE = "mini-kinch/koolkurkle"


class RepositoriesBeforeCloudAgentGateTests(unittest.TestCase):
    def test_contract_locks_connect_neq_acl_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split()).lower()
        self.assertIn("repositories() before cloudagent", text)
        self.assertIn("connect ≠ acl", text)
        self.assertIn("connect done is not repo acl done", text)
        self.assertIn("repositories()", raw)
        self.assertIn(LIVE.lower(), text)
        self.assertIn("before any cloudagent launch", text)
        self.assertIn("does not add cloudagent tooling", text)
        self.assertIn("does not change rem-legacy", text)
        self.assertIn("parked/historical", text)
        self.assertIn(PARKED, raw)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, raw)

        for lineno, line in enumerate(raw.splitlines(), 1):
            if PARKED in line:
                low = line.lower()
                self.assertTrue(
                    "parked" in low or "historical" in low,
                    msg="ops-terminal.md:%s names %s without parked/historical: %s"
                    % (lineno, PARKED, line),
                )

    def test_operators_can_find_the_gate(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split()).lower()
        self.assertIn("ops-terminal.md", raw)
        self.assertIn(LIVE.lower(), text)
        self.assertIn("connect ≠ acl", text)
        self.assertIn("repositories()", text)
        self.assertIn("cloudagent", text)
        self.assertNotIn(PARKED, raw)  # parked/historical; not live SoR
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, raw)


if __name__ == "__main__":
    unittest.main()
