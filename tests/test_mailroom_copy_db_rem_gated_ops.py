#!/usr/bin/env python3
"""KOO-45 mailroom_copy_db rem-gated copy contract.

Docs/tests only. No live MBP→Mini copy. No rem-legacy touch.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
HELPER = ROOT / "scripts" / "mailroom_copy_db.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## mailroom_copy_db rem-gated copy"


class MailroomCopyDbRemGatedOpsTests(unittest.TestCase):
    def test_contract_locks_rem_gated_copy_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("Mini copy only when rem-legacy is not writing", text)
        self.assertIn("EXIT 0", raw)
        self.assertIn("No SMB/NFS dual-write", raw)
        self.assertIn("No live MBP→Mini copy", raw)
        self.assertIn("mailroom-daily-copy.sqlite", raw)
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
        self.assertIn("mailroom_copy_db rem-gated copy", raw)
        self.assertIn("Mini copy only when rem-legacy is not writing", text)
        self.assertIn("no SMB/NFS dual-write", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_daily_and_helper_lock_rem_gated_no_live_copy(self):
        for path in (DAILY, MAILROOM, HELPER):
            text = path.read_text(encoding="utf-8")
            self.assertIn("rem-legacy", text.lower(), msg=path.name)
            self.assertIn("SMB/NFS", text, msg=path.name)
            self.assertIn("EXIT 0", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        daily = DAILY.read_text(encoding="utf-8")
        self.assertIn("Mini copy only when", daily)
        self.assertIn("No live MBP→Mini", daily)
        helper = HELPER.read_text(encoding="utf-8")
        self.assertIn("Rem-gated copy", helper)
        self.assertIn("No live MBP→Mini", helper)

    def test_fail_closed_no_smb_nfs_dual_write_or_live_copy(self):
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        self.assertIn("do not dual-write over smb/nfs", low)
        self.assertNotIn("smb/nfs dual-write is allowed", low)
        self.assertNotIn("live copy while rem-legacy is writing", low)
        self.assertNotIn("this gate runs a live copy", low)
        self.assertNotIn("this gate starts rem-legacy", low)


if __name__ == "__main__":
    unittest.main()
