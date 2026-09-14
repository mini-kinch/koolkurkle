#!/usr/bin/env python3
"""KOO-41 Mini MAILROOM_DB refuse SoR stub / copy-only until PR-5.

Docs/tests fail-closed. No RunAtLoad change. No live SoR open.
"""

from __future__ import annotations

import os
import plistlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mailroom_copy_db as copy_db  # noqa: E402

PLIST = ROOT / "launchd" / "com.mailroom.daily.plist"
ASK = ROOT / "docs" / "ask_mail.md"
README = ROOT / "README.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
OPS = ROOT / "docs" / "ops-terminal.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class MiniCopyOnlyUntilPr5Tests(unittest.TestCase):
    def test_unset_and_sor_basename_hard_fail(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAILROOM_DB", None)
            with self.assertRaises(copy_db.CopyDbRefuse) as unset:
                copy_db.resolve_copy_db(None)
        self.assertIn("unset", str(unset.exception).lower())
        with self.assertRaises(copy_db.CopyDbRefuse) as sor:
            copy_db.resolve_copy_db("/tmp/mailroom.sqlite")
        self.assertIn("mailroom.sqlite", str(sor.exception))
        path = copy_db.resolve_copy_db("/tmp/mailroom-copy.sqlite")
        self.assertEqual(path.name, "mailroom-copy.sqlite")

    def test_daily_plist_copy_only_runatload_unchanged(self):
        data = plistlib.loads(PLIST.read_bytes())
        env = data["EnvironmentVariables"]
        self.assertEqual(env["MAILROOM_DB"], "__HOME__/MailArchive/mailroom-copy.sqlite")
        self.assertNotEqual(Path(env["MAILROOM_DB"]).name, "mailroom.sqlite")
        self.assertTrue(data["RunAtLoad"])
        raw = PLIST.read_text(encoding="utf-8")
        self.assertIn("Copy-only until SoR cutover (PR-5)", raw)
        self.assertIn("mailroom.imap.app-password", raw)

    def test_ask_mail_mini_recipes_set_copy_db(self):
        for path in (ASK, README, DAILY, MAILROOM):
            text = path.read_text(encoding="utf-8")
            self.assertIn("MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite", text, msg=path.name)
            self.assertIn("until PR-5", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("hard refuse", ops)
        self.assertIn("db_mode=refused", ops)
        self.assertIn("mailroom-copy.sqlite", ops)
        self.assertIn("PR-5", ops)


if __name__ == "__main__":
    unittest.main()
