#!/usr/bin/env python3
"""Scope B allowlist: explicit mailroom.sqlite is db_mode=sor.

Hermetic. Temp dirs only. No network, no Ollama, no real MailArchive,
no Keychain. Rem and writer-lock probes are injected or use a temp lock.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mailroom_copy_db as copy_db  # noqa: E402
import mailroom_daily as daily  # noqa: E402
import with_writer_lock as wwl  # noqa: E402

COPY_A = "mailroom-copy.sqlite"
COPY_B = "mailroom-daily-copy.sqlite"
SOR = "mailroom.sqlite"
UNKNOWN = "other.sqlite"
REM = ((88, "embed_backfill.py --reembed-legacy --db /tmp/mailroom.sqlite"),)


def _stderr_stdout(fn):
    err = io.StringIO()
    out = io.StringIO()
    with redirect_stderr(err), redirect_stdout(out):
        rc = fn()
    return rc, out.getvalue(), err.getvalue()


class DbModeForTests(unittest.TestCase):
    def test_sor_basename_is_sor(self):
        self.assertEqual(copy_db.db_mode_for(Path("/tmp/mailroom.sqlite")), "sor")
        self.assertEqual(daily.db_mode_for(Path("/tmp/nested/mailroom.sqlite")), "sor")

    def test_copy_basenames_are_copy(self):
        self.assertEqual(copy_db.db_mode_for(Path("/tmp") / COPY_A), "copy")
        self.assertEqual(copy_db.db_mode_for(Path("/tmp") / COPY_B), "copy")
        self.assertEqual(daily.db_mode_for(Path("/var/tmp") / COPY_A), "copy")

    def test_unknown_basename_would_be_copy_but_allowlist_refuses(self):
        self.assertEqual(copy_db.db_mode_for(Path("/tmp") / UNKNOWN), "copy")
        self.assertFalse(copy_db.allowed_copy_db(Path("/tmp") / UNKNOWN))
        with self.assertRaises(copy_db.CopyDbRefuse):
            copy_db.resolve_copy_db("/tmp/%s" % UNKNOWN)

    def test_allowlist_is_copy_basenames_plus_explicit_sor(self):
        self.assertEqual(
            copy_db.ALLOWED_DB_BASENAMES,
            set(copy_db.COPY_DB_BASENAMES) | {copy_db.SOR_BASENAME},
        )
        self.assertNotIn(SOR, copy_db.COPY_DB_BASENAMES)
        self.assertTrue(copy_db.allowed_copy_db(Path("/tmp") / SOR))
        self.assertTrue(copy_db.allowed_copy_db(Path("/tmp") / COPY_A))
        self.assertTrue(copy_db.allowed_copy_db(Path("/tmp") / COPY_B))


class ChildMainGateTests(unittest.TestCase):
    def test_explicit_sor_allowed_when_rem_absent_and_lock_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = _stderr_stdout(
                lambda: copy_db.child_main(
                    ["--db", db],
                    cmdlines=(),
                    lock_held=False,
                )
            )
        self.assertEqual(rc, 0, err)
        self.assertIn("db_mode=sor", err)
        self.assertNotIn("db_mode=refused", err)
        self.assertNotIn("db_mode=copy", err)
        self.assertIn("opened_db=%s" % db, out)

    def test_explicit_sor_refuses_when_rem_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = _stderr_stdout(
                lambda: copy_db.child_main(
                    ["--db", db],
                    cmdlines=REM,
                    lock_held=False,
                )
            )
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn("opened_db=", out)
        self.assertNotIn("db_mode=sor", err)

    def test_explicit_sor_refuses_when_foreign_lock_injected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = _stderr_stdout(
                lambda: copy_db.child_main(
                    ["--db", db],
                    cmdlines=(),
                    lock_held=True,
                )
            )
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn("opened_db=", out)

    def test_explicit_sor_refuses_when_temp_lock_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            lock = Path(tmp) / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "other-writer")
            try:
                rc, out, err = _stderr_stdout(
                    lambda: copy_db.child_main(
                        ["--db", db],
                        cmdlines=(),
                        lock_path=lock,
                    )
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn("opened_db=", out)

    def test_copy_basenames_emit_copy_even_if_rem_present(self):
        for name in (COPY_A, COPY_B):
            with tempfile.TemporaryDirectory() as tmp:
                db = str(Path(tmp) / name)
                rc, out, err = _stderr_stdout(
                    lambda: copy_db.child_main(
                        ["--db", db],
                        cmdlines=REM,
                        lock_held=True,
                    )
                )
            self.assertEqual(rc, 0, err)
            self.assertIn("db_mode=copy", err)
            self.assertNotIn("db_mode=sor", err)
            self.assertNotIn("db_mode=refused", err)
            self.assertIn("opened_db=%s" % db, out)

    def test_unset_refuses(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAILROOM_DB", None)
            rc, out, err = _stderr_stdout(lambda: copy_db.child_main([], cmdlines=(), lock_held=False))
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("unset", err.lower())
        self.assertNotIn("opened_db=", out)

    def test_unknown_basename_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / UNKNOWN)
            rc, out, err = _stderr_stdout(
                lambda: copy_db.child_main(["--db", db], cmdlines=(), lock_held=False)
            )
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn(UNKNOWN, err)
        self.assertNotIn("opened_db=", out)

    def test_destructive_guard_still_refuses_explicit_sor(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = _stderr_stdout(
                lambda: copy_db.child_main(
                    ["--purge", "--db", db],
                    cmdlines=(),
                    lock_held=False,
                )
            )
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertNotIn("db_mode=sor", err)
        self.assertNotIn("opened_db=", out)


class CopyDbMainTests(unittest.TestCase):
    def _main(self, argv, *, env=None, rem=(), lock=None):
        updates = {} if env is None else dict(env)
        if lock is None:
            lock_rv = (False, "writer lock free")
        else:
            lock_rv = lock

        def run():
            with patch.dict(os.environ, updates, clear=False):
                with patch("sor_writer_gate.rem_process_hits", return_value=list(rem)):
                    with patch("sor_writer_gate.writer_lock_held", return_value=lock_rv):
                        return copy_db.main(argv)

        return _stderr_stdout(run)

    def test_explicit_sor_flag_is_sor_when_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = self._main(["--db", db], env={"MAILROOM_DB": ""})
        self.assertEqual(rc, 0, err)
        self.assertIn("db_mode=sor", err)
        self.assertIn(db, out)
        self.assertNotIn("db_mode=refused", err)
        self.assertNotIn("db_mode=copy", err)

    def test_explicit_sor_env_is_sor_when_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = self._main([], env={"MAILROOM_DB": db})
        self.assertEqual(rc, 0, err)
        self.assertIn("db_mode=sor", err)
        self.assertIn(db, out)

    def test_explicit_sor_refuses_when_rem_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = self._main(["--db", db], rem=REM)
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn(db + "\n", out)

    def test_explicit_sor_env_refuses_when_rem_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = self._main([], env={"MAILROOM_DB": db}, rem=REM)
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn(db, out)

    def test_explicit_sor_refuses_when_foreign_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = self._main(
                ["--db", db],
                lock=(True, "writer lock held: pid=9"),
            )
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)

    def test_copy_basenames_emit_copy(self):
        for name in (COPY_A, COPY_B):
            with tempfile.TemporaryDirectory() as tmp:
                db = str(Path(tmp) / name)
                rc, out, err = self._main(["--db", db], rem=REM, lock=(True, "held"))
            self.assertEqual(rc, 0, err)
            self.assertIn("db_mode=copy", err)
            self.assertIn(db, out)
            self.assertNotIn("db_mode=sor", err)

    def test_unset_refuses(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAILROOM_DB", None)
            rc, out, err = _stderr_stdout(lambda: copy_db.main([]))
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("unset", err.lower())
        self.assertIn("explicit copy path", err)
        self.assertEqual(out, "")

    def test_unknown_basename_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / UNKNOWN)
            rc, out, err = self._main(["--db", db])
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn(UNKNOWN, err)
        self.assertEqual(out.strip(), "")

    def test_destructive_and_imap_purge_still_refuse(self):
        db = "/tmp/%s" % SOR
        for argv in (["--expunge", "--db", db], ["--purge", "--db", db]):
            rc, out, err = _stderr_stdout(lambda argv=argv: copy_db.main(argv))
            self.assertEqual(rc, 2, err)
            self.assertIn("db_mode=refused", err)
            self.assertNotIn("db_mode=sor", err)
            self.assertEqual(out, "")


class DailyGateTests(unittest.TestCase):
    def _daily(self, tmp, db, *, rem=(), lock_path=None):
        argv = [
            "--archive",
            tmp,
            "--logs",
            tmp,
            "--scripts",
            tmp,
            "--db",
            db,
            "--print-plan",
            "--allow-missing",
        ]
        env = {
            "MAILROOM_DB": db,
            "MAILROOM_WRITE_LOCK": lock_path
            or str(Path(tmp) / "absent.write.lock"),
        }

        def run():
            with patch.dict(os.environ, env, clear=False):
                with patch("sor_writer_gate.rem_process_hits", return_value=list(rem)):
                    return daily.main(argv)

        return _stderr_stdout(run)

    def test_explicit_sor_allowed_when_rem_absent_and_lock_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, _out, err = self._daily(tmp, db)
        self.assertEqual(rc, 0, err)
        self.assertIn("db_mode=sor", err)
        self.assertNotIn("db_mode=refused", err)
        self.assertNotIn("db_mode=copy", err)

    def test_explicit_sor_refuses_when_rem_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = self._daily(tmp, db, rem=REM)
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn("db_mode=sor", err)
        self.assertNotIn("imap_newmail", out)

    def test_explicit_sor_refuses_when_temp_lock_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            lock = Path(tmp) / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "other-writer")
            try:
                rc, out, err = self._daily(tmp, db, lock_path=str(lock))
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(rc, 2, err)
        self.assertIn("db_mode=refused", err)
        self.assertIn("CONFLICT", err)
        self.assertNotIn("imap_newmail", out)

    def test_copy_basenames_emit_copy(self):
        for name in (COPY_A, COPY_B):
            with tempfile.TemporaryDirectory() as tmp:
                db = str(Path(tmp) / name)
                rc, _out, err = self._daily(tmp, db, rem=REM)
            self.assertEqual(rc, 0, err)
            self.assertIn("db_mode=copy", err)
            self.assertNotIn("db_mode=sor", err)
            self.assertNotIn("db_mode=refused", err)

    def test_unset_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "MAILROOM_DB"}
            env["MAILROOM_WRITE_LOCK"] = str(Path(tmp) / "absent.write.lock")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "mailroom_daily.py"),
                    "--archive",
                    tmp,
                    "--logs",
                    tmp,
                    "--print-plan",
                    "--allow-missing",
                ],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("db_mode=refused", proc.stderr)
        self.assertIn("unset", proc.stderr.lower())

    def test_unknown_basename_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / UNKNOWN)
            rc, out, err = self._daily(tmp, db)
        self.assertEqual(rc, 2)
        self.assertIn("db_mode=refused", err)
        self.assertIn(UNKNOWN, err)
        self.assertNotIn("db_mode=sor", err)
        self.assertNotIn("imap_newmail", out)

    def test_destructive_guard_still_refuses_explicit_sor(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / SOR)
            rc, out, err = _stderr_stdout(
                lambda: daily.main(
                    ["--delete-gone", "--archive", tmp, "--db", db, "--print-plan"]
                )
            )
        self.assertEqual(rc, 2)
        self.assertNotIn("db_mode=sor", err)
        self.assertNotIn("imap_newmail", out)

    def test_guards_remain_wired(self):
        daily_src = (SCRIPTS / "mailroom_daily.py").read_text(encoding="utf-8")
        copy_src = (SCRIPTS / "mailroom_copy_db.py").read_text(encoding="utf-8")
        for name in ("refuse_destructive_cli", "refuse_intended_sor_writer"):
            self.assertIn(name, daily_src)
        for name in (
            "refuse_destructive_cli",
            "refuse_imap_purge_cli",
            "refuse_intended_sor_writer",
            "refuse_copy_from_live_sor",
            "DestructiveRefuse",
            "SorWriterRefuse",
        ):
            self.assertIn(name, copy_src)
        self.assertIn("db_mode_for(path)", copy_src)
        self.assertIn("db_mode_for(db)", daily_src)


if __name__ == "__main__":
    unittest.main()
