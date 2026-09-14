#!/usr/bin/env python3
"""KOO-29 Warn before local-exec that may trigger macOS Allow sheets.

Docs/tests contract only. No live Mac writers. No live classify.
No live IMAP. No live SoR DB. No MailArchive. No Keychain reads.
No rem-legacy writer. No live machine SSH. No live Allow clicks.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Warn before local-exec that may trigger macOS Allow sheets"
ALLOWED_NO_INVENT = "do not invent click-paths"


class WarnBeforeLocalExecAllowSheetsOpsTests(unittest.TestCase):
    def test_contract_locks_warn_before_allow_sheet_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn(
            "warn the operator before any local-exec / Shell / machine action",
            text,
        )
        self.assertIn("macOS permission Allow sheets", text)
        self.assertIn("Documents/Desktop/Downloads", raw)
        self.assertIn("screen recording", text)
        self.assertIn("microphone", text)
        self.assertIn("camera", text)
        self.assertIn("similar Allow-sheet classes", text)
        self.assertIn(
            "Fail closed: if a local-exec, Shell, or machine action may raise an Allow sheet",
            raw,
        )
        self.assertIn("do not run it until the operator has been warned", text)
        self.assertIn("Do not invent click-paths", raw)
        self.assertIn("Name the permission class", raw)
        self.assertIn("Do not click the Allow sheet", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run live Mac writers", text)
        self.assertIn("does not run live classify", text)
        self.assertIn("does not run live IMAP", text)
        self.assertIn("does not open MailArchive or live sqlite", text)
        self.assertIn("does not write embed/SoR data", text)
        self.assertIn("does not read Keychain", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Warn before local-exec that may trigger macOS Allow sheets", raw)
        self.assertIn(
            "warn the operator before any local-exec / Shell / machine action",
            text,
        )
        self.assertIn("macOS permission Allow sheets", text)
        self.assertIn("Documents/Desktop/Downloads", raw)
        self.assertIn("screen recording", text)
        self.assertIn("microphone", text)
        self.assertIn("camera", text)
        self.assertIn("do not invent click-paths", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_local_exec_without_allow_sheet_warning(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_NO_INVENT, "")
        self.assertIn(
            "Fail closed: if a local-exec, Shell, or machine action may raise an Allow sheet",
            raw,
        )
        self.assertIn("do not run it until the operator has been warned", text)
        self.assertIn("Do not invent click-paths", raw)
        self.assertIn("Name the permission class", raw)
        self.assertIn("Do not click the Allow sheet", raw)
        self.assertIn("Documents/Desktop/Downloads", raw)
        self.assertIn("screen recording", text)
        self.assertIn("microphone", text)
        self.assertIn("camera", text)
        self.assertNotIn("invent click-paths", scrubbed)
        self.assertNotIn("run local-exec without warning", low)
        self.assertNotIn("skip the allow-sheet warning", low)
        self.assertNotIn("invent click-paths is ok", low)
        self.assertNotIn("click the allow sheet is ok", low)
        self.assertNotIn("this gate clicks allow sheets", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
