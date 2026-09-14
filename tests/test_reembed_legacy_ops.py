#!/usr/bin/env python3
"""KOO-10 --reembed-legacy ops contract. --help / parser / docs only.

No network, no MailArchive, no live sqlite, no rem-legacy writer.
"""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embed_backfill as eb  # noqa: E402

EMBED = ROOT / "docs" / "embed-backfill.md"
OPS = ROOT / "docs" / "ops-terminal.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
README = ROOT / "README.md"
BACKFILL = ROOT / "scripts" / "embed_backfill.py"
LIB = ROOT / "scripts" / "embed_lib.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
REFUSE = "--reembed-legacy requires --quote-strip"
EXISTING_FLAGS = ("--quote-strip", "--reembed-legacy", "--lock")


class ReembedLegacyOpsContractTests(unittest.TestCase):
    def test_help_locks_flag_coupling(self):
        proc = subprocess.run(
            [sys.executable, str(BACKFILL), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = " ".join(proc.stdout.split())
        self.assertIn("--reembed-legacy", text)
        self.assertIn("--quote-strip", text)
        self.assertIn("Requires --quote-strip", text)
        self.assertIn("Default off", text)
        self.assertIn("skipped_legacy_embedded", text)
        self.assertIn("HARD DECK", text)
        self.assertIn("content_hash", text)
        self.assertIn("daily/resume", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)
            self.assertNotIn(needle, proc.stderr)

    def test_parser_default_skip_and_combo(self):
        parser = eb.build_parser()
        option_strings = {
            opt
            for action in parser._actions
            for opt in (action.option_strings or ())
        }
        for flag in EXISTING_FLAGS:
            self.assertIn(flag, option_strings)
        self.assertNotIn("--reembed-legacy-daily", option_strings)

        bare = parser.parse_args([])
        self.assertFalse(bare.reembed_legacy)
        self.assertFalse(bare.quote_strip)

        combo = parser.parse_args(["--quote-strip", "--reembed-legacy"])
        self.assertTrue(combo.quote_strip)
        self.assertTrue(combo.reembed_legacy)

        help_text = " ".join(parser.format_help().split())
        self.assertIn("Requires --quote-strip", help_text)
        self.assertIn("Default off", help_text)

    def test_cli_refuses_without_quote_strip(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = eb.main(
                ["--reembed-legacy", "--dry-run", "--db", "/no/such/mailroom.sqlite"]
            )
        self.assertEqual(rc, 2)
        self.assertIn(REFUSE, err.getvalue())
        self.assertIn(REFUSE, BACKFILL.read_text(encoding="utf-8"))
        self.assertIn(REFUSE, LIB.read_text(encoding="utf-8"))

    def test_docs_lock_ops_contract(self):
        raw = EMBED.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("`--reembed-legacy` ops contract", text)
        self.assertIn("embed_backfill.py --help", text)
        self.assertIn(REFUSE, text)
        self.assertIn("`--reembed-legacy` requires `--quote-strip`", text)
        self.assertIn("opt-in, default off", text)
        self.assertIn("skipped_legacy_embedded", text)
        self.assertIn("content_hash", text)
        self.assertIn("Never two writers on one sqlite", text)
        self.assertIn("HARD DECK", text)
        self.assertIn("Not the daily path", text)
        self.assertIn("Do **not** put `--reembed-legacy` on the Mini daily argv", text)
        self.assertIn("No new flags", text)
        self.assertIn("Defaults unchanged", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, raw)
        self.assertNotIn("EXAMPLE_USER_LOCAL", raw)

    def test_operators_can_find_the_contract(self):
        for path in (README, OPS, DAILY):
            text = path.read_text(encoding="utf-8")
            self.assertIn("ops contract", text, msg=path.name)
            self.assertIn("--reembed-legacy", text, msg=path.name)
            self.assertIn("--quote-strip", text, msg=path.name)
            self.assertIn("embed-backfill.md", text, msg=path.name)
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, text, msg=path.name)


if __name__ == "__main__":
    unittest.main()
