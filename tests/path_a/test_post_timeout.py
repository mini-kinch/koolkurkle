#!/usr/bin/env python3
"""Offline timeout tests for scripts/qwen_paste_chat_post.py.

Slow fake server → exit 3 + restart hint + empty stdout.
Happy server → exit 0 + assistant content on stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "qwen_paste_chat_post.py")

HINT = (
    "hint: /v1/models may be up but generate is wedged — run qwen-chat-down.sh "
    "then qwen-chat-up.sh, confirm a tiny chat probe, then retry "
    "(see docs/path-a/mini-install.md)"
)

PASTE = "DATA\nline\n\nQUESTION\nping\n"


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, delay: float, status: int = 200, content: str = "path-a-ok"):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.delay = delay
        self.status = status
        self.content = content
        self.bodies: list[dict] = []
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            parsed = {}
        server: _Server = self.server  # type: ignore[assignment]
        with server.lock:
            server.bodies.append(parsed)
            delay = server.delay
            status = server.status
            content = server.content
        if delay:
            time.sleep(delay)
        if status != 200:
            payload = b"nope"
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._write(payload)
            return
        payload = json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "reasoning": None,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self._write(payload)

    def _write(self, payload: bytes) -> None:
        try:
            self.wfile.write(payload)
        except OSError:
            return

    def log_message(self, fmt: str, *args) -> None:
        return


class _Running:
    def __init__(self, server: _Server):
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self.server

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _run(
    base: str,
    env_extra: dict[str, str],
    stdin_text: str = PASTE,
    subprocess_timeout: float = 8,
    extra_args: list[str] | None = None,
):
    env = os.environ.copy()
    for key in ("QWEN_TIMEOUT", "QWEN_PROBE", "QWEN_PROBE_TIMEOUT"):
        env.pop(key, None)
    env.update(env_extra)
    cmd = [
        sys.executable,
        SCRIPT,
        "--base",
        base,
        "--model",
        "test-model",
        "--max-tokens",
        "32",
    ]
    if extra_args:
        cmd.extend(extra_args)
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        input=stdin_text,
        text=True,
        capture_output=True,
        env=env,
        timeout=subprocess_timeout,
    )
    elapsed = time.monotonic() - started
    return proc, elapsed


class PostTimeoutTests(unittest.TestCase):
    def test_slow_server_probe_exits_3_with_hint(self) -> None:
        probe_timeout = 1
        slack = 3
        with _Running(_Server(delay=8)) as server:
            base = "http://127.0.0.1:%s/v1" % server.server_address[1]
            proc, elapsed = _run(
                base,
                {
                    "QWEN_PROBE": "1",
                    "QWEN_PROBE_TIMEOUT": str(probe_timeout),
                    "QWEN_TIMEOUT": "30",
                },
            )
            with server.lock:
                bodies = list(server.bodies)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertIn(HINT, proc.stderr)
        self.assertIn("probe=on", proc.stderr)
        self.assertIn("probe_timeout=%s" % probe_timeout, proc.stderr)
        self.assertIn("qwen_paste_chat_post.py v4", proc.stderr)
        self.assertGreaterEqual(elapsed, probe_timeout * 0.5)
        self.assertLess(elapsed, probe_timeout + slack)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0].get("max_tokens"), 1)
        self.assertEqual(
            bodies[0].get("chat_template_kwargs"),
            {"enable_thinking": False},
        )

    def test_probe_off_main_timeout_exits_3_with_hint(self) -> None:
        main_timeout = 1
        slack = 3
        with _Running(_Server(delay=8)) as server:
            base = "http://127.0.0.1:%s/v1" % server.server_address[1]
            proc, elapsed = _run(
                base,
                {
                    "QWEN_PROBE": "0",
                    "QWEN_PROBE_TIMEOUT": "30",
                    "QWEN_TIMEOUT": str(main_timeout),
                },
            )
            with server.lock:
                bodies = list(server.bodies)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertIn(HINT, proc.stderr)
        self.assertIn("probe=off", proc.stderr)
        self.assertIn("timeout=%s" % main_timeout, proc.stderr)
        self.assertGreaterEqual(elapsed, main_timeout * 0.5)
        self.assertLess(elapsed, main_timeout + slack)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0].get("max_tokens"), 32)
        user = bodies[0]["messages"][1]["content"]
        self.assertIn("/no_think", user)
        self.assertEqual(
            bodies[0].get("chat_template_kwargs"),
            {"enable_thinking": False},
        )

    def test_happy_server_exits_0_with_content(self) -> None:
        with _Running(_Server(delay=0, content="path-a-ok")) as server:
            base = "http://127.0.0.1:%s/v1" % server.server_address[1]
            proc, elapsed = _run(
                base,
                {
                    "QWEN_PROBE": "1",
                    "QWEN_PROBE_TIMEOUT": "5",
                    "QWEN_TIMEOUT": "5",
                },
            )
            with server.lock:
                bodies = list(server.bodies)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "path-a-ok\n")
        self.assertNotIn(HINT, proc.stderr)
        self.assertLess(elapsed, 5)
        self.assertGreaterEqual(len(bodies), 2)
        self.assertEqual(bodies[0].get("max_tokens"), 1)
        self.assertEqual(bodies[1].get("max_tokens"), 32)
        self.assertIn("/no_think", bodies[1]["messages"][1]["content"])
        self.assertEqual(
            bodies[1].get("chat_template_kwargs"),
            {"enable_thinking": False},
        )

    def test_empty_paste_exits_2_without_hint(self) -> None:
        proc, _elapsed = _run(
            "http://127.0.0.1:9/v1",
            {"QWEN_PROBE": "1", "QWEN_PROBE_TIMEOUT": "1", "QWEN_TIMEOUT": "1"},
            stdin_text="  \n",
            subprocess_timeout=3,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("empty paste", proc.stderr)
        self.assertNotIn(HINT, proc.stderr)

    def test_http_error_without_probe_exits_2(self) -> None:
        with _Running(_Server(delay=0, status=500)) as server:
            base = "http://127.0.0.1:%s/v1" % server.server_address[1]
            proc, _elapsed = _run(
                base,
                {"QWEN_PROBE": "0", "QWEN_TIMEOUT": "5", "QWEN_PROBE_TIMEOUT": "5"},
            )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("HTTP 500", proc.stderr)
        self.assertNotIn(HINT, proc.stderr)

    def test_default_main_timeout_is_180(self) -> None:
        with _Running(_Server(delay=0, content="path-a-ok")) as server:
            base = "http://127.0.0.1:%s/v1" % server.server_address[1]
            proc, _elapsed = _run(
                base,
                {"QWEN_PROBE": "0", "QWEN_PROBE_TIMEOUT": "5"},
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("timeout=180", proc.stderr)
        self.assertEqual(proc.stdout, "path-a-ok\n")

    def test_cli_timeout_overrides_env(self) -> None:
        with _Running(_Server(delay=0, content="path-a-ok")) as server:
            base = "http://127.0.0.1:%s/v1" % server.server_address[1]
            proc, _elapsed = _run(
                base,
                {"QWEN_PROBE": "0", "QWEN_TIMEOUT": "180", "QWEN_PROBE_TIMEOUT": "5"},
                extra_args=["--timeout", "7"],
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("timeout=7", proc.stderr)
        self.assertNotIn("timeout=180", proc.stderr)


if __name__ == "__main__":
    unittest.main()
