#!/usr/bin/env python3
"""KOO-28 Status/handoff must include ETA until next Action required.

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
HEADING = "## Status/handoff: ETA until next Action required"
ALLOWED_LOCKED = "No false LOCKED ETAs"
ALLOWED_FAIL_CLOSED_LOCKED = "do not invent a LOCKED ETA"


class StatusHandoffEtaUntilNextArOpsTests(unittest.TestCase):
    def test_contract_locks_status_handoff_eta_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("status/handoff reports include ETA until next Action required", text)
        self.assertIn("Honest range only", raw)
        self.assertIn("No false LOCKED ETAs", raw)
        self.assertIn("No undeliverable certainty slogans", raw)
        self.assertIn("Fail closed: if a status or handoff report cannot give an honest range", raw)
        self.assertIn("until the next Action required", text)
        self.assertIn("do not invent a LOCKED ETA", text)
        self.assertIn("Do not claim undeliverable certainty", raw)
        self.assertIn("Give an honest range only, or say the ETA is unknown", raw)
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
        self.assertIn("Status/handoff reports include ETA until next Action required", raw)
        self.assertIn("honest range only", text)
        self.assertIn("no false LOCKED ETAs", text)
        self.assertIn("no undeliverable certainty slogans", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_false_locked_etas(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_LOCKED.lower(), "").replace(
            ALLOWED_FAIL_CLOSED_LOCKED.lower(), ""
        )
        self.assertIn("Fail closed: if a status or handoff report cannot give an honest range", raw)
        self.assertIn("do not invent a LOCKED ETA", text)
        self.assertIn("Do not claim undeliverable certainty", raw)
        self.assertIn("Honest range only", raw)
        self.assertIn("No false LOCKED ETAs", raw)
        self.assertIn("No undeliverable certainty slogans", raw)
        self.assertIn("or say the ETA is unknown", text)
        self.assertNotIn("locked eta is required", scrubbed)
        self.assertNotIn("false locked eta is ok", scrubbed)
        self.assertNotIn("claim a locked eta when unknown", scrubbed)
        self.assertNotIn("undeliverable certainty slogans are ok", low)
        self.assertNotIn("status may omit eta until next action required", low)
        self.assertNotIn("handoff may omit eta until next action required", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
