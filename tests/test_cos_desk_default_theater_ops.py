#!/usr/bin/env python3
"""KOO-31 CoS Desk is default theater; Merge ARs to Desk not 1:1.

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
HEADING = "## CoS Desk default theater (Merge ARs to Desk not 1:1)"
ALLOWED_PRIVACY = (
    "do not post merge ars to cos private 1:1 unless the user asks for privacy"
)
ALLOWED_FAIL_CLOSED_PRIVACY = (
    "do not post the merge ar to cos private 1:1"
)
ALLOWED_NEVER_CALL_PRIVATE = "never call it private"
ALLOWED_PRIVATE_WAKE = "developer ready arrives on a private agent wake"
ALLOWED_PRIVATE_11_JUMPSEAT = (
    "private 1:1 only for privacy from jumpseat or a card rooms cannot show"
)


class CosDeskDefaultTheaterMergeArsToDeskOpsTests(unittest.TestCase):
    def test_contract_locks_desk_default_theater_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("Desk default", raw)
        self.assertIn("CoS Desk is the default theater for factory Merge ARs", text)
        self.assertIn("status that needs user action", text)
        self.assertIn("Merge ARs and factory status go to CoS Desk", raw)
        self.assertIn(
            "Do not post Merge ARs to CoS private 1:1 unless the user asks for privacy",
            raw,
        )
        self.assertIn(
            "If Developer Ready arrives on a private agent wake, post Merge AR to Desk, not 1:1",
            text,
        )
        self.assertIn(
            "Private 1:1 only for privacy from Jumpseat or a card rooms cannot show",
            raw,
        )
        self.assertIn("rooms cannot show cards", text)
        self.assertIn(
            "card is in CoS 1:1 because rooms cannot show cards",
            text,
        )
        self.assertIn("never call it private", text)
        self.assertIn("One thing at a time", raw)
        self.assertIn("No dual-window / stacked AR", raw)
        self.assertIn(
            "Fail closed: if the user did not ask for privacy",
            raw,
        )
        self.assertIn("do not post the Merge AR to CoS private 1:1", text)
        self.assertIn("Post Merge ARs and factory status to CoS Desk", raw)
        self.assertIn("Do not open a dual-window", raw)
        self.assertIn("Do not stack an AR", raw)
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
        self.assertIn("CoS Desk default theater", raw)
        self.assertIn(
            "CoS Desk is the default theater for factory Merge ARs",
            text,
        )
        self.assertIn("status that needs user action", text)
        self.assertIn(
            "do not post Merge ARs to CoS private 1:1 unless the user asks for privacy",
            text,
        )
        self.assertIn("one thing at a time", text)
        self.assertIn("no dual-window / stacked AR", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_merge_ar_to_desk_not_one_to_one(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low
        for allowed in (
            ALLOWED_PRIVACY,
            ALLOWED_FAIL_CLOSED_PRIVACY,
            ALLOWED_NEVER_CALL_PRIVATE,
            ALLOWED_PRIVATE_WAKE,
            ALLOWED_PRIVATE_11_JUMPSEAT,
        ):
            scrubbed = scrubbed.replace(allowed, "")
        self.assertIn("Fail closed: if the user did not ask for privacy", raw)
        self.assertIn("do not post the Merge AR to CoS private 1:1", text)
        self.assertIn("Post Merge ARs and factory status to CoS Desk", raw)
        self.assertIn("post Merge AR to Desk, not 1:1", text)
        self.assertIn("Do not open a dual-window", raw)
        self.assertIn("Do not stack an AR", raw)
        self.assertIn(
            "card is in CoS 1:1 because rooms cannot show cards",
            text,
        )
        self.assertIn("never call it private", text)
        self.assertNotIn("1:1 is the default theater", low)
        self.assertNotIn("post merge ars to 1:1 by default", low)
        self.assertNotIn("merge ar stays on the private agent wake", low)
        self.assertNotIn("dual-window is ok", low)
        self.assertNotIn("stacked ar is ok", low)
        self.assertNotIn("call rooms cannot show private", scrubbed)
        self.assertNotIn("rooms cannot show cards is private", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
