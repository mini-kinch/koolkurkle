#!/usr/bin/env python3
"""KOO-36 After user PASS on a check: ack and next AR — no re-ask.

Docs/tests contract only. No live Mac writers. No live classify.
No live IMAP. No live SoR DB. No MailArchive. No Keychain reads.
No rem-legacy writer. No live machine SSH. No live gh/network debug.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## After user PASS on a check: ack and next AR — no re-ask"
ALLOWED_DO_NOT_REISSUE = "do not re-issue the same check"
ALLOWED_DO_NOT_REASK = "do not re-ask the same check"
ALLOWED_FAIL_CLOSED = (
    "if the user already passed / pasted for a check, ack pass and "
    "proceed to the next ar"
)


class AfterUserPassAckAndNextArNoReaskOpsTests(unittest.TestCase):
    def test_contract_locks_ack_pass_and_next_ar_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("after user PASS / paste for a check", text)
        self.assertIn("ack PASS and proceed to the next AR", text)
        self.assertIn("do not re-issue the same check", text)
        self.assertIn("ack PASS and ship the next AR", text)
        self.assertIn("Do not re-ask the same check", raw)
        self.assertIn(
            "Fail closed: if the user already PASSed / pasted for a check",
            raw,
        )
        self.assertIn("ack PASS and proceed to the next AR", text)
        self.assertIn("Do not re-issue the same check", raw)
        self.assertIn("Do not re-ask the same check", raw)
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
        self.assertIn("After user PASS on a check", raw)
        self.assertIn("ack PASS and proceed to the next AR", text)
        self.assertIn("do not re-issue the same check", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_reask_after_user_pass(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low
        for allowed in (
            ALLOWED_DO_NOT_REISSUE,
            ALLOWED_DO_NOT_REASK,
            ALLOWED_FAIL_CLOSED,
        ):
            scrubbed = scrubbed.replace(allowed, "")
        self.assertIn(
            "Fail closed: if the user already PASSed / pasted for a check",
            raw,
        )
        self.assertIn("ack PASS and proceed to the next AR", text)
        self.assertIn("Do not re-issue the same check", raw)
        self.assertIn("Do not re-ask the same check", raw)
        self.assertNotIn("re-issue the same check after pass is ok", scrubbed)
        self.assertNotIn("re-ask the same check after pass is ok", scrubbed)
        self.assertNotIn("re-issue the same check", scrubbed)
        self.assertNotIn("re-ask the same check", scrubbed)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)
        self.assertNotIn("this gate runs live gh", low)


if __name__ == "__main__":
    unittest.main()
