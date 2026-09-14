#!/usr/bin/env python3
"""KOO-35 Continuous keepgoing: immediate next AR after Done.

Docs/tests contract only. No live Mac writers. No live classify.
No live IMAP. No live SoR DB. No MailArchive. No Keychain reads.
No rem-legacy writer. No live machine SSH.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Continuous keepgoing: immediate next AR after Done"
ALLOWED_FORBID = 'forbid soft pause fillers like "next judgment when you want"'
ALLOWED_FAIL_CLOSED = (
    "if a task on an authorized chain is done, immediately issue the "
    "next ar/task"
)
ALLOWED_DO_NOT_INSERT = "do not insert a soft pause filler"
ALLOWED_DO_NOT_SAY = 'do not say "next judgment when you want"'


class ContinuousKeepgoingImmediateNextArAfterDoneOpsTests(unittest.TestCase):
    def test_contract_locks_keepgoing_immediate_next_ar_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("after Done on an authorized chain", text)
        self.assertIn("immediately issue the next AR/task", text)
        self.assertIn("forbid soft pause fillers", text)
        self.assertIn("next judgment when you want", text)
        self.assertIn("immediately issues the next Action required / next task", text)
        self.assertIn("Do not insert a soft pause", raw)
        self.assertIn(
            "Fail closed: if a task on an authorized chain is Done",
            raw,
        )
        self.assertIn("immediately issue the next AR/task", text)
        self.assertIn("Do not insert a soft pause filler", raw)
        self.assertIn('Do not say "next judgment when you want"', raw)
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
        self.assertIn("Continuous keepgoing", raw)
        self.assertIn("after Done on an authorized chain", text)
        self.assertIn("immediately issue the next AR/task", text)
        self.assertIn("forbid soft pause fillers", text)
        self.assertIn("next judgment when you want", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_soft_pause_after_done_on_authorized_chain(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low
        for allowed in (
            ALLOWED_FORBID,
            ALLOWED_FAIL_CLOSED,
            ALLOWED_DO_NOT_INSERT,
            ALLOWED_DO_NOT_SAY,
        ):
            scrubbed = scrubbed.replace(allowed, "")
        self.assertIn(
            "Fail closed: if a task on an authorized chain is Done",
            raw,
        )
        self.assertIn("immediately issue the next AR/task", text)
        self.assertIn("Do not insert a soft pause filler", raw)
        self.assertIn('Do not say "next judgment when you want"', raw)
        self.assertIn("forbid soft pause fillers", text)
        self.assertNotIn("soft pause fillers are ok", scrubbed)
        self.assertNotIn("next judgment when you want is ok", scrubbed)
        self.assertNotIn("pause after done on an authorized chain", scrubbed)
        self.assertNotIn("wait for the user to ask after done", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
