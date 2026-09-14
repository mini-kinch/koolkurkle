#!/usr/bin/env python3
"""KOO-30 No stacked ARs — exception only when user asks during host-kept job.

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
HEADING = (
    "## No stacked ARs (exception only when user asks during host-kept job)"
)
ALLOWED_EXCEPTION = (
    "exception only when the user explicitly asks for another ar during "
    "an active host-kept foreground job"
)
ALLOWED_PARALLEL = (
    "parallel next ars while a long host-kept job runs are ok when the "
    "user asks for the next task"
)
ALLOWED_FAIL_CLOSED = (
    "if the user did not explicitly ask for another ar during an active "
    "host-kept foreground job, do not stack action required / card-like "
    "prompts"
)
ALLOWED_NO_STACK_TURN = (
    "never stack action required / card-like prompts in one turn"
)
ALLOWED_NO_STACK_OPEN = "do not stack open action requireds"
ALLOWED_NO_STACK_DEFAULT = "do not stack open ars by default"
ALLOWED_NO_STACK_CARDS = "do not stack card-like prompts in one turn"


class NoStackedArsExceptionHostKeptOpsTests(unittest.TestCase):
    def test_contract_locks_no_stacked_ars_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn(
            "never stack Action required / card-like prompts in one turn",
            text,
        )
        self.assertIn(
            "Do not stack open Action requireds / problems onto the user",
            raw,
        )
        self.assertIn("No stacked open ARs by default", raw)
        self.assertIn(
            "Exception only when the user explicitly asks for another AR",
            raw,
        )
        self.assertIn("active host-kept foreground job", text)
        self.assertIn(
            "Parallel next ARs while a long host-kept job runs are OK",
            raw,
        )
        self.assertIn("when the user asks for the next task", text)
        self.assertIn(
            "One machine, one command, loud banner still applies",
            raw,
        )
        self.assertIn(
            "Fail closed: if the user did not explicitly ask for another AR",
            raw,
        )
        self.assertIn("during an active host-kept foreground job", text)
        self.assertIn(
            "do not stack Action required / card-like prompts",
            text,
        )
        self.assertIn("Do not stack open ARs by default", raw)
        self.assertIn("Do not stack card-like prompts in one turn", raw)
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
        self.assertIn("No stacked ARs", raw)
        self.assertIn(
            "never stack Action required / card-like prompts in one turn",
            text,
        )
        self.assertIn(
            "exception only when the user explicitly asks for another AR",
            text,
        )
        self.assertIn("active host-kept foreground job", text)
        self.assertIn(
            "one machine, one command, loud banner still applies",
            text,
        )
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_stacked_ars_without_user_ask(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low
        for allowed in (
            ALLOWED_EXCEPTION,
            ALLOWED_PARALLEL,
            ALLOWED_FAIL_CLOSED,
            ALLOWED_NO_STACK_TURN,
            ALLOWED_NO_STACK_OPEN,
            ALLOWED_NO_STACK_DEFAULT,
            ALLOWED_NO_STACK_CARDS,
        ):
            scrubbed = scrubbed.replace(allowed, "")
        self.assertIn(
            "Fail closed: if the user did not explicitly ask for another AR",
            raw,
        )
        self.assertIn("during an active host-kept foreground job", text)
        self.assertIn(
            "do not stack Action required / card-like prompts",
            text,
        )
        self.assertIn("Do not stack open ARs by default", raw)
        self.assertIn(
            "never stack Action required / card-like prompts in one turn",
            text,
        )
        self.assertIn(
            "One machine, one command, loud banner still applies",
            raw,
        )
        self.assertNotIn("stack action requireds in one turn is ok", scrubbed)
        self.assertNotIn("stack card-like prompts by default", scrubbed)
        self.assertNotIn(
            "issue stacked ars without user ask",
            scrubbed,
        )
        self.assertNotIn(
            "stack open ars onto the user by default",
            scrubbed,
        )
        self.assertNotIn(
            "parallel next ars without the user asking",
            scrubbed,
        )
        self.assertNotIn("skip one machine one command loud banner", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
