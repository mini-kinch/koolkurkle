#!/usr/bin/env python3
"""Search-resume watchdog. PATH shims stand in for launchctl and lsof.

No real launchctl, no real lsof, no database, no writer-lock acquire.
"""

from __future__ import annotations

import contextlib
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import search_resume_watchdog as watchdog  # noqa: E402

SCRIPT = SCRIPTS / "search_resume_watchdog.py"
PLIST = ROOT / "launchd" / "com.mailroom.search-resume-watchdog.plist.template"
DOC = ROOT / "docs" / "search-resume-watchdog.md"
ASK_PLIST = ROOT / "launchd" / "com.mailroom.ask-mail-serve.plist.template"
LABEL = "FAIL-OPEN FOR SEARCH AVAILABILITY, FAIL-CLOSED FOR THE SOR."
SECRET = "kwlocksecretvalue"

LAUNCHCTL_SHIM = """#!/bin/bash
log="${STUB_ARGV_LOG:?}"
{
  printf '%s' "$0"
  for a in "$@"; do
    printf '\\t%s' "$a"
  done
  printf '\\n'
} >> "$log"
cmd="${1:-}"
case "$cmd" in
  print)
    if [ -n "${STUB_PRINT_OUT:-}" ]; then
      printf '%s\\n' "$STUB_PRINT_OUT"
    fi
    if [ -n "${STUB_PRINT_ERR:-}" ]; then
      printf '%s\\n' "$STUB_PRINT_ERR" >&2
    fi
    exit "${STUB_PRINT_RC:-113}"
    ;;
  bootstrap)
    exit "${STUB_BOOTSTRAP_RC:-0}"
    ;;
  kickstart)
    exit "${STUB_KICKSTART_RC:-0}"
    ;;
esac
exit 99
"""

LSOF_SHIM = """#!/bin/bash
log="${STUB_ARGV_LOG:?}"
{
  printf '%s' "$0"
  for a in "$@"; do
    printf '\\t%s' "$a"
  done
  printf '\\n'
} >> "$log"
if [ -n "${STUB_LSOF_OUT:-}" ]; then
  printf '%s\\n' "$STUB_LSOF_OUT"
fi
if [ -n "${STUB_LSOF_ERR:-}" ]; then
  printf '%s\\n' "$STUB_LSOF_ERR" >&2
fi
exit "${STUB_LSOF_RC:-1}"
"""


def _rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            rows.append(line.split("\t"))
    return rows


def _named(rows: list[list[str]], name: str) -> list[list[str]]:
    return [row for row in rows if Path(row[0]).name == name]


class SearchResumeWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.bindir = root / "bin"
        self.bindir.mkdir()
        self.argv_log = root / "argv.log"
        self.deadline = root / "state" / "search_resume_after.epoch"
        self.log = root / "logs" / "search_resume_watchdog.log"
        self.plist = root / "agents" / "com.mailroom.ask-mail-serve.plist"
        self.plist.parent.mkdir()
        self.plist.write_text("template-stand-in\n", encoding="utf-8")
        self.lock = root / "mailroom.write.lock"
        self.now = 1700000000
        self._install_shim("launchctl", LAUNCHCTL_SHIM)
        self._install_shim("lsof", LSOF_SHIM)

    def _install_shim(self, name: str, body: str) -> None:
        path = self.bindir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _dead_pid(self) -> int:
        proc = subprocess.Popen(["/bin/sleep", "30"])
        try:
            pid = proc.pid
            proc.kill()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return pid

    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["UID"] = str(os.getuid())
        env["PATH"] = str(self.bindir) + os.pathsep + env.get("PATH", "")
        env["STUB_ARGV_LOG"] = str(self.argv_log)
        env["SEARCH_RESUME_DEADLINE_FILE"] = str(self.deadline)
        env["SEARCH_RESUME_LOG"] = str(self.log)
        env["SEARCH_RESUME_PLIST"] = str(self.plist)
        env["MAILROOM_WRITE_LOCK"] = str(self.lock)
        env["SEARCH_RESUME_NOW"] = str(self.now)
        env["STUB_PRINT_RC"] = "113"
        env["STUB_LSOF_RC"] = "1"
        env.pop("STUB_PRINT_OUT", None)
        env.pop("STUB_PRINT_ERR", None)
        env.pop("STUB_LSOF_OUT", None)
        env.pop("STUB_LSOF_ERR", None)
        env.pop("MAILROOM_SEARCH_RESUME_RUN_ID", None)
        env.update(extra)
        return env

    def _run(
        self,
        args: list[str],
        deadline: str | None = None,
        lock: str | None = None,
        **extra: str,
    ) -> subprocess.CompletedProcess[str]:
        if self.argv_log.exists():
            self.argv_log.unlink()
        if self.log.exists():
            self.log.unlink()
        if deadline is None:
            if self.deadline.exists():
                self.deadline.unlink()
        else:
            self.deadline.parent.mkdir(parents=True, exist_ok=True)
            self.deadline.write_text(deadline, encoding="utf-8")
        if lock is None:
            if self.lock.exists():
                self.lock.unlink()
        else:
            self.lock.write_text(lock, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=self._env(**extra),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def _past(self) -> str:
        return "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n" % (
            self.now - 100,
            self.now - 10,
        )

    def _future(self) -> str:
        return "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n" % (
            self.now + 100,
            self.now + 200,
        )

    def _uid(self) -> str:
        return str(os.getuid())

    def _service(self) -> str:
        return "gui/%s/com.mailroom.ask-mail-serve" % self._uid()

    def _launch(self) -> list[list[str]]:
        return [row[1:] for row in _named(_rows(self.argv_log), "launchctl")]

    def _lsof(self) -> list[list[str]]:
        return [row[1:] for row in _named(_rows(self.argv_log), "lsof")]

    def test_deadline_not_reached_does_nothing(self) -> None:
        proc = self._run(["watch"], deadline=self._future(), lock="pid=1\npurpose=x\n")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self._launch(), [])
        self.assertEqual(self._lsof(), [])
        self.assertFalse(self.log.exists())
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_deadline_passed_not_loaded_lock_free_restores(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self._launch(),
            [
                ["print", self._service()],
                ["bootstrap", "gui/%s" % self._uid(), str(self.plist)],
                ["kickstart", self._service()],
            ],
        )
        self.assertEqual(self._lsof(), [["-t", str(self.lock)]])
        flat = [arg for row in self._launch() for arg in row]
        self.assertNotIn("-k", flat)
        self.assertNotIn("bootout", flat)
        self.assertTrue(self.deadline.exists())
        self.assertIn("search restored", self.log.read_text(encoding="utf-8"))
        self.assertNotIn("search still off", self.log.read_text(encoding="utf-8"))

    def test_earlier_deadline_authorizes_restore_before_later_one(self) -> None:
        pid = self._dead_pid()
        text = "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n" % (
            self.now - 5,
            self.now + 500,
        )
        proc = self._run(
            ["watch"],
            deadline=text,
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch()[1][0], "bootstrap")
        self.assertEqual(self._launch()[2][:2], ["kickstart", self._service()])

    def test_deadline_passed_already_loaded_does_nothing(self) -> None:
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=migrate-copy\n" % os.getpid(),
            STUB_PRINT_RC="0",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        self.assertEqual(self._lsof(), [])
        self.assertFalse(self.log.exists())
        self.assertEqual(proc.stderr, "")

    def test_deadline_passed_lock_held_does_not_restore(self) -> None:
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=att0-migrate\n" % os.getpid(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        loud = "search still off: writer lock held pid=%s purpose=%s" % (
            os.getpid(),
            "att0-migrate",
        )
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn(loud, log_text)
        self.assertIn(loud, proc.stderr)
        self.assertNotIn("bootstrap", log_text)

    def test_lsof_holder_counts_as_held_when_recorded_pid_is_dead(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=att0-migrate\n" % pid,
            STUB_LSOF_RC="0",
            STUB_LSOF_OUT=str(os.getpid()),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        loud = "search still off: writer lock held pid=%s purpose=%s" % (
            pid,
            "att0-migrate",
        )
        self.assertIn(loud, proc.stderr)

    def test_ambiguous_lsof_output_counts_as_held(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=att0-migrate\n" % pid,
            STUB_LSOF_RC="0",
            STUB_LSOF_OUT="not-a-pid",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        self.assertIn("detection-fault, search left off", proc.stderr)
        self.assertNotIn("search still off", proc.stderr)
        self.assertFalse(any(row[0] == "bootstrap" for row in self._launch()))

    def test_lock_detection_error_counts_as_held(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=att0-migrate\n" % pid,
            STUB_LSOF_RC="2",
            STUB_LSOF_ERR="lsof-failed",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        self.assertIn("detection-fault, search left off", proc.stderr)
        self.assertIn(
            "detection-fault, search left off",
            self.log.read_text(encoding="utf-8"),
        )
        self.assertNotIn("search still off", proc.stderr)
        self.assertNotIn("search restored", self.log.read_text(encoding="utf-8"))

    def test_no_deadline_file_is_quiet(self) -> None:
        proc = self._run(["watch"], deadline=None, lock=None)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self._launch(), [])
        self.assertEqual(self._lsof(), [])
        self.assertFalse(self.log.exists())
        self.assertFalse(self.deadline.exists())
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_malformed_deadline_file_does_nothing(self) -> None:
        samples = (
            "",
            "not a deadline\n",
            "run_id=run1\n",
            "run_id=run1\ndeadline_26=nope\ndeadline_50=1\n",
            "run_id=\ndeadline_26=1\ndeadline_50=2\n",
            "run_id=run1\ndeadline_26=1\ndeadline_50=2\njunk line\n",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                proc = self._run(["watch"], deadline=sample, lock="pid=1\npurpose=x\n")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self._launch(), [])
                self.assertEqual(self._lsof(), [])
                log_text = self.log.read_text(encoding="utf-8")
                self.assertIn("search resume deadline file malformed", log_text)
                if sample.strip():
                    self.assertNotIn(sample, log_text)
                self.assertEqual(proc.stderr, "")

    def test_token_in_lock_file_is_never_logged(self) -> None:
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock=(
                "pid=%s\n"
                "purpose=att0-migrate\n"
                "token=%s\n"
                "lock_token=%s\n"
                "writer_token=%s\n"
            )
            % (os.getpid(), SECRET, SECRET, SECRET),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        blob = "\n".join(
            (
                proc.stdout,
                proc.stderr,
                self.log.read_text(encoding="utf-8"),
                self.argv_log.read_text(encoding="utf-8") if self.argv_log.exists() else "",
            )
        )
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("token=", blob)
        self.assertIn(
            "search still off: writer lock held pid=%s purpose=att0-migrate"
            % os.getpid(),
            proc.stderr,
        )
        self.assertEqual(self._launch(), [["print", self._service()]])

    def test_never_opens_sqlite_or_writes_lock(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for banned in ("sqlite3", "fcntl", "flock", "LOCK_EX", "LOCK_NB", "LOCK_SH", "bootout"):
            self.assertNotIn(banned, source)
        self.assertNotIn("os.open", source)
        pid = self._dead_pid()
        self.deadline.parent.mkdir(parents=True, exist_ok=True)
        self.deadline.write_text(self._past(), encoding="utf-8")
        self.lock.write_text(
            "pid=%s\npurpose=migrate-copy\n" % pid,
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            if list(argv[:2]) == ["launchctl", "print"]:
                return 113, "", "Could not find service"
            if list(argv[:2]) == ["lsof", "-t"]:
                return 1, "", ""
            if list(argv[:2]) == ["launchctl", "bootstrap"]:
                return 0, "", ""
            if list(argv[:2]) == ["launchctl", "kickstart"]:
                return 0, "", ""
            raise AssertionError(argv)

        real_open = open
        opened: list[tuple[str, str]] = []

        def guarded_open(file, mode="r", *args, **kwargs):
            path = os.fspath(file)
            mode_s = mode if isinstance(mode, str) else "r"
            base = mode_s.replace("b", "").replace("t", "")
            opened.append((path, mode_s))
            if path.endswith("mailroom.write.lock") and base != "r":
                raise AssertionError("wrote the writer lock: %s" % mode_s)
            if path.endswith(".sqlite") or os.path.basename(path) == "mailroom.sqlite":
                raise AssertionError("opened a database path")
            return real_open(file, mode, *args, **kwargs)

        env = {
            "HOME": str(self.home),
            "UID": self._uid(),
            "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            "SEARCH_RESUME_LOG": str(self.log),
            "SEARCH_RESUME_PLIST": str(self.plist),
            "MAILROOM_WRITE_LOCK": str(self.lock),
            "SEARCH_RESUME_NOW": str(self.now),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("builtins.open", guarded_open):
                with mock.patch("io.open", guarded_open):
                    with mock.patch(
                        "sqlite3.connect",
                        side_effect=AssertionError("sqlite3.connect"),
                    ):
                        rc = watchdog.watch_once(runner=runner, now=self.now)
        self.assertEqual(rc, 0)
        self.assertEqual(
            calls,
            [
                ["launchctl", "print", self._service()],
                ["lsof", "-t", str(self.lock)],
                ["launchctl", "bootstrap", "gui/%s" % self._uid(), str(self.plist)],
                ["launchctl", "kickstart", self._service()],
            ],
        )
        lock_modes = [mode for path, mode in opened if path.endswith("mailroom.write.lock")]
        self.assertEqual(lock_modes, ["r"])
        self.assertTrue(any(path == str(self.log) and "a" in mode for path, mode in opened))

    def test_s1_write_and_s2_clear(self) -> None:
        env = self._env()
        env.pop("SEARCH_RESUME_DEADLINE_FILE")
        env["HOME"] = str(self.home)
        wrote = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "write",
                "--run-id",
                "run1",
                "--now",
                "1000000",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(wrote.returncode, 0, wrote.stderr)
        path = self.home / "MailArchive" / "state" / "search_resume_after.epoch"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n"
            % (1000000 + 26 * 60, 1000000 + 50 * 60),
        )
        cleared = subprocess.run(
            [sys.executable, str(SCRIPT), "clear"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertFalse(path.exists())
        again = subprocess.run(
            [sys.executable, str(SCRIPT), "clear"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, "")
        self.assertEqual(again.stderr, "")

    def test_plist_template_and_docs(self) -> None:
        raw = PLIST.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        doc = DOC.read_text(encoding="utf-8")
        for text in (raw, script, doc):
            self.assertIn(LABEL, text)
            self.assertNotIn("/Users/", text)
        self.assertIn("CODE AND PLIST TEMPLATE ONLY, NO INSTALL.", raw)
        self.assertIn("CODE AND PLIST TEMPLATE ONLY, NO INSTALL.", doc)
        self.assertIn("$HOME/MailArchive/state/search_resume_after.epoch", script)
        self.assertIn("$HOME/MailArchive/logs/search_resume_watchdog.log", script)
        rendered = raw.replace("__HOME__", "/tmp/example-home")
        data = plistlib.loads(rendered.encode("utf-8"))
        self.assertEqual(data["Label"], "com.mailroom.search-resume-watchdog")
        self.assertEqual(data["StartInterval"], 60)
        self.assertIs(data["RunAtLoad"], False)
        self.assertNotIn("KeepAlive", data)
        self.assertEqual(
            data["ProgramArguments"],
            [
                "/usr/bin/python3",
                "/tmp/example-home/MailArchive/scripts/search_resume_watchdog.py",
                "watch",
            ],
        )
        self.assertNotIn("com.mailroom.ask-mail-serve", data["ProgramArguments"])
        ask = plistlib.loads(
            ASK_PLIST.read_text(encoding="utf-8")
            .replace("__HOME__", "/tmp/example-home")
            .encode("utf-8")
        )
        self.assertIs(ask["KeepAlive"], True)
        self.assertEqual(ask["Label"], "com.mailroom.ask-mail-serve")
        self.assertIn("write --run-id", doc)
        self.assertIn("arm --run-id", doc)
        self.assertIn("clear", doc)
        self.assertIn("launchctl bootstrap", doc)
        self.assertIn("launchctl kickstart", doc)
        self.assertIn("search_resume_after.epoch", doc)
        self.assertIn("detection-fault, search left off", doc)
        self.assertIn("Could not find service", doc)
        self.assertIn("MAILROOM_SEARCH_RESUME_RUN_ID", doc)
        self.assertIn("CODE AND TEMPLATE ONLY, NO INSTALL", doc)
        self.assertIn("PR #90", doc)
        self.assertIn("TODO", doc)
        self.assertNotIn("16:37", doc)
        self.assertNotIn("20:05", doc)
        self.assertNotIn("16:37", script)
        self.assertNotIn("20:05", script)
        self.assertNotIn("-k", data["ProgramArguments"])

    def _scripted(self, plan):
        calls = []

        def runner(argv):
            calls.append(list(argv))
            if not argv:
                raise AssertionError(argv)
            name = Path(argv[0]).name
            if name == "lsof":
                key = "lsof"
            elif len(argv) >= 2:
                key = argv[1]
            else:
                raise AssertionError(argv)
            if key not in plan:
                raise AssertionError(argv)
            step = plan[key]
            if isinstance(step, list):
                if not step:
                    raise AssertionError(argv)
                return step.pop(0)
            return step

        runner.calls = calls
        return runner

    def _call_watch(self, runner, deadline, lock, now=None, reset_log=True):
        clock = self.now if now is None else now
        if reset_log and self.log.exists():
            self.log.unlink()
        if deadline is None:
            if self.deadline.exists():
                self.deadline.unlink()
        else:
            self.deadline.parent.mkdir(parents=True, exist_ok=True)
            self.deadline.write_text(deadline, encoding="utf-8")
        if lock is None:
            if self.lock.exists():
                self.lock.unlink()
        else:
            self.lock.write_text(lock, encoding="utf-8")
        env = {
            "HOME": str(self.home),
            "UID": self._uid(),
            "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            "SEARCH_RESUME_LOG": str(self.log),
            "SEARCH_RESUME_PLIST": str(self.plist),
            "MAILROOM_WRITE_LOCK": str(self.lock),
            "SEARCH_RESUME_NOW": str(clock),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            return watchdog.watch_once(runner=runner, now=clock)

    def _no_bootstrap(self, calls) -> None:
        self.assertFalse(any(argv[1] == "bootstrap" for argv in calls))

    def test_not_loaded_signal_is_rc_113_or_text(self) -> None:
        self.assertTrue(watchdog._not_loaded_signal(113, "", ""))
        self.assertTrue(
            watchdog._not_loaded_signal(1, "", "Could not find service")
        )
        self.assertTrue(
            watchdog._not_loaded_signal(5, "Could not find service in domain", "")
        )
        self.assertFalse(watchdog._not_loaded_signal(0, "", ""))
        self.assertFalse(watchdog._not_loaded_signal(0, "Could not find service", ""))
        self.assertFalse(watchdog._not_loaded_signal(1, "", "other failure"))
        self.assertFalse(watchdog._not_loaded_signal(-1, "", ""))

    def test_print_rc_113_counts_as_not_loaded(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
            STUB_PRINT_RC="113",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self._launch(),
            [
                ["print", self._service()],
                ["bootstrap", "gui/%s" % self._uid(), str(self.plist)],
                ["kickstart", self._service()],
            ],
        )
        self.assertNotIn("-k", [arg for row in self._launch() for arg in row])

    def test_could_not_find_service_text_counts_as_not_loaded(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
            STUB_PRINT_RC="1",
            STUB_PRINT_ERR="Could not find service in domain",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch()[1][0], "bootstrap")
        self.assertEqual(self._launch()[2], ["kickstart", self._service()])

    def test_other_print_rc_logs_and_skips(self) -> None:
        pid = self._dead_pid()
        for rc in ("1", "7"):
            with self.subTest(rc=rc):
                proc = self._run(
                    ["watch"],
                    deadline=self._past(),
                    lock="pid=%s\npurpose=migrate-copy\n" % pid,
                    STUB_PRINT_RC=rc,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self._launch(), [["print", self._service()]])
                self.assertEqual(self._lsof(), [])
                self.assertIn(
                    "search resume launchctl print skipped rc=%s" % rc,
                    self.log.read_text(encoding="utf-8"),
                )
                self.assertEqual(proc.stderr, "")
                self.assertNotIn("bootstrap", self.log.read_text(encoding="utf-8"))

    def test_print_tool_failure_logs_and_skips(self) -> None:
        pid = self._dead_pid()
        runner = self._scripted({"print": (-1, "", "")})
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self._call_watch(
                runner,
                self._past(),
                "pid=%s\npurpose=migrate-copy\n" % pid,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls, [["launchctl", "print", self._service()]])
        self.assertIn(
            "search resume launchctl print skipped rc=-1",
            self.log.read_text(encoding="utf-8"),
        )
        self.assertEqual(buf.getvalue(), "")

    def test_loaded_print_stays_quiet_even_if_text_is_present(self) -> None:
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=migrate-copy\n" % os.getpid(),
            STUB_PRINT_RC="0",
            STUB_PRINT_ERR="Could not find service",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        self.assertFalse(self.log.exists())
        self.assertEqual(proc.stderr, "")

    def test_bootstrap_rc_0_kickstart_has_exact_argv_without_k(self) -> None:
        pid = self._dead_pid()
        runner = self._scripted(
            {
                "print": (113, "", ""),
                "lsof": (1, "", ""),
                "bootstrap": (0, "", ""),
                "kickstart": (0, "", ""),
            }
        )
        rc = self._call_watch(
            runner,
            self._past(),
            "pid=%s\npurpose=migrate-copy\n" % pid,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            runner.calls,
            [
                ["launchctl", "print", self._service()],
                ["lsof", "-t", str(self.lock)],
                ["launchctl", "bootstrap", "gui/%s" % self._uid(), str(self.plist)],
                ["launchctl", "kickstart", self._service()],
            ],
        )
        for argv in runner.calls:
            self.assertNotIn("-k", argv)
            self.assertNotIn("enable", argv)
            self.assertFalse(any(str(arg).startswith("user/") for arg in argv))
        self.assertIn("search restored", self.log.read_text(encoding="utf-8"))

    def test_s2_race_failed_bootstrap_then_loaded_is_noop(self) -> None:
        pid = self._dead_pid()
        runner = self._scripted(
            {
                "print": [
                    (113, "", "Could not find service"),
                    (0, "state = running", ""),
                ],
                "lsof": (1, "", ""),
                "bootstrap": (1, "", "Bootstrap failed: 5: Input/output error"),
            }
        )
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self._call_watch(
                runner,
                self._past(),
                "pid=%s\npurpose=migrate-copy\n" % pid,
            )
        self.assertEqual(rc, 0)
        bootstraps = [argv for argv in runner.calls if argv[1] == "bootstrap"]
        kicks = [argv for argv in runner.calls if argv[1] == "kickstart"]
        self.assertEqual(len(bootstraps), 1)
        self.assertEqual(kicks, [])
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("search already loaded", log_text)
        self.assertNotIn("failed", log_text)
        self.assertEqual(buf.getvalue(), "")

    def test_failed_bootstrap_still_unloaded_does_not_retry(self) -> None:
        pid = self._dead_pid()
        runner = self._scripted(
            {
                "print": [
                    (113, "", ""),
                    (113, "", "Could not find service"),
                ],
                "lsof": (1, "", ""),
                "bootstrap": (5, "", "nope"),
            }
        )
        rc = self._call_watch(
            runner,
            self._past(),
            "pid=%s\npurpose=migrate-copy\n" % pid,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            len([argv for argv in runner.calls if argv[1] == "bootstrap"]),
            1,
        )
        self.assertFalse(any(argv[1] == "kickstart" for argv in runner.calls))
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("search restore failed bootstrap rc=5", log_text)
        self.assertNotIn("search already loaded", log_text)

    def test_kickstart_not_found_then_loaded_is_noop(self) -> None:
        pid = self._dead_pid()
        runner = self._scripted(
            {
                "print": [(113, "", ""), (0, "", "")],
                "lsof": (1, "", ""),
                "bootstrap": (0, "", ""),
                "kickstart": (113, "", "Could not find service"),
            }
        )
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self._call_watch(
                runner,
                self._past(),
                "pid=%s\npurpose=migrate-copy\n" % pid,
            )
        self.assertEqual(rc, 0)
        kicks = [argv for argv in runner.calls if argv[1] == "kickstart"]
        self.assertEqual(kicks, [["launchctl", "kickstart", self._service()]])
        self.assertNotIn("-k", kicks[0])
        self.assertEqual(
            len([argv for argv in runner.calls if argv[1] == "bootstrap"]),
            1,
        )
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("search already loaded", log_text)
        self.assertNotIn("failed", log_text)
        self.assertEqual(buf.getvalue(), "")

    def test_kickstart_not_found_still_unloaded_does_not_retry_bootstrap(self) -> None:
        pid = self._dead_pid()
        runner = self._scripted(
            {
                "print": [
                    (113, "", ""),
                    (113, "", "Could not find service"),
                ],
                "lsof": (1, "", ""),
                "bootstrap": (0, "", ""),
                "kickstart": (1, "Could not find service", ""),
            }
        )
        rc = self._call_watch(
            runner,
            self._past(),
            "pid=%s\npurpose=migrate-copy\n" % pid,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            len([argv for argv in runner.calls if argv[1] == "bootstrap"]),
            1,
        )
        self.assertIn(
            "search restore failed kickstart rc=1",
            self.log.read_text(encoding="utf-8"),
        )

    def test_live_recorded_pid_is_held(self) -> None:
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=att0-migrate\n" % os.getpid(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        self.assertIn(
            "search still off: writer lock held pid=%s purpose=att0-migrate"
            % os.getpid(),
            proc.stderr,
        )
        self.assertNotIn("detection-fault", proc.stderr)

    def test_missing_lock_file_is_free(self) -> None:
        proc = self._run(["watch"], deadline=self._past(), lock=None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch()[1][0], "bootstrap")
        self.assertEqual(self._launch()[2][0], "kickstart")
        self.assertNotIn("detection-fault", proc.stderr)
        self.assertNotIn("search still off", proc.stderr)

    def test_dead_recorded_pid_with_no_lsof_holder_is_free(self) -> None:
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
            STUB_LSOF_RC="1",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch()[1][0], "bootstrap")
        self.assertIn("search restored", self.log.read_text(encoding="utf-8"))
        self.assertNotIn("detection-fault", proc.stderr)

    def test_parse_failure_is_detection_fault_and_hides_token(self) -> None:
        proc = self._run(
            ["watch"],
            deadline=self._past(),
            lock=(
                "pid=nope\n"
                "purpose=att0-migrate\n"
                "token=%s\n"
                "lock_token=%s\n"
                "writer_token=%s\n"
            )
            % (SECRET, SECRET, SECRET),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("detection-fault, search left off", proc.stderr)
        self.assertIn(
            "detection-fault, search left off",
            self.log.read_text(encoding="utf-8"),
        )
        blob = "\n".join(
            (
                proc.stdout,
                proc.stderr,
                self.log.read_text(encoding="utf-8"),
                self.argv_log.read_text(encoding="utf-8")
                if self.argv_log.exists()
                else "",
            )
        )
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("token=", blob)
        self.assertFalse(any(row[0] == "bootstrap" for row in self._launch()))

    def test_lock_read_failure_is_detection_fault(self) -> None:
        self.deadline.parent.mkdir(parents=True, exist_ok=True)
        self.deadline.write_text(self._past(), encoding="utf-8")
        if self.log.exists():
            self.log.unlink()
        calls = []

        def runner(argv):
            calls.append(list(argv))
            if argv[1] == "print":
                return 113, "", ""
            raise AssertionError(argv)

        env = {
            "HOME": str(self.home),
            "UID": self._uid(),
            "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            "SEARCH_RESUME_LOG": str(self.log),
            "SEARCH_RESUME_PLIST": str(self.plist),
            "MAILROOM_WRITE_LOCK": str(self.home),
            "SEARCH_RESUME_NOW": str(self.now),
        }
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with contextlib.redirect_stderr(buf):
                rc = watchdog.watch_once(runner=runner, now=self.now)
        self.assertEqual(rc, 0)
        self.assertIn(
            "detection-fault, search left off",
            self.log.read_text(encoding="utf-8"),
        )
        self.assertIn("detection-fault, search left off", buf.getvalue())
        self._no_bootstrap(calls)

    def test_detection_fault_logs_every_tick_and_never_starts(self) -> None:
        pid = self._dead_pid()
        lock = "pid=%s\npurpose=att0-migrate\ntoken=%s\n" % (pid, SECRET)
        calls = []

        def runner(argv):
            calls.append(list(argv))
            name = Path(argv[0]).name
            if name == "launchctl" and argv[1] == "print":
                return 113, "", ""
            if name == "lsof":
                return 2, "", "lsof-failed"
            raise AssertionError(argv)

        if self.log.exists():
            self.log.unlink()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            for _ in range(3):
                rc = self._call_watch(runner, self._past(), lock, reset_log=False)
                self.assertEqual(rc, 0)
        log_text = self.log.read_text(encoding="utf-8")
        self.assertEqual(log_text.count("detection-fault, search left off"), 3)
        self.assertEqual(buf.getvalue().count("detection-fault, search left off"), 3)
        self.assertNotIn(SECRET, log_text)
        self.assertNotIn(SECRET, buf.getvalue())
        self.assertNotIn("token=", log_text)
        self._no_bootstrap(calls)
        self.assertNotIn("start anyway", SCRIPT.read_text(encoding="utf-8"))

    def test_drop_early_deadline_is_atomic_idempotent_and_refuses(self) -> None:
        body = "run_id=run1\ndeadline_26=100\ndeadline_50=200\n"
        armed = "run_id=run1\ndeadline_50=200\n"
        self.deadline.parent.mkdir(parents=True, exist_ok=True)
        self.deadline.write_text(body, encoding="utf-8")
        saw = {}
        fsynced = []
        real_replace = os.replace
        real_fsync = os.fsync

        def spy_replace(src, dst):
            saw["src"] = Path(src)
            saw["dst"] = Path(dst)
            return real_replace(src, dst)

        def spy_fsync(fd):
            fsynced.append(fd)
            return real_fsync(fd)

        with mock.patch("search_resume_watchdog.os.replace", spy_replace):
            with mock.patch("search_resume_watchdog.os.fsync", spy_fsync):
                watchdog.drop_early_deadline("run1", self.deadline)
        self.assertTrue(fsynced)
        self.assertEqual(saw["src"].parent, saw["dst"].parent)
        self.assertEqual(saw["dst"], self.deadline)
        self.assertTrue(saw["src"].name.endswith(".tmp"))
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), armed)
        self.assertFalse((self.deadline.parent / (self.deadline.name + ".tmp")).exists())
        watchdog.drop_early_deadline("run1", self.deadline)
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), armed)
        self.deadline.write_text(body, encoding="utf-8")
        with self.assertRaises(watchdog.DeadlineRefusal) as mismatch:
            watchdog.drop_early_deadline("other", self.deadline)
        self.assertIn("run-id", str(mismatch.exception))
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), body)
        self.deadline.unlink()
        with self.assertRaises(watchdog.DeadlineRefusal) as missing:
            watchdog.drop_early_deadline("run1", self.deadline)
        self.assertIn("missing", str(missing.exception))
        self.assertFalse(self.deadline.exists())
        self.deadline.write_text(body, encoding="utf-8")
        with mock.patch(
            "search_resume_watchdog.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaises(OSError):
                watchdog.drop_early_deadline("run1", self.deadline)
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), body)

    def test_arm_cli_drops_and_refuses(self) -> None:
        body = "run_id=run1\ndeadline_26=100\ndeadline_50=200\n"
        armed = "run_id=run1\ndeadline_50=200\n"
        dropped = self._run(["arm", "--run-id", "run1"], deadline=body)
        self.assertEqual(dropped.returncode, 0, dropped.stderr)
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), armed)
        again = self._run(["arm", "--run-id", "run1"], deadline=armed)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), armed)
        refused = self._run(["arm", "--run-id", "other"], deadline=body)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("run-id", refused.stderr)
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), body)
        missing = self._run(["arm", "--run-id", "run1"], deadline=None)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("missing", missing.stderr)
        self.assertFalse(self.deadline.exists())

    def _checkpoints(self):
        start = self.now
        return start, start + 26 * 60, start + 50 * 60

    def test_no_restore_at_plus_26_after_drop_while_lock_is_free(self) -> None:
        _start, d26, d50 = self._checkpoints()
        body = "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n" % (d26, d50)
        armed = self._run(["arm", "--run-id", "run1"], deadline=body)
        self.assertEqual(armed.returncode, 0, armed.stderr)
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=self.deadline.read_text(encoding="utf-8"),
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
            SEARCH_RESUME_NOW=str(d26),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [])
        self.assertEqual(self._lsof(), [])
        self.assertFalse(self.log.exists())
        self.assertLess(d26, d50)

    def test_restore_at_plus_50_when_lock_is_free(self) -> None:
        _start, d26, d50 = self._checkpoints()
        armed = "run_id=run1\ndeadline_50=%d\n" % d50
        pid = self._dead_pid()
        proc = self._run(
            ["watch"],
            deadline=armed,
            lock="pid=%s\npurpose=migrate-copy\n" % pid,
            SEARCH_RESUME_NOW=str(d50),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch()[1][0], "bootstrap")
        self.assertEqual(self._launch()[2], ["kickstart", self._service()])
        self.assertNotIn("-k", [arg for row in self._launch() for arg in row])
        self.assertIn("search restored", self.log.read_text(encoding="utf-8"))
        self.assertNotIn("deadline_26", armed)

    def test_plus_50_is_noop_while_lock_is_held(self) -> None:
        _start, _d26, d50 = self._checkpoints()
        proc = self._run(
            ["watch"],
            deadline="run_id=run1\ndeadline_50=%d\n" % d50,
            lock="pid=%s\npurpose=att0-migrate\n" % os.getpid(),
            SEARCH_RESUME_NOW=str(d50),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._launch(), [["print", self._service()]])
        self.assertIn(
            "search still off: writer lock held pid=%s purpose=att0-migrate"
            % os.getpid(),
            proc.stderr,
        )
        self.assertNotIn("detection-fault", proc.stderr)
        self.assertNotIn("search restored", self.log.read_text(encoding="utf-8"))

    def test_writer_lock_hook_is_delimited(self) -> None:
        text = (SCRIPTS / "with_writer_lock.py").read_text(encoding="utf-8")
        self.assertIn("BEGIN search-resume +26 drop", text)
        self.assertIn("END search-resume +26 drop", text)
        self.assertIn("MAILROOM_SEARCH_RESUME_RUN_ID", text)
        self.assertIn("PR #90", text)

    def test_wrapper_env_unset_does_not_drop(self) -> None:
        import with_writer_lock as wwl

        body = "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n" % (
            self.now + 10,
            self.now + 50,
        )
        self.deadline.parent.mkdir(parents=True, exist_ok=True)
        self.deadline.write_text(body, encoding="utf-8")
        marker = self.home / "ran"

        def refuse(run_id, path=None):
            raise AssertionError("drop called")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAILROOM_SEARCH_RESUME_RUN_ID", None)
            os.environ["SEARCH_RESUME_DEADLINE_FILE"] = str(self.deadline)
            with mock.patch("search_resume_watchdog.drop_early_deadline", refuse):
                rc = wwl.run_with_lock(
                    "unit-test",
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path(%r).write_text('ok')"
                        % str(marker),
                    ],
                    lock_path=self.lock,
                    action_required_path=self.home / "ACTION_REQUIRED",
                )
        self.assertEqual(rc, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "ok")
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), body)

    def test_wrapper_env_set_drops_after_acquire(self) -> None:
        import with_writer_lock as wwl

        events = []
        real_acquire = wwl.acquire_writer_lock
        real_drop = watchdog.drop_early_deadline
        real_run = subprocess.run

        def acquire(*args, **kwargs):
            events.append("acquire")
            return real_acquire(*args, **kwargs)

        def drop(run_id, path=None):
            events.append("drop")
            return real_drop(run_id, path)

        def run(cmd, check=False, env=None):
            events.append("child")
            return real_run(cmd, check=check, env=env)

        body = "run_id=run1\ndeadline_26=%d\ndeadline_50=%d\n" % (
            self.now + 26 * 60,
            self.now + 50 * 60,
        )
        self.deadline.parent.mkdir(parents=True, exist_ok=True)
        self.deadline.write_text(body, encoding="utf-8")
        marker = self.home / "ran"
        with mock.patch.dict(
            os.environ,
            {
                "MAILROOM_SEARCH_RESUME_RUN_ID": "run1",
                "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            },
            clear=False,
        ):
            with mock.patch.object(wwl, "acquire_writer_lock", acquire):
                with mock.patch.object(watchdog, "drop_early_deadline", drop):
                    with mock.patch.object(wwl.subprocess, "run", run):
                        rc = wwl.run_with_lock(
                            "unit-test",
                            [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; Path(%r).write_text('ok')"
                                % str(marker),
                            ],
                            lock_path=self.lock,
                            action_required_path=self.home / "ACTION_REQUIRED",
                        )
        self.assertEqual(rc, 0)
        self.assertEqual(events, ["acquire", "drop", "child"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "ok")
        text = self.deadline.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            "run_id=run1\ndeadline_50=%d\n" % (self.now + 50 * 60),
        )
        self.assertNotIn("deadline_26", text)
        held = wwl.acquire_writer_lock(self.lock, "later")
        wwl.release_writer_lock(held)

    def test_wrapper_drop_failure_skips_child_and_releases_lock(self) -> None:
        import with_writer_lock as wwl

        events = []
        marker = self.home / "should-not"

        def drop(run_id, path=None):
            events.append("drop")
            raise watchdog.DeadlineRefusal("deadline file missing")

        def run(cmd, check=False, env=None):
            events.append("child")
            raise AssertionError("child ran")

        with mock.patch.dict(
            os.environ,
            {
                "MAILROOM_SEARCH_RESUME_RUN_ID": "run1",
                "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            },
            clear=False,
        ):
            with mock.patch.object(watchdog, "drop_early_deadline", drop):
                with mock.patch.object(wwl.subprocess, "run", run):
                    with self.assertRaises(wwl.WriterLockError) as ctx:
                        wwl.run_with_lock(
                            "unit-test",
                            [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; Path(%r).write_text('ran')"
                                % str(marker),
                            ],
                            lock_path=self.lock,
                            action_required_path=self.home / "ACTION_REQUIRED",
                        )
        self.assertIn("child not started", str(ctx.exception))
        self.assertIn("deadline file missing", str(ctx.exception))
        self.assertEqual(events, ["drop"])
        self.assertFalse(marker.exists())
        held = wwl.acquire_writer_lock(self.lock, "later")
        wwl.release_writer_lock(held)

    def test_wrapper_cli_drop_failure_exits_nonzero(self) -> None:
        import with_writer_lock as wwl

        marker = self.home / "should-not"
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["MAILROOM_SEARCH_RESUME_RUN_ID"] = "run1"
        env["SEARCH_RESUME_DEADLINE_FILE"] = str(self.deadline)
        if self.deadline.exists():
            self.deadline.unlink()
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "with_writer_lock.py"),
                "--purpose",
                "cli-probe",
                "--lock-file",
                str(self.lock),
                "--action-required-file",
                str(self.home / "ACTION_REQUIRED"),
                "--",
                sys.executable,
                "-c",
                "from pathlib import Path; Path(%r).write_text('ran')" % str(marker),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("child not started", proc.stderr)
        self.assertIn("deadline file missing", proc.stderr)
        self.assertNotIn(SECRET, proc.stderr)
        self.assertFalse(marker.exists())
        self.assertNotIn("ran", proc.stdout)
        held = wwl.acquire_writer_lock(self.lock, "later")
        wwl.release_writer_lock(held)

    def test_status_prints_checkpoints_and_refuses_bad_files(self) -> None:
        body = "run_id=att0-L1-EXAMPLE\ndeadline_26=100\ndeadline_50=200\n"
        live = self._run(["status"], deadline=body)
        self.assertEqual(live.returncode, 0, live.stderr)
        self.assertEqual(
            live.stdout,
            "run_id=att0-L1-EXAMPLE\n"
            "deadline_26=100\n"
            "deadline_50=200\n"
            "plus_26_live=yes\n",
        )
        self.assertEqual(live.stderr, "")
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), body)
        self.assertEqual(self._launch(), [])
        self.assertFalse(self.lock.exists())
        armed = "run_id=att0-L1-EXAMPLE\ndeadline_50=200\n"
        dropped = self._run(["status"], deadline=armed)
        self.assertEqual(dropped.returncode, 0, dropped.stderr)
        self.assertEqual(
            dropped.stdout,
            "run_id=att0-L1-EXAMPLE\n"
            "deadline_26=absent\n"
            "deadline_50=200\n"
            "plus_26_live=no\n",
        )
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), armed)
        secret_body = (
            "run_id=att0-L1-EXAMPLE\n"
            "deadline_26=100\n"
            "deadline_50=200\n"
            "token=%s\n"
            "writer_token=%s\n" % (SECRET, SECRET)
        )
        hidden = self._run(["status"], deadline=secret_body)
        self.assertEqual(hidden.returncode, 0, hidden.stderr)
        self.assertNotIn(SECRET, hidden.stdout)
        self.assertNotIn(SECRET, hidden.stderr)
        self.assertNotIn("token=", hidden.stdout)
        missing = self._run(["status"], deadline=None)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stderr, "deadline file missing\n")
        self.assertEqual(missing.stdout, "")
        self.assertFalse(self.deadline.exists())
        bad = self._run(["status"], deadline="not a deadline\n")
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(bad.stderr, "deadline file unreadable\n")
        self.assertEqual(bad.stdout, "")
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), "not a deadline\n")

    def test_restore_overwrite_then_att0_restore_drops_plus_26(self) -> None:
        import with_writer_lock as wwl

        s1 = "att0-L1-EXAMPLE"
        restore = "att0-L1-EXAMPLE-R"
        d26 = self.now + 26 * 60
        d50 = self.now + 50 * 60
        s1_body = "run_id=%s\ndeadline_26=%d\ndeadline_50=%d\n" % (s1, d26, d50)
        restore_body = "run_id=%s\ndeadline_26=%d\ndeadline_50=%d\n" % (
            restore,
            d26,
            d50,
        )
        self.deadline.parent.mkdir(parents=True, exist_ok=True)
        self.deadline.write_text(s1_body, encoding="utf-8")
        self.deadline.write_text(restore_body, encoding="utf-8")
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), restore_body)
        marker = self.home / "restore-ran"
        stale_marker = self.home / "stale-ran"
        with mock.patch.dict(
            os.environ,
            {
                "MAILROOM_SEARCH_RESUME_RUN_ID": s1,
                "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            },
            clear=False,
        ):
            with self.assertRaises(wwl.WriterLockError) as ctx:
                wwl.run_with_lock(
                    "att0-restore",
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path(%r).write_text('stale')"
                        % str(stale_marker),
                    ],
                    lock_path=self.lock,
                    action_required_path=self.home / "ACTION_REQUIRED",
                )
        self.assertIn("run-id mismatch", str(ctx.exception))
        self.assertFalse(stale_marker.exists())
        self.assertEqual(self.deadline.read_text(encoding="utf-8"), restore_body)
        held = wwl.acquire_writer_lock(self.lock, "after-stale")
        wwl.release_writer_lock(held)
        with mock.patch.dict(
            os.environ,
            {
                "MAILROOM_SEARCH_RESUME_RUN_ID": restore,
                "SEARCH_RESUME_DEADLINE_FILE": str(self.deadline),
            },
            clear=False,
        ):
            rc = wwl.run_with_lock(
                "att0-restore",
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path(%r).write_text('ok')" % str(marker),
                ],
                lock_path=self.lock,
                action_required_path=self.home / "ACTION_REQUIRED",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "ok")
        text = self.deadline.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            "run_id=%s\ndeadline_50=%d\n" % (restore, d50),
        )
        self.assertNotIn("deadline_26", text)
        self.assertNotIn(s1 + "\n", text)
        info = wwl.read_lock_info(self.lock)
        self.assertEqual(info.purpose, "att0-restore")
        self.assertNotIn(SECRET, text)

    def test_gate_refuses_att0_restore_purpose(self) -> None:
        import sor_writer_gate as gate
        import with_writer_lock as wwl
        from datetime import datetime, timezone

        self.assertNotIn("att0-restore", gate.WRITER_PURPOSE_ALLOWLIST)
        self.assertIn("att0-migrate", gate.WRITER_PURPOSE_ALLOWLIST)
        token = "fixture-token-value"
        when = datetime(2026, 9, 27, 22, 15, tzinfo=timezone.utc)

        def decide(purpose: str):
            self.lock.write_text(
                wwl.format_lock_payload(
                    purpose=purpose,
                    now=when,
                    pid=os.getpid(),
                    hostname="fixture",
                    token=token,
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {wwl.LOCK_TOKEN_ENV: token}, clear=False
            ):
                return gate._identity_decision(self.lock, child_pid=os.getpid())

        matched, why = decide("att0-restore")
        self.assertFalse(matched)
        self.assertEqual(why, "purpose not allowed")
        self.assertNotIn(token, why)
        matched_ok, why_ok = decide("att0-migrate")
        self.assertTrue(matched_ok)
        self.assertEqual(why_ok, "match")
        self.assertNotIn(token, why_ok)


if __name__ == "__main__":
    unittest.main()
