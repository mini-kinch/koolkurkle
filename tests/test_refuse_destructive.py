#!/usr/bin/env python3
"""KOO-37: hard-refuse destructive CLI verbs. No IMAP, no SoR writers."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_mail  # noqa: E402
import embed_backfill as eb  # noqa: E402
import imap_tombstone  # noqa: E402
import mailroom_copy_db as copy_db  # noqa: E402
import mailroom_daily as daily  # noqa: E402
import refuse_destructive as rd  # noqa: E402

VERBS = ("purge", "expunge", "empty-trash", "delete-gone", "drop-messages")
PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class RefuseDestructiveUnitTests(unittest.TestCase):
    def test_finds_flag_and_bare_verbs(self):
        for verb in VERBS:
            self.assertEqual(rd.find_destructive_verb([verb]), verb)
            self.assertEqual(rd.find_destructive_verb(["--%s" % verb]), verb)
            self.assertEqual(rd.find_destructive_verb(["--%s=1" % verb]), verb)
        self.assertIsNone(rd.find_destructive_verb(["invoice"]))
        self.assertIsNone(rd.find_destructive_verb(["--json", "purge the inbox"]))
        self.assertIsNone(rd.find_destructive_verb([]))

    def test_refuse_raises_soft_delete_message(self):
        with self.assertRaises(rd.DestructiveRefuse) as ctx:
            rd.refuse_destructive_cli(["--expunge"])
        msg = str(ctx.exception)
        self.assertIn(rd.REFUSE_PREFIX, msg)
        self.assertIn("expunge", msg)
        self.assertIn("Never physically purge", msg)
        self.assertIn("EXPUNGE", msg)
        self.assertIn("present_on_server", msg)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, msg)

    def test_standalone_cli_exits_2(self):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = rd.main(["purge"])
        self.assertEqual(rc, 2)
        self.assertIn(rd.REFUSE_PREFIX, err.getvalue())
        self.assertNotIn("ok:", out.getvalue())
        with redirect_stderr(io.StringIO()), redirect_stdout(out):
            self.assertEqual(rd.main(["--json"]), 0)


class RefuseImapPurgeVerbTests(unittest.TestCase):
    def test_finds_store_deleted_expunge_trash_purge(self):
        self.assertEqual(rd.find_imap_purge_verb(["STORE"]), "store")
        self.assertEqual(rd.find_imap_purge_verb(["\\Deleted"]), "\\deleted")
        self.assertEqual(rd.find_imap_purge_verb(["--expunge"]), "expunge")
        self.assertEqual(rd.find_imap_purge_verb(["Trash-purge"]), "trash-purge")
        self.assertIsNone(rd.find_imap_purge_verb(["--db", "/tmp/mailroom-copy.sqlite"]))

    def test_refuse_imap_purge_message_is_local_only(self):
        with self.assertRaises(rd.DestructiveRefuse) as ctx:
            rd.refuse_imap_purge_cli(["STORE", "\\Deleted"])
        msg = str(ctx.exception)
        self.assertIn(rd.IMAP_PURGE_PREFIX, msg)
        self.assertIn("STORE \\Deleted", msg)
        self.assertIn("present_on_server", msg)
        self.assertIn("Trash-purge", msg)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, msg)

    def test_tombstone_refuses_imap_purge_verbs(self):
        copy = "/tmp/mailroom-copy.sqlite"
        for argv in (
            ["STORE", "--db", copy],
            ["\\Deleted", "--db", copy],
            ["Trash-purge", "--db", copy],
            ["--expunge", "--db", copy],
        ):
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                rc = imap_tombstone.main(argv)
            self.assertEqual(rc, 2, msg=argv)
            self.assertNotIn("opened_db=", out.getvalue())
            self.assertTrue(
                rd.REFUSE_PREFIX in err.getvalue()
                or rd.IMAP_PURGE_PREFIX in err.getvalue(),
                msg=err.getvalue(),
            )


class RefuseDestructiveWiredCliTests(unittest.TestCase):
    def _assert_refuses(self, fn, argv: list[str]) -> None:
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = fn(argv)
        self.assertEqual(rc, 2, msg="%s %s" % (fn, argv))
        self.assertIn(rd.REFUSE_PREFIX, err.getvalue())
        self.assertNotIn("opened_db=", out.getvalue())

    def test_daily_children_and_ask_mail_refuse(self):
        copy = ["/tmp/mailroom-copy.sqlite"]
        self._assert_refuses(imap_tombstone.main, ["--expunge", "--db", copy[0]])
        self._assert_refuses(copy_db.child_main, ["--purge", "--db", copy[0]])
        self._assert_refuses(copy_db.main, ["empty-trash", "--db", copy[0]])
        self._assert_refuses(daily.main, ["--delete-gone", "--print-plan"])
        self._assert_refuses(ask_mail.main, ["drop-messages"])
        self._assert_refuses(eb.main, ["--purge", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
