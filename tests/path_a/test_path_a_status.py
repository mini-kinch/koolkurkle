#!/usr/bin/env python3
"""Offline tests for scripts/path_a_status.sh.

Temp MAILARCHIVE fixtures only. Loopback HTTP stubs. No live mail, sqlite,
Keychain, or absolute home paths in assertions.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "path_a_status.sh"

PATH_A = (
    "ask_mail_wire.sh",
    "ask_mail_paste.sh",
    "qwen_paste_chat.sh",
    "qwen_paste_chat_post.py",
    "ask_mail_paste_fmt.py",
)
K_FILES = (
    "ask_mail_paste.sh",
    "ask_mail_wire.sh",
    "qwen_paste_chat.sh",
)
SENTINEL = "SENTINEL_DO_NOT_PRINT_9f3a"
WEDGED = "generate wedged: qwen-chat-down.sh then qwen-chat-up.sh"
ASK_MIN = 10240


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _k_line(k: int) -> str:
    return 'export ASK_MAIL_PASTE_K="${ASK_MAIL_PASTE_K:-%d}"\n' % k


def _write_fixture(
    root: Path,
    k: int = 20,
    ask_bytes: int = 64,
    executable: bool = True,
    omit: str = "",
    k_body: str | None = None,
) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in PATH_A:
        if name == omit:
            continue
        if name in K_FILES:
            body = k_body if k_body is not None else _k_line(k)
            text = "# fixture\n# old k=8 under-packed\n# %s\n%s" % (SENTINEL, body)
        else:
            text = "# fixture helper\n# %s\n" % SENTINEL
        path = scripts / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
    ask = scripts / "ask_mail.py"
    marker = ("# %s\n" % SENTINEL).encode("utf-8")
    if ask_bytes <= len(marker):
        payload = b"x" * ask_bytes
    else:
        payload = marker + (b"x" * (ask_bytes - len(marker)))
    ask.write_bytes(payload)
    ask.chmod(0o644)
    return ask


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, b'{"ok":true}')
        elif path == "/v1/models":
            model = getattr(self.server, "model_id", "fixture-model")
            raw = json.dumps({"data": [{"id": model}]}).encode("utf-8")
            self._send(200, raw)
        elif path == "/api/tags":
            self._send(200, b'{"models":[]}')
        else:
            self._send(404, b"{}")

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n else b""
        self.server.posts.append(raw)
        delay = getattr(self.server, "delay", 0)
        if delay:
            import time

            time.sleep(delay)
        self._send(200, b'{"choices":[{"message":{"content":"x"}}]}')


class _Server(ThreadingHTTPServer):
    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.posts = []
        self.delay = 0
        self.model_id = "fixture-model"


def _serve(delay: float = 0, model_id: str = "fixture-model") -> _Server:
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.delay = delay
    httpd.model_id = model_id
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _stop(httpd: _Server | None) -> None:
    if httpd is None:
        return
    httpd.shutdown()
    httpd.server_close()


class PathAStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.servers: list[_Server] = []
        self.addCleanup(self._stop_servers)

    def _mkdtemp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="path-a-status-")

    def _stop_servers(self) -> None:
        for httpd in self.servers:
            _stop(httpd)
        self.servers.clear()

    def _env(self, mail: Path, **urls: str) -> dict[str, str]:
        env = os.environ.copy()
        env["MAILARCHIVE"] = str(mail)
        env["RETRIEVE_URL"] = urls.get("retrieve", "http://127.0.0.1:%d" % _closed_port())
        env["QWEN_BASE_URL"] = urls.get("qwen", "http://127.0.0.1:%d/v1" % _closed_port())
        env["OLLAMA_BASE_URL"] = urls.get("ollama", "http://127.0.0.1:%d" % _closed_port())
        env["QWEN_PROBE_TIMEOUT"] = urls.get("timeout", "2")
        return env

    def _run(
        self,
        mail: Path,
        args: list[str],
        shell: str = "bash",
        env: dict[str, str] | None = None,
        timeout: int = 15,
    ) -> subprocess.CompletedProcess[str]:
        before = {p.relative_to(mail).as_posix() for p in mail.rglob("*")}
        ask = mail / "scripts" / "ask_mail.py"
        ask_before = ask.read_bytes() if ask.is_file() else None
        proc = subprocess.run(
            [shell, str(SCRIPT), *args],
            cwd=str(ROOT),
            env=env if env is not None else self._env(mail),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        after = {p.relative_to(mail).as_posix() for p in mail.rglob("*")}
        self.assertEqual(before, after, msg="status script wrote under MAILARCHIVE")
        if ask_before is not None and ask.is_file():
            self.assertEqual(ask.read_bytes(), ask_before)
        blob = proc.stdout + proc.stderr
        self.assertNotIn(SENTINEL, blob)
        self.assertNotIn(str(mail), blob)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("/home/", blob)
        return proc

    def _line(self, stdout: str, name: str) -> str:
        for line in stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[1] == name:
                return line
        self.fail("missing check %s in:\n%s" % (name, stdout))
        return ""

    def test_script_is_read_only_source(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("set -u", text)
        self.assertIn(WEDGED, text)
        self.assertIn('"max_tokens": 1', text)
        self.assertIn('"enable_thinking": False', text)
        self.assertNotIn("/Users/", text)
        for banned in ("mkdir", "rm ", "cp ", "mv ", "tee ", "launchctl", "ask_mail.py "):
            self.assertNotIn(banned, text)

    def test_closed_ports_no_probe_stub_warns_and_exits_1(self) -> None:
        mail = self.tmp / "good-stub"
        ask = _write_fixture(mail, k=20, ask_bytes=32)
        digest = hashlib.sha256(ask.read_bytes()).hexdigest()
        proc = self._run(mail, ["--no-probe"])
        out = proc.stdout
        self.assertEqual(proc.returncode, 1, msg=out + proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertIn("FAIL retrieve_health /health unreachable", out)
        self.assertIn("FAIL mlx_models /v1/models unreachable", out)
        self.assertNotIn("mlx_probe", out)
        self.assertNotIn(WEDGED, out)
        self.assertIn("PASS ollama down", out)
        self.assertIn("PASS path_a_files present+executable", out)
        self.assertIn("PASS paste_k default 20", out)
        warn = self._line(out, "ask_mail_py")
        self.assertTrue(warn.startswith("WARN ask_mail_py "))
        self.assertIn("size=%d" % ask.stat().st_size, warn)
        self.assertIn("sha256=%s" % digest[:12], warn)
        self.assertNotIn(digest, warn)
        self.assertIn("<10KB possible MCP stub", warn)
        self.assertIn("SUMMARY pass=3 warn=1 fail=2", out)

    def test_closed_ports_probe_fails_without_wedged_hint(self) -> None:
        mail = self.tmp / "probe-down"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        proc = self._run(mail, [])
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("FAIL mlx_probe chat probe failed", proc.stdout)
        self.assertNotIn(WEDGED, proc.stdout)
        self.assertIn("PASS ask_mail_py size=%d" % ASK_MIN, proc.stdout)
        self.assertNotIn("WARN ask_mail_py", proc.stdout)

    def test_k8_fails_before_other_k_problems(self) -> None:
        mail = self.tmp / "k8"
        _write_fixture(mail, k=8, ask_bytes=ASK_MIN)
        proc = self._run(mail, ["--no-probe"])
        self.assertEqual(proc.returncode, 1)
        line = self._line(proc.stdout, "paste_k")
        self.assertTrue(line.startswith("FAIL paste_k "))
        self.assertIn("scripts/ask_mail_paste.sh default 8", line)
        self.assertIn("scripts/ask_mail_wire.sh default 8", line)
        self.assertIn("scripts/qwen_paste_chat.sh default 8", line)
        self.assertLess(line.find("default 8"), line.find("default not 20") if "default not 20" in line else len(line))

    def test_k8_token_in_comment_fails_even_if_code_says_20(self) -> None:
        mail = self.tmp / "k8-comment"
        body = "# note ASK_MAIL_PASTE_K:-8\n" + _k_line(20)
        _write_fixture(mail, ask_bytes=ASK_MIN, k_body=body)
        proc = self._run(mail, ["--no-probe"])
        line = self._line(proc.stdout, "paste_k")
        self.assertTrue(line.startswith("FAIL paste_k "))
        self.assertIn("default 8", line)

    def test_old_k8_prose_with_default_20_passes(self) -> None:
        mail = self.tmp / "k20-prose"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        proc = self._run(mail, ["--no-probe"])
        self.assertIn("PASS paste_k default 20", proc.stdout)

    def test_quoted_k20_passes_and_k80_is_not_8(self) -> None:
        mail = self.tmp / "k20-quoted"
        _write_fixture(
            mail,
            ask_bytes=ASK_MIN,
            k_body="K=\"${ASK_MAIL_PASTE_K:-'20'}\"\n",
        )
        proc = self._run(mail, ["--no-probe"])
        self.assertIn("PASS paste_k default 20", proc.stdout)

        mail80 = self.tmp / "k80"
        _write_fixture(mail80, ask_bytes=ASK_MIN, k_body=_k_line(80))
        proc80 = self._run(mail80, ["--no-probe"])
        line = self._line(proc80.stdout, "paste_k")
        self.assertTrue(line.startswith("FAIL paste_k "))
        self.assertIn("default not 20", line)
        self.assertNotIn("default 8", line)

    def test_missing_and_not_executable(self) -> None:
        mail = self.tmp / "files"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN, executable=False, omit="ask_mail_paste_fmt.py")
        proc = self._run(mail, ["--no-probe"])
        line = self._line(proc.stdout, "path_a_files")
        self.assertTrue(line.startswith("FAIL path_a_files "))
        self.assertIn("missing:scripts/ask_mail_paste_fmt.py", line)
        self.assertIn("not_executable:scripts/ask_mail_wire.sh", line)
        self.assertNotIn(str(mail), line)

    def test_missing_ask_mail_fails(self) -> None:
        mail = self.tmp / "no-ask"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        (mail / "scripts" / "ask_mail.py").unlink()
        proc = self._run(mail, ["--no-probe"])
        self.assertIn("FAIL ask_mail_py missing scripts/ask_mail.py", proc.stdout)
        self.assertEqual(proc.returncode, 1)

    def test_boundary_10kb(self) -> None:
        small = self.tmp / "boundary-small"
        _write_fixture(small, k=20, ask_bytes=ASK_MIN - 1)
        proc = self._run(small, ["--no-probe"])
        self.assertIn("WARN ask_mail_py size=%d" % (ASK_MIN - 1), proc.stdout)
        big = self.tmp / "boundary-big"
        _write_fixture(big, k=20, ask_bytes=ASK_MIN)
        proc = self._run(big, ["--no-probe"])
        self.assertIn("PASS ask_mail_py size=%d" % ASK_MIN, proc.stdout)

    def test_json_one_object(self) -> None:
        mail = self.tmp / "json"
        _write_fixture(mail, k=20, ask_bytes=16)
        proc = self._run(mail, ["--json", "--no-probe"])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr, "")
        obj = json.loads(proc.stdout)
        self.assertIsInstance(obj, dict)
        self.assertEqual(obj["fail"], 2)
        self.assertEqual(obj["warn"], 1)
        self.assertEqual(obj["pass"], 3)
        names = [item["name"] for item in obj["checks"]]
        self.assertEqual(
            names,
            [
                "retrieve_health",
                "mlx_models",
                "ollama",
                "path_a_files",
                "paste_k",
                "ask_mail_py",
            ],
        )
        self.assertNotIn("SUMMARY", proc.stdout)
        self.assertNotIn("mlx_probe", names)

    def test_mock_services_exit_0_and_probe_shape(self) -> None:
        httpd = _serve(model_id=SENTINEL)
        self.servers.append(httpd)
        port = httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        mail = self.tmp / "up"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        env = self._env(
            mail,
            retrieve=base,
            qwen=base + "/v1",
            timeout="3",
        )
        proc = self._run(mail, [], env=env)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertIn("PASS retrieve_health /health reachable", proc.stdout)
        self.assertIn("PASS mlx_models /v1/models reachable", proc.stdout)
        self.assertIn("PASS mlx_probe chat probe max_tokens=1 thinking=off", proc.stdout)
        self.assertIn("PASS ollama down", proc.stdout)
        self.assertIn("PASS path_a_files present+executable", proc.stdout)
        self.assertIn("PASS paste_k default 20", proc.stdout)
        self.assertIn("SUMMARY pass=7 warn=0 fail=0", proc.stdout)
        self.assertNotIn(SENTINEL, proc.stdout)
        self.assertEqual(len(httpd.posts), 1)
        body = json.loads(httpd.posts[0].decode("utf-8"))
        self.assertEqual(body["max_tokens"], 1)
        self.assertIs(body["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(body["model"], SENTINEL)
        self.assertEqual(body["messages"], [{"role": "user", "content": "ok"}])

    def test_probe_timeout_names_wedged_generate(self) -> None:
        httpd = _serve(delay=3)
        self.servers.append(httpd)
        port = httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        mail = self.tmp / "wedge"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        env = self._env(mail, retrieve=base, qwen=base + "/v1", timeout="1")
        proc = self._run(mail, [], env=env, timeout=10)
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS mlx_models /v1/models reachable", proc.stdout)
        self.assertIn("FAIL mlx_probe chat probe timed out; " + WEDGED, proc.stdout)
        self.assertEqual(proc.stdout.count("FAIL "), 1)

    def test_no_probe_skips_hanging_chat(self) -> None:
        httpd = _serve(delay=5)
        self.servers.append(httpd)
        port = httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        mail = self.tmp / "skip-probe"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        env = self._env(mail, retrieve=base, qwen=base + "/v1", timeout="1")
        proc = self._run(mail, ["--no-probe"], env=env, timeout=8)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertNotIn("mlx_probe", proc.stdout)
        self.assertEqual(httpd.posts, [])
        self.assertIn("SUMMARY pass=6 warn=0 fail=0", proc.stdout)

    def test_ollama_up_while_qwen_up_warns_exit_0(self) -> None:
        httpd = _serve()
        self.servers.append(httpd)
        port = httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        mail = self.tmp / "ollama"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        env = self._env(
            mail,
            retrieve=base,
            qwen=base + "/v1",
            ollama=base,
            timeout="3",
        )
        proc = self._run(mail, ["--no-probe"], env=env)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn(
            "WARN ollama up while :1234 up (RAM rule: stop Ollama while Qwen up)",
            proc.stdout,
        )
        self.assertIn("SUMMARY pass=5 warn=1 fail=0", proc.stdout)

    def test_ollama_up_while_qwen_down_is_pass(self) -> None:
        httpd = _serve()
        self.servers.append(httpd)
        port = httpd.server_address[1]
        base = "http://127.0.0.1:%d" % port
        mail = self.tmp / "ollama-only"
        _write_fixture(mail, k=20, ask_bytes=ASK_MIN)
        env = self._env(mail, ollama=base)
        proc = self._run(mail, ["--no-probe"], env=env)
        self.assertIn("PASS ollama up; mlx :1234 not up", proc.stdout)
        self.assertNotIn("WARN ollama", proc.stdout)
        self.assertEqual(proc.returncode, 1)

    def test_zsh_matches_bash(self) -> None:
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh not installed")
        mail = self.tmp / "zsh"
        _write_fixture(mail, k=20, ask_bytes=24)
        env = self._env(mail)
        bash = self._run(mail, ["--no-probe", "--json"], shell="bash", env=env)
        zsh_proc = self._run(mail, ["--no-probe", "--json"], shell=zsh, env=env)
        self.assertEqual(bash.returncode, zsh_proc.returncode)
        self.assertEqual(json.loads(bash.stdout), json.loads(zsh_proc.stdout))

    def test_unknown_flag_exits_2(self) -> None:
        mail = self.tmp / "flag"
        _write_fixture(mail, k=20, ask_bytes=8)
        proc = self._run(mail, ["--write"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown flag", proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
