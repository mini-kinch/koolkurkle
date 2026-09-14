#!/usr/bin/env python3
"""KOO-24 After Action required: zero chatter until Done.

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
HEADING = "## After Action required: zero chatter until Done"
ALLOWED_NO_STACK = "do not stack chatter or routine status on an open AR"


class AfterArZeroChatterUntilDoneOpsTests(unittest.TestCase):
    def test_contract_locks_after_ar_zero_chatter_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("after an Action required, silence until Done/Blocked/explicit reply", text)
        self.assertIn("Zero further user-facing messages", raw)
        self.assertIn("until Done / Blocked / explicit reply", text)
        self.assertIn("Exceptions only STOP / hello / wake-up", raw)
        self.assertIn("answer immediately", text)
        self.assertIn("Do not stack chatter or routine status on an open AR", raw)
        self.assertIn("Fail closed: if an Action required is still open", raw)
        self.assertIn("do not send further user-facing messages", text)
        self.assertIn("Stay silent until Done/Blocked/explicit reply", raw)
        self.assertIn("Do not stack chatter", raw)
        self.assertIn("Do not post routine status on an open AR", raw)
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
        self.assertIn("After Action required: zero chatter until Done", raw)
        self.assertIn("after an Action required, silence until Done/Blocked/explicit reply", text)
        self.assertIn("exceptions only STOP / hello / wake-up", text)
        self.assertIn("do not stack chatter or routine status on an open AR", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_chatter_on_open_ar(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_NO_STACK, "")
        self.assertIn("Fail closed: if an Action required is still open", raw)
        self.assertIn("do not send further user-facing messages", text)
        self.assertIn("Stay silent until Done/Blocked/explicit reply", raw)
        self.assertIn("Exceptions only STOP / hello / wake-up", raw)
        self.assertIn("answer immediately", text)
        self.assertIn("Do not stack chatter or routine status on an open AR", raw)
        self.assertNotIn("stack chatter or routine status on an open AR", scrubbed)
        self.assertNotIn("keep talking after action required", low)
        self.assertNotIn("post routine status on an open ar is ok", low)
        self.assertNotIn("chatter until done is allowed", low)
        self.assertNotIn("exceptions include routine status", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
