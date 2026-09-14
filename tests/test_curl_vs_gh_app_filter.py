#!/usr/bin/env python3
"""KOO-16 curl≠gh dial bad-file-descriptor / app-filter ops contract.

Docs/tests contract only. No live SoR DB, no MailArchive, no
Keychain reads, no rem-legacy writer, no live machine SSH, no
gh auth.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
ELLIPSIS_DIAL = "dial tcp … connect: bad file descriptor"
HEADING = "## curl≠gh dial bad-file-descriptor / app filter"
IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _section_after(raw: str, heading: str) -> str:
    start = raw.find(heading)
    if start < 0:
        raise AssertionError("missing heading: %s" % heading)
    rest = raw[start + len(heading) :]
    nxt = rest.find("\n## ")
    return heading + (rest if nxt < 0 else rest[:nxt])


class CurlVsGhAppFilterContractTests(unittest.TestCase):
    def test_contract_locks_curl_neq_gh_app_filter_language(self):
        raw = OPS.read_text(encoding="utf-8")
        section = _section_after(raw, HEADING)
        text = " ".join(raw.split())
        self.assertIn("curl≠gh dial bad-file-descriptor / app filter", text)
        self.assertIn("`curl` to `api.github.com`", text)
        self.assertIn("return **200**", text)
        self.assertIn("Homebrew `gh`", text)
        self.assertIn(ELLIPSIS_DIAL, raw)
        self.assertIn("not a token reject", text)
        self.assertIn("not basic network down", text)
        self.assertIn("app-level filter", text)
        self.assertIn("Little Snitch", text)
        self.assertIn("`/opt/homebrew/bin/gh`", raw)
        self.assertIn("do not re-auth blindly", text)
        self.assertIn("Check app filter / Allow for `gh`", text)
        self.assertIn("Unauthenticated `gh api rate_limit`", text)
        self.assertIn("isolates binary network vs token", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run `gh auth`", text)
        self.assertIn("read Keychain", text)
        self.assertIn("SSH a live machine", text)
        self.assertIn("change rem-legacy", text)
        self.assertNotIn("re-auth first when curl is 200", text.lower())
        self.assertNotIn("token reject is the diagnosis", text.lower())
        self.assertNotIn("basic network is down", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertIsNone(
            IPV4_RE.search(section),
            msg="no live IPs in curl≠gh section; use ellipsis dial form",
        )

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("curl≠gh dial bad-file-descriptor", text)
        self.assertIn("curl 200", text)
        self.assertIn("Homebrew gh", text)
        self.assertIn(ELLIPSIS_DIAL, raw)
        self.assertIn("app-level filter", text)
        self.assertIn("/opt/homebrew/bin/gh", raw)
        self.assertIn("do not re-auth blindly", text)
        self.assertIn("gh api rate_limit", text)
        self.assertIn("isolates binary network vs token", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        pointer_lines = [
            line for line in raw.splitlines() if "curl≠gh dial bad-file-descriptor" in line
        ]
        self.assertTrue(pointer_lines, msg="README must point at the curl≠gh contract")
        for line in pointer_lines:
            self.assertIsNone(
                IPV4_RE.search(line),
                msg="no live IPs in curl≠gh pointer; use ellipsis dial form",
            )

    def test_fail_closed_curl_200_is_not_token_or_network_down(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("do not re-auth blindly", text)
        self.assertIn("not a token reject", text)
        self.assertIn("not basic network down", text)
        self.assertIn("app-level filter", text)
        self.assertIn("Check app filter / Allow for `gh`", text)
        self.assertIn("Unauthenticated `gh api rate_limit`", text)
        self.assertNotIn("run gh auth login", text.lower())
        self.assertNotIn("re-auth blindly when curl is 200", text.lower())
        self.assertNotIn("treat as token reject", text.lower())
        self.assertNotIn("treat as basic network down", text.lower())
        self.assertIn(ELLIPSIS_DIAL, raw)
        self.assertNotIn("dial tcp 1", raw)
        self.assertNotIn("gh auth login", raw)
        self.assertNotIn("gh auth refresh", raw)


if __name__ == "__main__":
    unittest.main()
