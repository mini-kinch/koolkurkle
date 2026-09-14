#!/usr/bin/env python3
"""KOO-34 Machine AR: SWITCH callout from last input source.

Docs/tests contract only. No live Mac writers. No live classify.
No live IMAP. No live SoR DB. No MailArchive. No Keychain reads.
No rem-legacy writer. No live machine SSH. No live Terminal focus.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## SWITCH TO Mini/MBP before machine-specific Terminal AR"
ALLOWED_FAIL_CLOSED = "fail closed: no host proof, no machine-specific card"
ALLOWED_DETECT = (
    "detect the machine only from prompt hostname / sent-from-machine / "
    "pasted proof"
)


class MachineArSwitchCalloutFromLastInputOpsTests(unittest.TestCase):
    def test_contract_locks_switch_callout_from_last_input_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("Before a machine-specific Terminal AR", text)
        self.assertIn("last user input", text)
        self.assertIn(
            "Detect the machine only from prompt hostname / "
            "Sent-from-machine / pasted proof",
            text,
        )
        self.assertIn("**Sent-from-machine**", raw)
        self.assertIn("**hostname** proof only", raw)
        self.assertIn(
            "Agents cannot see which Terminal window is focused",
            raw,
        )
        self.assertIn("cannot see the focused Terminal window", text)
        self.assertIn("loud SWITCH TO", text)
        self.assertIn("**SWITCH TO MBP**", raw)
        self.assertIn("**SWITCH TO Mini**", raw)
        self.assertIn("explicitly before or with the AR", text)
        self.assertIn("Fail closed: no host proof, no machine-specific card", text)
        self.assertIn("One machine per Terminal AR", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("Keychain", raw)
        self.assertIn("rem-legacy", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("SWITCH TO Mini/MBP", raw)
        self.assertIn("machine-specific Terminal AR", text)
        self.assertIn("Sent-from-machine", text)
        self.assertIn("hostname proof only", text)
        self.assertIn("one machine per AR", text)
        self.assertIn("detect machine only from prompt hostname", text)
        self.assertIn("pasted proof", text)
        self.assertIn("loud SWITCH TO", text)
        self.assertIn("agents cannot see which Terminal window is focused", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_switch_without_host_proof(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_FAIL_CLOSED, "").replace(
            ALLOWED_DETECT, ""
        )
        self.assertIn("Fail closed: no host proof, no machine-specific card", text)
        self.assertIn(
            "Detect the machine only from prompt hostname / "
            "Sent-from-machine / pasted proof",
            text,
        )
        self.assertIn(
            "Agents cannot see which Terminal window is focused",
            raw,
        )
        self.assertIn("loud SWITCH TO", text)
        self.assertIn("explicitly before or with the AR", text)
        self.assertNotIn("assume the focused window", low)
        self.assertNotIn("guess the host", low)
        self.assertNotIn("skip switch to when mismatch", low)
        self.assertNotIn("two machines in one terminal ar", low)
        self.assertNotIn("detect host from focused terminal window", scrubbed)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)
        self.assertNotIn("this gate sees the focused terminal window", low)


if __name__ == "__main__":
    unittest.main()
