#!/usr/bin/env python3
"""Q12: metadata fill exits 4 on Keychain or auth failure."""

from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.meta_fill as meta  # noqa: E402
import attachments.migrate_att0_schema as mig  # noqa: E402
from imap_keychain import KeychainError  # noqa: E402

AUTH_STDERR = "error: imap auth failed\n"


def _db(root: Path) -> Path:
    path = root / "mailroom-copy.sqlite"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE messages (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              folder TEXT,
              uid TEXT,
              jsonl_offset INTEGER,
              jsonl_len INTEGER,
              has_attachments INTEGER DEFAULT 0,
              subject TEXT
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


class MetaFillAuthExitTests(unittest.TestCase):
    def test_pinned_status_and_stderr(self) -> None:
        note = (ROOT / "docs" / "att" / "meta-fill-auth-exit.md").read_text(encoding="utf-8")
        self.assertIn("status **4**", note)
        self.assertIn(AUTH_STDERR.strip(), note)
        self.assertEqual(meta.AUTH_EXIT, 4)
        self.assertNotEqual(meta.AUTH_EXIT, 2)

    def test_missing_keychain_password_exits_4(self) -> None:
        def missing():
            raise KeychainError("imap keychain password is missing")

        with tempfile.TemporaryDirectory() as tmp:
            db = _db(Path(tmp))
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), mock.patch(
                "attachments.meta_fill.read_imap_app_password", missing
            ):
                rc = meta.main(
                    ["--db", str(db), "--source", "imap", "--max-messages", "0", "--timeout", "0"],
                    env={
                        "MAILROOM_IMAP_HOST": "imap.example.invalid",
                        "MAILROOM_IMAP_USER": "fixture-user",
                    },
                )
        self.assertEqual(rc, 4)
        self.assertEqual(err.getvalue(), AUTH_STDERR)
        self.assertIn("att0 meta fill", out.getvalue())
        self.assertIn("imap keychain password is missing", out.getvalue())
        self.assertNotIn("usage:", err.getvalue())

    def test_curl_auth_rc_67_exits_4_and_connect_rc_7_stays_0(self) -> None:
        def password():
            return "example-secret-token"

        def runner(code: int):
            def _run(argv, config_text, env, timeout):
                return code, "", "no"
            return _run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = []
            for code in (67, 7):
                path = root / ("mailroom-copy-%s.sqlite" % code)
                conn = sqlite3.connect(str(path))
                conn.execute(
                    """
                    CREATE TABLE messages (
                      id TEXT PRIMARY KEY, source TEXT NOT NULL, folder TEXT,
                      uid TEXT, jsonl_offset INTEGER, jsonl_len INTEGER,
                      has_attachments INTEGER DEFAULT 0, subject TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO messages (id, source, uid, subject, folder) "
                    "VALUES ('ex-1', 'imap-live', '9', 'synthetic', 'INBOX')"
                )
                conn.commit()
                conn.close()
                mig.migrate_database(path, cmdlines=[], lock_held=False)
                cases.append((code, path))
            for code, path in cases:
                out = io.StringIO()
                err = io.StringIO()
                with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), mock.patch(
                    "attachments.meta_fill.read_imap_app_password", password
                ), mock.patch("imap_curl.run_subprocess", runner(code)):
                    rc = meta.main(
                        [
                            "--db", str(path), "--source", "imap", "--apply",
                            "--max-messages", "0", "--timeout", "0",
                        ],
                        env={
                            "MAILROOM_IMAP_HOST": "imap.example.invalid",
                            "MAILROOM_IMAP_USER": "fixture-user",
                        },
                    )
                text = err.getvalue()
                if code == 67:
                    self.assertEqual(rc, 4, text)
                    self.assertIn(AUTH_STDERR, text)
                    self.assertIn("rc=67", text)
                    self.assertNotIn("usage:", text)
                    self.assertIn("rc 67", out.getvalue())
                else:
                    self.assertEqual(rc, 0, text)
                    self.assertNotIn("imap auth failed", text)
                    self.assertIn("rc=7", text)
                    self.assertIn("rc 7", out.getvalue())

    def test_argparse_stays_2(self) -> None:
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as ctx:
                meta.main([])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("usage:", err.getvalue())
        self.assertNotIn("imap auth failed", err.getvalue())

    def test_timeout_phrase_is_not_auth_exit(self) -> None:
        self.assertFalse(
            meta.report_auth_failed({"curl_failures": ["imap keychain read timed out"]})
        )


if __name__ == "__main__":
    unittest.main()
