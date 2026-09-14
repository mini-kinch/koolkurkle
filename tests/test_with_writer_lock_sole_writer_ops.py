#!/usr/bin/env python3
"""KOO-44 with_writer_lock sole-writer wrapper contract.

Docs/tests/fixtures. No rem-legacy start. No live SoR writers.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import with_writer_lock as wwl  # noqa: E402

OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
LOCK = ROOT / "docs" / "pr0" / "with_writer_lock_DESIGN.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## with_writer_lock sole-writer wrapper"


class WithWriterLockSoleWriterOpsTests(unittest.TestCase):
    def test_contract_locks_sole_writer_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("sole-writer wrapper", text)
        self.assertIn("Busy/lock refuse before a second writer", raw)
        self.assertIn("Shipping this guard", raw)
        self.assertIn("is not starting rem-legacy", text)
        self.assertIn("Do not steal", raw)
        self.assertIn("Do not start rem-legacy from this wrapper", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("with_writer_lock sole-writer wrapper", raw)
        self.assertIn("busy/lock refuse before second writer", text)
        self.assertIn("shipping this guard is not starting rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_design_locks_shipping_guard_neq_rem_legacy(self):
        text = LOCK.read_text(encoding="utf-8")
        self.assertIn("sole-writer", text.lower())
        self.assertIn("refuses before a second writer", text)
        self.assertIn("starting rem-legacy", text)
        self.assertIn("ops-terminal.md", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fixture_busy_lock_refuses_before_second_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "first")
            try:
                with self.assertRaises(wwl.WriterLockError) as ctx:
                    wwl.acquire_writer_lock(lock, "second")
                msg = str(ctx.exception)
                self.assertIn("writer lock held", msg)
                self.assertEqual(wwl.read_lock_info(lock).purpose, "first")
                self.assertEqual(wwl.read_lock_info(lock).hostname, socket.gethostname())
            finally:
                wwl.release_writer_lock(held)

    def test_fail_closed_shipping_guard_does_not_start_rem_legacy(self):
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        self.assertIn("shipping this guard is not starting rem-legacy", low)
        self.assertNotIn("this gate starts rem-legacy", low)
        self.assertNotIn("this document starts rem-legacy", low)
        self.assertNotIn("steal the writer lock", low)
        self.assertNotIn("second writer is allowed while busy", low)


if __name__ == "__main__":
    unittest.main()
