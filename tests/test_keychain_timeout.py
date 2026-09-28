#!/usr/bin/env python3
"""Q13: Keychain read timeout is exit 5 and kills the child."""

from __future__ import annotations

import io
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.meta_fill as meta  # noqa: E402
import attachments.migrate_att0_schema as mig  # noqa: E402
import imap_keychain  # noqa: E402

ITEM = "keychain-item-under-test"
TIMEOUT_STDERR = "error: imap keychain read timed out\n"


def _db(root: Path) -> Path:
    path = root / "mailroom-copy.sqlite"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE messages (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, folder TEXT, uid TEXT,
              jsonl_offset INTEGER, jsonl_len INTEGER,
              has_attachments INTEGER DEFAULT 0, subject TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO messages (id, source, uid, subject, folder) "
            "VALUES ('ex-1', 'imap-live', '9', 'synthetic', 'INBOX')"
        )
        conn.commit()
    finally:
        conn.close()
    mig.migrate_database(path, cmdlines=[], lock_held=False)
    return path


class KeychainTimeoutTests(unittest.TestCase):
    def test_deadline_kills_a_hung_child(self) -> None:
        note = (ROOT / "docs" / "att" / "keychain-timeout-exit.md").read_text(encoding="utf-8")
        self.assertIn("exits **5**", note)
        self.assertEqual(imap_keychain.KEYCHAIN_TIMEOUT_S, 15.0)
        self.assertEqual(meta.KEYCHAIN_TIMEOUT_EXIT, 5)
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "hang.sh"
            script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            started = time.monotonic()
            with self.assertRaises(imap_keychain.KeychainTimeout) as ctx:
                imap_keychain._run_security(str(script), ITEM, timeout_s=0.4)
            elapsed = time.monotonic() - started
        self.assertEqual(str(ctx.exception), "imap keychain read timed out")
        self.assertLess(elapsed, 5.0)

    def test_fill_exits_5_when_the_password_read_times_out(self) -> None:
        def slow(*_args, **_kwargs):
            raise imap_keychain.KeychainTimeout("imap keychain read timed out")

        with tempfile.TemporaryDirectory() as tmp:
            db = _db(Path(tmp))
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), mock.patch.object(
                meta, "read_imap_app_password", slow
            ):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--max-messages", "0", "--timeout", "0"],
                    env={
                        "MAILROOM_IMAP_HOST": "imap.example.invalid",
                        "MAILROOM_IMAP_USER": "fixture-user",
                        "MAILROOM_KEYCHAIN_ITEM": ITEM,
                    },
                )
        self.assertEqual(rc, 5)
        self.assertIn(TIMEOUT_STDERR, err.getvalue())
        self.assertIn("imap keychain read timed out", out.getvalue())
        self.assertNotIn("usage:", err.getvalue())

    def test_unpinned_item_is_a_hard_stop_without_a_fallback(self) -> None:
        env = {
            "MAILROOM_KEYCHAIN_ITEM": "",
            "MAILROOM_KEYCHAIN_CONFIG": "",
            "IMAP_APP_PASSWORD": "env-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MAILROOM_KEYCHAIN_ITEM", None)
            os.environ.pop("MAILROOM_KEYCHAIN_CONFIG", None)
            with self.assertRaises(imap_keychain.KeychainError) as ctx:
                imap_keychain.read_imap_app_password()
        self.assertEqual(str(ctx.exception), "imap keychain item is not pinned")

    def test_config_file_pins_the_item(self) -> None:
        def runner(binary, service, timeout_s=None):
            self.assertEqual(service, ITEM)
            self.assertEqual(binary, "/usr/bin/security")
            return 0, "from-config"

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "item"
            config.write_text("# comment\n%s\n" % ITEM, encoding="utf-8")
            env = {"MAILROOM_KEYCHAIN_CONFIG": str(config)}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("MAILROOM_KEYCHAIN_ITEM", None)
                password = imap_keychain.read_imap_app_password(runner=runner, env=env)
        self.assertEqual(password, "from-config")

    def _fill(self, password, runner):
        steps = []

        def read_password(*_args, **_kwargs):
            steps.append("keychain")
            return password()

        def auth(*_args, **_kwargs):
            steps.append("auth")
            return runner()

        with tempfile.TemporaryDirectory() as tmp:
            db = _db(Path(tmp))
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), mock.patch.object(
                meta, "read_imap_app_password", read_password
            ), mock.patch("imap_curl.run_subprocess", auth):
                rc = meta.main(
                    [
                        "--db", str(db), "--source", "imap", "--apply",
                        "--max-messages", "0", "--timeout", "0",
                    ],
                    env={
                        "MAILROOM_IMAP_HOST": "imap.example.invalid",
                        "MAILROOM_IMAP_USER": "fixture-user",
                        "MAILROOM_KEYCHAIN_ITEM": ITEM,
                    },
                )
        return rc, out.getvalue(), err.getvalue(), steps

    def test_timed_out_keychain_read_exits_5_without_auth(self) -> None:
        classified = []
        real = meta.report_auth_failed

        def classify(report):
            classified.append(True)
            return real(report)

        def timed_out():
            raise imap_keychain.KeychainTimeout("imap keychain read timed out")

        with mock.patch.object(meta, "report_auth_failed", classify):
            rc, out, err, steps = self._fill(timed_out, lambda: (67, "", "no"))
        self.assertEqual(rc, 5)
        self.assertEqual(steps, ["keychain"])
        self.assertEqual(classified, [])
        self.assertIn(TIMEOUT_STDERR, err)
        self.assertNotIn("imap auth failed", err)
        self.assertIn("imap keychain read timed out", out)
        self.assertNotIn("usage:", err)

    def test_keychain_read_then_auth_failure_exits_4(self) -> None:
        rc, out, err, steps = self._fill(
            lambda: "example-secret-token",
            lambda: (67, "", "no"),
        )
        self.assertEqual(steps, ["keychain", "auth"])
        self.assertEqual(rc, 4)
        self.assertIn("error: imap auth failed\n", err)
        self.assertNotIn("imap keychain read timed out", err)
        self.assertIn("rc 67", out)
        self.assertNotIn("example-secret-token", out)
        self.assertNotIn("usage:", err)

    def test_keychain_read_then_auth_success_exits_0(self) -> None:
        rc, out, err, steps = self._fill(
            lambda: "example-secret-token",
            lambda: (
                0,
                "* OK [UIDVALIDITY 5] UIDs valid\n"
                '* 1 FETCH (UID 9 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 4 1))\n',
                "",
            ),
        )
        self.assertEqual(steps, ["keychain", "auth"])
        self.assertEqual(rc, 0)
        self.assertIn("messages=1", out)
        self.assertNotIn("imap auth failed", err)
        self.assertNotIn("imap keychain read timed out", err)
        self.assertNotIn("example-secret-token", out)


if __name__ == "__main__":
    unittest.main()
