#!/usr/bin/env python3
"""KOO-32 Watch proof: quote last sample or say not watching.

Docs/tests contract only. No live Mac writers. No live classify.
No live IMAP. No live SoR DB. No MailArchive. No Keychain reads.
No rem-legacy writer. No live machine SSH. No live watch sampling.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Watch proof: quote last sample or say not watching"
SAMPLE = "PT | job | alive/stalled/EXIT | n/N"
LIVE_PID = re.compile(r"caffeinate\s+-w\s+\d+")
LIVE_NUMERIC_PID = re.compile(r"\bpid\s+\d{3,}\b", re.I)
ALLOWED_NOT_WATCHING = "or explicitly say not watching"
ALLOWED_FAIL_CLOSED_NOT_WATCHING = "explicitly say not watching"
ALLOWED_INVENT = "Do not invent progress"
ALLOWED_LOCKED = "Do not claim LOCKED monitor"
ALLOWED_RESTART = "Do not restart watched jobs from status reports"


def _section(raw: str, heading: str) -> str:
    start = raw.index(heading)
    rest = raw[start + len(heading) :]
    nxt = rest.find("\n## ")
    return heading + (rest if nxt < 0 else rest[:nxt])


class WatchProofQuoteLastSampleOrSayNotWatchingOpsTests(unittest.TestCase):
    def test_contract_locks_watch_proof_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("when reporting on a watched job", text)
        self.assertIn("rem-legacy / host-kept embed", text)
        self.assertIn("quote the last real sample line", text)
        self.assertIn(SAMPLE, raw)
        self.assertIn("or explicitly say not watching", text)
        self.assertIn("Do not invent progress", raw)
        self.assertIn("Do not claim LOCKED monitor", raw)
        self.assertIn("Do not restart watched jobs from status reports", raw)
        self.assertIn("Placeholder classes only", raw)
        self.assertIn("Never a live numeric PID as a standing example", raw)
        self.assertIn("Fail closed: if a watch claim cannot quote a last real sample line", raw)
        self.assertIn("explicitly say not watching", text)
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
        self.assertIn("does not sample a live watch", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertNotRegex(raw, LIVE_PID)
        self.assertNotRegex(raw, LIVE_NUMERIC_PID)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Watch proof", raw)
        self.assertIn("quote last sample or say not watching", text)
        self.assertIn("do not invent progress", text)
        self.assertIn("do not claim LOCKED monitor", text)
        self.assertIn("do not restart watched jobs from status reports", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertNotRegex(raw, LIVE_PID)
        self.assertNotRegex(raw, LIVE_NUMERIC_PID)

    def test_watch_proof_section_uses_placeholders_not_live_pids(self):
        raw = OPS.read_text(encoding="utf-8")
        section = _section(raw, HEADING)
        self.assertIn(SAMPLE, section)
        self.assertIn("Placeholder classes only", section)
        self.assertIn("Never a live numeric PID as a standing example", section)
        self.assertNotRegex(section, LIVE_PID)
        self.assertNotRegex(section, LIVE_NUMERIC_PID)
        self.assertNotRegex(section, re.compile(r"\b\d{4,}\b"))

    def test_fail_closed_quote_sample_or_say_not_watching(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = (
            low.replace(ALLOWED_NOT_WATCHING.lower(), "")
            .replace(ALLOWED_FAIL_CLOSED_NOT_WATCHING.lower(), "")
            .replace(ALLOWED_INVENT.lower(), "")
            .replace(ALLOWED_LOCKED.lower(), "")
            .replace(ALLOWED_RESTART.lower(), "")
        )
        self.assertIn("Fail closed: if a watch claim cannot quote a last real sample line", raw)
        self.assertIn("explicitly say not watching", text)
        self.assertIn(SAMPLE, raw)
        self.assertIn("Do not invent progress", raw)
        self.assertIn("Do not claim LOCKED monitor", raw)
        self.assertIn("Do not restart watched jobs from status reports", raw)
        self.assertNotIn("invent progress is ok", scrubbed)
        self.assertNotIn("claim locked monitor", scrubbed)
        self.assertNotIn("locked monitor slogans are ok", low)
        self.assertNotIn("restart watched jobs from status reports", scrubbed)
        self.assertNotIn("watch claim may omit last sample", low)
        self.assertNotIn("invent a last sample line", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)
        self.assertNotIn("this gate samples a live watch", low)
        self.assertNotIn("this gate restarts rem-legacy", low)


if __name__ == "__main__":
    unittest.main()
