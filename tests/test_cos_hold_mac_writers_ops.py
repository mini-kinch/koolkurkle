#!/usr/bin/env python3
"""KOO-22 CoS HOLD Mac writers — Developer owns Mac ops contract.

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
HEADING = "## CoS HOLD Mac writers (Developer owns Mac ops)"


class CosHoldMacWritersDeveloperOwnsOpsTests(unittest.TestCase):
    def test_contract_locks_cos_hold_mac_writers_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("CoS HOLD on specialist Mac writer ops", raw)
        self.assertIn("CoS does not run Mac writer/recovery ops", raw)
        self.assertIn("CoS orders Developer, collects status, issues user ARs only", text)
        self.assertIn("Developer owns Mac process ownership and installs", raw)
        self.assertIn("Fail closed: if a Mac writer, recovery, process, or install step would", raw)
        self.assertIn("require CoS to run it, do not run it", text)
        self.assertIn("Order Developer", raw)
        self.assertIn("Collect status", raw)
        self.assertIn("Issue a user AR only", raw)
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
        self.assertNotIn("CoS runs Mac writer/recovery ops", text)
        self.assertNotIn("CoS owns Mac process ownership", text)
        self.assertNotIn("CoS owns Mac installs", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("CoS HOLD Mac writers", raw)
        self.assertIn("CoS does not run Mac writer/recovery ops", text)
        self.assertIn("CoS orders Developer, collects status, issues user ARs only", text)
        self.assertIn("Developer owns Mac process ownership and installs", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_cos_does_not_run_mac_writer_ops(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        self.assertIn("Fail closed: if a Mac writer, recovery, process, or install step would", raw)
        self.assertIn("require CoS to run it, do not run it", text)
        self.assertIn("CoS does not run Mac writer/recovery ops", raw)
        self.assertIn("Developer owns Mac process ownership and installs", raw)
        self.assertIn("issues user ARs only", text)
        self.assertNotIn("cos may run mac writer ops", low)
        self.assertNotIn("cos owns mac process ownership", low)
        self.assertNotIn("cos owns mac installs", low)
        self.assertNotIn("skip developer and run the writer", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
