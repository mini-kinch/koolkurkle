#!/usr/bin/env python3
"""Search-resume watchdog. PATH shims stand in for launchctl and lsof.

No real launchctl, no real lsof, no database, no writer-lock acquire.
"""

from __future__ import annotations

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
    exit "${STUB_PRINT_RC:-1}"
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
        env["STUB_PRINT_RC"] = "1"
        env["STUB_LSOF_RC"] = "1"
        env.pop("STUB_PRINT_OUT", None)
        env.pop("STUB_LSOF_OUT", None)
        env.pop("STUB_LSOF_ERR", None)
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
        self.assertIn("search still off: writer lock held", proc.stderr)

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
        loud = "search still off: writer lock held pid=%s purpose=%s" % (
            pid,
            "att0-migrate",
        )
        self.assertIn(loud, self.log.read_text(encoding="utf-8"))
        self.assertIn(loud, proc.stderr)

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
            )
            % (os.getpid(), SECRET, SECRET),
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
                return 1, "", ""
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
        self.assertIn("clear", doc)
        self.assertIn("launchctl bootstrap", doc)
        self.assertIn("launchctl kickstart", doc)
        self.assertIn("search_resume_after.epoch", doc)
        self.assertNotIn("-k", data["ProgramArguments"])


if __name__ == "__main__":
    unittest.main()
