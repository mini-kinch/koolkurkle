#!/usr/bin/env python3
"""KOO-64 rem-window freeze + lock lifetime + generate topology + Ready."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
FREEZE = ROOT / "docs" / "rem-window-freeze.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
LOCK = ROOT / "docs" / "pr0" / "with_writer_lock_DESIGN.md"
LOCK_PY = ROOT / "scripts" / "with_writer_lock.py"
GATES = ROOT / "docs" / "model-runtime-gates.md"


class RemWindowFreezeOpsTests(unittest.TestCase):
    def test_default_is_freeze_not_interleave(self):
        text = FREEZE.read_text(encoding="utf-8")
        self.assertIn("sor_increment=frozen", text)
        self.assertIn("freeze until EXIT", text)
        self.assertIn("Do **not** switch to interleave", text)
        self.assertIn("process-lifetime", text)
        self.assertIn("not a per-batch drop", text)
        self.assertIn("Retrieve on the SoR host", text)
        self.assertIn("Generate localhost", text)
        self.assertIn("127.0.0.1:1234", text)
        self.assertIn("Rem EXIT 0 handling is out of scope", text)
        self.assertNotIn("default is interleave", text.lower())
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_lock_lifetime_and_ops(self):
        lock = LOCK.read_text(encoding="utf-8")
        src = LOCK_PY.read_text(encoding="utf-8")
        self.assertIn("process-lifetime", lock)
        self.assertIn("sor_increment=frozen", lock)
        self.assertIn("process-lifetime", src)
        self.assertIn("sor_increment=frozen", src)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("Do **not** switch to interleave", ops)
        self.assertIn("process-lifetime for rem", ops)
        self.assertIn("generate localhost", ops.lower())
        readme = README.read_text(encoding="utf-8")
        self.assertIn("sor_increment=frozen", readme)

    def test_ready_handoff_rule(self):
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("Ready handoff (PASS or fail-open-only)", ops)
        self.assertIn("Docs PR Ready ≠ permission to enable", ops)
        self.assertIn("interface proof", ops.lower())
        self.assertIn("negative smoke", ops.lower())
        gates = GATES.read_text(encoding="utf-8")
        self.assertIn("Docs PR Ready ≠ permission to enable", gates)
        self.assertIn("batch bump / Mini MLX / sidecar", gates)


if __name__ == "__main__":
    unittest.main()
