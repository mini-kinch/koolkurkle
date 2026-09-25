#!/usr/bin/env python3
"""Offline tests for the Path A mlx-lm-server watchdog.

Temp HOME, a launchctl stub, and an ephemeral loopback HTTP server only.
Never calls real launchctl, never binds port 1234, never reads the real home.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP = ROOT / "scripts" / "qwen-chat-up.sh"
DOWN = ROOT / "scripts" / "qwen-chat-down.sh"
WATCHDOG = ROOT / "scripts" / "qwen-mlx-watchdog.sh"
PLIST = ROOT / "launchd" / "com.mailroom.qwen-watchdog.plist.template"
DOC = ROOT / "docs" / "path-a" / "watchdog.md"

# qwen-chat-down.sh is unchanged from the live import.
DOWN_SHA = "a21917f600199e241f92a101935557fba8518ac2fdb719e000db43f7144c8483"
SNAP = "10c35caafbb80f7dc6a7a432cdd11af10a6d4818"
LABEL = "com.mailroom.mlx-lm-server"

STUB = """#!/bin/bash
log="${STUB_ARGV_LOG:?}"
cmd="${1:-}"
{
  printf '%s' "$cmd"
  shift || true
  for a in "$@"; do
    printf '\\t%s' "$a"
  done
  printf '\\n'
} >> "$log"
if [ "$cmd" = "print" ]; then
  rc="${STUB_PRINT_RC:-0}"
  if [ "$rc" != "0" ]; then
    exit "$rc"
  fi
  if [ -n "${STUB_PRINT_FILE:-}" ] && [ -f "$STUB_PRINT_FILE" ]; then
    cat "$STUB_PRINT_FILE"
  fi
  exit 0
fi
if [ "$cmd" = "kickstart" ]; then
  exit "${STUB_KICKSTART_RC:-0}"
