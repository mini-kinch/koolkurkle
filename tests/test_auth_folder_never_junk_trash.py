#!/usr/bin/env python3
"""KOO-21 auth/2FA mail never Junk or Trash — Auth folder ops contract.

Docs/tests contract only. No live classify. No live IMAP. No live
SoR DB. No MailArchive. No Keychain reads. No rem-legacy writer.
No live machine SSH.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Auth/2FA mail never Junk or Trash (Auth folder)"


class AuthFolderNeverJunkTrashContractTests(unittest.TestCase):
    def test_contract_locks_auth_folder_never_junk_trash_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("Standing classify/rules contract", raw)
        self.assertIn("auth/2FA mail must not be classified into Junk or Trash", text)
        self.assertIn("The destination hygiene folder is Auth", raw)
        self.assertIn("Fail closed: if classify or rules cannot place auth/2FA mail into Auth", text)
        self.assertIn("do not classify it into Junk or Trash", text)
        self.assertIn("Do not guess Junk", raw)
        self.assertIn("Do not fall through to Trash", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run live classify", text)
        self.assertIn("does not open MailArchive or live sqlite", text)
        self.assertIn("does not write embed/SoR data", text)
        self.assertIn("does not read Keychain", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("does not change rem-legacy", text)
        self.assertNotIn("Junk is the destination hygiene folder", text)
        self.assertNotIn("Trash is the destination hygiene folder", text)
        self.assertNotIn("classify auth/2fa mail into junk", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Auth/2FA mail never Junk or Trash", raw)
        self.assertIn("destination hygiene folder is Auth", text)
        self.assertIn("fail closed for classify/rules", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_classify_rules_never_junk_or_trash(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        self.assertIn("Fail closed: if classify or rules cannot place auth/2FA mail into Auth", text)
        self.assertIn("do not classify it into Junk or Trash", text)
        self.assertIn("destination hygiene folder is Auth", text)
        self.assertNotIn("guess junk when unsure", low)
        self.assertNotIn("fall through to trash is ok", low)
        self.assertNotIn("junk is an allowed auth destination", low)
        self.assertNotIn("trash is an allowed auth destination", low)
        self.assertNotIn("classify auth into junk is allowed", low)
        self.assertNotIn("this gate runs live classify", low)
        self.assertNotIn("this document runs live classify", low)


if __name__ == "__main__":
    unittest.main()
