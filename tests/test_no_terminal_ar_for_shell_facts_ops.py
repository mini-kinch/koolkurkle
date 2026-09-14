#!/usr/bin/env python3
"""KOO-25 No Terminal AR for facts Shell can read.

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
HEADING = "## No Terminal AR for facts Shell can read"
ALLOWED_NO_ISSUE = "do not issue terminal ar for facts agent shell can read"


class NoTerminalArForShellFactsOpsTests(unittest.TestCase):
    def test_contract_locks_no_terminal_ar_for_shell_facts_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("do not issue Terminal AR for facts agent Shell can read", text)
        self.assertIn(
            "Terminal AR only for GUI / Little Snitch / sudo / secrets in a real Terminal",
            text,
        )
        self.assertIn("agent Shell can already read the fact", text)
        self.assertIn("Read it in Shell", raw)
        self.assertIn("Do not ask a human to paste that fact from a Mac Terminal", raw)
        self.assertIn(
            "Issue a Terminal AR only when the step needs a real Terminal for GUI / Little Snitch / sudo / secrets",
            text,
        )
        self.assertIn("Fail closed: if the agent Shell can read the fact", raw)
        self.assertIn("do not issue a Terminal AR", text)
        self.assertIn(
            "Do not issue a Terminal AR for ls, cat, git status, or other Shell-readable facts",
            raw,
        )
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
        self.assertIn("No Terminal AR for facts Shell can read", raw)
        self.assertIn("do not issue Terminal AR for facts agent Shell can read", text)
        self.assertIn(
            "Terminal AR only for GUI / Little Snitch / sudo / secrets in a real Terminal",
            text,
        )
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_terminal_ar_for_shell_readable_facts(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_NO_ISSUE, "")
        self.assertIn("Fail closed: if the agent Shell can read the fact", raw)
        self.assertIn("do not issue a Terminal AR", text)
        self.assertIn(
            "Terminal AR only for GUI / Little Snitch / sudo / secrets in a real Terminal",
            text,
        )
        self.assertIn("Read it in Shell", raw)
        self.assertIn(
            "Do not issue a Terminal AR for ls, cat, git status, or other Shell-readable facts",
            raw,
        )
        self.assertNotIn("issue terminal ar for facts agent shell can read", scrubbed)
        self.assertNotIn("terminal ar for ls is ok", low)
        self.assertNotIn("paste git status from a human terminal is required", low)
        self.assertNotIn("issue terminal ar when shell can read the fact", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
