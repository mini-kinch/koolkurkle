#!/usr/bin/env python3
"""KOO-15 MBP SoR vs Mini copy-only ops contract.

Since the 2026-09-24 SoR flip, Mini is the SoR writer under the
daily only and the MBP is a non-writer. Docs/tests contract only.
No live SoR DB, no MailArchive, no Keychain reads, no rem-legacy
writer, no live machine SSH.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mailroom_copy_db as copy_db  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class MbpSorMiniCopyOnlyContractTests(unittest.TestCase):
    def test_contract_locks_mbp_sor_mini_copy_only_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("MBP SoR vs Mini copy-only", text)
        self.assertIn("Since the 2026-09-24 SoR flip", text)
        self.assertIn("**Mini** is the SoR writer under the daily only", text)
        self.assertIn("`mailroom.sqlite`", text)
        self.assertIn("The MBP is a non-writer", text)
        self.assertIn("rollback/read", text)
        self.assertIn("No MBP writers against SoR", text)
        self.assertIn("mailroom-copy.sqlite", text)
        self.assertIn("mailroom-daily-copy.sqlite", text)
        self.assertIn("hard refuse", text)
        self.assertIn("db_mode=refused", text)
        self.assertIn("writer lock", text)
        self.assertIn("flock", text)
        self.assertIn("Do not start a second writer against SoR", text)
        self.assertIn("Do not start a Mini writer against SoR", text)
        self.assertIn("Do not promote Mini", text)
        self.assertIn("gated on rem-legacy", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not open MailArchive or live sqlite", text)
        self.assertIn("read Keychain", text)
        self.assertIn("SSH a live machine", text)
        self.assertIn("change rem-legacy", text)
        self.assertNotIn("dual writers are allowed", text.lower())
        self.assertNotIn("MBP may write SoR", text)
        self.assertNotIn("MBP writers against SoR are allowed", text)
        self.assertNotIn("second writer against SoR is allowed", text.lower())
        self.assertNotIn("**MBP** is the live Source of Record", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("MBP SoR vs Mini copy-only", text)
        self.assertIn("MBP is the live Source of Record", text)
        self.assertIn("mailroom.sqlite", text)
        self.assertIn("Mini is copy-only", text)
        self.assertIn("no Mini writers against SoR", text)
        self.assertIn("PR-5 cutover still gated on rem-legacy EXIT 0", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_mbp_is_not_sor_writer(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("No MBP writers against SoR", text)
        self.assertIn("SoR writer under the daily only", text)
        self.assertIn("The MBP is a non-writer", text)
        self.assertIn("rollback/read", text)
        self.assertIn("hard refuse", text)
        self.assertIn("db_mode=refused", text)
        self.assertIn("writer lock", text)
        self.assertIn("Do not start a second writer against SoR", text)
        self.assertIn("Do not start a Mini writer against SoR", text)
        self.assertIn("Do not promote Mini", text)
        self.assertIn("gated on rem-legacy", text)
        self.assertNotIn("cutover is not gated", text.lower())
        self.assertNotIn("dual writers are allowed", text.lower())
        self.assertNotIn("MBP may write SoR", text)
        self.assertNotIn("MBP writers against SoR are allowed", text)
        self.assertNotIn("Mini is the live Source of Record", text)
        self.assertNotIn("second writer against SoR is allowed", text.lower())

    def test_non_allowlisted_basename_and_second_writer_are_refused(self):
        """A non-allowlisted basename is refused, and a second SoR writer is refused while the writer lock is held.

        Uses bind_copy_db / child_main only. Temp dir and fake basenames.
        Does not open MailArchive or a live sqlite.
        """
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "other-writer.sqlite"
            sor = Path(tmp) / "mailroom.sqlite"
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MAILROOM_DB", None)
                with self.assertRaises(copy_db.CopyDbRefuse) as caught:
                    copy_db.bind_copy_db(["--db", str(other)])
            self.assertIn(other.name, str(caught.exception))
            err = io.StringIO()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MAILROOM_DB", None)
                with redirect_stderr(err):
                    rc = copy_db.child_main(
                        ["--db", str(other)],
                        cmdlines=(),
                        lock_held=False,
                    )
            self.assertEqual(rc, 2)
            self.assertIn("db_mode=refused", err.getvalue())
            err_lock = io.StringIO()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MAILROOM_DB", None)
                with redirect_stderr(err_lock):
                    rc_lock = copy_db.child_main(
                        ["--db", str(sor)],
                        cmdlines=(),
                        lock_held=True,
                    )
            self.assertEqual(rc_lock, 2)
            self.assertIn("db_mode=refused", err_lock.getvalue())
            self.assertFalse(other.exists())
            self.assertFalse(sor.exists())


if __name__ == "__main__":
    unittest.main()
