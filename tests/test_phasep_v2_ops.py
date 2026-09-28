"""Contingency Phase P v2: W22 per-file locks, W23 curl group, W24 group kill.

Stub binaries only. No network, no Keychain, no writes outside temp dirs.
"""

from __future__ import annotations

import os
import random
import signal
import stat
import subprocess
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "att0" / "phaseP_v2.sh"
REAL_LOCK = ROOT / "scripts" / "with_writer_lock.py"

BK_SHA = "ab" * 32
BK_STATL = "1 2"
DAILY_STAMP = "2026-09-26 16:51:58"
IMAP_STAMP = "2026-09-26 08:50:16"

MARKERS = ("n1", "p1", "p2", "p3", "p4", "p5", "p6a", "p6b", "offline")


def _five_shas() -> list[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith('FIVE_SHAS="'):
            return line.split('"', 2)[1].split()
    raise AssertionError("FIVE_SHAS missing")


def _write(path: Path, body: str, mode: int = 0o755) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(mode)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pgid_members(pgid: int) -> list[int]:
    out = subprocess.check_output(["ps", "-axo", "pid=,pgid="], text=True)
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        pid_s, pgid_s = parts[0], parts[1]
        if int(pgid_s) == pgid:
            found.append(int(pid_s))
    return found


class PhasePV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.curl_bin = Path("/tmp/phasep-v2-pause")
        src = Path("/tmp/phasep-v2-pause.c")
        src.write_text(
            "#include <unistd.h>\nint main(void){for(;;) sleep(30);return 0;}\n",
            encoding="utf-8",
        )
        subprocess.check_call(["cc", "-O2", "-o", str(cls.curl_bin), str(src)])
        src.unlink()

    def setUp(self) -> None:
        self._kids: list[int] = []
        self.stamp = "29991231-%06d" % random.randint(0, 999999)
        self.work = Path("/tmp/phasep-v2-%s" % self.stamp)
        self.work.mkdir()
        self.home = self.work / "home"
        self.stubs = self.work / "stubs"
        self.hold = self.work / "hold"
        self.stubs.mkdir()
        self.hold.mkdir()
        self.lsof_log = self.work / "lsof.log"
        self.pgrep_log = self.work / "pgrep.log"
        self.time_pid = self.work / "time.pid"
        self.curl_pid = self.work / "curl.pid"
        self.holder_pid = self.work / "holder.pid"
        self.holder_ready = self.work / "holder.ready"
        self.extra_env: dict[str, str] = {}
        self.hold_lock = False

    def tearDown(self) -> None:
        for pid in list(self._kids):
            self._kill_tree(pid)
        for pidfile in (self.curl_pid, self.holder_pid):
            if pidfile.is_file():
                try:
                    self._kill_tree(int(pidfile.read_text(encoding="utf-8").strip()))
                except ValueError:
                    pass
        for name in MARKERS:
            Path("/tmp/phaseP-%s-%s.OK" % (name, self.stamp)).unlink(missing_ok=True)
        Path("/tmp/phaseP-fillstart-%s.OK" % self.stamp).unlink(missing_ok=True)
        Path("/tmp/phaseP-state-%s" % self.stamp).unlink(missing_ok=True)
        Path("/tmp/phaseP-p7harness-%s" % self.stamp).unlink(missing_ok=True)

    def _kill_tree(self, pid: int) -> None:
        # unshare does not start a new process group, so a holder can share
        # this test's group. Never signal that group.
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        if pgid != os.getpgrp():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _stage_mail(self, meta_fill: str) -> None:
        ma = self.home / "MailArchive"
        scripts = ma / "scripts"
        att = scripts / "attachments"
        logs = ma / "logs"
        dry = ma / "dryrun" / ("att0-livepath-%s" % self.stamp)
        backups = ma / "backups"
        for path in (scripts, att, logs, dry, backups):
            path.mkdir(parents=True)
        (ma / "mailroom.sqlite").write_bytes(b"")
        (ma / "mailroom.write.lock").write_bytes(b"")
        (ma / "mailroom.daily.lock").write_bytes(b"")
        (logs / "last_daily_rag_ok").write_bytes(b"")
        (logs / "last_imap_ok").write_bytes(b"")
        (dry / "mailroom.sqlite").write_bytes(b"")
        (backups / ("mailroom-pre-att0-live-%s.sqlite" % self.stamp)).write_bytes(b"")
        (scripts / "with_writer_lock.py").write_bytes(REAL_LOCK.read_bytes())
        _write(
            scripts / "sor_writer_gate.py",
            "def rem_process_hits():\n    return []\n",
            0o644,
        )
        _write(
            scripts / "search_resume_watchdog.py",
            textwrap.dedent(
                """\
                import sys
                if len(sys.argv) > 1 and sys.argv[1] == "status":
                    sys.stdout.write("status=missing\\n")
                    raise SystemExit(1)
                raise SystemExit(0)
                """
            ),
            0o644,
        )
        _write(
            scripts / "imap_tombstone.py",
            'EMAIL = "placeholder"\n',
            0o644,
        )
        _write(att / "migrate_att0_schema.py", "", 0o644)
        _write(att / "meta_fill.py", meta_fill, 0o644)
        state = Path("/tmp/phaseP-state-%s" % self.stamp)
        state.write_text(
            "STAMP=%s\nBK_SHA=%s\nBK_STATL=%s\n" % (self.stamp, BK_SHA, BK_STATL),
            encoding="utf-8",
        )
        for name in MARKERS:
            Path("/tmp/phaseP-%s-%s.OK" % (name, self.stamp)).write_bytes(b"")

    def _install_stubs(self, lsof_mode: str) -> None:
        five = _five_shas()
        self.assertEqual(len(five), 5)
        _write(
            self.stubs / "lsof",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import sys
                log = %r
                mode = %r
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(" ".join(sys.argv[1:]) + "\\n")
                args = sys.argv[1:]
                if "--" in args:
                    files = args[args.index("--") + 1:]
                else:
                    files = [a for a in args if not a.startswith("-")]
                    with open(log, "a", encoding="utf-8") as fh:
                        fh.write("NO-DDASH\\n")
                if len(files) >= 2:
                    with open(log, "a", encoding="utf-8") as fh:
                        fh.write("MULTI\\n")
                    if mode == "held":
                        print("4242")
                    raise SystemExit(1)
                held = (
                    len(files) == 1
                    and files[0].endswith("mailroom.write.lock")
                    and mode == "held"
                )
                if held:
                    # Ambiguous rc. Old code treated rc 1 as free.
                    print("4242")
                    raise SystemExit(1)
                raise SystemExit(1)
                """
                % (str(self.lsof_log), lsof_mode)
            ),
        )
        _write(
            self.stubs / "pgrep",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import sys
                with open(%r, "a", encoding="utf-8") as fh:
                    fh.write(" ".join(sys.argv[1:]) + "\\n")
                raise SystemExit(1)
                """
                % str(self.pgrep_log)
            ),
        )
        _write(
            self.stubs / "stat",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import sys
                args = sys.argv[1:]
                fmt = None
                path = None
                i = 0
                while i < len(args):
                    if args[i] == "-f":
                        fmt = args[i + 1]
                        i += 2
                    elif args[i] == "-t":
                        i += 2
                    else:
                        path = args[i]
                        i += 1
                base = (path or "").rsplit("/", 1)[-1]
                if fmt == "%%Sm":
                    if base == "last_daily_rag_ok":
                        print(%r)
                        raise SystemExit(0)
                    if base == "last_imap_ok":
                        print(%r)
                        raise SystemExit(0)
                if fmt == "%%HT %%l":
                    print("Regular File 1")
                    raise SystemExit(0)
                if fmt == "%%z %%m":
                    print(%r)
                    raise SystemExit(0)
                sys.stderr.write("stat-stub-miss\\n")
                raise SystemExit(1)
                """
                % (DAILY_STAMP, IMAP_STAMP, BK_STATL)
            ),
        )
        five_literal = ", ".join(repr(item) for item in five)
        _write(
            self.stubs / "shasum",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import sys",
                    "args = sys.argv[1:]",
                    'if args[:2] != ["-a", "256"]:',
                    "    raise SystemExit(2)",
                    "files = args[2:]",
                    "five = [%s]" % five_literal,
                    "if len(files) == 5:",
                    "    for digest, name in zip(five, files):",
                    '        print("%s  %s" % (digest, name))',
                    "    raise SystemExit(0)",
                    "if len(files) == 1:",
                    '    print("%%s  %%s" %% (%r, files[0]))' % BK_SHA,
                    "    raise SystemExit(0)",
                    "raise SystemExit(1)",
                    "",
                ]
            ),
        )
        _write(
            self.stubs / "launchctl",
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "$1" = "print-disabled" ]; then
                    printf '%s\\n' '"com.mailroom.daily" => disabled'
                    exit 0
                fi
                case "$2" in
                    */com.mailroom.daily)
                        echo 'Could not find service'
                        exit 113
                        ;;
                    */com.mailroom.search-resume-watchdog)
                        exit 0
                        ;;
                esac
                exit 1
                """
            ),
        )
        _write(
            self.stubs / "time",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os, sys
                open(%r, "w", encoding="utf-8").write(str(os.getpid()))
                args = sys.argv[1:]
                if args and args[0] == "-l":
                    args = args[1:]
                os.execv(args[0], args)
                """
                % str(self.time_pid)
            ),
        )
        for name in ("dscacheutil", "nc"):
            _write(self.stubs / name, "#!/bin/sh\nexit 0\n")
        os.chmod(self.curl_bin, self.curl_bin.stat().st_mode | stat.S_IXUSR)

    def _launcher(self, start_curl: bool) -> Path:
        path = self.work / "launch.sh"
        curl_block = ""
        if start_curl:
            # No PYTHONPATH: the test guard must not see this exec.
            curl_block = textwrap.dedent(
                """\
                env -u PYTHONPATH /usr/bin/python3 - <<'PY'
                import os, time
                pidfile = %r
                pid = os.fork()
                if pid > 0:
                    time.sleep(0.2)
                    open(pidfile, "w", encoding="utf-8").write(str(pid))
                    os._exit(0)
                os.setsid()
                os.execv(
                    "/usr/bin/curl",
                    ["/usr/bin/curl", "--silent", "--show-error", "--fail-early", "-K", "-"],
                )
                PY
                """
                % str(self.curl_pid)
            )
        hold_block = ""
        if self.hold_lock:
            lock_path = self.home / "MailArchive" / "mailroom.write.lock"
            hold_block = textwrap.dedent(
                """\
                env -u PYTHONPATH LOCK_PATH=%s READY_PATH=%s /usr/bin/python3 - <<'PY' &
                import fcntl, os, time
                fh = open(os.environ["LOCK_PATH"], "a+")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                open(os.environ["READY_PATH"], "w", encoding="utf-8").write("held")
                while True:
                    time.sleep(30)
                PY
                echo $! > %s
                _i=0
                while [ ! -f %s ]; do
                    _i=$((_i + 1))
                    [ "$_i" -gt 50 ] && exit 1
                    sleep 0.05
                done
                """
                % (
                    lock_path,
                    self.holder_ready,
                    self.holder_pid,
                    self.holder_ready,
                )
            )
        env_extra = ""
        for key, value in self.extra_env.items():
            env_extra += '  %s=%s \\\n' % (key, value)
        body = textwrap.dedent(
            """\
            #!/bin/bash
            set -eu
            HOLD=%(hold)s
            STUBS=%(stubs)s
            WORK=%(work)s
            copy_bin() {
                src=$1
                name=$2
                if [ -L "$src" ]; then
                    src=$(readlink -f "$src")
                fi
                cp "$src" "$HOLD/$name"
                chmod 755 "$HOLD/$name"
            }
            copy_bin /bin/bash bash
            copy_bin /bin/sh sh
            copy_bin /usr/bin/python3 python3
            copy_bin /usr/bin/python3 python3.12
            copy_bin /usr/bin/perl perl
            copy_bin /usr/bin/awk awk
            copy_bin /usr/bin/grep grep
            copy_bin /usr/bin/id id
            copy_bin /usr/bin/env env
            copy_bin /bin/sleep sleep
            copy_bin /bin/mkdir mkdir
            copy_bin /bin/date date
            copy_bin /bin/cat cat
            copy_bin /bin/true true
            copy_bin /bin/ps ps
            copy_bin /usr/bin/pkill pkill
            copy_bin /usr/bin/openssl openssl
            copy_bin /usr/bin/sqlite3 sqlite3
            copy_bin /bin/chmod chmod
            copy_bin /bin/cp cp
            copy_bin /bin/rm rm
            cp /bin/cp "$WORK/cp.bin"
            cp /bin/mount "$WORK/mount.bin"
            chmod 755 "$WORK/cp.bin" "$WORK/mount.bin"
            "$WORK/mount.bin" -t tmpfs tmpfs /usr/sbin
            "$WORK/cp.bin" "$STUBS/lsof" /usr/sbin/lsof
            chmod 755 /usr/sbin/lsof
            "$WORK/mount.bin" -t tmpfs tmpfs /usr/bin
            "$WORK/cp.bin" "$HOLD"/* /usr/bin/
            for stub in shasum launchctl time stat pgrep dscacheutil nc; do
                "$WORK/cp.bin" "$STUBS/$stub" "/usr/bin/$stub"
                chmod 755 "/usr/bin/$stub"
            done
            "$WORK/cp.bin" %(curl)s /usr/bin/curl
            chmod 755 /usr/bin/curl
            %(curl_block)s
            %(hold_block)s
            set +e
            /usr/bin/env -i \\
              HOME=%(home)s \\
              PATH=/usr/bin:/bin:/usr/sbin:/sbin \\
              LANG=C \\
              LC_ALL=C \\
              TMPDIR=/tmp \\
            %(env_extra)s  /bin/bash %(script)s fill %(stamp)s
            rc=$?
            if [ -f %(curl_pid)s ]; then
                kill -KILL "$(cat %(curl_pid)s)" 2>/dev/null || true
            fi
            if [ -f %(holder_pid)s ]; then
                kill -KILL "$(cat %(holder_pid)s)" 2>/dev/null || true
            fi
            exit $rc
            """
            % {
                "hold": self.hold,
                "stubs": self.stubs,
                "work": self.work,
                "curl": self.curl_bin,
                "curl_block": curl_block,
                "home": self.home,
                "env_extra": env_extra,
                "script": SCRIPT,
                "stamp": self.stamp,
                "curl_pid": self.curl_pid,
                "holder_pid": self.holder_pid,
                "curl_block": curl_block,
                "hold_block": hold_block,
            }
        )
        # textwrap.dedent on the outer string already ran. curl_block is indented
        # for a heredoc; re-indenting the whole launcher is done by dedent above
        # only if body was a single literal. Rebuild was via % so dedent already
        # applied to the template. Good.
        _write(path, body)
        return path

    def _run(self, start_curl: bool = False, timeout: int = 25) -> subprocess.CompletedProcess[str]:
        launcher = self._launcher(start_curl)
        proc = subprocess.run(
            [
                "unshare",
                "--user",
                "--mount",
                "--map-root-user",
                "/bin/bash",
                str(launcher),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc

    def test_source_contract(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(text.count("alarm shift; exec"), 1)
        self.assertIn("setpgrp(0, 0)", text)
        self.assertIn("select(undef, undef, undef, 5)", text)
        self.assertIn('GONE_EXPECT=992', text)
        self.assertNotIn(
            '"$LSOF" "$MA/mailroom.daily.lock" "$MA/mailroom.write.lock"',
            text,
        )
        self.assertNotIn("[c]url.*imap", text)
        self.assertNotIn("[/]usr/bin/curl.*imap", text)
        self.assertIn(
            "[/]usr/bin/curl --silent --show-error --fail-early -K -",
            text,
        )
        self.assertIn("MAILROOM_WRITER_LOCK_TOKEN", text)
        self.assertIn("PYTHONHOME", text)
        self.assertIn('case "$MODE" in', text)
        self.assertIn("offline)", text)
        self.assertIn("fill)", text)

    def test_w22_held_write_lock_free_daily_is_held(self) -> None:
        self._stage_mail("raise SystemExit(0)\n")
        self._install_stubs("held")
        # Old multi-path lsof: rc 1 and a pid on stdout. That rc is not freedom.
        old = subprocess.run(
            [str(self.stubs / "lsof"), "daily.lock", "mailroom.write.lock"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(old.returncode, 1)
        self.assertEqual(old.stdout.strip(), "4242")
        self.assertIn("MULTI", self.lsof_log.read_text(encoding="utf-8"))
        self.lsof_log.write_text("", encoding="utf-8")

        proc = self._run()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("PHASEP STOP p1-lock-HELD mailroom.write.lock", out)
        self.assertNotIn("LOCKS-FREE-OK", out)
        log = self.lsof_log.read_text(encoding="utf-8")
        self.assertNotIn("MULTI", log)
        self.assertNotIn("NO-DDASH", log)
        self.assertIn("mailroom.daily.lock", log)
        self.assertIn("mailroom.write.lock", log)
        daily_lines = [
            line
            for line in log.splitlines()
            if "mailroom.daily.lock" in line and "mailroom.write.lock" not in line
        ]
        self.assertTrue(daily_lines, log)
        self.assertTrue(any(line.endswith("mailroom.write.lock") for line in log.splitlines()), log)
        self.assertTrue(all(" -- " in line or line.startswith("-t -- ") or "--" in line for line in log.splitlines() if "lock" in line), log)

    def test_w22_flock_probe_rc2_is_stop(self) -> None:
        self._stage_mail("raise SystemExit(0)\n")
        self._install_stubs("free")
        # Holder runs inside the mount namespace, on with_writer_lock's own path.
        self.hold_lock = True
        proc = self._run()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("PHASEP STOP p1-lock-HELD flock", out)
        self.assertNotIn("LOCKS-FREE-OK", out)
        log = self.lsof_log.read_text(encoding="utf-8")
        self.assertNotIn("MULTI", log)
        self.assertNotIn("4242", proc.stdout)

    def test_w23_orphan_pinned_curl_is_detected(self) -> None:
        self._stage_mail("raise SystemExit(0)\n")
        self._install_stubs("free")
        proc = self._run(start_curl=True)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("curl-process-group", out)
        self.assertNotIn("NO-WRITER-OK", out)
        pattern = self.pgrep_log.read_text(encoding="utf-8")
        # pgrep's own argv must not match, so the pinned argv is bracketed.
        self.assertIn("[/]usr/bin/curl --silent --show-error --fail-early -K -", pattern)
        self.assertNotIn("curl.*imap", pattern)
        self.assertTrue(self.curl_pid.is_file(), out)
        orphan = int(self.curl_pid.read_text(encoding="utf-8").strip())
        # Detection ran while it was alive; launcher reaps it afterwards.
        self.assertGreater(orphan, 1)

    def test_w24_hung_fill_timeout_kills_group(self) -> None:
        child_file = self.work / "fill-child.pid"
        parent_file = self.work / "fill-parent.pid"
        meta = textwrap.dedent(
            """\
            import os, time
            open(%r, "w", encoding="utf-8").write(str(os.getpid()))
            pid = os.fork()
            if pid == 0:
                open(%r, "w", encoding="utf-8").write(str(os.getpid()))
                while True:
                    time.sleep(30)
            while True:
                time.sleep(30)
            """
            % (str(parent_file), str(child_file))
        )
        self._stage_mail(meta)
        self._install_stubs("free")
        self.extra_env["PHASEP_FILL_ALARM"] = "5"
        started = time.time()
        proc = self._run(timeout=40)
        elapsed = time.time() - started
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertGreaterEqual(elapsed, 8.0, out)
        lines = [line for line in out.splitlines() if "FILL-RC=" in line or "HARNESS=" in line]
        self.assertGreaterEqual(len(lines), 2, out)
        self.assertTrue(lines[0].startswith("PHASEP P7 FILL-RC="), lines)
        self.assertNotEqual(lines[0], "PHASEP P7 FILL-RC=0")
        self.assertEqual(lines[1], "PHASEP P7 HARNESS=timeout then TERM/KILL")
        self.assertLess(out.index("P7 HARNESS="), out.index("P7 FILL-END"))
        self.assertIn("PHASEP STOP p7-harness", out)
        self.assertNotIn("P8-OK", out)
        self.assertNotIn("NOTHING-LEFT-RUNNING-OK", out)
        self.assertNotIn("PHASEP P8 ", out)
        status = Path("/tmp/phaseP-p7harness-%s" % self.stamp).read_text(encoding="utf-8")
        self.assertIn("group_empty=yes", status)
        self.assertTrue(child_file.is_file(), out)
        self.assertTrue(parent_file.is_file(), out)
        child = int(child_file.read_text(encoding="utf-8").strip())
        parent = int(parent_file.read_text(encoding="utf-8").strip())
        self.assertFalse(_pid_alive(child), "fill child still alive")
        self.assertFalse(_pid_alive(parent), "fill parent still alive")

    def test_w24_normal_exit_still_kills_group(self) -> None:
        child_file = self.work / "fill-child.pid"
        meta = textwrap.dedent(
            """\
            import os, sys, time
            pid = os.fork()
            if pid == 0:
                open(%r, "w", encoding="utf-8").write(str(os.getpid()))
                while True:
                    time.sleep(30)
            while not os.path.exists(%r):
                time.sleep(0.01)
            raise SystemExit(0)
            """
            % (str(child_file), str(child_file))
        )
        self._stage_mail(meta)
        self._install_stubs("free")
        started = time.time()
        proc = self._run(timeout=40)
        elapsed = time.time() - started
        out = proc.stdout + proc.stderr
        self.assertGreaterEqual(elapsed, 4.5, out)
        lines = [line for line in out.splitlines() if "FILL-RC=" in line or "HARNESS=" in line]
        self.assertGreaterEqual(len(lines), 2, out)
        self.assertEqual(lines[0], "PHASEP P7 FILL-RC=0", out)
        self.assertEqual(lines[1], "PHASEP P7 HARNESS=normal", out)
        self.assertNotIn("p7-group-not-empty", out)
        self.assertNotIn("p7-harness-missing", out)
        status = Path("/tmp/phaseP-p7harness-%s" % self.stamp).read_text(encoding="utf-8")
        self.assertIn("group_empty=yes", status)
        self.assertIn("harness=normal", status)
        self.assertTrue(child_file.is_file(), out)
        child = int(child_file.read_text(encoding="utf-8").strip())
        self.assertFalse(_pid_alive(child), "normal-exit child still alive")
        self._kids.append(child)


if __name__ == "__main__":
    unittest.main()
