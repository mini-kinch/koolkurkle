#!/usr/bin/env python3
"""KOO-13 ZERO personal info on GitHub gate.

Docs/tests contract only. No network, no MailArchive, no live sqlite,
no Keychain reads, no rem-legacy writer.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

HOME_PLACEHOLDER = "/Users/<operator>/"
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
ABS_HOME_RE = re.compile(r"/Users/[^\s`'\"()]+")
ALLOWED_EMAIL_DOMAINS = frozenset({"example.com", "example.invalid"})
PERSONAL_EMAIL_SUFFIXES = (
    "@icloud.com",
    "@me.com",
    "@mac.com",
    "@gmail.com",
    "@hotmail.com",
    "@outlook.com",
    "@yahoo.com",
)
SCAN_ROOTS = ("README.md", "docs", "scripts")
TEXT_SUFFIXES = {
    "",
    ".example",
    ".json",
    ".md",
    ".plist",
    ".py",
    ".sh",
    ".sql",
    ".template",
    ".txt",
    ".yaml",
    ".yml",
}


def _scrub_home_placeholder(text: str) -> str:
    return text.replace(HOME_PLACEHOLDER, "").replace("/Users/<operator>", "")


def _tracked_doc_script_paths() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCAN_ROOTS],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError("git ls-files failed; refuse to pass without a scan")
    if not proc.stdout:
        raise AssertionError("git ls-files returned no tracked docs/scripts")
    paths = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8")
        path = ROOT / rel
        if path.is_file():
            paths.append(path)
    if not paths:
        raise AssertionError("no tracked docs/scripts files to scan")
    return paths


def _is_allowed_email(addr: str) -> bool:
    domain = addr.rsplit("@", 1)[-1].lower()
    return domain in ALLOWED_EMAIL_DOMAINS


class ZeroPersonalInfoOnGitHubTests(unittest.TestCase):
    def test_contract_locks_zero_pii_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ZERO personal info on GitHub", raw)
        self.assertIn("anywhere", text)
        self.assertIn("placeholder classes only", text)
        self.assertIn("`user@example.com`", raw)
        self.assertIn("`<operator>@example.com`", raw)
        self.assertIn("`/Users/<operator>/...`", raw)
        self.assertIn("`~/...`", raw)
        self.assertIn("as generic", text)
        self.assertIn("`<service>`", raw)
        self.assertIn("`<account>`", raw)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not read Keychain", text)
        self.assertIn("does not", text.lower())
        self.assertIn("rem-legacy", text.lower())
        self.assertIn("EXAMPLE_USER_LOCAL", raw)
        self.assertIn("example.invalid", raw)
        self.assertNotIn("@me.com", raw)
        self.assertNotIn("@icloud.com", raw)
        self.assertNotIn("/Users/", _scrub_home_placeholder(raw))

    def test_operators_can_find_the_gate(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("ZERO personal info on GitHub", raw)
        self.assertIn("placeholder classes only", text)
        self.assertNotIn("/Users/", raw)
        self.assertNotIn("@me.com", raw)
        self.assertNotIn("@icloud.com", raw)

    def test_tracked_docs_scripts_have_no_personal_email_or_abs_home(self):
        hits = []
        for path in _tracked_doc_script_paths():
            if path.suffix not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self.fail(
                    "%s is not UTF-8 text; refuse to scan"
                    % path.relative_to(ROOT)
                )
            rel = path.relative_to(ROOT)
            for lineno, line in enumerate(text.splitlines(), 1):
                where = "%s:%s" % (rel, lineno)
                for suffix in PERSONAL_EMAIL_SUFFIXES:
                    if suffix in line.lower():
                        hits.append(where + " personal-email-suffix")
                for match in EMAIL_RE.finditer(line):
                    if not _is_allowed_email(match.group(1)):
                        hits.append(where + " personal-email")
                for match in ABS_HOME_RE.finditer(line):
                    if not match.group(0).startswith("/Users/<operator>"):
                        hits.append(where + " absolute-home-path")
        self.assertEqual(
            hits,
            [],
            msg="redact to a placeholder; refusing personal-email / "
            "absolute-home-path in tracked docs/scripts: %s"
            % ", ".join(hits[:20]),
        )


if __name__ == "__main__":
    unittest.main()
