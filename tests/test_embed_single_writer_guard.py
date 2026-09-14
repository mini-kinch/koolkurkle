#!/usr/bin/env python3
"""KOO-42 embed single-writer guard + rem-legacy ≠ Mini daily.

No live embed start. No rem-legacy touch. No MailArchive SoR writer.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
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
MAILROOM = ROOT / "docs" / "MAILROOM.md"
BACKFILL = ROOT / "scripts" / "embed_backfill.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class EmbedGuardUnitTests(unittest.TestCase):
    def test_busy_refuse_same_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            db.write_text("", encoding="utf-8")
            cmdlines = (
                (111, "/opt/homebrew/bin/python3 embed_backfill.py --db %s" % db),
                (os_getpid_other(), "unrelated"),
            )
            with self.assertRaises(eb.EmbedError) as ctx:
                eb.refuse_if_embed_busy(db, self_pid=999, cmdlines=cmdlines)
            self.assertIn(eb.BUSY_REFUSE, str(ctx.exception))
            self.assertIn("111", str(ctx.exception))

    def test_busy_allows_other_db_and_self(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            other = Path(tmp) / "mailroom.sqlite"
            db.write_text("", encoding="utf-8")
            cmdlines = (
                (1, "embed_backfill.py --db %s" % other),
                (42, "embed_backfill.py --db %s --quote-strip" % db),
            )
            eb.refuse_if_embed_busy(db, self_pid=42, cmdlines=cmdlines)

    def test_lockfile_second_holder_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            db.write_text("", encoding="utf-8")
            first = eb.acquire_embed_start_guard(db)
            self.assertIsNotNone(first)
            try:
                with self.assertRaises(eb.EmbedError) as ctx:
                    eb.acquire_embed_start_guard(db)
                self.assertIn(eb.LOCKFILE_REFUSE, str(ctx.exception))
            finally:
                eb.release_embed_start_guard(first)

    def test_missing_parent_skips_lockfile(self):
        held = eb.acquire_embed_start_guard(Path("/no/such/mailroom.sqlite"))
        self.assertIsNone(held)


def os_getpid_other() -> int:
    return 7


class EmbedGuardDocTests(unittest.TestCase):
    def test_docs_lock_rem_neq_daily_and_guard(self):
        for path in (EMBED, OPS, DAILY, README, MAILROOM, BACKFILL):
            text = path.read_text(encoding="utf-8")
            self.assertIn("HARD DECK", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        embed = EMBED.read_text(encoding="utf-8")
        self.assertIn("Shipping guard", embed)
        self.assertIn("lockfile or busy refuse", embed)
        self.assertIn("Shipping the guard ≠ starting a writer", embed)
        self.assertIn("Rem-legacy", embed)
        self.assertIn("do not restart rem for daily", embed)
        self.assertIn("--quote-strip", embed)
        self.assertIn("until EXIT", embed)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("lockfile or busy", ops)
        self.assertIn("Rem-legacy ≠ Mini daily", ops)
        readme = README.read_text(encoding="utf-8")
        self.assertIn("Rem-legacy is not the Mini", readme)
        self.assertIn("restart rem for daily", readme)


if __name__ == "__main__":
    unittest.main()
