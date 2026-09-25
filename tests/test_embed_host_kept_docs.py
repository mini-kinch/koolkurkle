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
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertNotIn("EXAMPLE_USER_LOCAL", raw)

    def test_operators_can_find_the_contract(self):
        for path in (README, OPS, DAILY):
            raw = path.read_text(encoding="utf-8")
            text = " ".join(raw.split())
            self.assertIn("embed-backfill.md", raw, msg=path.name)
            self.assertIn("host-kept", text, msg=path.name)
            self.assertIn("nohup", text, msg=path.name)
            self.assertIn("HARD DECK", raw, msg=path.name)
            hay = raw.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

        ops = OPS.read_text(encoding="utf-8")
        self.assertIn(CAFFEINATE, ops)
        self.assertIn("Rem-legacy is not the daily path", ops)
        self.assertNotRegex(ops, LIVE_PID)

        readme = README.read_text(encoding="utf-8")
        self.assertIn(CAFFEINATE, readme)
        self.assertIn("not the Mini daily path", " ".join(readme.split()))
        self.assertNotRegex(readme, LIVE_PID)

    def test_remote_shell_vs_launchagent_lifetime(self):
        ops = OPS.read_text(encoding="utf-8")
        heading = "## Remote Shell vs LaunchAgent lifetime"
        self.assertEqual(ops.count(heading), 1)
        start = ops.index(heading)
        rest = ops[start + len(heading):]
        nxt = rest.find("\n## ")
        section = rest if nxt < 0 else rest[:nxt]
        flat = " ".join(section.replace("*", "").split())

        self.assertIn("process group is torn down", flat)
        self.assertIn("nohup ... &", section)
        self.assertIn("qwen-chat-down/up", section)
        self.assertIn("BATCH_START", section)
        self.assertIn(
            "run long chains in the foreground of the call with a "
            "long enough timeout, or run them as a LaunchAgent",
            flat,
        )

        self.assertIn("gui/<uid>", section)
        self.assertIn("launchctl bootstrap", section)
        self.assertIn("launchctl kickstart", section)
        self.assertIn("com.mailroom.bodies-embed-once", section)
        self.assertIn("survives Shell teardown", flat)
        self.assertIn(
            "anything that must outlive the call is a LaunchAgent or "
            "runs in a user-kept terminal (host-kept foreground)",
            flat,
        )
        self.assertIn("never two embed writers on the same file", flat)

        hay = section.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertNotRegex(section, LIVE_PID)

        embed = EMBED.read_text(encoding="utf-8")
        marker = "## Host-kept foreground embed ops contract"
        self.assertIn(marker, embed)
        chunk = embed[embed.index(marker):]
        end = chunk.find("\n## ", len(marker))
        contract = chunk if end < 0 else chunk[:end]
        xref = [
            line
            for line in contract.splitlines()
            if "Remote Shell vs LaunchAgent lifetime" in line
        ]
        self.assertEqual(len(xref), 1)
        self.assertIn("ops-terminal.md", xref[0])
        self.assertNotIn("/Users/", contract)


if __name__ == "__main__":
    unittest.main()
