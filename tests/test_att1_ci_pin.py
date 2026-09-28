#!/usr/bin/env python3
"""Q10: ATT-1 CI pins Python and OpenSSL, not Mini curl."""

from __future__ import annotations

import ssl
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Att1CiPinTests(unittest.TestCase):
    def test_workflow_pins_python_312_and_not_mini_curl(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        note = (ROOT / "docs" / "att" / "att1-ci-pin.md").read_text(encoding="utf-8")
        self.assertIn('python-version: "3.12"', workflow)
        self.assertNotIn("8.7.1", workflow)
        self.assertEqual(sys.version_info[:2], (3, 12))
        self.assertTrue(ssl.OPENSSL_VERSION)
        self.assertIn("3.12", note)
        self.assertIn("ssl.OPENSSL_VERSION", note)
        self.assertIn("8.7.1", note)
        self.assertIn("not a CI pin", note)
        self.assertIn("not proof for Mini", note)
        self.assertNotIn("8.7.1", workflow)


if __name__ == "__main__":
    unittest.main()
