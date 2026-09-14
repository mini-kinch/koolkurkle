#!/usr/bin/env python3
"""KOO-11 host-kept foreground embed ops contract. Docs only.

No network, no MailArchive, no live sqlite, no rem-legacy writer.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMBED = ROOT / "docs" / "embed-backfill.md"
OPS = ROOT / "docs" / "ops-terminal.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
CAFFEINATE = "caffeinate -w <pid>"
LIVE_PID = re.compile(r"caffeinate\s+-w\s+\d+")


class HostKeptForegroundEmbedOpsTests(unittest.TestCase):
    def test_contract_locks_host_kept_language(self):
        raw = EMBED.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("Host-kept foreground embed ops contract", text)
        self.assertIn("host-kept foreground", text)
        self.assertIn("host Terminal", text)
        self.assertIn("Do **not** background with `nohup` or `&`", text)
        self.assertIn("never two writers on one", text)
        self.assertIn("HARD DECK", text)
        self.assertIn(CAFFEINATE, raw)
        self.assertIn("placeholder `<pid>`", text)
        self.assertIn("`--reembed-legacy` is **not** the daily path", text)
        self.assertIn("README.mailroom-daily.md", text)
        self.assertIn("--skip-auth --quote-strip --lock", text)
        self.assertIn("No new flags", text)
        self.assertIn("Defaults unchanged", text)
        self.assertNotRegex(raw, LIVE_PID)
        self.assertNotIn("nohup embed_backfill", raw)
        self.assertNotRegex(raw, r"(?m)^nohup\s")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, raw)
        self.assertNotIn("EXAMPLE_USER_LOCAL", raw)

    def test_operators_can_find_the_contract(self):
        for path in (README, OPS, DAILY):
            raw = path.read_text(encoding="utf-8")
            text = " ".join(raw.split())
            self.assertIn("embed-backfill.md", raw, msg=path.name)
            self.assertIn("host-kept", text, msg=path.name)
            self.assertIn("nohup", text, msg=path.name)
            self.assertIn("HARD DECK", raw, msg=path.name)
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, raw, msg=path.name)

        ops = OPS.read_text(encoding="utf-8")
        self.assertIn(CAFFEINATE, ops)
        self.assertIn("Rem-legacy is not the daily path", ops)
        self.assertNotRegex(ops, LIVE_PID)

        readme = README.read_text(encoding="utf-8")
        self.assertIn(CAFFEINATE, readme)
        self.assertIn("not the Mini daily path", " ".join(readme.split()))
        self.assertNotRegex(readme, LIVE_PID)


if __name__ == "__main__":
    unittest.main()
