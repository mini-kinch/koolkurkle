#!/usr/bin/env python3
"""Read-only post-restore check. Temp sqlite and PATH stubs only."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
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
FOREIGN_PID = "424242"

LSOF_STUB = """#!/usr/bin/env python3
import os
import sys

log = os.environ.get("LSOF_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("\\t".join(sys.argv) + "\\n")
if "--" not in sys.argv:
    sys.stderr.write("missing --\\n")
    sys.exit(2)
paths = sys.argv[sys.argv.index("--") + 1 :]
if len(paths) != 1:
    sys.stderr.write("multi-path\\n")
    sys.exit(2)
mode = os.environ.get("LSOF_MODE", "free")
held = os.environ.get("LSOF_HELD_BASENAME", "")
base = paths[0].rsplit("/", 1)[-1]
if held and base == held:
    sys.stdout.write("4242\\n")
    sys.exit(0)
if mode == "held":
    sys.stdout.write("4242\\n")
    sys.exit(0)
if mode == "stderr":
    sys.stderr.write("lsof: status error\\n")
    sys.exit(1)
if mode == "rc2":
    sys.stderr.write("lsof failed\\n")
    sys.exit(2)
if mode == "nondigit":
    sys.stdout.write("not-a-pid\\n")
    sys.exit(0)
if mode == "digit-and-junk":
    sys.stdout.write("123\\nnot-a-pid\\n")
    sys.exit(2)
sys.exit(1)
"""

PGREP_STUB = """#!/usr/bin/env python3
import os
import sys

log = os.environ.get("PGREP_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("\\t".join(sys.argv) + "\\n")
pattern = sys.argv[-1]
mode = os.environ.get("PGREP_MODE", "absent")
if mode == "self":
    sys.stdout.write("%s\\n" % os.getppid())
    sys.exit(0)
if mode == "self-and-parent":
    checker = os.getppid()
    parent = 0
    try:
        status = open("/proc/%s/status" % checker, encoding="utf-8").read()
    except OSError:
        status = ""
    for line in status.splitlines():
        if line.startswith("PPid:"):
            parent = int(line.split()[1])
            break
    sys.stdout.write("%s\\n" % checker)
    if parent > 0:
        sys.stdout.write("%s\\n" % parent)
    sys.exit(0)
if mode == "present":
    only = os.environ.get("PGREP_PRESENT_PATTERN", "")
    if only and pattern != only:
        sys.exit(1)
    sys.stdout.write(os.environ.get("PGREP_PID", "424242") + "\\n")
    sys.exit(0)
if mode == "error":
    only = os.environ.get("PGREP_ERROR_PATTERN", "")
    if only and pattern != only:
        sys.exit(1)
    sys.stderr.write("pgrep failed\\n")
    sys.exit(2)
if mode == "rc1-noise":
    sys.stdout.write("noise\\n")
    sys.exit(1)
sys.exit(1)
"""


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


def _install_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _quiet_lsof(_path: str) -> tuple[int, str, str]:
    return 1, "", ""


def _quiet_pgrep(_pattern: str) -> tuple[int, str, str]:
    return 1, "", ""


def _writer_lines(state: str = "absent") -> list[str]:
    return ["%s=%s" % (name, state) for name, _pattern in check.WRITER_CHECKS]


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


class LsofRuleTests(unittest.TestCase):
    def test_digit_line_is_held_even_with_a_bad_status(self):
        self.assertEqual(check.classify_lsof(0, "4242\n", ""), "held")
        self.assertEqual(check.classify_lsof(2, "  123  \nnot-a-pid\n", "nope"), "held")

    def test_rc_1_empty_is_free(self):
        self.assertEqual(check.classify_lsof(1, "", ""), "free")
        self.assertEqual(check.classify_lsof(1, "\n", "  "), "free")

    def test_other_results_are_lsof_error(self):
        self.assertEqual(check.classify_lsof(0, "", ""), "lsof-error")
        self.assertEqual(check.classify_lsof(0, "not-a-pid\n", ""), "lsof-error")
        self.assertEqual(check.classify_lsof(1, "", "lsof: status error\n"), "lsof-error")
        self.assertEqual(check.classify_lsof(2, "", ""), "lsof-error")
        self.assertEqual(check.classify_lsof(1, "noise\n", ""), "lsof-error")

    def test_missing_write_lock_is_bad_and_skips_lsof(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)

            def boom(_path: str) -> tuple[int, str, str]:
                raise AssertionError("lsof must not run for a missing lock")

            original = check.run_lsof
            check.run_lsof = boom
            try:
                self.assertEqual(
                    check.probe_lock_file(archive / check.WRITE_LOCK_NAME),
                    "missing",
                )
            finally:
                check.run_lsof = original

    def test_missing_daily_lock_is_missing_without_lsof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / check.DAILY_LOCK_NAME
            original = check.run_lsof
            check.run_lsof = lambda _path: (_ for _ in ()).throw(
                AssertionError("lsof must not run")
            )
            try:
                self.assertEqual(check.probe_lock_file(path), "missing")
            finally:
                check.run_lsof = original


class PgrepRuleTests(unittest.TestCase):
    def test_rc_0_foreign_pid_is_present(self):
        self.assertEqual(
            check.classify_pgrep(0, FOREIGN_PID + "\n", "", {1, 2}),
            "present",
        )

    def test_rc_1_empty_is_absent(self):
        self.assertEqual(check.classify_pgrep(1, "", "", {1, 2}), "absent")

    def test_other_results_are_error_not_absent(self):
        self.assertEqual(check.classify_pgrep(2, "", "", {1}), "error")
        self.assertEqual(check.classify_pgrep(1, "noise\n", "", {1}), "error")
        self.assertEqual(check.classify_pgrep(1, "", "pgrep failed\n", {1}), "error")
        self.assertEqual(check.classify_pgrep(0, "", "", {1}), "error")
        self.assertEqual(check.classify_pgrep(0, "nope\n", "", {1}), "error")
        self.assertEqual(check.classify_pgrep(127, "", "exec-failed", {1}), "error")

    def test_self_and_parent_pids_are_not_writers(self):
        self.assertEqual(
            check.classify_pgrep(0, "10\n20\n", "", {10, 20}),
            "absent",
        )
        self.assertEqual(
            check.classify_pgrep(0, "10\n424242\n", "", {10, 20}),
            "present",
        )

    def test_curl_pattern_is_anchored_argv(self):
        patterns = {pattern for _name, pattern in check.WRITER_CHECKS}
        self.assertIn("^/usr/bin/curl( |$)", patterns)
        self.assertNotIn("curl.*imap", patterns)
        self.assertIn("security find-generic", patterns)
        self.assertIn("phaseP_", patterns)


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self._lsof = check.run_lsof
        self._pgrep = check.run_pgrep
        check.run_lsof = _quiet_lsof
        check.run_pgrep = _quiet_pgrep

    def tearDown(self):
        check.run_lsof = self._lsof
        check.run_pgrep = self._pgrep

    def _archive(self, tmp: str, *, write_lock: bool = True) -> Path:
        archive = Path(tmp)
        _write_db(archive / "mailroom.sqlite")
        _stamp(archive, NEWER)
        if write_lock:
            (archive / check.WRITE_LOCK_NAME).write_text("", encoding="utf-8")
        return archive

    def test_ok_lines_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            before = _sha256(archive / "mailroom.sqlite")
            names_before = sorted(p.name for p in archive.iterdir())
            lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_OK)
            self.assertEqual(
                lines,
                [
                    "last_daily_rag_ok=%s newer_than_since=yes" % NEWER,
                    "G=2",
                    "messages_inserted_since=3",
                    "urgent_texts_sent_since=2",
                    "bills_added_since=2",
                    "write.lock=free",
                    "daily.lock=missing",
                    *_writer_lines(),
                    "POST-RESTORE PASS",
                ],
            )
            self.assertEqual(_sha256(archive / "mailroom.sqlite"), before)
            self.assertEqual(sorted(p.name for p in archive.iterdir()), names_before)

    def test_each_lock_is_a_separate_lsof_of_one_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            (archive / check.DAILY_LOCK_NAME).write_text("", encoding="utf-8")
            calls: list[str] = []

            def record(path: str) -> tuple[int, str, str]:
                calls.append(path)
                return 1, "", ""

            check.run_lsof = record
            _lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_OK)
            self.assertEqual(
                [Path(path).name for path in calls],
                [check.WRITE_LOCK_NAME, check.DAILY_LOCK_NAME],
            )
            for path in calls:
                self.assertNotIn(" ", Path(path).name)

    def test_old_stamp_exits_stamp_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            _stamp(archive, OLDER)
            lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_STAMP)
            self.assertTrue(lines[-1].endswith("POST-RESTORE FAIL last_daily_rag_ok"))

    def test_missing_sqlite_is_db_class_and_still_reports_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            _stamp(archive, NEWER)
            (archive / check.WRITE_LOCK_NAME).write_text("", encoding="utf-8")
            lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_DB)
            self.assertIn("G=unavailable", lines)
            self.assertIn(
                "last_daily_rag_ok=%s newer_than_since=yes" % NEWER,
                lines,
            )
            self.assertEqual(lines[-1], "POST-RESTORE FAIL sqlite")

    def test_missing_write_lock_fails_and_missing_daily_lock_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp, write_lock=False)
            lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_WRITE_LOCK)
            self.assertIn("write.lock=missing", lines)
            self.assertIn("daily.lock=missing", lines)
            self.assertEqual(lines[-1], "POST-RESTORE FAIL write.lock")

    def test_held_write_lock_and_old_stamp_use_the_earliest_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)
            _stamp(archive, OLDER)

            def held(path: str) -> tuple[int, str, str]:
                if path.endswith(check.WRITE_LOCK_NAME):
                    return 0, "4242\n", ""
                return 1, "", ""

            check.run_lsof = held
            lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_STAMP)
            self.assertIn("write.lock=held", lines)
            self.assertIn("daily.lock=missing", lines)
            self.assertEqual(
                lines[-1],
                "POST-RESTORE FAIL last_daily_rag_ok,write.lock",
            )

    def test_pgrep_error_is_process_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(tmp)

            def errored(pattern: str) -> tuple[int, str, str]:
                if pattern == "meta_fill":
                    return 2, "", "pgrep failed"
                return 1, "", ""

            check.run_pgrep = errored
            lines, code = check.evaluate(archive, SINCE_DT)
            self.assertEqual(code, check.EXIT_PROCESS)
            self.assertIn("meta_fill=error", lines)
            self.assertEqual(lines[-1], "POST-RESTORE FAIL meta_fill")


class CliTests(unittest.TestCase):
    def _env(self, tmp: str, stub: Path, **extra: str) -> dict[str, str]:
        env = {
            "PATH": "%s:%s" % (stub, os.environ.get("PATH", "")),
            "HOME": tmp,
            "PYTHONDONTWRITEBYTECODE": "1",
            "LSOF_MODE": "free",
            "PGREP_MODE": "absent",
        }
        env.update(extra)
        return env

    def _ready(self, tmp: str, *, write_lock: bool = True, daily_lock: bool = False) -> Path:
        archive = Path(tmp)
        _write_db(archive / "mailroom.sqlite")
        _stamp(archive, NEWER)
        if write_lock:
            (archive / check.WRITE_LOCK_NAME).write_text("", encoding="utf-8")
        if daily_lock:
            (archive / check.DAILY_LOCK_NAME).write_text("", encoding="utf-8")
        return archive

    def _run(self, tmp: str, env: dict[str, str], argv: list[str] | None = None):
        command = argv or [
            sys.executable,
            str(SCRIPTS / "post_restore_check.py"),
            "--archive",
            tmp,
            "--since",
            SINCE,
        ]
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

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

    def test_help_documents_exit_classes(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "post_restore_check.py"), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout
        self.assertIn("POST-RESTORE PASS", text)
        self.assertIn("POST-RESTORE FAIL", text)
        for code in ("0", "1", "2", "3", "4", "5", "6"):
            self.assertIn(code, text)
        self.assertIn("lsof -t -- FILE", text)
        self.assertIn("pgrep -f PATTERN", text)
        self.assertIn("^/usr/bin/curl( |$)", text)
        self.assertIn("missing write.lock", text)
        self.assertIn("missing daily.lock", text)

    def test_idle_stub_system_reports_no_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._ready(tmp, daily_lock=True)
            stub = Path(tmp) / "bin"
            stub.mkdir()
            _install_stub(stub, "lsof", LSOF_STUB)
            _install_stub(stub, "pgrep", PGREP_STUB)
            lsof_log = str(Path(tmp) / "lsof.log")
            pgrep_log = str(Path(tmp) / "pgrep.log")
            before = _sha256(archive / "mailroom.sqlite")
            proc = self._run(
                tmp,
                self._env(tmp, stub, LSOF_LOG=lsof_log, PGREP_LOG=pgrep_log),
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(proc.stdout.rstrip().endswith("POST-RESTORE PASS"))
            for name, _pattern in check.WRITER_CHECKS:
                self.assertIn("%s=absent\n" % name, proc.stdout)
            self.assertNotIn("=present", proc.stdout)
            self.assertNotIn("=held", proc.stdout)
            self.assertNotIn("lsof-error", proc.stdout)
            self.assertIn("write.lock=free\n", proc.stdout)
            self.assertIn("daily.lock=free\n", proc.stdout)
            self.assertEqual(_sha256(archive / "mailroom.sqlite"), before)
            lsof_lines = Path(lsof_log).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lsof_lines), 2)
            paths = []
            for line in lsof_lines:
                parts = line.split("\t")
                dash = parts.index("--")
                self.assertEqual(parts[dash - 1], "-t")
                tail = parts[dash + 1 :]
                self.assertEqual(len(tail), 1)
                paths.append(tail[0])
            self.assertEqual(
                [Path(path).name for path in paths],
                [check.WRITE_LOCK_NAME, check.DAILY_LOCK_NAME],
            )
            pgrep_lines = Path(pgrep_log).read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [line.split("\t")[-1] for line in pgrep_lines],
                [pattern for _name, pattern in check.WRITER_CHECKS],
            )
            self.assertNotIn("curl.*imap", Path(pgrep_log).read_text(encoding="utf-8"))

    def test_self_and_parent_pgrep_hits_are_not_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ready(tmp)
            stub = Path(tmp) / "bin"
            stub.mkdir()
            _install_stub(stub, "lsof", LSOF_STUB)
            _install_stub(stub, "pgrep", PGREP_STUB)
            wrapper = (
                "exec \"$PY\" \"$SCRIPT\" --archive \"$ARCHIVE\" --since \"$SINCE\"\n"
                "# mailroom_daily imap_newmail imap_tombstone imap_fetch_bodies\n"
                "# notify_bills rem-legacy meta_fill migrate_att0 embed_backfill\n"
                "# embed_merge_shards embed_sidecar_apply post_rem_embed_batch\n"
                "# with_writer_lock security find-generic phaseP_ /usr/bin/curl\n"
            )
            proc = subprocess.run(
                ["bash", "-c", wrapper],
                check=False,
                capture_output=True,
                text=True,
                env=self._env(
                    tmp,
                    stub,
                    PGREP_MODE="self-and-parent",
                    PY=sys.executable,
                    SCRIPT=str(SCRIPTS / "post_restore_check.py"),
                    ARCHIVE=tmp,
                    SINCE=SINCE,
                ),
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(proc.stdout.rstrip().endswith("POST-RESTORE PASS"))
            self.assertNotIn("=present", proc.stdout)
            self.assertNotIn("=error", proc.stdout)

    def test_foreign_pgrep_pid_is_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ready(tmp)
            stub = Path(tmp) / "bin"
            stub.mkdir()
            _install_stub(stub, "lsof", LSOF_STUB)
            _install_stub(stub, "pgrep", PGREP_STUB)
            proc = self._run(
                tmp,
                self._env(
                    tmp,
                    stub,
                    PGREP_MODE="present",
                    PGREP_PRESENT_PATTERN="meta_fill",
                    PGREP_PID=FOREIGN_PID,
                ),
            )
            self.assertNotEqual(int(FOREIGN_PID), os.getpid())
            self.assertNotEqual(int(FOREIGN_PID), os.getppid())
            self.assertEqual(proc.returncode, check.EXIT_PROCESS, proc.stdout)
            self.assertIn("meta_fill=present\n", proc.stdout)
            self.assertIn("mailroom_daily=absent\n", proc.stdout)
            self.assertEqual(proc.stdout.rstrip().splitlines()[-1], "POST-RESTORE FAIL meta_fill")

    def test_lsof_held_and_error_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ready(tmp, daily_lock=True)
            stub = Path(tmp) / "bin"
            stub.mkdir()
            _install_stub(stub, "lsof", LSOF_STUB)
            _install_stub(stub, "pgrep", PGREP_STUB)
            held = self._run(
                tmp,
                self._env(tmp, stub, LSOF_HELD_BASENAME=check.WRITE_LOCK_NAME),
            )
            self.assertEqual(held.returncode, check.EXIT_WRITE_LOCK, held.stdout)
            self.assertIn("write.lock=held\n", held.stdout)
            self.assertIn("daily.lock=free\n", held.stdout)
            self.assertEqual(
                held.stdout.rstrip().splitlines()[-1],
                "POST-RESTORE FAIL write.lock",
            )
            errored = self._run(tmp, self._env(tmp, stub, LSOF_MODE="rc2"))
            self.assertEqual(errored.returncode, check.EXIT_WRITE_LOCK, errored.stdout)
            self.assertIn("write.lock=lsof-error\n", errored.stdout)
            self.assertIn("daily.lock=lsof-error\n", errored.stdout)
            self.assertEqual(
                errored.stdout.rstrip().splitlines()[-1],
                "POST-RESTORE FAIL write.lock,daily.lock",
            )

    def test_archive_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ready(tmp)
            stub = Path(tmp) / "bin"
            stub.mkdir()
            _install_stub(stub, "lsof", LSOF_STUB)
            _install_stub(stub, "pgrep", PGREP_STUB)
            env = self._env(tmp, stub, ARCHIVE_ROOT=tmp)
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
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("messages_inserted_since=3\n", proc.stdout)
            self.assertTrue(proc.stdout.rstrip().endswith("POST-RESTORE PASS"))


def _ledger_ids(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT message_id FROM notify_log ORDER BY message_id"
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def _connect_with_rules(rules, current=None):
    """Ledger connections that raise OperationalError for selected SQL.

    ``rules`` maps a message id (or None for every id) to ``begin``,
    ``insert``, or ``commit``. ``current`` is the id ``_send_one`` is on.
    """
    real = check._connect_ledger

    def connect(path):
        conn = real(path)
        real_execute = conn.execute

        def execute(sql, *args, **kwargs):
            text = sql if isinstance(sql, str) else ""
            active = None if current is None else current.get("id")
            fault = rules.get(active, rules.get(None))
            if fault == "begin" and "BEGIN IMMEDIATE" in text:
                raise sqlite3.OperationalError("database is locked")
            if fault == "insert" and "INSERT INTO notify_log" in text:
                raise sqlite3.OperationalError("disk I/O error")
            if fault == "commit" and text == "COMMIT":
                raise sqlite3.OperationalError("commit failed")
            return real_execute(sql, *args, **kwargs)

        class _Proxy:
            def execute(self, sql, *args, **kwargs):
                return execute(sql, *args, **kwargs)

            def close(self):
                conn.close()

        return _Proxy()

    return connect


class UrgentTextOnceTests(unittest.TestCase):
    def test_same_id_twice_in_one_run_sends_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "notify_log.sqlite"
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            sent = check.send_urgent_texts(ledger, ["msg-1", "msg-1"], send)
            self.assertEqual(sent, 1)
            self.assertEqual(calls, ["msg-1"])
            self.assertEqual(_ledger_ids(ledger), ["msg-1"])

    def test_same_id_across_two_runs_sends_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "notify_log.sqlite"
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            first = check.send_urgent_texts(ledger, ["msg-1"], send)
            second = check.send_urgent_texts(ledger, ["msg-1"], send)
            self.assertEqual(first, 1)
            self.assertEqual(second, 0)
            self.assertEqual(calls, ["msg-1"])
            self.assertEqual(_ledger_ids(ledger), ["msg-1"])

    def test_failed_send_then_retry_sends_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "notify_log.sqlite"
            calls: list[str] = []
            attempts = {"n": 0}

            def send(message_id: str) -> None:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("transport down")
                calls.append(message_id)

            first = check.send_urgent_texts(ledger, ["msg-1"], send)
            self.assertEqual(first, 0)
            self.assertEqual(first.failed, ("msg-1",))
            self.assertEqual(_ledger_ids(ledger), [])
            self.assertEqual(calls, [])
            self.assertEqual(check.send_urgent_texts(ledger, ["msg-1"], send), 1)
            self.assertEqual(check.send_urgent_texts(ledger, ["msg-1"], send), 0)
            self.assertEqual(calls, ["msg-1"])
            self.assertEqual(attempts["n"], 2)
            self.assertEqual(_ledger_ids(ledger), ["msg-1"])

    def test_post_restore_rescan_of_texted_ids_sends_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            db = archive / "mailroom.sqlite"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE messages ("
                "id TEXT PRIMARY KEY, urgent INTEGER, ingested_at TEXT, "
                "present_on_server INTEGER)"
            )
            conn.execute(
                "CREATE TABLE notify_log ("
                "ts TEXT NOT NULL, message_id TEXT NOT NULL, "
                "channel TEXT NOT NULL, result TEXT, "
                "UNIQUE(message_id, channel))"
            )
            conn.executemany(
                "INSERT INTO messages (id, urgent, ingested_at, present_on_server) "
                "VALUES (?, ?, ?, ?)",
                [
                    ("a", 1, NEWER, 1),
                    ("b", 1, NEWER, 1),
                    ("c", 1, NEWER, 1),
                    ("quiet", 0, NEWER, 1),
                ],
            )
            conn.execute(
                "INSERT INTO notify_log (ts, message_id, channel, result) "
                "VALUES (?, ?, ?, ?)",
                ("2026-09-27T01:00:00Z", "c", check.URGENT_TEXT_CHANNEL, "ok"),
            )
            conn.commit()
            conn.close()
            _stamp(archive, NEWER)
            (archive / check.WRITE_LOCK_NAME).write_text("", encoding="utf-8")
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            blob = db.read_bytes()
            with self.assertRaises(check.CheckError):
                check.send_urgent_texts(db, ["a"], send)
            self.assertEqual(db.read_bytes(), blob)
            self.assertEqual(calls, [])

            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            self.assertEqual(calls, [])
            ledger = check.urgent_ledger_path(archive)
            self.assertEqual(_ledger_ids(ledger), ["a", "b", "c"])
            self.assertEqual(check.rescan_urgent_texts(ledger, ["a", "b", "c"], send), 0)
            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            self.assertEqual(calls, [])

            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM notify_log")
            conn.commit()
            conn.close()
            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            self.assertEqual(calls, [])
            self.assertEqual(_ledger_ids(ledger), ["a", "b", "c"])

            before = _ledger_ids(ledger)
            saved_lsof = check.run_lsof
            saved_pgrep = check.run_pgrep
            check.run_lsof = _quiet_lsof
            check.run_pgrep = _quiet_pgrep
            try:
                _lines, code = check.evaluate(
                    archive, SINCE_DT, urgent_send=send
                )
            finally:
                check.run_lsof = saved_lsof
                check.run_pgrep = saved_pgrep
            self.assertEqual(code, check.EXIT_OK)
            self.assertEqual(calls, [])
            self.assertEqual(_ledger_ids(ledger), before)

    def test_two_distinct_ids_send_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "notify_log.sqlite"
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            sent = check.send_urgent_texts(ledger, ["a", "b"], send)
            self.assertEqual(sent, 2)
            self.assertEqual(calls, ["a", "b"])
            self.assertEqual(_ledger_ids(ledger), ["a", "b"])

    def test_concurrent_runs_send_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "notify_log.sqlite"
            calls: list[str] = []
            results: list[int] = []
            errors: list[BaseException] = []
            lock = threading.Lock()
            start = threading.Barrier(2)

            def worker() -> None:
                def send(message_id: str) -> None:
                    with lock:
                        calls.append(message_id)
                    time.sleep(0.2)

                try:
                    start.wait(timeout=5)
                    sent = check.send_urgent_texts(ledger, ["msg-1"], send)
                    with lock:
                        results.append(sent)
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(sum(results), 1)
            self.assertEqual(calls, ["msg-1"])
            self.assertEqual(_ledger_ids(ledger), ["msg-1"])

    def _passing_archive(self, root: Path, ids: list[str]) -> None:
        conn = sqlite3.connect(root / "mailroom.sqlite")
        conn.execute(
            "CREATE TABLE messages ("
            "id TEXT PRIMARY KEY, urgent INTEGER, ingested_at TEXT, "
            "present_on_server INTEGER)"
        )
        conn.execute(
            "CREATE TABLE notify_log ("
            "ts TEXT NOT NULL, message_id TEXT NOT NULL, "
            "channel TEXT NOT NULL, result TEXT)"
        )
        conn.executemany(
            "INSERT INTO messages (id, urgent, ingested_at, present_on_server) "
            "VALUES (?, 1, ?, 1)",
            [(message_id, "2025-06-01T00:00:00Z") for message_id in ids],
        )
        conn.commit()
        conn.close()
        _stamp(root, NEWER)
        (root / check.WRITE_LOCK_NAME).write_text("", encoding="utf-8")

    def test_first_ledger_creation_does_not_text_existing_urgent_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            old = ["old-%02d" % n for n in range(50)]
            self._passing_archive(archive, old)
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            ledger = check.urgent_ledger_path(archive)
            real_link = check.os.link

            def fail_link(_src, _dst):
                raise OSError("link failed")

            check.os.link = fail_link
            try:
                with self.assertRaises(OSError):
                    check.deliver_archive_urgent_texts(archive, send)
            finally:
                check.os.link = real_link
            self.assertFalse(ledger.exists())
            self.assertEqual(calls, [])

            report = check.deliver_archive_urgent_texts(archive, send)
            self.assertEqual(report, 0)
            self.assertEqual(report.overflow, 0)
            self.assertEqual(calls, [])
            self.assertEqual(_ledger_ids(ledger), old)
            conn = sqlite3.connect(ledger)
            try:
                results = {
                    row[0]
                    for row in conn.execute("SELECT result FROM notify_log")
                }
            finally:
                conn.close()
            self.assertEqual(results, {check.BASELINE_RESULT})

            conn = sqlite3.connect(archive / "mailroom.sqlite")
            conn.execute(
                "INSERT INTO messages (id, urgent, ingested_at, present_on_server) "
                "VALUES ('new-1', 1, ?, 1)",
                ("2026-09-28T00:00:00Z",),
            )
            conn.commit()
            conn.close()
            again = check.deliver_archive_urgent_texts(archive, send)
            self.assertEqual(again, 1)
            self.assertEqual(calls, ["new-1"])
            third = check.deliver_archive_urgent_texts(archive, send)
            self.assertEqual(third, 0)
            self.assertEqual(calls, ["new-1"])

    def test_send_cap_reports_overflow_then_remainder(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            self._passing_archive(archive, ["old"])
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            self.assertEqual(check.URGENT_SEND_CAP, 20)
            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            self.assertEqual(calls, [])
            fresh = ["n%02d" % n for n in range(check.URGENT_SEND_CAP + 5)]
            conn = sqlite3.connect(archive / "mailroom.sqlite")
            conn.executemany(
                "INSERT INTO messages (id, urgent, ingested_at, present_on_server) "
                "VALUES (?, 1, '2026-09-28T00:00:00Z', 1)",
                [(message_id,) for message_id in fresh],
            )
            conn.commit()
            conn.close()
            buf = io.StringIO()
            with redirect_stdout(buf):
                first = check.deliver_archive_urgent_texts(archive, send)
            self.assertEqual(first, check.URGENT_SEND_CAP)
            self.assertEqual(first.overflow, 5)
            self.assertEqual(list(first.lines), ["urgent_texts_overflow=5"])
            self.assertNotIn("urgent_texts_overflow=", buf.getvalue())
            self.assertEqual(calls, fresh[: check.URGENT_SEND_CAP])
            ledger = check.urgent_ledger_path(archive)
            recorded = set(_ledger_ids(ledger))
            for message_id in fresh[check.URGENT_SEND_CAP :]:
                self.assertNotIn(message_id, recorded)
            second = check.deliver_archive_urgent_texts(archive, send)
            self.assertEqual(second, 5)
            self.assertEqual(second.overflow, 0)
            self.assertEqual(calls, fresh)
            self.assertEqual(
                check.deliver_archive_urgent_texts(archive, send),
                0,
            )

    def test_locked_ledger_defers_id_and_evaluate_still_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            self._passing_archive(archive, ["old"])
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            conn = sqlite3.connect(archive / "mailroom.sqlite")
            conn.executemany(
                "INSERT INTO messages (id, urgent, ingested_at, present_on_server) "
                "VALUES (?, 1, '2026-09-28T00:00:00Z', 1)",
                [("ok-new",), ("lock-me",)],
            )
            conn.commit()
            conn.close()
            real = check._send_one

            def flaky(path, message_id, send_one):
                if message_id == "lock-me":
                    raise sqlite3.OperationalError("database is locked")
                return real(path, message_id, send_one)

            check._send_one = flaky
            saved_lsof = check.run_lsof
            saved_pgrep = check.run_pgrep
            check.run_lsof = _quiet_lsof
            check.run_pgrep = _quiet_pgrep
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    lines, code = check.evaluate(
                        archive, SINCE_DT, urgent_send=send
                    )
            finally:
                check._send_one = real
                check.run_lsof = saved_lsof
                check.run_pgrep = saved_pgrep
            self.assertEqual(code, check.EXIT_OK)
            self.assertTrue(lines[-1].endswith("POST-RESTORE PASS"))
            self.assertEqual(lines.count("urgent_text_deferred=lock-me"), 1)
            self.assertNotIn("urgent_text_deferred=", buf.getvalue())
            self.assertEqual(calls, ["ok-new"])
            self.assertNotIn("lock-me", _ledger_ids(check.urgent_ledger_path(archive)))
            self.assertIn("ok-new", _ledger_ids(check.urgent_ledger_path(archive)))

    def test_failing_check_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            self._passing_archive(archive, ["old-%02d" % n for n in range(3)])
            _stamp(archive, OLDER)
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            saved_lsof = check.run_lsof
            saved_pgrep = check.run_pgrep
            check.run_lsof = _quiet_lsof
            check.run_pgrep = _quiet_pgrep
            try:
                lines, code = check.evaluate(
                    archive, SINCE_DT, urgent_send=send
                )
            finally:
                check.run_lsof = saved_lsof
                check.run_pgrep = saved_pgrep
            self.assertEqual(code, check.EXIT_STAMP)
            self.assertTrue(lines[-1].endswith("POST-RESTORE FAIL last_daily_rag_ok"))
            self.assertEqual(calls, [])
            self.assertFalse(check.urgent_ledger_path(archive).exists())

    def _add_urgent(self, archive: Path, ids: list[str]) -> None:
        conn = sqlite3.connect(archive / "mailroom.sqlite")
        conn.executemany(
            "INSERT INTO messages (id, urgent, ingested_at, present_on_server) "
            "VALUES (?, 1, '2026-09-28T00:00:00Z', 1)",
            [(message_id,) for message_id in ids],
        )
        conn.commit()
        conn.close()

    def _evaluate_send(self, archive: Path, send):
        saved_lsof = check.run_lsof
        saved_pgrep = check.run_pgrep
        check.run_lsof = _quiet_lsof
        check.run_pgrep = _quiet_pgrep
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                lines, code = check.evaluate(
                    archive, SINCE_DT, urgent_send=send
                )
        finally:
            check.run_lsof = saved_lsof
            check.run_pgrep = saved_pgrep
        return lines, code, buf.getvalue()

    def test_raised_sender_records_neighbors_and_retries_that_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            self._passing_archive(archive, ["old"])
            calls: list[str] = []
            boom = {"armed": True}

            def send(message_id: str) -> None:
                if message_id == "2" and boom["armed"]:
                    boom["armed"] = False
                    raise RuntimeError("transport down")
                calls.append(message_id)

            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            self.assertEqual(calls, [])
            self._add_urgent(archive, ["1", "2", "3"])
            lines, code, _stdout = self._evaluate_send(archive, send)
            self.assertEqual(code, check.EXIT_OK)
            self.assertEqual(lines.count("urgent_text_failed=2"), 1)
            self.assertTrue(lines[-1].endswith("POST-RESTORE PASS"))
            ledger = check.urgent_ledger_path(archive)
            recorded = _ledger_ids(ledger)
            self.assertIn("1", recorded)
            self.assertIn("3", recorded)
            self.assertNotIn("2", recorded)
            self.assertEqual(calls, ["1", "3"])
            calls.clear()
            again = check.deliver_archive_urgent_texts(archive, send)
            self.assertEqual(again, 1)
            self.assertEqual(again.failed, ())
            self.assertEqual(calls, ["2"])
            self.assertIn("2", _ledger_ids(ledger))

    def test_pre_send_lock_error_defers_without_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            self._passing_archive(archive, ["old"])
            calls: list[str] = []

            def send(message_id: str) -> None:
                calls.append(message_id)

            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            self._add_urgent(archive, ["lock-me"])
            real_connect = check._connect_ledger
            check._connect_ledger = _connect_with_rules({None: "begin"})
            try:
                lines, code, stdout = self._evaluate_send(archive, send)
            finally:
                check._connect_ledger = real_connect
            self.assertEqual(code, check.EXIT_OK)
            self.assertEqual(calls, [])
            self.assertEqual(lines.count("urgent_text_deferred=lock-me"), 1)
            self.assertNotIn("urgent_text_unconfirmed=", "\n".join(lines))
            self.assertNotIn("urgent_text_deferred=", stdout)
            self.assertNotIn(
                "lock-me", _ledger_ids(check.urgent_ledger_path(archive))
            )

    def test_post_send_insert_or_commit_error_is_unconfirmed(self):
        for fault in ("insert", "commit"):
            with self.subTest(fault=fault):
                with tempfile.TemporaryDirectory() as tmp:
                    archive = Path(tmp)
                    self._passing_archive(archive, ["old"])
                    calls: list[str] = []

                    def send(message_id: str) -> None:
                        calls.append(message_id)

                    self.assertEqual(
                        check.deliver_archive_urgent_texts(archive, send), 0
                    )
                    self._add_urgent(archive, ["gone-out"])
                    real_connect = check._connect_ledger
                    check._connect_ledger = _connect_with_rules({None: fault})
                    try:
                        lines, code, _stdout = self._evaluate_send(archive, send)
                    finally:
                        check._connect_ledger = real_connect
                    self.assertEqual(code, check.EXIT_OK)
                    self.assertEqual(calls, ["gone-out"])
                    self.assertEqual(
                        lines.count("urgent_text_unconfirmed=gone-out"), 1
                    )
                    self.assertNotIn("urgent_text_deferred=", "\n".join(lines))
                    self.assertNotIn(
                        "gone-out",
                        _ledger_ids(check.urgent_ledger_path(archive)),
                    )

    def test_notice_lines_are_returned_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            self._passing_archive(archive, ["old"])
            calls: list[str] = []

            def send(message_id: str) -> None:
                if message_id == "b-fail":
                    raise RuntimeError("transport down")
                calls.append(message_id)

            self.assertEqual(check.deliver_archive_urgent_texts(archive, send), 0)
            ok_ids = ["a-ok-%02d" % n for n in range(17)]
            self._add_urgent(
                archive,
                ok_ids
                + [
                    "b-fail",
                    "c-lock",
                    "d-unconfirmed",
                    "e-over-1",
                    "e-over-2",
                    "e-over-3",
                ],
            )
            current = {"id": None}
            real_one = check._send_one
            real_connect = check._connect_ledger

            def send_one(path, message_id, sender):
                current["id"] = message_id
                try:
                    return real_one(path, message_id, sender)
                finally:
                    current["id"] = None

            check._send_one = send_one
            check._connect_ledger = _connect_with_rules(
                {"c-lock": "begin", "d-unconfirmed": "insert"},
                current,
            )
            try:
                lines, code, stdout = self._evaluate_send(archive, send)
            finally:
                check._send_one = real_one
                check._connect_ledger = real_connect
            self.assertEqual(code, check.EXIT_OK)
            expected = (
                "urgent_texts_overflow=3",
                "urgent_text_deferred=c-lock",
                "urgent_text_failed=b-fail",
                "urgent_text_unconfirmed=d-unconfirmed",
            )
            shown = stdout + "\n".join(lines) + "\n"
            for line in expected:
                self.assertEqual(lines.count(line), 1)
                self.assertNotIn(line, stdout)
                self.assertEqual(shown.count(line + "\n"), 1)
            self.assertEqual(calls, ok_ids + ["d-unconfirmed"])
            recorded = set(_ledger_ids(check.urgent_ledger_path(archive)))
            for message_id in ok_ids:
                self.assertIn(message_id, recorded)
            for message_id in (
                "b-fail",
                "c-lock",
                "d-unconfirmed",
                "e-over-1",
                "e-over-2",
                "e-over-3",
            ):
                self.assertNotIn(message_id, recorded)


class SourceContractTests(unittest.TestCase):
    def test_script_is_read_only_and_takes_no_lock(self):
        text = (SCRIPTS / "post_restore_check.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", text)
        self.assertIn("PRAGMA query_only=ON", text)
        self.assertIn('["lsof", "-t", "--", path]', text)
        self.assertIn('["pgrep", "-f", pattern]', text)
        self.assertNotIn("fcntl.flock", text)
        self.assertNotIn("LOCK_EX", text)
        self.assertNotIn("LOCK_SH", text)
        self.assertNotIn("find-generic-password", text)
        self.assertNotIn("curl.*imap", text)
        self.assertNotIn("&&", text)
        self.assertNotIn("urllib", text)
        self.assertNotIn("imaplib", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("Path.home", text)

    def test_review_opens_with_the_required_line(self):
        doc = ROOT / "docs" / "att0" / "post-restore-backlog-review.md"
        if not doc.is_file():
            self.skipTest("review doc absent")
        first = doc.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(
            first,
            "Repo copies only; the installed copies on the Mac may differ and were not reviewed.",
        )

    def test_urgent_ledger_path_is_the_only_per_message_sender(self):
        self.assertEqual(check.URGENT_LEDGER_NAME, "notify_log.sqlite")
        self.assertEqual(check.URGENT_SEND_CAP, 20)
        self.assertGreaterEqual(check._LEDGER_TIMEOUT_S, 90)
        archive = Path("archive-root")
        self.assertEqual(
            check.urgent_ledger_path(archive),
            archive / "logs" / "notify_log.sqlite",
        )
        script = (SCRIPTS / "post_restore_check.py").read_text(encoding="utf-8")
        self.assertIn(
            "logs/notify_log.sqlite is the only per-message urgent ledger",
            script,
        )
        self.assertIn(
            "every future per-message urgent sender must go through send_urgent_texts",
            script,
        )
        self.assertIn(
            "at-least-once if the process dies between the send returning and COMMIT",
            script,
        )
        for path in SCRIPTS.rglob("*.py"):
            if path.name == "post_restore_check.py":
                continue
            body = path.read_text(encoding="utf-8")
            self.assertNotIn("logs/notify_log.sqlite", body)
            self.assertNotIn("send_urgent_texts", body)
            self.assertNotIn("URGENT_LEDGER_NAME", body)
            writes_log = (
                "INSERT INTO notify_log" in body
                or "INSERT OR REPLACE INTO notify_log" in body
                or "INSERT OR IGNORE INTO notify_log" in body
            )
            if writes_log:
                self.assertEqual(path.name, "notify_bills.py")
                self.assertIn("bills-", body)


if __name__ == "__main__":
    unittest.main()
