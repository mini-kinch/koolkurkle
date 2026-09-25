#!/usr/bin/env python3
"""Offline tests for scripts/path_a_bench.py.

Local stub HTTP only. No real model, no mail, no home-directory paths.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "path_a_bench.py"
FIXTURE = ROOT / "tests" / "fixtures" / "path_a" / "paste_4k_synthetic.txt"
POST = ROOT / "scripts" / "qwen_paste_chat_post.py"
SENTINEL = "SENTINEL_CONTENT_DO_NOT_PRINT"
USERS_PREFIX = "/" + "Users" + "/"
HOME_PREFIX = "/" + "home" + "/"
ICLOUD_MARK = "@" + "icloud"
ME_MARK = "@" + "me" + ".com"

_HOOKS = (
    "PATH_A_BENCH_TEST_HOOKS",
    "PATH_A_BENCH_TIME_SCALE",
    "PATH_A_BENCH_WALL_ADD",
    "PATH_A_BENCH_WARM_WALL_MAX",
    "PATH_A_BENCH_WARM_MEDIAN_MAX",
    "PATH_A_BENCH_COLD_WALL_MAX",
)


def _load_bench():
    spec = importlib.util.spec_from_file_location("path_a_bench", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load path_a_bench")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = _load_bench()


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _long(fill: str = "E") -> str:
    base = SENTINEL + " "
    pad = fill * 400
    return (base + pad)[:360]


def _completion(content: str, finish: str = "stop", reasoning=None, usage=True) -> bytes:
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    payload = {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": message,
            }
        ]
    }
    if usage:
        payload["usage"] = {
            "prompt_tokens": 1400,
            "completion_tokens": 40,
            "total_tokens": 1440,
        }
    return json.dumps(payload).encode("utf-8")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.posts = []
        self.kind = "ok"
        self.delay = 1.0
        self.model_id = "synth-model"
        self.mutate_ask = None
        self.ask_mutated = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except OSError:
            return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/v1/models":
            self._send(404, b"{}")
            return
        raw = json.dumps(
            {
                "data": [
                    {"id": self.server.model_id},
                    {"id": "second-model"},
                ]
            }
        ).encode("utf-8")
        self._send(200, raw)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        server: _Server = self.server  # type: ignore[assignment]
        server.posts.append(raw)
        if server.mutate_ask is not None and not server.ask_mutated:
            path = server.mutate_ask
            path.write_bytes(path.read_bytes() + b"\n# changed\n")
            server.ask_mutated = True
        kind = server.kind
        if kind == "hang":
            time.sleep(server.delay)
            self._send(200, _completion(_long()))
            return
        if kind == "empty":
            self._send(200, b"")
            return
        if kind == "badjson":
            self._send(200, b"{")
            return
        if kind == "reasoning":
            if len(server.posts) == 1:
                self._send(200, _completion("", finish="stop", reasoning="hidden chain"))
            else:
                self._send(200, _completion(_long("B"), finish="stop", reasoning="note"))
            return
        if kind == "length":
            self._send(200, _completion(_long("C"), finish="length", reasoning="note"))
            return
        if kind == "short":
            self._send(200, _completion("S" * 300, finish="stop", reasoning="note"))
            return
        self._send(200, _completion(_long("E"), finish="stop", reasoning="note"))


def _serve() -> _Server:
    httpd = _Server()
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return httpd


def _stop(httpd: _Server | None) -> None:
    if httpd is None:
        return
    httpd.shutdown()
    httpd.server_close()


SYSCTL_OK = (
    "vm.swapusage: total = 2048.00M  used = 512.50M  free = 1535.50M  (encrypted)\n"
)
SYSCTL_FULL = (
    "vm.swapusage: total = 4096.00M  used = 1024.00M  free = 3072.00M  (encrypted)\n"
)
SYSCTL_GB = (
    "vm.swapusage: total = 2.00G  used = 1.50G  free = 512.00M  (encrypted)\n"
)
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               1200.
Pages active:                            34000.
Pages inactive:                           8000.
Pages speculative:                         100.
Pages wired down:                        15000.
Pages purgeable:                           200.
"""


class PathABenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="path-a-bench-"))
        self.addCleanup(self._cleanup_tmp)
        self.httpd = None
        self.addCleanup(self._cleanup_server)

    def _cleanup_tmp(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cleanup_server(self) -> None:
        _stop(self.httpd)
        self.httpd = None

    def _start(self, kind: str = "ok") -> str:
        self.httpd = _serve()
        self.httpd.kind = kind
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def _ask(self, payload: bytes = b"# synthetic ask_mail fixture\n") -> Path:
        path = self.tmp / "ask_mail.py"
        path.write_bytes(payload)
        return path

    def _run(self, args: list, env: dict | None = None, timeout: float = 15):
        child_env = os.environ.copy()
        for key in _HOOKS:
            child_env.pop(key, None)
        if env:
            child_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        blob = proc.stdout + proc.stderr
        self.assertNotIn("Traceback", blob)
        self.assertNotIn(USERS_PREFIX, blob)
        self.assertNotIn(HOME_PREFIX, blob)
        self.assertNotIn(ICLOUD_MARK, blob.lower())
        self.assertNotIn(ME_MARK, blob.lower())
        self.assertNotIn(SENTINEL, blob)
        self.assertNotIn("Brindle Mercantile", blob)
        self.assertNotIn("qwen-chat-down", blob)
        self.assertNotIn("qwen-chat-up", blob)
        return proc

    def _rows(self) -> list:
        path = self.tmp / "out.jsonl"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn(USERS_PREFIX, text)
        self.assertNotIn(HOME_PREFIX, text)
        self.assertNotIn("Brindle Mercantile", text)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _cmd(self, mode: str, base: str, ask: Path, extra: list | None = None) -> list:
        return [
            mode,
            "--base-url",
            base,
            "--fixture",
            str(FIXTURE),
            "--ask-mail",
            str(ask),
            "--out",
            str(self.tmp / "out.jsonl"),
            *(extra or []),
        ]

    def test_fixture_size_and_shape(self) -> None:
        raw = FIXTURE.read_bytes()
        self.assertGreaterEqual(len(raw), 3800)
        self.assertLessEqual(len(raw), 4200)
        text = raw.decode("utf-8")
        self.assertTrue(text.startswith("DATA:\n"))
        self.assertIn("\nQUESTION:\n", text)
        ids = re.findall(r"(?m)^\d+\. message_id: syn-\d+", text)
        self.assertGreaterEqual(len(ids), 15)
        self.assertLessEqual(len(ids), 20)
        self.assertIn("from: sender1@example.com", text)
        self.assertIn("from: sender%d@example.com" % len(ids), text)
        self.assertNotIn(ICLOUD_MARK, text.lower())
        self.assertNotIn(ME_MARK, text.lower())
        self.assertNotIn(USERS_PREFIX, text)
        self.assertIn("Copperline Notices renewal", text)

    def test_source_has_no_personal_or_restart_strings(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for banned in (
            USERS_PREFIX,
            ICLOUD_MARK,
            ME_MARK,
            "qwen-chat-down",
            "qwen-chat-up",
            "launchctl",
            "LaunchAgent",
        ):
            self.assertNotIn(banned, text)
        doc = (ROOT / "docs" / "path-a" / "bench.md").read_text(encoding="utf-8")
        self.assertNotIn(USERS_PREFIX, doc)
        self.assertNotIn(ICLOUD_MARK, doc.lower())
        self.assertNotIn(ME_MARK, doc.lower())
        self.assertIn("$HOME", doc)
        self.assertIn("does not restart", doc.lower())

    def test_system_prompt_matches_post_py(self) -> None:
        post = POST.read_text(encoding="utf-8")
        for part in (
            "Answer only from DATA. Cite date/from/subject when relevant.",
            "If DATA is insufficient, say so. Do not invent.",
            "Reply with the final answer only — no chain-of-thought.",
            "/no_think",
            "Final answer only.",
            "enable_thinking",
        ):
            self.assertIn(part, post)
            self.assertIn(part, BENCH.SYSTEM_PROMPT if part.startswith("Answer") or part.startswith("If ") or part.startswith("Reply") else SCRIPT.read_text(encoding="utf-8"))
        body = BENCH.build_chat_body("DATA:\n\nQUESTION:\nhi\n", "synth-model", 512, retry=False)
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["max_tokens"], 512)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["messages"][0]["content"], BENCH.SYSTEM_PROMPT)
        self.assertTrue(body["messages"][1]["content"].endswith("/no_think"))
        retry = BENCH.build_chat_body("DATA:\n\nQUESTION:\nhi\n", "synth-model", 512, retry=True)
        self.assertEqual(retry["max_tokens"], 768)
        self.assertIn("Final answer only.", retry["messages"][1]["content"])
        self.assertFalse(retry["chat_template_kwargs"]["enable_thinking"])

    def test_warm_all_pass_and_autopick(self) -> None:
        base = self._start("ok")
        ask = self._ask()
        proc = self._run(self._cmd("warm", base, ask, ["-n", "3", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS warm_requests", proc.stdout)
        self.assertIn("PASS warm_median", proc.stdout)
        self.assertIn("OVERALL PASS", proc.stdout)
        self.assertIn("model synth-model", proc.stdout)
        self.assertNotIn("second-model", proc.stdout)
        self.assertEqual(len(self.httpd.posts), 3)
        for raw in self.httpd.posts:
            body = json.loads(raw.decode("utf-8"))
            self.assertEqual(body["model"], "synth-model")
            self.assertNotEqual(body["model"], "second-model")
            self.assertEqual(body["temperature"], 0.2)
            self.assertEqual(body["max_tokens"], 512)
            self.assertEqual(body["chat_template_kwargs"]["enable_thinking"], False)
            self.assertEqual(body["messages"][0]["content"], BENCH.SYSTEM_PROMPT)
            self.assertTrue(body["messages"][1]["content"].rstrip().endswith("/no_think"))
            self.assertIn("DATA:", body["messages"][1]["content"])
            self.assertIn("QUESTION:", body["messages"][1]["content"])
        rows = self._rows()
        requests = [row for row in rows if row.get("kind") == "request"]
        self.assertEqual(len(requests), 3)
        for row in requests:
            self.assertEqual(row["verdict"], "pass")
            self.assertEqual(row["finish_reason"], "stop")
            self.assertGreater(row["content_len"], 300)
            self.assertEqual(row["http_status"], 200)
            self.assertEqual(row["prompt_tokens"], 1400)
            self.assertEqual(row["completion_tokens"], 40)
            self.assertEqual(row["reasoning_len"], 4)
            self.assertIsNone(row["error"])
            self.assertIn("T", row["ts"])
            self.assertEqual(row["mode"], "warm")
        summary = rows[-1]
        self.assertEqual(summary["kind"], "summary")
        self.assertEqual(summary["overall"], "PASS")
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(summary["hung"], 0)
        digest = __import__("hashlib").sha256(ask.read_bytes()).hexdigest()
        self.assertIn("PASS ask_mail sha256=%s" % digest, proc.stdout)

    def test_warm_median_over_35_fails(self) -> None:
        base = self._start("ok")
        ask = self._ask()
        proc = self._run(
            self._cmd("warm", base, ask, ["-n", "3", "--timeout", "5"]),
            env={
                "PATH_A_BENCH_TEST_HOOKS": "1",
                "PATH_A_BENCH_WALL_ADD": "40",
                "PATH_A_BENCH_WARM_WALL_MAX": "1000",
            },
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS warm_requests", proc.stdout)
        median_line = ""
        for line in proc.stdout.splitlines():
            if "warm_median" in line:
                median_line = line
        self.assertTrue(median_line.startswith("FAIL "), msg=proc.stdout)
        self.assertIn("limit_s=35.000", median_line)
        match = re.search(r"median_s=([0-9.]+)", median_line)
        self.assertIsNotNone(match)
        median = float(match.group(1))
        self.assertGreater(median, 35.0)
        self.assertIn("OVERALL FAIL", proc.stdout)
        self.assertNotIn("WEDGE", proc.stdout)

    def test_wall_add_ignored_without_hook(self) -> None:
        base = self._start("ok")
        ask = self._ask()
        proc = self._run(
            self._cmd("warm", base, ask, ["-n", "1", "--timeout", "5"]),
            env={"PATH_A_BENCH_WALL_ADD": "40"},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS warm_median", proc.stdout)

    def test_finish_reason_length_fails(self) -> None:
        base = self._start("length")
        proc = self._run(self._cmd("warm", base, self._ask(), ["-n", "1", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        row = [item for item in self._rows() if item.get("kind") == "request"][0]
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["finish_reason"], "length")
        self.assertEqual(row["error"], "finish_reason=length")
        self.assertGreater(row["content_len"], 300)
        self.assertIn("FAIL warm_requests", proc.stdout)
        self.assertIn("OVERALL FAIL", proc.stdout)

    def test_content_len_300_fails(self) -> None:
        base = self._start("short")
        proc = self._run(self._cmd("warm", base, self._ask(), ["-n", "1", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        row = [item for item in self._rows() if item.get("kind") == "request"][0]
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["content_len"], 300)
        self.assertEqual(row["error"], "content_len=300")
        self.assertEqual(row["finish_reason"], "stop")

    def test_empty_body_is_error(self) -> None:
        base = self._start("empty")
        proc = self._run(self._cmd("warm", base, self._ask(), ["-n", "1", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        row = [item for item in self._rows() if item.get("kind") == "request"][0]
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["error"], "empty body")
        self.assertEqual(row["http_status"], 200)
        self.assertIsNone(row["content_len"])
        self.assertIn("OVERALL FAIL", proc.stdout)

    def test_malformed_json_is_error(self) -> None:
        base = self._start("badjson")
        proc = self._run(self._cmd("warm", base, self._ask(), ["-n", "1", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        row = [item for item in self._rows() if item.get("kind") == "request"][0]
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["error"], "malformed json")
        self.assertIn("OVERALL FAIL", proc.stdout)

    def test_soak_timeout_wedges(self) -> None:
        base = self._start("hang")
        self.httpd.delay = 1.0
        proc = self._run(
            self._cmd(
                "soak",
                base,
                self._ask(),
                ["-n", "4", "--interval", "0", "--timeout", "0.3"],
            ),
            timeout=10,
        )
        self.assertEqual(proc.returncode, 4, msg=proc.stdout + proc.stderr)
        self.assertIn("WEDGE", proc.stdout)
        self.assertIn("WEDGE", proc.stderr)
        self.assertRegex(proc.stdout, r"WEDGE soak idx=1 local_time=\d{4}-\d{2}-\d{2}T")
        self.assertEqual(len(self.httpd.posts), 1)
        requests = [row for row in self._rows() if row.get("kind") == "request"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["verdict"], "hung")
        self.assertEqual(requests[0]["error"], "timeout")
        self.assertIn("OVERALL WEDGE", proc.stdout)
        self.assertIn("hung=1", proc.stdout)

    def test_soak_failure_does_not_wedge(self) -> None:
        base = self._start("short")
        proc = self._run(
            self._cmd(
                "soak",
                base,
                self._ask(),
                ["-n", "2", "--interval", "0", "--timeout", "5"],
            )
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertNotIn("WEDGE", proc.stdout + proc.stderr)
        self.assertEqual(len(self.httpd.posts), 2)
        self.assertIn("FAIL soak", proc.stdout)
        self.assertIn("failures=2", proc.stdout)
        self.assertIn("hung=0", proc.stdout)

    def test_server_unreachable_exits_2(self) -> None:
        port = _closed_port()
        proc = self._run(
            self._cmd(
                "warm",
                "http://127.0.0.1:%d" % port,
                self._ask(),
                ["-n", "1", "--timeout", "2"],
            )
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("server unreachable at start", proc.stderr)
        self.assertNotIn("OVERALL", proc.stdout)
        self.assertFalse((self.tmp / "out.jsonl").exists())

    def test_bad_fixture_exits_2(self) -> None:
        base = self._start("ok")
        args = self._cmd("warm", base, self._ask(), ["-n", "1", "--timeout", "2"])
        fixture_at = args.index("--fixture")
        args[fixture_at + 1] = str(self.tmp / "missing-fixture.txt")
        proc = self._run(args)
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("bad fixture", proc.stderr)
        self.assertEqual(self.httpd.posts, [])

    def test_ask_mail_changed_fails(self) -> None:
        base = self._start("ok")
        ask = self._ask()
        self.httpd.mutate_ask = ask
        proc = self._run(self._cmd("warm", base, ask, ["-n", "1", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("FAIL ask_mail sha256 changed", proc.stdout)
        self.assertIn("OVERALL FAIL", proc.stdout)
        summary = self._rows()[-1]
        self.assertNotEqual(summary["ask_mail_sha256_start"], summary["ask_mail_sha256_end"])
        self.assertNotEqual(summary["ask_mail_sha256_start"], "absent")

    def test_ask_mail_absent(self) -> None:
        base = self._start("ok")
        missing = self.tmp / "missing" / "ask_mail.py"
        proc = self._run(self._cmd("cold", base, missing, ["--timeout", "5"]))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS ask_mail sha256=absent", proc.stdout)
        self.assertNotIn("changed", proc.stdout)
        self.assertIn("30-minute", proc.stdout)
        self.assertIn("PASS cold_request", proc.stdout)
        summary = self._rows()[-1]
        self.assertEqual(summary["ask_mail_sha256_start"], "absent")
        self.assertEqual(summary["ask_mail_sha256_end"], "absent")

    def test_cold_pass_prints_idle_note(self) -> None:
        base = self._start("ok")
        proc = self._run(self._cmd("cold", base, self._ask(), ["--timeout", "5"]))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("30-minute", proc.stdout)
        self.assertIn("PASS cold_request", proc.stdout)
        self.assertIn("limit_s=90.000", proc.stdout)
        self.assertEqual(len(self.httpd.posts), 1)

    def test_reasoning_retry_counts_content(self) -> None:
        base = self._start("reasoning")
        proc = self._run(self._cmd("warm", base, self._ask(), ["-n", "1", "--timeout", "5"]))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertEqual(len(self.httpd.posts), 2)
        first = json.loads(self.httpd.posts[0].decode("utf-8"))
        second = json.loads(self.httpd.posts[1].decode("utf-8"))
        self.assertEqual(first["max_tokens"], 512)
        self.assertNotIn("Final answer only.", first["messages"][1]["content"])
        self.assertEqual(second["max_tokens"], 768)
        self.assertIn("Final answer only.", second["messages"][1]["content"])
        self.assertFalse(second["chat_template_kwargs"]["enable_thinking"])
        requests = [row for row in self._rows() if row.get("kind") == "request"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["verdict"], "pass")
        self.assertGreater(requests[0]["content_len"], 300)

    def test_mem_parse_samples(self) -> None:
        self.assertAlmostEqual(BENCH.parse_swap_used_mb(SYSCTL_OK), 512.50)
        self.assertAlmostEqual(BENCH.parse_swap_used_mb(SYSCTL_FULL), 1024.0)
        self.assertAlmostEqual(BENCH.parse_swap_used_mb(SYSCTL_GB), 1536.0)
        self.assertIsNone(BENCH.parse_swap_used_mb("no swap here"))
        self.assertIsNone(BENCH.parse_swap_used_mb(""))
        pages = BENCH.parse_vm_stat_pages(VM_STAT)
        self.assertEqual(pages["free"], 1200)
        self.assertEqual(pages["active"], 34000)
        self.assertEqual(pages["wired"], 15000)
        self.assertEqual(BENCH.parse_vm_stat_pages("nope")["free"], None)

        ok = BENCH.evaluate_mem("darwin", SYSCTL_OK, VM_STAT, "down", "closed")
        lines, code = BENCH.render_mem(ok, "abc", "abc")
        text = "\n".join(lines)
        self.assertEqual(code, 0)
        self.assertIn("PASS swap used_mb=512.50 limit_mb=1024", text)
        self.assertIn("INFO vm_stat free_pages=1200 active_pages=34000 wired_pages=15000", text)
        self.assertIn("PASS ollama process=down port_11434=closed", text)
        self.assertIn("PASS ask_mail sha256=abc", text)
        self.assertIn("OVERALL PASS", text)

        full = BENCH.evaluate_mem("darwin", SYSCTL_FULL, VM_STAT, "down", "closed")
        full_lines, full_code = BENCH.render_mem(full, "absent", "absent")
        self.assertEqual(full_code, 1)
        self.assertIn("FAIL swap", "\n".join(full_lines))
        self.assertIn("sha256=absent", "\n".join(full_lines))

        gb = BENCH.evaluate_mem("darwin", SYSCTL_GB, VM_STAT, "up", "closed")
        gb_lines, gb_code = BENCH.render_mem(gb, "abc", "abc")
        gb_text = "\n".join(gb_lines)
        self.assertEqual(gb_code, 1)
        self.assertIn("FAIL swap used_mb=1536.00", gb_text)
        self.assertIn("FAIL ollama process=up", gb_text)

        under = BENCH.evaluate_mem(
            "darwin",
            "vm.swapusage: total = 2048.00M  used = 1023.99M  free = 1024.01M\n",
            VM_STAT,
            "down",
            "closed",
        )
        self.assertEqual(under["swap_status"], "PASS")

        other = BENCH.evaluate_mem("linux", SYSCTL_OK, VM_STAT, "down", "closed")
        other_lines, other_code = BENCH.render_mem(other, "absent", "absent")
        other_text = "\n".join(other_lines)
        self.assertEqual(other_code, 1)
        self.assertIn("UNKNOWN swap used_mb=unknown", other_text)
        self.assertIn("free_pages=unknown", other_text)
        self.assertIn("PASS ollama", other_text)
        self.assertNotIn("Traceback", other_text)

        changed_lines, changed_code = BENCH.render_mem(ok, "aaa", "bbb")
        self.assertEqual(changed_code, 1)
        self.assertIn("FAIL ask_mail sha256 changed start=aaa end=bbb", "\n".join(changed_lines))

    def test_mem_cli_non_macos(self) -> None:
        if sys.platform == "darwin":
            self.skipTest("host is macOS")
        missing = self.tmp / "no-ask.py"
        proc = self._run(
            [
                "mem",
                "--ask-mail",
                str(missing),
                "--out",
                str(self.tmp / "out.jsonl"),
            ]
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("UNKNOWN swap", proc.stdout)
        self.assertIn("INFO vm_stat", proc.stdout)
        self.assertIn("sha256=absent", proc.stdout)
        self.assertIn("OVERALL FAIL", proc.stdout)
        row = self._rows()[0]
        self.assertEqual(row["kind"], "mem")
        self.assertEqual(row["swap_status"], "UNKNOWN")
        self.assertEqual(row["ask_mail_sha256_start"], "absent")
        self.assertEqual(row["ask_mail_sha256_end"], "absent")


if __name__ == "__main__":
    unittest.main()
