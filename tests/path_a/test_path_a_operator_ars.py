#!/usr/bin/env python3
"""Offline tests for Path A operator drills, cold, and soak wrappers.

PATH-shimmed launchctl, curl, kill, ps, and lsof. Loopback HTTP only on an
ephemeral port. Never binds 1234, 8743, or 11434.
"""
from __future__ import print_function

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRILL = ROOT / "scripts" / "path_a_drill.sh"
COLD = ROOT / "scripts" / "path_a_cold_ar.sh"
SOAK = ROOT / "scripts" / "path_a_soak_ar.sh"
BENCH = ROOT / "scripts" / "path_a_bench.py"
FAKE = ROOT / "tests" / "path_a" / "fake_host_tools.py"
_LOGFMT_PATH = ROOT / "tests" / "path_a" / "watchdog_logfmt.py"
DOC_DRILL = ROOT / "docs" / "path-a" / "ar7-drills.md"
DOC_COLD = ROOT / "docs" / "path-a" / "ar8-cold.md"
DOC_SOAK = ROOT / "docs" / "path-a" / "ar9-soak.md"
ASK_MAIL = ROOT / "scripts" / "ask_mail.py"

FORBIDDEN_PORTS = (1234, 8743, 11434)
USERS_PREFIX = "/" + "Users" + "/"
HOME_PREFIX = "/" + "home" + "/"
ICLOUD_MARK = "@" + "icloud"
ME_MARK = "@" + "me" + ".com"


