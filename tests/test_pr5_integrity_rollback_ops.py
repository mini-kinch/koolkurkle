#!/usr/bin/env python3
"""KOO-61 PR-5 integrity + rollback + post-EXIT catch-up (docs only)."""

from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "pr5-cutover.md"
CATCHUP = ROOT / "docs" / "post-exit-catchup.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
PLIST = ROOT / "launchd" / "com.mailroom.daily.plist"
PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class Pr5IntegrityRollbackTests(unittest.TestCase):
    def test_checklist_expands_integrity_and_rollback(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        self.assertIn("Integrity pack", text)
        self.assertIn("Copy freshness", text)
        self.assertIn("Quote-strip generation match", text)
        self.assertIn("One cutover + one rollback", text)
        self.assertIn("RunAtLoad is a separate GO", text)
        self.assertIn("Does not enable PR-5 cutover", text)
        self.assertIn("Does not enable RunAtLoad", text)
        self.assertIn("copy_age", text)
        self.assertIn("post-exit-catchup.md", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_catchup_is_docs_only_before_pr5(self):
        text = CATCHUP.read_text(encoding="utf-8")
        self.assertIn("Do not run catch-up until rem-legacy EXIT 0", text)
        self.assertIn("human go", text)
        self.assertIn("IMAP + bodies-FTS", text)
        self.assertIn("with_writer_lock", text)
        self.assertIn("Mini ← SoR copy", text)
        self.assertIn("Integrity pack", text)
        self.assertIn("sor_increment=frozen", text)
        self.assertIn("Rem EXIT 0 handling is out of scope", text)
        self.assertIn("does not run live IMAP", text)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("Post-EXIT catch-up BEFORE PR-5", ops)
        self.assertIn("integrity pack", README.read_text(encoding="utf-8").lower())

    def test_still_does_not_enable_runatload_or_cutover(self):
        data = plistlib.loads(PLIST.read_bytes())
        self.assertEqual(
            data["EnvironmentVariables"]["MAILROOM_DB"],
            "__HOME__/MailArchive/mailroom-copy.sqlite",
        )
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        self.assertIn("do not enable runatload in this change", low)
        self.assertNotIn("this gate starts rem-legacy", low)


if __name__ == "__main__":
    unittest.main()