fi
exit 0
"""


def _pin(home: Path) -> Path:
    return (
        home
        / ".cache/huggingface/hub/models--mlx-community--Qwen3.8-27B-4bit/snapshots"
        / SNAP
    )


def _decoy(home: Path) -> Path:
    return home / ".cache/huggingface/hub/models--aaa-Qwen-decoy/snapshots/x"


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = "healthy"
        self.delay = 3.0
        self.paths: list[str] = []
        self.bodies: list[dict] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        # When set, every request appends one access-log line at start
        # (mlx_lm.server logs the POST when it starts, not during decode).
        self.access_log = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET", b"")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self._dispatch("POST", raw)

    def _touch_access_log(self) -> None:
        server: _Server = self.server  # type: ignore[assignment]
        path = server.access_log
        if path is None:
            return
        with open(path, "ab") as handle:
            handle.write(b"127.0.0.1 - - request\n")
        now = time.time()
        os.utime(path, (now, now))

    def _dispatch(self, method: str, raw: bytes) -> None:
        server: _Server = self.server  # type: ignore[assignment]
        path = self.path.split("?", 1)[0]
        # Access line is written when the request starts, before any hang.
        self._touch_access_log()
        with server.lock:
            server.paths.append(path)
            if method == "POST":
                try:
                    server.bodies.append(json.loads(raw.decode() or "{}"))
                except json.JSONDecodeError:
                    server.bodies.append({})
            mode = server.mode
            delay = server.delay
        hang = mode == "hang" or (mode == "chat_hang" and path.startswith("/v1/chat"))
        if hang:
            end = time.monotonic() + delay
            while time.monotonic() < end:
                if server.stop.is_set():
                    break
                time.sleep(0.05)
        if mode == "models_500" and path.startswith("/v1/models"):
            self._send(500, b"nope", "text/plain")
            return
        if mode in ("chat_fail", "models_ok_chat_fail") and path.startswith("/v1/chat"):
            self._send(500, b"nope", "text/plain")
            return
        if path.startswith("/v1/chat"):
            payload = json.dumps(
                {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}
            ).encode()
            self._send(200, payload)
            return
        if path.startswith("/v1/models"):
            payload = json.dumps({"object": "list", "data": [{"id": "pinned"}]}).encode()
            self._send(200, payload)
            return
        self._send(404, b"no", "text/plain")

    def _send(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return


class WatchdogBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.state = self.home / "qwen-mlx" / "watchdog"
        self.state.mkdir(parents=True)
        self.log = self.home / "MailArchive" / "logs" / "qwen-watchdog.log"
        self.hold = self.home / "qwen-mlx" / "HOLD"
        self.argv_log = Path(self._tmp.name) / "launchctl-argv.log"
        self.argv_log.write_text("")
        self.print_file = Path(self._tmp.name) / "print.txt"
        self.stub = Path(self._tmp.name) / "launchctl-stub.sh"
        self.stub.write_text(STUB)
        self.stub.chmod(0o755)
        self.tmp_dir = Path(self._tmp.name) / "tmp"
        self.tmp_dir.mkdir()
        self._write_pin()
        self._write_print(_pin(self.home))
        self.server = _Server()
        self.assertNotEqual(self.server.server_address[1], 1234)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.real_home = os.environ.get("HOME", "")
        self.assertNotEqual(str(self.home), self.real_home)

    def tearDown(self) -> None:
        self.server.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        self._tmp.cleanup()

    def _write_pin(self, incomplete: bool = False) -> None:
        snap = _pin(self.home)
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "model.safetensors").write_bytes(b"not-a-real-weight")
        if incomplete:
            blob = snap.parent.parent / "blobs"
            blob.mkdir(parents=True, exist_ok=True)
            (blob / "partial.incomplete").write_bytes(b"partial")

    def _write_print(self, model: Path) -> None:
        lines = [
            "com.mailroom.mlx-lm-server = {",
            "\tstate = running",
            "\targuments = {",
            "\t\t/opt/qwen-mlx/bin/python",
            "\t\t-m",
            "\t\tmlx_lm.server",
            "\t\t--model",
            "\t\t" + str(model),
            "\t\t--host",
            "\t\t127.0.0.1",
            "\t\t--port",
            "\t\t1234",
            "\t}",
            "}",
            "",
        ]
        self.print_file.write_text("\n".join(lines))

    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "TMPDIR": str(self.tmp_dir),
                "WD_HOST": "127.0.0.1",
                "WD_PORT": str(self.server.server_address[1]),
                "WD_LABEL": LABEL,
                "WD_HOLD": str(self.hold),
                "WD_STATE_DIR": str(self.state),
                "WD_LOG": str(self.log),
                "WD_LAUNCHCTL": str(self.stub),
                "WD_CURL": "curl",
                "STUB_ARGV_LOG": str(self.argv_log),
                "STUB_PRINT_FILE": str(self.print_file),
                "STUB_PRINT_RC": "0",
                "STUB_KICKSTART_RC": "0",
            }
        )
        env.pop("WD_MODEL_PATH", None)
        env.update(extra)
        self.assertNotEqual(env["WD_PORT"], "1234")
        self.assertNotEqual(env["HOME"], self.real_home)
        return env

    def _run(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(WATCHDOG)],
            env=self._env(**extra),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def _argv(self) -> list[list[str]]:
        rows = []
        for line in self.argv_log.read_text().splitlines():
            if line:
                rows.append(line.split("\t"))
        return rows

    def _kickstarts(self) -> list[list[str]]:
        return [row for row in self._argv() if row and row[0] == "kickstart"]

    def _log(self) -> str:
        if not self.log.is_file():
            return ""
        return self.log.read_text()

    def _fails(self) -> str:
        path = self.state / "consecutive_fails"
        if not path.is_file():
            return ""
        return path.read_text().strip()

    def _assert_only_mlx_label(self) -> None:
        uid = os.getuid()
        target = "gui/%s/%s" % (uid, LABEL)
        for row in self._argv():
            self.assertIn(row[0], ("print", "kickstart"))
            self.assertNotIn("ask-mail-serve", "\t".join(row))
            if row[0] == "print":
                self.assertEqual(row, ["print", target])
            if row[0] == "kickstart":
                self.assertEqual(row, ["kickstart", "-k", target])

    def test_healthy_resets_counter_and_does_not_restart(self) -> None:
        (self.state / "consecutive_fails").write_text("4\n")
        started = time.monotonic()
        proc = self._run()
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._fails(), "0")
        self.assertEqual(self._kickstarts(), [])
        self.assertIn("ok", self._log())
        self.assertIn("latency=", self._log())
        self.assertEqual(
            self.server.bodies,
            [
                {
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                }
            ],
        )
        self.assertEqual(self.server.paths, ["/v1/models", "/v1/chat/completions"])
        self._assert_only_mlx_label()

    def test_one_fail_then_second_fail_kickstarts_mlx_only(self) -> None:
        self.server.mode = "models_500"
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(self.server.paths, ["/v1/models"])
        second = self._run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("fail consecutive=2", self._log())
        self.assertEqual(len(self._kickstarts()), 1)
        self._assert_only_mlx_label()
        joined = self.argv_log.read_text()
        self.assertNotIn("ask-mail-serve", joined)
        self.assertNotIn(":8743", joined)
        self.assertEqual(self._fails(), "0")

    def test_hold_skips_probe_and_kickstart(self) -> None:
        self.hold.parent.mkdir(parents=True, exist_ok=True)
        self.hold.write_text("pause\n")
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("hold", self._log())
        self.assertEqual(self.server.paths, [])
        self.assertEqual(self._argv(), [])
        self.assertEqual(self._kickstarts(), [])

    def test_agent_not_loaded_skips_and_resets_counter(self) -> None:
        (self.state / "consecutive_fails").write_text("4\n")
        proc = self._run(STUB_PRINT_RC="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("agent not loaded, skip", self._log())
        self.assertEqual(self._fails(), "0")
        self.assertEqual(self.server.paths, [])
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(self._argv()[0][0], "print")

    def test_grace_skips_probe(self) -> None:
        (self.state / "last_restart").write_text("%s\n" % int(time.time()))
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("grace", self._log())
        self.assertEqual(self.server.paths, [])
        self.assertEqual(self._kickstarts(), [])

    def test_loop_guard_writes_hold_and_does_not_restart(self) -> None:
        now = int(time.time())
        epochs = [now - 900, now - 800, now - 700]
        (self.state / "restarts").write_text("".join("%s\n" % item for item in epochs))
        (self.state / "last_restart").write_text("%s\n" % epochs[-1])
        self.server.mode = "models_500"
        self._run()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._kickstarts(), [])
        self.assertTrue(self.hold.is_file())
        self.assertEqual(self.hold.read_text().strip(), "loop guard: HOLD written")
        self.assertIn("loop guard: HOLD written", self._log())
        self.assertEqual(
            [int(line) for line in (self.state / "restarts").read_text().split()],
            epochs,
        )

    def test_lock_prevents_concurrent_run(self) -> None:
        lock = self.state / "lock"
        lock.mkdir()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("lock busy, skip", self._log())
        self.assertEqual(self.server.paths, [])
        self.assertEqual(self._kickstarts(), [])
        self.assertTrue(lock.is_dir())

    def test_stale_lock_is_removed(self) -> None:
        lock = self.state / "lock"
        lock.mkdir()
        old = time.time() - 1000
        os.utime(lock, (old, old))
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("stale lock removed", self._log())
        self.assertIn("ok", self._log())
        self.assertFalse(lock.exists())
        self.assertEqual(self._kickstarts(), [])

    def test_decoy_hub_dir_is_never_used(self) -> None:
        decoy = _decoy(self.home)
        decoy.mkdir(parents=True)
        (decoy / "model.safetensors").write_bytes(b"decoy-weight")
        blobs = decoy.parent.parent / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        (blobs / "partial.incomplete").write_bytes(b"partial")
        self.server.mode = "models_500"
        self._run()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self._kickstarts()), 1)
        self._assert_only_mlx_label()
        blob = self.argv_log.read_text() + self._log()
        self.assertNotIn("decoy", blob)
        self.assertNotIn("models--aaa-Qwen-decoy", blob)
        self.assertNotIn("ask-mail-serve", blob)

    def test_pin_missing_writes_hold(self) -> None:
        shutil.rmtree(self.home / ".cache")
        self.server.mode = "models_500"
        self._run()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(self.hold.read_text().strip(), "pin: model path missing")
        self.assertIn("pin: model path missing", self._log())

    def test_incomplete_snapshot_writes_hold(self) -> None:
        self._write_pin(incomplete=True)
        self.server.mode = "models_500"
        self._run()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(self.hold.read_text().strip(), "pin: model path missing")

    def test_loaded_model_mismatch_writes_hold(self) -> None:
        self._write_print(_decoy(self.home))
        self.server.mode = "models_500"
        self._run()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(
            self.hold.read_text().strip(),
            "pin: loaded agent model != pinned path",
        )
        self.assertIn("pin: loaded agent model != pinned path", self._log())
        self.assertNotIn("ask-mail-serve", self.argv_log.read_text())

    def test_hang_past_timeout_counts_as_one_failure(self) -> None:
        self.server.mode = "chat_hang"
        self.server.delay = 8.0
        started = time.monotonic()
        proc = self._run(WD_MODELS_TIMEOUT="2", WD_PROBE_TIMEOUT="1")
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(elapsed, 5.0)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self._kickstarts(), [])
        self.assertIn("/v1/models", self.server.paths)
        self.assertIn("/v1/chat/completions", self.server.paths)

    def test_models_hang_does_not_post_chat(self) -> None:
        self.server.mode = "hang"
        self.server.delay = 8.0
        started = time.monotonic()
        proc = self._run(WD_MODELS_TIMEOUT="1", WD_PROBE_TIMEOUT="1")
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(elapsed, 4.0)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self.server.paths, ["/v1/models"])
        self.assertEqual(self._kickstarts(), [])

    def _server_log(self, age_s: float) -> Path:
        path = Path(self._tmp.name) / "mlx_lm_server_1234.log"
        path.write_bytes(b"completed-request\n")
        when = time.time() - age_s
        os.utime(path, (when, when))
        return path

    def _posts(self) -> list[str]:
        return [path for path in self.server.paths if path.startswith("/v1/chat")]

    def test_recent_log_skips_chat_and_resets_counter(self) -> None:
        log_path = self._server_log(5)
        (self.state / "consecutive_fails").write_text("4\n")
        proc = self._run(WD_SERVER_LOG=str(log_path), WD_QUIET_SECS="900")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._posts(), [])
        self.assertEqual(self.server.bodies, [])
        self.assertEqual(self.server.paths, ["/v1/models"])
        self.assertEqual(self._fails(), "0")
        self.assertEqual(self._kickstarts(), [])
        self.assertIn("ok models-only (active ", self._log())
        self.assertIn("s ago)", self._log())
        self.assertNotIn("latency=", self._log())

    def test_old_log_runs_chat_probe(self) -> None:
        log_path = self._server_log(1200)
        proc = self._run(WD_SERVER_LOG=str(log_path), WD_QUIET_SECS="900")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._posts(), ["/v1/chat/completions"])
        self.assertEqual(len(self.server.bodies), 1)
        self.assertIn("latency=", self._log())
        self.assertNotIn("models-only", self._log())
        self.assertEqual(self._kickstarts(), [])

    def test_old_log_hanging_chat_two_passes_kickstart(self) -> None:
        log_path = self._server_log(1200)
        self.server.mode = "chat_hang"
        self.server.delay = 8.0
        extra = {
            "WD_SERVER_LOG": str(log_path),
            "WD_QUIET_SECS": "900",
            "WD_MODELS_TIMEOUT": "2",
            "WD_PROBE_TIMEOUT": "1",
        }
        first = self._run(**extra)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self._kickstarts(), [])
        self.assertGreaterEqual(len(self._posts()), 1)
        self._server_log(1200)
        second = self._run(**extra)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self._kickstarts()), 1)
        self._assert_only_mlx_label()

    def test_models_500_with_recent_log_counts_as_fail(self) -> None:
        log_path = self._server_log(3)
        self.server.mode = "models_500"
        proc = self._run(WD_SERVER_LOG=str(log_path), WD_QUIET_SECS="900")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(self._posts(), [])
        self.assertNotIn("models-only", self._log())
        self.assertIn("fail consecutive=1", self._log())

    def test_own_requests_do_not_refresh_quiet_window(self) -> None:
        # Consecutive passes whose only log writes are the watchdog's own
        # GETs stay models-only until last_activity is older than the quiet
        # window. The next pass then runs the chat probe.
        log_path = self._server_log(0)
        self.server.access_log = log_path
        extra = {"WD_SERVER_LOG": str(log_path), "WD_QUIET_SECS": "900"}
        first = self._run(**extra)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._posts(), [])
        activity = (self.state / "last_activity").read_text().strip()
        self.assertTrue(activity.isdigit())
        second = self._run(**extra)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self._posts(), [])
        self.assertEqual((self.state / "last_activity").read_text().strip(), activity)
        self.assertIn("models-only", self._log())
        quiet_epoch = int(time.time()) - 1000
        (self.state / "last_activity").write_text("%s\n" % quiet_epoch)
        os.utime(
            self.state / "last_activity",
            (quiet_epoch, quiet_epoch),
        )
        third = self._run(**extra)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(self._posts(), ["/v1/chat/completions"])
        self.assertIn("latency=", self._log())
        self.assertEqual(self._kickstarts(), [])

    def test_external_write_between_passes_stays_models_only(self) -> None:
        log_path = self._server_log(0)
        self.server.access_log = log_path
        extra = {"WD_SERVER_LOG": str(log_path), "WD_QUIET_SECS": "900"}
        first = self._run(**extra)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._posts(), [])
        # Quiet would be true if the only writer were the watchdog.
        old = int(time.time()) - 2000
        (self.state / "last_activity").write_text("%s\n" % old)
        stored = int((self.state / "self_log_mtime").read_text().strip())
        external = time.time()
        if int(external) == stored:
            external = float(stored + 1)
        os.utime(log_path, (external, external))
        self.assertNotEqual(int(log_path.stat().st_mtime), stored)
        second = self._run(**extra)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self._posts(), [])
        self.assertIn("ok models-only (active ", self._log())
        self.assertEqual(self._kickstarts(), [])

    def test_self_touches_only_hung_chat_two_passes_kickstart(self) -> None:
        # Regression: the access log is touched only by the watchdog's own
        # models GET and chat POST. Two quiet passes still kickstart.
        log_path = self._server_log(1200)
        self.server.access_log = log_path
        self.server.mode = "chat_hang"
        self.server.delay = 8.0
        extra = {
            "WD_SERVER_LOG": str(log_path),
            "WD_QUIET_SECS": "900",
            "WD_MODELS_TIMEOUT": "2",
            "WD_PROBE_TIMEOUT": "1",
        }
        first = self._run(**extra)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self._kickstarts(), [])
        self.assertGreaterEqual(len(self._posts()), 1)
        now = time.time()
        self.assertLess(now - log_path.stat().st_mtime, 30)
        last = int((self.state / "last_activity").read_text().strip())
        self.assertGreaterEqual(now - last, 900)
        recorded = int((self.state / "self_log_mtime").read_text().strip())
        self.assertEqual(recorded, int(log_path.stat().st_mtime))
        second = self._run(**extra)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertGreaterEqual(len(self._posts()), 2)
        self.assertEqual(len(self._kickstarts()), 1)
        self._assert_only_mlx_label()

    def test_models_ok_chat_fail(self) -> None:
        self.server.mode = "models_ok_chat_fail"
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._fails(), "1")
        self.assertEqual(self._kickstarts(), [])
        self.assertEqual(self.server.paths, ["/v1/models", "/v1/chat/completions"])
        self.assertEqual(
            self.server.bodies,
            [
                {
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                }
            ],
        )


class WatchdogStaticTests(unittest.TestCase):
    def test_bash_n(self) -> None:
        for path in (UP, DOWN, WATCHDOG):
            proc = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_watchdog_script_forbidden_tokens(self) -> None:
        text = WATCHDOG.read_text()
        for token in (
            "ollama",
            "qwen-chat-up",
            "bootout",
            "bootstrap",
            "ask-mail-serve",
            "sqlite",
            "nohup",
            "models--*",
            "head -1",
        ):
            self.assertNotIn(token, text, token)
        for line in text.splitlines():
            code = line.split("#", 1)[0].rstrip()
            if not code:
                continue
            self.assertFalse(
                code.endswith("&") and not code.endswith("&&"),
                "backgrounding: %s" % line,
            )

    def test_up_keeps_hold_thinking_and_cache(self) -> None:
        text = UP.read_text()
        self.assertNotIn("models--*", text)
        self.assertNotIn("ls -d", text)
        self.assertNotIn("snapshots/*", text)
        self.assertIn(SNAP, text)
        self.assertIn("(CoS 2026-09-24)", text)
        thinking = (
            "    <string>--chat-template-args</string>\n"
            '    <string>{"enable_thinking":false}</string>\n'
        )
        cache = (
            "    <string>--prompt-cache-size</string>\n"
            "    <string>1</string>\n"
        )
        self.assertIn(thinking + cache, text)
        hold_rm = 'rm -f "$HOME/qwen-mlx/HOLD"\n'
        hold_log = "echo \"[$(date '+%Y-%m-%d %H:%M:%S')] cleared watchdog HOLD\"\n"
        self.assertEqual(text.count(hold_rm), 1)
        self.assertEqual(text.count(hold_log), 1)
        self.assertEqual(text.count(cache), 1)
        self.assertEqual(hashlib.sha256(DOWN.read_bytes()).hexdigest(), DOWN_SHA)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/Users/", WATCHDOG.read_text())

    def test_plist_template(self) -> None:
        raw = PLIST.read_text()
        self.assertNotIn("/Users/", raw)
        self.assertIn("__HOME__", raw)
        rendered = raw.replace("__HOME__", "/tmp/example-home")
        data = plistlib.loads(rendered.encode())
        self.assertEqual(data["Label"], "com.mailroom.qwen-watchdog")
        self.assertEqual(data["StartInterval"], 300)
        self.assertIs(data["RunAtLoad"], False)
        self.assertNotIn("KeepAlive", data)
        self.assertEqual(
            data["ProgramArguments"],
            [
                "/bin/bash",
                "/tmp/example-home/MailArchive/scripts/qwen-mlx-watchdog.sh",
            ],
        )
        self.assertEqual(
            data["StandardOutPath"],
            "/tmp/example-home/MailArchive/logs/qwen-watchdog.launchd.log",
        )
        self.assertEqual(data["StandardErrorPath"], data["StandardOutPath"])

    def test_docs_pin_install_and_rollback(self) -> None:
        text = DOC.read_text()
        self.assertNotIn("/Users/", text)
        self.assertIn("Install (separate user approval)", text)
        self.assertIn(
            "the cache flag only takes effect the next time qwen-chat-up.sh is run, "
            "which restarts mlx_lm.server and bounces retrieval; the watchdog plist "
            "does nothing until someone runs launchctl bootstrap on it",
            text,
        )
        self.assertIn(
            "only when up.sh next runs, since up.sh regenerates it",
            text,
        )
        self.assertIn("~/Library/LaunchAgents", text)
        self.assertIn("pin: model path missing", text)
        self.assertIn("pin: loaded agent model != pinned path", text)
        self.assertIn(SNAP, text)
        self.assertIn(":8743", text)
        self.assertIn("does not bounce retrieval", text.lower())
        self.assertIn("loop guard: HOLD written", text)
        self.assertIn("## Prompt cache interaction", text)
        self.assertIn("WD_SERVER_LOG", text)
        self.assertIn("WD_QUIET_SECS", text)
        self.assertIn("ok models-only (active <N>s ago)", text)
        self.assertIn("self_log_mtime", text)
        self.assertIn("last_activity", text)
        self.assertIn(
            "quiet window + up to 1 pass + probe timeout + 1 pass + probe timeout",
            text,
        )
        self.assertIn("qwen-chat-up.sh", text)
        for token in ("bootout", "HOLD", "watchdog state"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
