#!/usr/bin/env python3
"""Operator text for the Mini SoR-basename retrieve refuse."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import retrieve_db_labels as labels  # noqa: E402


class MiniSorBasenameMessageTests(unittest.TestCase):
    def test_refuse_keeps_copy_db_instruction(self):
        with self.assertRaises(labels.RetrieveLabelRefuse) as caught:
            labels.retrieve_db_labels(Path("mailroom.sqlite"), host="Mini")
        message = str(caught.exception)
        self.assertIn("Mini retrieve must use a copy DB", message)
        self.assertNotIn("never imply live/SoR", message)


if __name__ == "__main__":
    unittest.main()
