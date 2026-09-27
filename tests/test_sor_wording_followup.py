"""Runtime wording lock for the Mini-daily SoR writer follow-up.

Hermetic: in-process parser help only. No Keychain, no network, no curl.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pr5_preflight as pre  # noqa: E402
import sor_writer_gate as gate  # noqa: E402

CANONICAL = (
    "The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only)."
)
STALE_HELP = "confirm copy-only MAILROOM_DB"


class MbpEightPmRefuseLabelTests(unittest.TestCase):
    def test_refuse_label_names_mbp_non_writer(self):
        label = gate.REFUSE_WHILE_REM_ON_LIVE_SOR[0]
        self.assertIn("the MBP is a non-writer (rollback, read-only)", label)
        self.assertNotIn("Classic MBP 8pm", label)
        self.assertNotIn("→ SoR", label)


class RepoPlistHelpTests(unittest.TestCase):
    def test_repo_plist_help_states_sole_sor_writer(self):
        help_text = " ".join(pre.build_parser().format_help().split())
        self.assertIn(CANONICAL, help_text)
        self.assertNotIn("copy-only", help_text)
        self.assertNotIn(STALE_HELP, help_text)


if __name__ == "__main__":
    unittest.main()
