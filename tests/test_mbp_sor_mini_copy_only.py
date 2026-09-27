#!/usr/bin/env python3
"""KOO-15 Mini SoR (sole writer) vs MBP non-writer ops contract.

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
SOR_HEADING = "## Mini SoR (sole writer) vs MBP non-writer"


def sor_section(raw: str) -> str:
    """Return the SoR section body, stopping at the next heading."""
    start = raw.index(SOR_HEADING)
    nxt = raw.find("\n## ", start + len(SOR_HEADING))
    if nxt == -1:
        return raw[start:]
    return raw[start:nxt]


class MbpSorMiniCopyOnlyContractTests(unittest.TestCase):
    def test_contract_locks_mbp_sor_mini_copy_only_language(self):
        raw = OPS.read_text(encoding="utf-8")
        section = sor_section(raw)
        self.assertIn("## Mini SoR (sole writer) vs MBP non-writer", section)
        self.assertNotIn("## MBP SoR vs Mini copy-only", raw)
        self.assertIn("Since the 2026-09-24 SoR flip", section)
        self.assertIn("**Mini** is the SoR writer under the daily only", section)
        self.assertIn("`mailroom.sqlite`", section)
        self.assertIn("The MBP is a non-writer", section)
        self.assertIn("rollback/read", section)
        self.assertIn("No MBP writers against SoR", section)
        self.assertIn("mailroom-copy.sqlite", section)
        self.assertIn("mailroom-daily-copy.sqlite", section)
        self.assertIn(
            "`db_mode=sor` only when `mailroom.sqlite` is explicitly named and rem-legacy is absent.",
            section,
        )
        self.assertIn("explicitly named and rem-legacy is absent", section)
        self.assertIn(
            "There is no silent default to `mailroom.sqlite`.",
            section,
        )
        self.assertIn(
            "An unset or unknown basename is a hard refuse (`db_mode=refused`).",
            section,
        )
        self.assertIn("hard refuse", section)
        self.assertIn("db_mode=refused", section)
        self.assertIn("EXIT 0", section)
        self.assertIn("promotion stays gated on rem-legacy EXIT 0", section)
        self.assertIn("One writer holds the writer lock (flock).", section)
        self.assertIn("Do not start a second writer against SoR", section)
        self.assertIn(
            "Do not start a Mini writer against SoR while rem-legacy is live",
            section,
        )
        self.assertIn(
            "Do not promote Mini (or any other writer) while rem-legacy is live",
            section,
        )
        self.assertIn("gated on rem-legacy", section)
        self.assertIn("docs/tests only", section)
        self.assertNotIn("or host", section)
        self.assertNotIn("dual writers are allowed", section.lower())
        self.assertNotIn("MBP may write SoR", section)
        self.assertNotIn("MBP writers against SoR are allowed", section)
        self.assertNotIn("second writer against sor is allowed", section.lower())
        self.assertNotIn("**MBP** is the live Source of Record", section)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        self.assertIn("ops-terminal.md", raw)
        index = next(
            line
            for line in raw.splitlines()
            if line.startswith("Mini SoR (sole writer) vs MBP non-writer")
        )
        self.assertIn("Mini SoR (sole writer) vs MBP non-writer", index)
        self.assertNotIn("MBP SoR vs Mini copy-only", index)
        self.assertIn("Mini is the only SoR writer", index)
        self.assertIn("`mailroom.sqlite`", index)
        self.assertIn("via the daily job only", index)
        self.assertNotIn("under the daily only", index)
        self.assertIn("the MBP is a non-writer", index)
        self.assertIn("no MBP writers against SoR", index)
        self.assertIn("PR-5 cutover still gated on rem-legacy EXIT 0", index)
        self.assertIn(
            "authority CRM-log/20260924-1201-mini-only-writer-user.md:4",
            index,
        )
        self.assertNotIn("MBP is the live Source of Record", index)
        self.assertNotIn("Mini is copy-only", index)
        self.assertNotIn("no Mini writers against SoR", index)
        recipes = next(
            line
            for line in raw.splitlines()
            if line.startswith("`mailroom.sqlite` name the Mini SoR")
        )
        self.assertIn("the only SoR writer", recipes)
        self.assertIn("The MBP is a non-writer", recipes)
        self.assertNotIn("MBP-SoR-only", recipes)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_mbp_is_not_sor_writer(self):
        raw = OPS.read_text(encoding="utf-8")
        section = sor_section(raw)
        self.assertIn("No MBP writers against SoR", section)
        self.assertIn("SoR writer under the daily only", section)
        self.assertIn("The MBP is a non-writer", section)
        self.assertIn("rollback/read", section)
        self.assertIn(
            "An unset or unknown basename is a hard refuse (`db_mode=refused`).",
            section,
        )
        self.assertIn("One writer holds the writer lock (flock).", section)
        self.assertIn("Do not start a second writer against SoR", section)
        self.assertIn(
            "Do not start a Mini writer against SoR while rem-legacy is live",
            section,
        )
        self.assertIn(
            "Do not promote Mini (or any other writer) while rem-legacy is live",
            section,
        )
        self.assertIn("promotion stays gated on rem-legacy EXIT 0", section)
        self.assertNotIn("or host", section)
        self.assertNotIn("cutover is not gated", section.lower())
        self.assertNotIn("dual writers are allowed", section.lower())
        self.assertNotIn("MBP may write SoR", section)
        self.assertNotIn("MBP writers against SoR are allowed", section)
        self.assertNotIn("Mini is the live Source of Record", section)
        self.assertNotIn("second writer against sor is allowed", section.lower())

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
