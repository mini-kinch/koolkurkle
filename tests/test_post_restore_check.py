#!/usr/bin/env python3
"""Read-only post-restore check. Temp sqlite only. No network, no Keychain."""

from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import post_restore_check as check  # noqa: E402

SINCE = "2026-09-26T15:50:00Z"
SINCE_DT = check.parse_ts(SINCE)
NEWER = "2026-09-28T04:00:00Z"
OLDER = "2026-09-26T08:50:00Z"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE messages (
          id TEXT PRIMARY KEY,
          ingested_at TEXT,
          present_on_server INTEGER
        );
        CREATE TABLE notify_log (
          ts TEXT NOT NULL,
          message_id TEXT NOT NULL,
          channel TEXT NOT NULL,
          result TEXT
        );
        CREATE TABLE bills (
          id TEXT PRIMARY KEY,
          message_id TEXT NOT NULL,
          due_date TEXT,
          status TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO messages (id, ingested_at, present_on_server) VALUES (?, ?, ?)",
        [
            ("old-live", "2026-09-25T12:00:00Z", 1),
            ("old-gone", "2026-09-20T00:00:00Z", 0),
            ("new-gone", "2026-09-27T01:00:00Z", 0),
            ("new-live", "2026-09-27T02:00:00Z", None),
            ("at-since", SINCE, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO notify_log (ts, message_id, channel, result) VALUES (?, ?, ?, ?)",
        [
            ("2026-09-25T00:00:00Z", "old-live", "imessage", "ok"),
            ("2026-09-27T03:00:00Z", "new-live", "imessage", "ok"),
            ("2026-09-27T03:30:00-07:00", "bills-2026-09-27", "imessage", "ok"),
            (SINCE, "at-since", "imessage", "ok"),
        ],
    )
    conn.executemany(
        "INSERT INTO bills (id, message_id, due_date, status) VALUES (?, ?, ?, ?)",
        [
            ("b-old", "old-live", "2026-09-01", "open"),
            ("b-new", "new-live", "2026-09-30", "open"),
            ("b-at", "at-since", "2026-09-26", "open"),
        ],
    )
    conn.commit()
    conn.close()


def _stamp(archive: Path, value: str, note: str = "embed=skipped") -> None:
    logs = archive / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / check.STAMP_NAME).write_text(value + "\n" + note + "\n", encoding="utf-8")


def _quiet_processes() -> dict[str, str]:
    return {
        "meta_fill": "absent",
        "with_writer_lock": "absent",
        "daily_process": "absent",
    }


class ParseAndCountTests(unittest.TestCase):
    def test_naive_timestamp_is_utc(self):
        parsed = check.parse_ts("2026-09-26 15:50:00")
        self.assertEqual(parsed, SINCE_DT)

    def test_offset_compares_in_utc(self):
        parsed = check.parse_ts("2026-09-27T03:30:00-07:00")
        self.assertIsNotNone(parsed)
        self.assertGreaterEqual(parsed, SINCE_DT)

    def test_fixture_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom.sqlite"
            _write_db(db)
            before = _sha256(db)
            conn = check.open_readonly(db)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("INSERT INTO messages (id) VALUES ('x')")
                self.assertEqual(check.gone_count(conn), 2)
                self.assertEqual(check.messages_inserted_since(conn, SINCE_DT), 3)
                self.assertEqual(check.urgent_texts_since(conn, SINCE_DT), 2)
                self.assertEqual(check.bills_added_since(conn, SINCE_DT), 2)
            finally:
                conn.close()
            self.assertEqual(_sha256(db), before)

    def test_bills_timestamp_column_beats_message_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom.sqlite"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE messages (id TEXT PRIMARY KEY, ingested_at TEXT, "
                "present_on_server INTEGER)"
            )
            conn.execute(
                "INSERT INTO messages (id, ingested_at, present_on_server) "
                "VALUES ('old', '2026-01-01T00:00:00Z', 1)"
            )
            conn.execute(
                "CREATE TABLE bills (id TEXT PRIMARY KEY, message_id TEXT, "
                "created_at TEXT, status TEXT)"
            )
            conn.execute(
                "INSERT INTO bills (id, message_id, created_at, status) "
                "VALUES ('late', 'old', '2026-09-27T00:00:00Z', 'open')"
            )
            conn.commit()
            conn.close()
            ro = check.open_readonly(db)
            try:
                self.assertEqual(check.bills_added_since(ro, SINCE_DT), 1)
            finally:
                ro.close()

    def test_equal_stamp_is_not_newer(self):
        self.assertFalse(check.stamp_is_newer(SINCE, SINCE_DT))
        self.assertTrue(check.stamp_is_newer(NEWER, SINCE_DT))
        self.assertFalse(check.stamp_is_newer(None, SINCE_DT))


