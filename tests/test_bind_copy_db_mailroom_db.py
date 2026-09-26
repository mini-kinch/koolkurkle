#!/usr/bin/env python3
"""KOO-46 bind_copy_db / daily children honor MAILROOM_DB.

Fixture unit tests only. No live SoR open.
argv=None → sys.argv[1:]; children open copy DB; refuse SoR stub.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import classify  # noqa: E402
import imap_fetch_bodies_fts  # noqa: E402
import imap_newmail  # noqa: E402
import imap_tombstone  # noqa: E402
import mailroom_copy_db as copy_db  # noqa: E402
import notify_bills  # noqa: E402

OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"

CHILD_MODULES = (
    imap_newmail,
    imap_tombstone,
    imap_fetch_bodies_fts,
    classify,
    notify_bills,
)
CHILD_NAMES = (
    "imap_newmail.py",
    "imap_tombstone.py",
    "imap_fetch_bodies_fts.py",
    "classify.py",
    "notify_bills.py",
)
COPY_A = "mailroom-copy.sqlite"
COPY_B = "mailroom-daily-copy.sqlite"
SOR_STUB = "mailroom.sqlite"


def _opened_db_line(name, db):
    """classify.py logs the basename; other children log the full path."""
    if name == "classify.py":
        return "opened_db=%s" % Path(db).name
    return "opened_db=%s" % db

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class BindCopyDbArgvNoneFixtureTests(unittest.TestCase):
    def test_argv_none_reads_sys_argv_tail(self):
        copy = "/tmp/%s" % COPY_A
        with patch.object(sys, "argv", ["imap_tombstone.py", "--db", copy]):
            self.assertEqual(copy_db.parse_db_cli(None), copy)
            path = copy_db.bind_copy_db(None)
        self.assertEqual(str(path), copy)
        self.assertEqual(os.environ.get("MAILROOM_DB"), copy)

    def test_source_documents_none_means_sys_argv(self):
        src = Path(copy_db.__file__).read_text(encoding="utf-8")
        self.assertIn("sys.argv[1:]", src)
        self.assertIn("argv is None", src)
        self.assertIn("empty SoR stub", src)


class DailyChildrenOpenCopyDbTests(unittest.TestCase):
    def test_each_child_opens_copy_not_sor_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / COPY_B
            for mod in CHILD_MODULES:
                with self.subTest(mod=mod.__name__):
                    rc = mod.main(["--db", str(db)])
                    self.assertEqual(rc, 0)
                    self.assertEqual(os.environ.get("MAILROOM_DB"), str(db))
                    self.assertEqual(Path(os.environ["MAILROOM_DB"]).name, COPY_B)

    def test_cli_children_report_copy_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / COPY_A
            for name in CHILD_NAMES:
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / name), "--db", str(db)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("db_mode=copy", proc.stderr)
                self.assertIn(_opened_db_line(name, db), proc.stdout)
                if name == "classify.py":
                    self.assertNotIn(str(db), proc.stdout)
                self.assertNotIn("db_mode=refused", proc.stderr)


class ExplicitSorBasenameTests(unittest.TestCase):
    def test_explicit_sor_stub_via_flag_and_env_is_sor_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / SOR_STUB
            env_base = {k: v for k, v in os.environ.items() if k != "MAILROOM_DB"}
            env_base["MAILROOM_WRITE_LOCK"] = str(Path(tmp) / "absent.write.lock")
            for name in CHILD_NAMES:
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / name), "--db", str(stub)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env_base,
                )
                self.assertEqual(proc.returncode, 0, "%s %s" % (name, proc.stderr))
                self.assertIn("db_mode=sor", proc.stderr)
                self.assertTrue(
                    ("opened_db=%s" % stub) in proc.stdout
                    or ("opened_db=%s" % SOR_STUB) in proc.stdout,
                    msg=proc.stdout,
                )
                self.assertNotIn("db_mode=refused", proc.stderr)
            env = {**env_base, "MAILROOM_DB": str(stub)}
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "imap_tombstone.py")],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("db_mode=sor", proc.stderr)
            self.assertNotIn("db_mode=refused", proc.stderr)

class BindCopyDbOpsPointerTests(unittest.TestCase):
    def test_ops_and_readme_lock_bind_contract(self):
        for path in (OPS, README, MAILROOM):
            text = path.read_text(encoding="utf-8")
            self.assertIn("sys.argv[1:]", text, msg=path.name)
            self.assertIn("argv=None", text.replace(" ", ""), msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("Refuse the SoR stub", ops)
        self.assertIn("Tests only; no live SoR open", ops)


if __name__ == "__main__":
    unittest.main()
