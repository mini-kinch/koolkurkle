#!/usr/bin/env python3
"""KOO-48 sor_health_pack read-only / Mini-copy OK.

Docs/tests only. No live SoR writer. Mini copy is not a second writer.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
HEALTH = ROOT / "docs" / "sor-health.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
SCRIPT = ROOT / "scripts" / "sor_health_pack.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## sor_health_pack read-only / Mini-copy OK"


class SorHealthReadOnlyMiniCopyOpsTests(unittest.TestCase):
    def test_contract_locks_readonly_mini_copy_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("sor_health_pack is read-only", text)
        self.assertIn("Mini on a copy DB is OK", raw)
        self.assertIn("is not a second writer", text)
        self.assertIn("replica, not a second live writer", text)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not write embed/SoR data", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("sor_health_pack read-only / Mini-copy OK", raw)
        self.assertIn("read-only health", text)
        self.assertIn("Mini on a copy DB is OK", text)
        self.assertIn("is not a second writer", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_health_docs_and_script_are_read_only(self):
        docs = HEALTH.read_text(encoding="utf-8")
        mail = MAILROOM.read_text(encoding="utf-8")
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("sor_health_pack is read-only", docs)
        self.assertIn("Mini on a copy DB is OK and is not a second writer", docs)
        self.assertIn("copy DB is OK; not a second writer", docs)
        self.assertIn("sor_health_pack is read-only", mail)
        self.assertIn("Mini on a copy DB is OK", mail)
        self.assertIn("Read-only", src)
        self.assertIn("Mini on a copy DB is OK", src)
        self.assertIn("not a second writer", src)
        self.assertNotIn("INSERT ", src)
        self.assertNotIn("UPDATE ", src)
        self.assertNotIn("DELETE FROM", src)
        for text in (docs, mail, src):
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay)

    def test_fail_closed_health_is_not_a_writer(self):
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        self.assertIn("health is read-only", low)
        self.assertNotIn("health pack writes the db", low)
        self.assertNotIn("mini copy is a second writer", low)
        self.assertNotIn("this gate starts rem-legacy", low)
        self.assertNotIn("this document starts a second writer", low)


if __name__ == "__main__":
    unittest.main()
