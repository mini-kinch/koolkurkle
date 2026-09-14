#!/usr/bin/env python3
"""KOO-23 Discuss ≠ authorize — implement only on do it / approved.

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
HEADING = "## Discuss ≠ authorize (implement only on do it / approved)"


class DiscussNotAuthorizeOpsTests(unittest.TestCase):
    def test_contract_locks_discuss_not_authorize_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("**Discuss ≠ authorize**", raw)
        self.assertIn("Discussion and how questions are not authorization", text)
        self.assertIn("Implement only on do it / approved / implement", text)
        self.assertIn("standing authorized process", text)
        self.assertIn("already-authorized standing process", text)
        self.assertIn("In CoS Desk discussion/troubleshooting, do not act until explicit", text)
        self.assertIn("Fail closed: if the request is discussion or a how question", raw)
        self.assertIn("do not implement", text)
        self.assertIn("Do not treat discussion as authorization", raw)
        self.assertIn("Wait for do it / approved / implement", text)
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
        self.assertNotIn("discussion is authorization", text.lower())
        self.assertNotIn("how questions are authorization", text.lower())
        self.assertNotIn("implement on discuss", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Discuss ≠ authorize", raw)
        self.assertIn("discussion and how questions are not authorization", text)
        self.assertIn("implement only on do it / approved / implement", text)
        self.assertIn("standing authorized process", text)
        self.assertIn("CoS Desk discussion/troubleshooting", text)
        self.assertIn("do not act until explicit", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_discussion_is_not_authorization(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        self.assertIn("Fail closed: if the request is discussion or a how question", raw)
        self.assertIn("do not implement", text)
        self.assertIn("Do not treat discussion as authorization", raw)
        self.assertIn("In CoS Desk discussion/troubleshooting, do not act until explicit", text)
        self.assertIn("Implement only on do it / approved / implement", text)
        self.assertNotIn("discussion is authorization", low)
        self.assertNotIn("how questions are authorization", low)
        self.assertNotIn("implement on discuss", low)
        self.assertNotIn("act in cos desk discussion without explicit", low)
        self.assertNotIn("treat how questions as authorization", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
