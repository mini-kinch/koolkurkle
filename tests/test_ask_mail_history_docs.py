#!/usr/bin/env python3
"""Doc-contract: ask_mail retrieve default=history; --live additive SELECT.

No network, no IMAP, no MailArchive / live sqlite.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_mail  # noqa: E402

ASK = ROOT / "docs" / "ask_mail.md"
OPS = ROOT / "docs" / "ops-terminal.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
README = ROOT / "README.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
CLI = ROOT / "scripts" / "ask_mail.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")

EXISTING_RETRIEVE_FLAGS = ("--lane", "--after", "--before", "--fts-only")
EXISTING_GENERATE_OPT_IN = ("--llm", "--phase generate", "--no-generate", "--phase retrieve")


class AskMailHistoryDefaultDocTests(unittest.TestCase):
    def test_contract_locks_history_default_live_opt_in(self):
        text = ASK.read_text(encoding="utf-8")
        low = text.lower()
        self.assertIn("Retrieve contract (history default)", text)
        self.assertIn("Retrieve default is **history**", text)
        self.assertIn("standing default", text)
        self.assertIn("DECIDED", text)
        self.assertIn("Live modes are explicit **opt-in**", text)
        self.assertIn("additive SELECT", text)
        self.assertIn("`--live`", text)
        self.assertIn("`--history`", text)
        self.assertIn("present_on_server", text)
        self.assertIn("Deleted-folder ≠ present=0", text)
        self.assertIn("does not open IMAP", text)
        self.assertIn("MCP stub", text)
        self.assertIn("test_ask_mail_never_mcp_stub.py", text)
        for flag in EXISTING_RETRIEVE_FLAGS:
            self.assertIn("`%s`" % flag, text)
        for flag in EXISTING_GENERATE_OPT_IN:
            self.assertIn("`%s`" % flag, text)
        self.assertIn("`$MAILROOM_GENERATE_MODEL`", text)
        self.assertIn("generate_mode=hits_only", text)
        self.assertIn("semantic_search.retrieve()", text)
        self.assertIn("opt-in", low)
        self.assertIn("history", low)
        self.assertNotIn("EXAMPLE_USER_LOCAL", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_cli_comment_matches_contract(self):
        text = CLI.read_text(encoding="utf-8")
        self.assertIn("Retrieve default is history (local SoR)", text)
        self.assertIn("Live modes are explicit opt-in", text)
        self.assertIn("additive SELECT filter only", text)
        self.assertIn("No ``--history``", text)
        self.assertNotIn("EXAMPLE_USER_LOCAL", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)

    def test_live_is_additive_select_history_stays_default(self):
        parser = ask_mail.build_parser()
        option_strings = {
            opt
            for action in parser._actions
            for opt in (action.option_strings or ())
        }
        for flag in EXISTING_RETRIEVE_FLAGS:
            self.assertIn(flag, option_strings)
        self.assertIn("--llm", option_strings)
        self.assertIn("--phase", option_strings)
        self.assertIn("--no-generate", option_strings)
        self.assertIn("--live", option_strings)
        self.assertNotIn("--history", option_strings)

        args = parser.parse_args(["invoice"])
        cfg = ask_mail._cli_config(args)
        self.assertIsNone(cfg["generate"])
        self.assertIsNone(cfg["lane"])
        self.assertIsNone(cfg["after"])
        self.assertIsNone(cfg["before"])
        self.assertFalse(cfg["fts_only"])
        self.assertFalse(cfg["live"])

        live_args = parser.parse_args(["--live", "invoice"])
        live_cfg = ask_mail._cli_config(live_args)
        self.assertTrue(live_cfg["live"])

        seen: dict[str, object] = {}

        def _fn(query, **kwargs):
            seen.update(kwargs)
            return []

        ask_mail.retrieve_hits(
            "invoice",
            db=Path("/tmp/mailroom-copy.sqlite"),
            k=8,
            lane=None,
            after=None,
            before=None,
            rerank=False,
            fts_only=True,
            retrieve_fn=_fn,
            retrieve_kwargs=None,
            live=True,
        )
        self.assertTrue(seen.get("live"))

    def test_operators_can_find_the_contract(self):
        for path in (README, OPS, DAILY, TOMBSTONE, MAILROOM):
            text = path.read_text(encoding="utf-8")
            low = text.lower()
            self.assertIn("ask_mail.md", text, msg=path.name)
            self.assertIn("history", low, msg=path.name)
            self.assertIn("opt-in", low, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


if __name__ == "__main__":
    unittest.main()