class ProcessAndLockUnitTests(unittest.TestCase):
    def test_classify_processes_matches_needles_only(self):
        text = "\n".join(
            [
                "python3 scripts/post_restore_check.py --since 2026-09-26T00:00:00Z",
                "python3 scripts/attachments/meta_fill.py --apply",
                "python3 scripts/with_writer_lock.py --purpose x -- true",
                "/bin/zsh scripts/run_mailroom_daily.sh",
            ]
        )
        states = check.classify_processes(text)
        self.assertEqual(states["meta_fill"], "running")
        self.assertEqual(states["with_writer_lock"], "running")
        self.assertEqual(states["daily_process"], "running")

    def test_absent_when_only_the_checker_is_listed(self):
        states = check.classify_processes("python3 scripts/post_restore_check.py\n")
        self.assertEqual(states, _quiet_processes())

    def test_missing_lock_file_is_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check.lock_state(Path(tmp) / "mailroom.write.lock"), "free")

    def test_unlocked_file_is_free_via_proc_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mailroom.daily.lock"
            path.write_text("idle\n", encoding="utf-8")
            self.assertEqual(check.lock_state(path), "free")

    def test_proc_locks_token_matches_a_held_flock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mailroom.write.lock"
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                proc_locks = Path("/proc/locks").read_text(encoding="utf-8")
                self.assertTrue(check.lock_held_in_proc(path, proc_locks))
                self.assertEqual(check.lock_state(path), "held")
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self._ps = check.read_process_table
        check.read_process_table = lambda: ""

    def tearDown(self):
        check.read_process_table = self._ps

    def _archive(self, tmp: str) -> Path:
        archive = Path(tmp)
        _write_db(archive / "mailroom.sqlite")
        _stamp(archive, NEWER)
        return archive

    def test_ok_lines_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            before = _sha256(archive / "mailroom.sqlite")
            names_before = sorted(p.name for p in archive.iterdir())
            lines, bits = check.evaluate(archive, SINCE_DT)
            self.assertEqual(bits, check.EXIT_OK)
            self.assertEqual(
                lines,
                [
                    "last_daily_rag_ok=%s newer_than_since=yes" % NEWER,
                    "G=2",
                    "messages_inserted_since=3",
                    "urgent_texts_sent_since=2",
                    "bills_added_since=2",
                    "write.lock=free",
                    "daily.lock=free",
                    "meta_fill=absent",
                    "with_writer_lock=absent",
                    "daily_process=absent",
                ],
            )
            self.assertEqual(_sha256(archive / "mailroom.sqlite"), before)
            self.assertEqual(sorted(p.name for p in archive.iterdir()), names_before)

    def test_old_stamp_sets_stamp_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            _stamp(archive, OLDER)
            _lines, bits = check.evaluate(archive, SINCE_DT)
            self.assertEqual(bits & check.EXIT_STAMP, check.EXIT_STAMP)
            self.assertEqual(bits & check.EXIT_DB, 0)

    def test_missing_sqlite_sets_db_bit_and_still_reports_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            _stamp(archive, NEWER)
            lines, bits = check.evaluate(archive, SINCE_DT)
            self.assertEqual(bits & check.EXIT_DB, check.EXIT_DB)
            self.assertIn("G=unavailable", lines)
            self.assertIn(
                "last_daily_rag_ok=%s newer_than_since=yes" % NEWER,
                lines,
            )

    def test_held_lock_and_old_stamp_combine_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            _stamp(archive, OLDER)
            lock = archive / "mailroom.write.lock"
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lines, bits = check.evaluate(archive, SINCE_DT)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            self.assertEqual(bits, check.EXIT_STAMP | check.EXIT_LOCK)
            self.assertIn("write.lock=held", lines)
            self.assertIn("daily.lock=free", lines)