def _load_logfmt():
    spec = importlib.util.spec_from_file_location("watchdog_logfmt_ars", str(_LOGFMT_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load watchdog log format")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOGFMT = _load_logfmt()


def _load_bench():
    spec = importlib.util.spec_from_file_location("path_a_bench_ars", str(BENCH))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load bench")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ephemeral_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    if port in FORBIDDEN_PORTS:
        return _ephemeral_port()
    return port


def _completion(content):
    payload = {
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }
    return json.dumps(payload).encode("utf-8")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.posts = []
        self.delay = 0.0
        self.hang_first = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def _send(self, code, body):
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except OSError:
            return

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/v1/models":
            self._send(404, b"{}")
            return
        raw = json.dumps({"data": [{"id": "synth-model"}]}).encode("utf-8")
        self._send(200, raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        server = self.server
        server.posts.append(raw)
        if server.hang_first and len(server.posts) == 1:
            import time

            time.sleep(server.delay)
            self._send(200, _completion("E" * 360))
            return
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        if body.get("max_tokens") == 1:
            self._send(200, _completion("ok"))
            return
        self._send(200, _completion("E" * 360))


def _serve():
    httpd = _Server()
    if httpd.server_address[1] in FORBIDDEN_PORTS:
        httpd.server_close()
        raise RuntimeError("ephemeral port collided with a live port")
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    return httpd


class OperatorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.fake_root = self.tmp / "fake"
        self.fake_root.mkdir()
        self.trace = self.tmp / "trace.log"
        self.trace.write_text("")
        self.log = self.tmp / "logs" / "qwen-watchdog.log"
        self.log.parent.mkdir()
        self.log.write_text("")
        self.hold = self.tmp / "qwen-mlx" / "HOLD"
        self.hold.parent.mkdir()
        self.ask = self.tmp / "ask_mail.py"
        self.ask.write_text("# synthetic\n")
        self._write("pid", "4100")
        self._write("phase", "up")
        self._write("loaded", "1")
        self._write("down_curls", "0")
        self._write("stop_curls", "0")
        self._write("restarts_done", "0")
        self._write("keepalive", "true")
        self.plist = self.home / "Library" / "LaunchAgents" / "com.mailroom.mlx-lm-server.plist"
        self.plist.parent.mkdir(parents=True)
        self.plist.write_text("KeepAlive true\n")
        for name in ("launchctl", "curl", "kill", "ps", "lsof", "plutil"):
            os.symlink(str(FAKE), str(self.bin_dir / name))
        self.port = _ephemeral_port()
        self.assertNotIn(self.port, FORBIDDEN_PORTS)
        self.real_home = os.environ.get("HOME", "")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, value):
        (self.fake_root / name).write_text(str(value))

    def _env(self, scenario="launchd", extra=None):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PATH"] = str(self.bin_dir) + os.pathsep + env.get("PATH", "")
        env["PATH_A_OFFLINE"] = "1"
        env["FAKE_HOST_TOOLS"] = "1"
        env["FAKE_ROOT"] = str(self.fake_root)
        env["FAKE_TRACE"] = str(self.trace)
        env["FAKE_SCENARIO"] = scenario
        env["DRILL_HOST"] = "127.0.0.1"
        env["DRILL_PORT"] = str(self.port)
        env["DRILL_HOLD"] = str(self.hold)
        env["DRILL_LOG"] = str(self.log)
        env.pop("FAKE_MUTATE_ASK", None)
        env.pop("FAKE_ASK", None)
        env.pop("FAKE_IGNORE_BOOTOUT", None)
        if extra:
            env.update(extra)
        return env

    def _run(self, script, args, scenario="launchd", extra=None, timeout=20):
        proc = subprocess.run(
            ["/bin/bash", str(script), *args],
            cwd=str(ROOT),
            env=self._env(scenario, extra),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        blob = proc.stdout + proc.stderr
        self.assertNotIn("Traceback", blob)
        self.assertNotIn(USERS_PREFIX, blob)
        self.assertNotIn(ICLOUD_MARK, blob.lower())
        self.assertNotIn(ME_MARK, blob.lower())
        self.assertIsNone(re.search(r":1234(?!\d)", blob))
        self.assertIsNone(re.search(r":8743(?!\d)", blob))
        if self.real_home:
            self.assertNotIn(self.real_home, blob)
        return proc

    def _trace(self):
        return self.trace.read_text()

    def _commands(self):
        names = []
        for line in self._trace().splitlines():
            if not line.strip():
                continue
            names.append(line.split("\t", 1)[0])
        return names


class DrillTests(OperatorCase):
    def test_bash_n_and_bash32_subset(self):
        subprocess.check_call(["/bin/bash", "-n", str(DRILL)])
        text = DRILL.read_text()
        for banned in ("declare -A", "mapfile", "readarray", "|&", ";&", "^^", ",,"):
            self.assertNotIn(banned, text)
        self.assertNotIn(USERS_PREFIX, text)
        self.assertNotIn(ICLOUD_MARK, text.lower())
        self.assertNotIn("qwen-chat-up", text)
        self.assertNotIn("qwen-chat-down", text)
        self.assertNotIn("ask-mail-serve", text)
        self.assertIn("enable -n kill", text)

    def test_defaults_name_the_live_host(self):
        text = DRILL.read_text()
        self.assertIn('DRILL_PORT="${DRILL_PORT:-1234}"', text)
        self.assertIn("com.mailroom.mlx-lm-server", text)
        self.assertIn("com.mailroom.qwen-watchdog", text)
        self.assertIn("$HOME/qwen-mlx/HOLD", text)
        self.assertIn("$HOME/MailArchive/logs/qwen-watchdog.log", text)
        self.assertIn("restart kickstart", text)
        self.assertIn("loop guard", text)
        self.assertIn("fail consecutive=", text)
        self.assertIn("plutil -extract KeepAlive raw", text)
        self.assertIn("com.mailroom.mlx-lm-server.plist", text)
        self.assertIn("DRILL_SERVER_PLIST", text)
        self.assertIn("recovered_by=launchd-keepalive", text)
        self.assertIn("recovered_by=watchdog", text)
        self.assertIn("stop-hold", text)
        self.assertIn("kill -STOP", text)

    def test_dry_run_does_not_call_host_tools(self):
        for cmd in ("port-kill", "stop-hold", "stop-cont", "loop3"):
            self.trace.write_text("")
            proc = self._run(DRILL, ["--dry-run", "--ask-mail", str(self.ask), cmd])
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            self.assertIn("PASS dry-run %s" % cmd, proc.stdout)
            self.assertEqual(self._trace(), "")
            self.assertFalse(self.hold.exists())
        proc = self._run(DRILL, ["--dry-run", "port-kill"])
        self.assertIn("dry-run: kill <pid>", proc.stdout)
        self.assertIn("dry-run: lsof -nP -iTCP:%d" % self.port, proc.stdout)
        self.assertIn("dry-run: ps -p <pid>", proc.stdout)
        self.assertIn("curl -sS -m 10", proc.stdout)
        self.assertIn("launchctl print gui/", proc.stdout)
        self.assertIn("plutil -extract KeepAlive raw ", proc.stdout)
        self.assertIn("recovered_by=launchd-keepalive", proc.stdout)
        self.assertIn("recovered_by=watchdog", proc.stdout)
        self.assertNotIn("dry-run: touch", proc.stdout)
        proc = self._run(DRILL, ["--dry-run", "port-kill-hold"])
        self.assertIn("alias of stop-hold", proc.stdout)
        self.assertIn("PASS dry-run stop-hold", proc.stdout)
        self.assertIn("dry-run: touch ", proc.stdout)
        self.assertIn("dry-run: kill -STOP <pid>", proc.stdout)
        self.assertIn("dry-run: rm -f ", proc.stdout)
        self.assertIn("no restart kickstart", proc.stdout)
        self.assertIn("fail consecutive=", proc.stdout)
        self.assertEqual(self._trace(), "")
        proc = self._run(DRILL, ["--dry-run", "stop-cont"])
        self.assertIn("dry-run: kill -STOP <pid>", proc.stdout)
        self.assertIn("dry-run: kill -CONT <pid>", proc.stdout)
        proc = self._run(DRILL, ["--dry-run", "loop3"])
        self.assertEqual(proc.stdout.count("dry-run: kill -STOP <pid>"), 4)
        self.assertIn("kill -CONT every stopped pid", proc.stdout)
        self.assertIn("loop guard", proc.stdout)
        self.assertIn("wait 1800s", proc.stdout)
        proc = self._run(DRILL, ["--dry-run", "port-kill"])
        self.assertIn("wait 900s", proc.stdout)

    def test_unknown_command_is_usage(self):
        proc = self._run(DRILL, ["nope"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage:", proc.stderr)
        self.assertEqual(self._trace(), "")

    def test_offline_refuses_live_port(self):
        env_port = {"DRILL_PORT": "1234"}
        proc = self._run(DRILL, ["port-kill"], extra=env_port)
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("refusing live port", proc.stderr)
        self.assertEqual(self._trace(), "")

    def test_port_kill_pass_keepalive_relaunch(self):
        proc = self._run(
            DRILL,
            ["--ask-mail", str(self.ask), "--wait", "5", "--poll", "0", "port-kill"],
            scenario="port-kill",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS port-kill ", proc.stdout)
        self.assertIn("pid_before=4100", proc.stdout)
        self.assertIn("pid_after=4101", proc.stdout)
        self.assertIn("recovered_by=launchd-keepalive", proc.stdout)
        self.assertIn("preflight keepalive=true", proc.stdout)
        self.assertIn("preflight ask_mail sha256=", proc.stdout)
        self.assertIn("preflight ps=4100 mlx_lm.server", proc.stdout)
        self.assertNotIn("restart kickstart", self.log.read_text())
        self.assertIn("restore models=200", proc.stdout)
        self.assertNotIn("\tkickstart\t", "\n" + self._trace())
        self.assertIn("plutil\t-extract\tKeepAlive\traw\t", self._trace())
        self.assertFalse(self.hold.exists())
        self.assertIn("kill", self._commands())
        joined = self._trace()
        self.assertIn("127.0.0.1:%d" % self.port, joined)
        self.assertIsNone(re.search(r":1234(?!\d)", joined))
        self.assertIsNone(re.search(r":8743(?!\d)", joined))

    def test_port_kill_watchdog_when_keepalive_false(self):
        self._write("keepalive", "false")
        proc = self._run(
            DRILL,
            ["--wait", "5", "--poll", "0", "port-kill"],
            scenario="port-kill",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("preflight keepalive=false", proc.stdout)
        self.assertIn("recovered_by=watchdog", proc.stdout)
        self.assertIn("pid_before=4100", proc.stdout)
        self.assertIn("pid_after=4101", proc.stdout)
        log = self.log.read_text()
        self.assertTrue(log.startswith("["), msg=log)
        self.assertIn("] restart kickstart -k gui/1/com.mailroom.mlx-lm-server rc=0", log)
        self.assertIn("restore models=200", proc.stdout)
        self.assertNotIn("\tkickstart\t", "\n" + self._trace())

    def test_port_kill_old_restart_line_is_not_this_recovery(self):
        self.log.write_text(
            LOGFMT.format_watchdog_line(
                "2026-09-25 11:00:00",
                "restart kickstart -k gui/1/com.mailroom.mlx-lm-server rc=0",
            )
        )
        proc = self._run(
            DRILL,
            ["--wait", "5", "--poll", "0", "port-kill"],
            scenario="port-kill",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("recovered_by=launchd-keepalive", proc.stdout)
        self.assertEqual(self.log.read_text().count("restart kickstart"), 1)

    def test_port_kill_stuck_fails(self):
        proc = self._run(
            DRILL,
            ["--wait", "1", "--poll", "1", "port-kill"],
            scenario="stuck-down",
            timeout=15,
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=port did not recover", proc.stdout)
        self.assertIn("kickstart\t-k\tgui/", self._trace())

    def test_missing_plist_prints_unknown_keepalive(self):
        self.plist.unlink()
        proc = self._run(
            DRILL,
            ["--wait", "5", "--poll", "0", "port-kill"],
            scenario="port-kill",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("preflight keepalive=unknown", proc.stdout)
        self.assertIn("recovered_by=launchd-keepalive", proc.stdout)
        self.assertNotIn("plutil", self._commands())

    def test_server_plist_path_override(self):
        custom = self.tmp / "server.plist"
        custom.write_text("KeepAlive false\n")
        self._write("keepalive", "false")
        proc = self._run(
            DRILL,
            ["--dry-run", "port-kill"],
            extra={"DRILL_SERVER_PLIST": str(custom)},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("plutil -extract KeepAlive raw %s" % custom, proc.stdout)
        self.assertEqual(self._trace(), "")

    def test_stop_hold_pass_then_recovers(self):
        proc = self._run(
            DRILL,
            ["--wait", "1", "--poll", "0", "stop-hold"],
            scenario="stop-hold",
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS stop-hold ", proc.stdout)
        self.assertIn("hold_elapsed_s=", proc.stdout)
        self.assertIn("recovery_elapsed_s=", proc.stdout)
        self.assertIn("action touch HOLD", proc.stdout)
        self.assertIn("action kill -STOP pid=4100", proc.stdout)
        self.assertIn("action rm HOLD", proc.stdout)
        self.assertNotIn("restart while HOLD", proc.stdout)
        self.assertIn("fail consecutive=1", self.log.read_text())
        self.assertIn("fail consecutive=2", self.log.read_text())
        self.assertEqual(self.log.read_text().count("restart kickstart"), 1)
        self.assertIn("pid_after=4101", proc.stdout)
        self.assertFalse(self.hold.exists())
        self.assertIn("restore cont pid=4100", proc.stdout)
        self.assertIn("restore models=200", proc.stdout)
        self.assertIn("kill\t-STOP\t4100", self._trace())
        self.assertIn("kill\t-CONT\t4100", self._trace())

    def test_port_kill_hold_alias_prints_stop_hold(self):
        proc = self._run(
            DRILL,
            ["--wait", "1", "--poll", "0", "port-kill-hold"],
            scenario="port-kill-hold",
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("port-kill-hold alias of stop-hold:", proc.stdout)
        self.assertIn("kill -STOP under HOLD", proc.stdout)
        self.assertIn("PASS stop-hold ", proc.stdout)
        self.assertIn("action kill -STOP pid=4100", proc.stdout)
        self.assertEqual(self.log.read_text().count("restart kickstart"), 1)
        self.assertIn("restore cont pid=4100", proc.stdout)

    def test_hold_violation_fails_and_clears_hold(self):
        proc = self._run(
            DRILL,
            ["--wait", "5", "--poll", "0", "port-kill-hold"],
            scenario="hold-violated",
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("FAIL stop-hold ", proc.stdout)
        self.assertIn("reason=restart while HOLD set", proc.stdout)
        self.assertIn("restore cleared HOLD", proc.stdout)
        self.assertIn("restore cont pid=4100", proc.stdout)
        self.assertFalse(self.hold.exists())
        self.assertIn("kickstart", self._trace())
        self.assertIn("kill\t-CONT\t4100", self._trace())

    def test_stop_cont_pass_and_trap_cont(self):
        proc = self._run(
            DRILL,
            ["--wait", "5", "--poll", "0", "stop-cont"],
            scenario="stop-cont",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS stop-cont ", proc.stdout)
        self.assertIn("pid_before=4100", proc.stdout)
        self.assertIn("pid_after=4101", proc.stdout)
        self.assertIn("restore cont pid=4100", proc.stdout)
        self.assertIn("fail consecutive=1", self.log.read_text())
        self.assertIn("fail consecutive=2", self.log.read_text())
        self.assertIn("restart kickstart", self.log.read_text())
        self.assertIn("kill\t-STOP\t4100", self._trace())
        self.assertIn("kill\t-CONT\t4100", self._trace())
        self.assertNotIn("\tkickstart\t", "\n" + self._trace())

    def test_stop_cont_failure_still_cont_and_kickstart(self):
        proc = self._run(
            DRILL,
            ["--wait", "1", "--poll", "1", "stop-cont"],
            scenario="no-recovery",
            timeout=15,
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=no probe failure then kickstart", proc.stdout)
        self.assertIn("kill\t-STOP\t4100", self._trace())
        self.assertIn("kill\t-CONT\t4100", self._trace())
        self.assertIn("kickstart\t-k\tgui/", self._trace())
        self.assertIn("com.mailroom.mlx-lm-server", self._trace())
        self.assertNotIn("com.mailroom.qwen-watchdog", self._trace().split("kickstart", 1)[-1])

    def test_loop3_writes_guard_and_restore_clears_it(self):
        proc = self._run(
            DRILL,
            ["--wait", "5", "--poll", "0", "loop3"],
            scenario="loop3",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("loop3 hold_text=loop guard: HOLD written", proc.stdout)
        self.assertIn("PASS loop3 ", proc.stdout)
        self.assertIn("hold=loop-guard", proc.stdout)
        self.assertIn("restarts_seen=3", proc.stdout)
        self.assertIn("action kill -STOP pid=4100 cycle=1", proc.stdout)
        self.assertIn("loop3 cycle=1 pid_before=4100 pid_after=4101", proc.stdout)
        self.assertIn("loop3 cycle=3 pid_before=4102 pid_after=4103", proc.stdout)
        self.assertIn("action kill -STOP pid=4103 cycle=guard", proc.stdout)
        self.assertEqual(self.log.read_text().count("restart kickstart"), 3)
        self.assertTrue(self.log.read_text().startswith("["))
        self.assertIn("restore cleared HOLD", proc.stdout)
        self.assertFalse(self.hold.exists())
        self.assertIn("restore kickstart label=com.mailroom.mlx-lm-server", proc.stdout)
        stops = [line for line in self._trace().splitlines() if line.startswith("kill\t-STOP\t")]
        conts = [line for line in self._trace().splitlines() if line.startswith("kill\t-CONT\t")]
        self.assertEqual(stops, ["kill\t-STOP\t4100", "kill\t-STOP\t4101", "kill\t-STOP\t4102", "kill\t-STOP\t4103"])
        self.assertEqual(conts, ["kill\t-CONT\t4100", "kill\t-CONT\t4101", "kill\t-CONT\t4102", "kill\t-CONT\t4103"])

    def test_label_not_loaded_does_not_kill(self):
        (self.fake_root / "loaded").unlink()
        proc = self._run(DRILL, ["--wait", "5", "--poll", "0", "port-kill"], scenario="port-kill")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=label not loaded", proc.stdout)
        self.assertNotIn("kill", self._commands())
        self.assertNotIn("kickstart", self._trace())

    def test_no_listener(self):
        self._write("pid", "")
        proc = self._run(DRILL, ["port-kill"], scenario="port-kill")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=no listener", proc.stdout)
        self.assertNotIn("kill", self._commands())

    def test_models_down_at_start(self):
        self._write("phase", "down")
        proc = self._run(DRILL, ["--wait", "5", "--poll", "0", "port-kill"], scenario="port-kill")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=models not up", proc.stdout)
        self.assertNotIn("kill", self._commands())

    def test_existing_hold_refuses(self):
        self.hold.write_text("operator\n")
        proc = self._run(DRILL, ["port-kill"], scenario="port-kill")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=HOLD already present", proc.stdout)
        self.assertEqual(self.hold.read_text(), "operator\n")
        self.assertNotIn("kill", self._commands())

    def test_ask_mail_missing(self):
        missing = self.tmp / "missing.py"
        proc = self._run(DRILL, ["--ask-mail", str(missing), "port-kill"], scenario="port-kill")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=ask_mail missing", proc.stdout)
        self.assertNotIn("launchctl", self._commands())

    def test_ask_mail_sha_change_fails(self):
        proc = self._run(
            DRILL,
            ["--ask-mail", str(self.ask), "--wait", "5", "--poll", "0", "port-kill"],
            scenario="port-kill",
            extra={"FAKE_MUTATE_ASK": "1", "FAKE_ASK": str(self.ask)},
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=ask_mail sha changed", proc.stdout)
        self.assertIn("kill", self._commands())
        self.assertIn("# mutated", self.ask.read_text())

    def test_repo_ask_mail_untouched(self):
        before = ASK_MAIL.read_bytes()
        self._run(DRILL, ["--dry-run", "port-kill"])
        self.assertEqual(ASK_MAIL.read_bytes(), before)


class ColdTests(OperatorCase):
    def test_bash_n_and_scope(self):
        subprocess.check_call(["/bin/bash", "-n", str(COLD)])
        text = COLD.read_text()
        for banned in ("declare -A", "mapfile", "readarray", "|&"):
            self.assertNotIn(banned, text)
        self.assertNotIn(USERS_PREFIX, text)
        self.assertNotIn("ask-mail-serve", text)
        self.assertNotIn("mlx-lm-server", text)
        self.assertIn("com.mailroom.qwen-watchdog", text)
        self.assertIn("--idle", text)
        self.assertIn("1800", text)

    def test_dry_run_default_idle(self):
        proc = self._run(COLD, ["--dry-run"])
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS dry-run cold", proc.stdout)
        self.assertIn("launchctl bootout gui/", proc.stdout)
        self.assertIn("com.mailroom.qwen-watchdog.plist", proc.stdout)
        self.assertIn("cold --idle 1800", proc.stdout)
        self.assertIn("launchctl bootstrap gui/", proc.stdout)
        self.assertEqual(self._trace(), "")

    def test_refuses_other_plist_basename(self):
        plist = self.tmp / "com.mailroom.mlx-lm-server.plist"
        plist.write_text("nope\n")
        proc = self._run(COLD, ["--plist", str(plist)])
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=refusing plist basename", proc.stdout)
        self.assertEqual(self._trace(), "")

    def test_not_loaded_does_not_bootstrap(self):
        (self.fake_root / "loaded").unlink()
        plist = self.home / "Library" / "LaunchAgents" / "com.mailroom.qwen-watchdog.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("plist\n")
        proc = self._run(COLD, ["--plist", str(plist), "--base-url", "http://127.0.0.1:%d" % self.port])
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=watchdog not loaded", proc.stdout)
        self.assertNotIn("bootout", self._trace())
        self.assertNotIn("bootstrap", self._trace())
        self.assertNotIn("python", self._trace())

    def test_bootout_ignored_does_not_run_bench_but_bootstraps(self):
        plist = self.tmp / "com.mailroom.qwen-watchdog.plist"
        plist.write_text("plist\n")
        marker = self.tmp / "python-ran"
        proc = self._run(
            COLD,
            ["--plist", str(plist), "--base-url", "http://127.0.0.1:%d" % self.port, "--idle", "0"],
            extra={"FAKE_IGNORE_BOOTOUT": "1", "COLD_PYTHON": str(self._python_marker(marker))},
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=watchdog still loaded", proc.stdout)
        self.assertFalse(marker.exists())
        self.assertIn("restore bootstrap label=com.mailroom.qwen-watchdog", proc.stdout)
        self.assertIn("bootstrap", self._trace())
        self.assertNotIn("python", self._commands())

    def test_offline_without_base_url_does_not_call_launchctl(self):
        plist = self.tmp / "com.mailroom.qwen-watchdog.plist"
        plist.write_text("plist\n")
        proc = self._run(COLD, ["--plist", str(plist), "--idle", "0"])
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("offline run requires --base-url", proc.stderr)
        self.assertEqual(self._trace(), "")

    def test_integration_pass_bootout_then_bootstrap(self):
        httpd = _serve()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        self.assertNotIn(port, FORBIDDEN_PORTS)
        plist = self.tmp / "com.mailroom.qwen-watchdog.plist"
        plist.write_text("plist\n")
        out = self.tmp / "cold.jsonl"
        proc = self._run(
            COLD,
            [
                "--plist",
                str(plist),
                "--idle",
                "0",
                "--ask-mail",
                str(self.ask),
                "--out",
                str(out),
                "--base-url",
                "http://127.0.0.1:%d" % port,
            ],
            extra={"COLD_PYTHON": sys.executable},
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS cold_probe", proc.stdout)
        self.assertIn("PASS cold_request", proc.stdout)
        self.assertIn("OVERALL PASS", proc.stdout)
        self.assertNotIn("WEDGE", proc.stdout + proc.stderr)
        self.assertIn("PASS cold ", proc.stdout)
        self.assertIn("idle=0", proc.stdout)
        self.assertIn("restore bootstrap label=com.mailroom.qwen-watchdog", proc.stdout)
        names = self._commands()
        self.assertEqual(names[:3], ["launchctl", "launchctl", "launchctl"])
        text = self._trace()
        self.assertIn("print\tgui/", text)
        self.assertIn("bootout\tgui/", text)
        self.assertIn("\t" + str(plist), text)
        self.assertIn("bootstrap\tgui/", text)
        self.assertLess(text.index("bootout"), text.index("bootstrap"))
        self.assertNotIn("8743", text)
        self.assertNotIn("mlx-lm-server", text)
        self.assertNotIn("ask-mail", text)
        self.assertTrue(os.path.exists(self.fake_root / "loaded"))
        self.assertGreaterEqual(len(httpd.posts), 2)
        probe = json.loads(httpd.posts[0].decode("utf-8"))
        self.assertEqual(probe["max_tokens"], 1)

    def test_bench_failure_still_bootstraps(self):
        closed = _ephemeral_port()
        plist = self.tmp / "com.mailroom.qwen-watchdog.plist"
        plist.write_text("plist\n")
        proc = self._run(
            COLD,
            [
                "--plist",
                str(plist),
                "--idle",
                "0",
                "--base-url",
                "http://127.0.0.1:%d" % closed,
                "--ask-mail",
                str(self.ask),
            ],
            extra={"COLD_PYTHON": sys.executable},
            timeout=20,
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("FAIL cold ", proc.stdout)
        self.assertIn("reason=bench_rc=2", proc.stdout)
        self.assertIn("restore bootstrap label=com.mailroom.qwen-watchdog", proc.stdout)
        self.assertIn("bootstrap", self._trace())
        self.assertTrue(ASK_MAIL.is_file())

    def _python_marker(self, marker):
        path = self.tmp / "python-marker.sh"
        path.write_text(
            "#!/bin/bash\ntouch %s\nexit 0\n" % _shell_quote(str(marker))
        )
        path.chmod(0o755)
        return path


class SoakTests(OperatorCase):
    def test_bash_n_and_leaves_watchdog_alone(self):
        subprocess.check_call(["/bin/bash", "-n", str(SOAK)])
        text = SOAK.read_text()
        for banned in ("declare -A", "mapfile", "readarray", "|&", "bootout"):
            self.assertNotIn(banned, text)
        self.assertNotIn(USERS_PREFIX, text)
        self.assertNotIn("8743", text)
        self.assertIn("com.mailroom.path-a-soak", text)
        self.assertIn("--continue-on-hang", text)
        self.assertIn("launchctl submit", text)
        self.assertIn("launchctl remove", text)

    def test_dry_run_start_is_four_hours(self):
        proc = self._run(SOAK, ["--dry-run", "start"])
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS dry-run soak-start", proc.stdout)
        self.assertIn("launchctl submit -l com.mailroom.path-a-soak", proc.stdout)
        self.assertIn("--continue-on-hang", proc.stdout)
        self.assertIn("--mem-at 24", proc.stdout)
        self.assertIn("-n 48", proc.stdout)
        self.assertIn("--interval 300", proc.stdout)
        self.assertIn("path_a_bench.py soak", proc.stdout)
        self.assertEqual(self._trace(), "")

    def test_start_submits_and_does_not_boot_out(self):
        log = self.tmp / "soak.log"
        out = self.tmp / "soak.jsonl"
        wd = self.tmp / "wd.log"
        proc = self._run(
            SOAK,
            [
                "--log",
                str(log),
                "--out",
                str(out),
                "--watchdog-log",
                str(wd),
                "--ask-mail",
                str(self.ask),
                "start",
            ],
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS soak-start label=com.mailroom.path-a-soak n=48 interval_s=300 mem_at=24", proc.stdout)
        self.assertIn("watchdog=left-loaded", proc.stdout)
        fields = self._trace().strip().split("\t")
        self.assertEqual(fields[0], "launchctl")
        self.assertEqual(fields[1], "submit")
        self.assertEqual(fields[2:4], ["-l", "com.mailroom.path-a-soak"])
        self.assertIn("-o", fields)
        self.assertIn("-e", fields)
        self.assertIn("--", fields)
        self.assertIn("soak", fields)
        self.assertIn("--continue-on-hang", fields)
        self.assertIn("--mem-at", fields)
        self.assertEqual(fields[fields.index("--mem-at") + 1], "24")
        self.assertEqual(fields[fields.index("-n") + 1], "48")
        self.assertEqual(fields[fields.index("--interval") + 1], "300")
        self.assertIn(str(wd), fields)
        self.assertIn(str(self.ask), fields)
        self.assertNotIn("bootout", fields)
        self.assertNotIn("8743", self._trace())
        self.assertTrue(log.parent.is_dir())

    def test_mem_at_past_n_is_usage(self):
        proc = self._run(SOAK, ["--n", "4", "start"])
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("mem-at greater than n", proc.stderr)
        self.assertEqual(self._trace(), "")

    def test_status_and_remove(self):
        proc = self._run(SOAK, ["status"])
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS status label=com.mailroom.path-a-soak", proc.stdout)
        self.assertIn("print\tgui/", self._trace())
        self.assertIn("com.mailroom.path-a-soak", self._trace())
        self.trace.write_text("")
        proc = self._run(SOAK, ["remove"])
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS remove label=com.mailroom.path-a-soak", proc.stdout)
        self.assertIn("remove\tcom.mailroom.path-a-soak", self._trace())
        proc = self._run(SOAK, ["--dry-run", "remove"])
        self.assertIn("dry-run: launchctl remove com.mailroom.path-a-soak", proc.stdout)

    def test_status_when_missing(self):
        (self.fake_root / "loaded").unlink()
        proc = self._run(SOAK, ["status"])
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("reason=not loaded", proc.stdout)


class BenchFlagTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ask = self.tmp / "ask_mail.py"
        self.ask.write_text("# synthetic\n")
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self._tmp.cleanup()

    def test_thresholds_match_the_ars(self):
        bench = _load_bench()
        self.assertEqual(bench.PROBE_TIMEOUT, 60.0)
        self.assertEqual(bench.COLD_WALL_MAX, 90.0)
        self.assertEqual(bench.WATCHDOG_WINDOW_S, 300.0)
        help_soak = subprocess.run(
            [sys.executable, str(BENCH), "soak", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_soak.returncode, 0)
        self.assertIn("--continue-on-hang", help_soak.stdout)
        self.assertIn("--mem-at", help_soak.stdout)
        self.assertIn("--watchdog-log", help_soak.stdout)
        help_cold = subprocess.run(
            [sys.executable, str(BENCH), "cold", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("--idle", help_cold.stdout)

    def test_cold_idle_zero_does_not_false_wedge(self):
        self.httpd = _serve()
        port = self.httpd.server_address[1]
        out = self.tmp / "cold.jsonl"
        proc = subprocess.run(
            [
                sys.executable,
                str(BENCH),
                "cold",
                "--idle",
                "0",
                "--timeout",
                "5",
                "--base-url",
                "http://127.0.0.1:%d" % port,
                "--ask-mail",
                str(self.ask),
                "--out",
                str(out),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertNotIn("WEDGE", proc.stdout + proc.stderr)
        self.assertIn("PASS cold_probe", proc.stdout)
        self.assertIn("PASS cold_request", proc.stdout)
        self.assertIn("limit_s=90.000", proc.stdout)
        self.assertEqual(len(self.httpd.posts), 2)
        probe = json.loads(self.httpd.posts[0].decode("utf-8"))
        full = json.loads(self.httpd.posts[1].decode("utf-8"))
        self.assertEqual(probe["max_tokens"], 1)
        self.assertGreater(full["max_tokens"], 1)

    def test_soak_continue_on_hang_and_mem_at(self):
        self.httpd = _serve()
        self.httpd.hang_first = True
        self.httpd.delay = 0.4
        port = self.httpd.server_address[1]
        log = self.tmp / "watchdog.log"
        log.write_text(
            LOGFMT.format_watchdog_line("2026-09-25 03:00:30", "ok latency=1.113314s")
            + LOGFMT.format_watchdog_line(
                "2026-09-25 03:01:00",
                "restart kickstart -k gui/1/com.mailroom.mlx-lm-server rc=0",
            )
        )
        out = self.tmp / "soak.jsonl"
        env = os.environ.copy()
        env["PATH_A_BENCH_TEST_HOOKS"] = "1"
        env["PATH_A_BENCH_NOW"] = "2026-09-25T03:00:00+00:00"
        env["PATH_A_BENCH_INJECT_SWAP_MB"] = "100"
        env["PATH_A_BENCH_OLLAMA_PROCESS"] = "down"
        env["PATH_A_BENCH_OLLAMA_PORT"] = "closed"
        proc = subprocess.run(
            [
                sys.executable,
                str(BENCH),
                "soak",
                "-n",
                "2",
                "--interval",
                "0",
                "--timeout",
                "0.2",
                "--continue-on-hang",
                "--watchdog-log",
                str(log),
                "--mem-at",
                "2",
                "--base-url",
                "http://127.0.0.1:%d" % port,
                "--ask-mail",
                str(self.ask),
                "--out",
                str(out),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertNotIn("WEDGE", proc.stdout + proc.stderr)
        self.assertIn("hung=1", proc.stdout)
        self.assertIn("next=pass watchdog=2026-09-25 03:01:00", proc.stdout)
        self.assertIn("PASS soak_mem_at k=2", proc.stdout)
        self.assertIn("OVERALL PASS", proc.stdout)
        self.assertEqual(len(self.httpd.posts), 2)


class RunbookTests(unittest.TestCase):
    def test_runbooks_cover_the_operator_contract(self):
        for path in (DOC_DRILL, DOC_COLD, DOC_SOAK):
            text = path.read_text()
            self.assertNotIn(USERS_PREFIX, text)
            self.assertNotIn(ICLOUD_MARK, text.lower())
            self.assertNotIn(ME_MARK, text.lower())
            lowered = text.lower()
            self.assertIn("precondition", lowered)
            self.assertIn("restore", lowered)
            self.assertIn("8743", text)
            self.assertIn("ask_mail.py", text)
            self.assertIn("hf-qwen-stage", text)
            self.assertIn("ollama", lowered)
            self.assertTrue("minute" in lowered or "hour" in lowered)
        drill = DOC_DRILL.read_text()
        self.assertIn("port-kill-hold", drill)
        self.assertIn("stop-hold", drill)
        self.assertIn("stop-cont", drill)
        self.assertIn("loop3", drill)
        self.assertIn("loop guard", drill)
        self.assertIn("kill -CONT", drill)
        self.assertIn("kill -STOP", drill)
        self.assertIn("kickstart -k", drill)
        self.assertIn("KeepAlive", drill)
        self.assertIn("recovered_by=launchd-keepalive", drill)
        self.assertIn("recovered_by=watchdog", drill)
        self.assertIn("plutil -extract KeepAlive raw", drill)
        cold = DOC_COLD.read_text()
        self.assertIn("--idle 1800", cold)
        self.assertIn("bootout", cold)
        self.assertIn("bootstrap", cold)
        soak = DOC_SOAK.read_text()
        self.assertIn("launchctl submit -l com.mailroom.path-a-soak", soak)
        self.assertIn("launchctl remove com.mailroom.path-a-soak", soak)
        self.assertIn("--continue-on-hang", soak)
        self.assertIn("--mem-at 24", soak)
        self.assertIn("48", soak)
        self.assertIn("300", soak)
        self.assertIn("5 minute", soak.lower())
        self.assertIn("restart kickstart", soak)
        self.assertIn("ok latency", soak)
        self.assertIn("[YYYY-MM-DD HH:MM:SS]", soak)


def _shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    unittest.main()
