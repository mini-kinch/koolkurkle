#!/usr/bin/env python3
"""KOO-27 Agent Shell non-interactive — no read/getpass.

Docs/tests contract only. No live secret entry. No Keychain writes.
No live Mac writers. No live SoR DB. No MailArchive. No rem-legacy.
No live machine SSH.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Agent Shell non-interactive (no read/getpass)"
ALLOWED_NO_READ_GETPASS = "do not use `read` or `getpass` in agent scripts"


class AgentShellNonInteractiveOpsTests(unittest.TestCase):
    def test_contract_locks_agent_shell_non_interactive_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("**Agent Shell is non-interactive**", raw)
        self.assertIn("Forbid `read`/`getpass` in agent scripts", raw)
        self.assertIn("Do not use `read` or `getpass` in agent scripts", raw)
        self.assertIn("Agent Shell cannot collect a secret at a prompt", raw)
        self.assertIn("security -w last or it stores empty", text)
        self.assertIn("Do not put the secret on the command line", raw)
        self.assertIn("Secrets only in a **real Terminal**", raw)
        self.assertIn("reports `wc -c` only", raw)
        self.assertIn("No secret values in the repo", raw)
        self.assertIn("Fail closed: if a step needs a secret, do not prompt in Agent Shell", raw)
        self.assertIn("Do not run `security -w` from Agent Shell", raw)
        self.assertIn("Issue a real-Terminal card", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run live Mac writers", text)
        self.assertIn("does not run live classify", text)
        self.assertIn("does not run live IMAP", text)
        self.assertIn("does not open MailArchive or live sqlite", text)
        self.assertIn("does not write embed/SoR data", text)
        self.assertIn("does not read Keychain", text)
        self.assertIn("does not write Keychain", text)
        self.assertIn("does not run live secret entry", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Agent Shell non-interactive", raw)
        self.assertIn("no read/getpass in agent scripts", text)
        self.assertIn("security -w last or it stores empty", text)
        self.assertIn("secrets only in real Terminal", text)
        self.assertIn("report wc -c only", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_read_getpass_in_agent_shell(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_NO_READ_GETPASS, "")
        self.assertIn("Fail closed: if a step needs a secret, do not prompt in Agent Shell", raw)
        self.assertIn("Forbid `read`/`getpass` in agent scripts", raw)
        self.assertIn("Do not use `read` or `getpass` in agent scripts", raw)
        self.assertIn("Do not run `security -w` from Agent Shell", raw)
        self.assertIn("security -w last or it stores empty", text)
        self.assertIn("reports `wc -c` only", raw)
        self.assertIn("No secret values in the repo", raw)
        self.assertNotIn("use `read` or `getpass` in agent scripts", scrubbed)
        self.assertNotIn("agent shell is interactive", low)
        self.assertNotIn("read/getpass in agent scripts is ok", low)
        self.assertNotIn("type secrets in agent shell", low)
        self.assertNotIn("put the secret on the command line is ok", low)
        self.assertNotIn("secret values in the repo are ok", low)
        self.assertNotIn("this gate writes keychain", low)
        self.assertNotIn("this gate runs live secret entry", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)
        self.assertNotIn("this gate runs live mac writers", low)


if __name__ == "__main__":
    unittest.main()