class CliTests(unittest.TestCase):
    def test_usage_codes(self):
        self.assertEqual(check.main([]), check.EXIT_USAGE)
        self.assertEqual(check.main(["--since", SINCE]), check.EXIT_USAGE)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                check.main(["--archive", tmp, "--since", "yesterday"]),
                check.EXIT_USAGE,
            )
            missing = str(Path(tmp) / "no-such-archive")
            self.assertEqual(
                check.main(["--archive", missing, "--since", SINCE]),
                check.EXIT_USAGE,
            )

    def test_cli_ok_against_temp_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            db = archive / "mailroom.sqlite"
            _write_db(db)
            _stamp(archive, NEWER)
            before = _sha256(db)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "post_restore_check.py"),
                    "--archive",
                    tmp,
                    "--since",
                    SINCE,
                ],
                check=False,
                capture_output=True,
                text=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": tmp,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("G=2\n", proc.stdout)
            self.assertIn("newer_than_since=yes\n", proc.stdout)
            self.assertEqual(_sha256(db), before)
            self.assertFalse((archive / "mailroom.sqlite-journal").exists())
            self.assertFalse((archive / "mailroom.sqlite-wal").exists())

    def test_archive_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            _write_db(archive / "mailroom.sqlite")
            _stamp(archive, NEWER)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "post_restore_check.py"),
                    "--since",
                    SINCE,
                ],
                check=False,
                capture_output=True,
                text=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": tmp,
                    "ARCHIVE_ROOT": tmp,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("messages_inserted_since=3\n", proc.stdout)

    def test_subprocess_sees_held_lock_and_does_not_take_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            _write_db(archive / "mailroom.sqlite")
            _stamp(archive, NEWER)
            lock = archive / "mailroom.daily.lock"
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "post_restore_check.py"),
                        "--archive",
                        tmp,
                        "--since",
                        SINCE,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "HOME": tmp,
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                )
                self.assertEqual(proc.returncode, check.EXIT_LOCK, proc.stderr)
                self.assertIn("daily.lock=held\n", proc.stdout)
                self.assertIn("write.lock=free\n", proc.stdout)
                probe = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import fcntl, os, sys\n"
                            "fd = os.open(sys.argv[1], os.O_RDWR)\n"
                            "try:\n"
                            "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                            "except BlockingIOError:\n"
                            "    sys.exit(0)\n"
                            "sys.exit(3)\n"
                        ),
                        str(lock),
                    ],
                    check=False,
                )
                self.assertEqual(probe.returncode, 0)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def test_real_ps_sees_meta_fill_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            _write_db(archive / "mailroom.sqlite")
            _stamp(archive, NEWER)
            sleeper = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)", "meta_fill.py"]
            )
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "post_restore_check.py"),
                        "--archive",
                        tmp,
                        "--since",
                        SINCE,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "HOME": tmp,
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                )
                self.assertEqual(proc.returncode, check.EXIT_PROCESS, proc.stderr)
                self.assertIn("meta_fill=running\n", proc.stdout)
                self.assertIn("daily_process=absent\n", proc.stdout)
                self.assertIn("with_writer_lock=absent\n", proc.stdout)
            finally:
                sleeper.kill()
                sleeper.wait(timeout=5)


class SourceContractTests(unittest.TestCase):
    def test_script_is_read_only_and_takes_no_lock(self):
        text = (SCRIPTS / "post_restore_check.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", text)
        self.assertIn("PRAGMA query_only=ON", text)
        self.assertNotIn("fcntl.flock", text)
        self.assertNotIn("LOCK_EX", text)
        self.assertNotIn("LOCK_SH", text)
        self.assertNotIn("find-generic-password", text)
        self.assertNotIn("urllib", text)
        self.assertNotIn("imaplib", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("Path.home", text)

    def test_review_opens_with_the_required_line(self):
        doc = ROOT / "docs" / "att0" / "post-restore-backlog-review.md"
        first = doc.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(
            first,
            "Repo copies only; the installed copies on the Mac may differ and were not reviewed.",
        )


if __name__ == "__main__":
    unittest.main()
