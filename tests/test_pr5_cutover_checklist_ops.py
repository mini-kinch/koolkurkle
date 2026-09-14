#!/usr/bin/env python3
"""KOO-47 PR-5 cutover checklist (docs only — do not enable).

Docs/tests only. No RunAtLoad enable. No rem-legacy change.
"""

from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
CHECKLIST = ROOT / "docs" / "pr5-cutover.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
PLIST = ROOT / "launchd" / "com.mailroom.daily.plist"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## PR-5 cutover checklist (docs only — do not enable)"


class Pr5CutoverChecklistOpsTests(unittest.TestCase):
    def test_contract_locks_docs_only_do_not_enable(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("docs only", text)
        self.assertIn("gated on rem-legacy", text)
        self.assertIn("EXIT 0", raw)
        self.assertIn("Mini SoR switch steps", raw)
        self.assertIn("does **not** enable PR-5 cutover", raw)
        self.assertIn("does **not** enable RunAtLoad", raw)
        self.assertIn("pr5-cutover.md", raw)
        self.assertIn("Do not promote Mini", raw)
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
        self.assertIn("PR-5 cutover checklist", raw)
        self.assertIn("docs only", text)
        self.assertIn("do not enable", text)
        self.assertIn("gated on rem-legacy EXIT 0", text)
        self.assertIn("Mini SoR switch steps", text)
        self.assertIn("does not enable cutover or RunAtLoad", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_checklist_file_has_gates_and_mini_sor_switch(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        self.assertIn("docs only — do not enable", text)
        self.assertIn("gated on rem-legacy EXIT 0", text)
        self.assertIn("Mini SoR switch steps", text)
        self.assertIn("Does not enable PR-5 cutover", text)
        self.assertIn("Does not enable RunAtLoad", text)
        self.assertIn("Does not change rem-legacy", text)
        self.assertIn("mailroom.sqlite", text)
        self.assertIn("CoS GO", text)
        self.assertIn("ops-terminal.md", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_mailroom_and_daily_point_at_checklist_without_enabling(self):
        for path in (MAILROOM, DAILY):
            text = path.read_text(encoding="utf-8")
            self.assertIn("pr5-cutover.md", text, msg=path.name)
            self.assertIn("do not enable", text.lower(), msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

    def test_this_change_does_not_enable_runatload_or_cutover(self):
        data = plistlib.loads(PLIST.read_bytes())
        env = data["EnvironmentVariables"]
        self.assertEqual(env["MAILROOM_DB"], "__HOME__/MailArchive/mailroom-copy.sqlite")
        self.assertNotEqual(Path(env["MAILROOM_DB"]).name, "mailroom.sqlite")
        # Template RunAtLoad stays as previously checked in — not a cutover enable.
        self.assertTrue(data["RunAtLoad"])
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        allowed = (
            "this change does **not** enable pr-5 cutover and does **not** enable runatload",
            "do not enable runatload in this change",
            "do not enable cutover",
        )
        scrubbed = low
        for phrase in allowed:
            scrubbed = scrubbed.replace(phrase, "")
        self.assertNotIn("enable pr-5 cutover in this change", scrubbed)
        self.assertNotIn("enable runatload in this change", scrubbed)
        self.assertNotIn("cutover is not gated", low)
        self.assertNotIn("this gate starts rem-legacy", low)
        self.assertIn("do not enable runatload in this change", low)


if __name__ == "__main__":
    unittest.main()
