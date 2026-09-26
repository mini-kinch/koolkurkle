"""The Ollama guard fails closed on loopback port 11434 and nowhere else."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import ollama_guard

ollama_guard.install()


class _OkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):  # noqa: N802
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OllamaGuardTests(unittest.TestCase):
    def test_install_is_idempotent(self):
        ollama_guard.install()
        ollama_guard.install()
        self.assertIs(urllib.request.urlopen.__name__, "_guarded_urlopen")

    def test_urlopen_default_host_raises(self):
        with self.assertRaises(ollama_guard.AccidentalOllamaCall) as ctx:
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1)
        self.assertIn("11434", str(ctx.exception))
        self.assertIn("/api/tags", str(ctx.exception))

    def test_urlopen_localhost_embed_raises(self):
        with self.assertRaises(ollama_guard.AccidentalOllamaCall):
            urllib.request.urlopen("http://localhost:11434/api/embed", timeout=1)

    def test_urlopen_api_routes_raise(self):
        for path in ("/api/embeddings", "/api/generate", "/api/chat"):
            with self.assertRaises(ollama_guard.AccidentalOllamaCall):
                urllib.request.urlopen(
                    "http://127.0.0.1:11434%s" % path, timeout=1
                )

    def test_create_connection_raises(self):
        with self.assertRaises(ollama_guard.AccidentalOllamaCall):
            socket.create_connection(("127.0.0.1", 11434), timeout=1)

    def test_ipv6_loopback_raises_before_connect(self):
        with self.assertRaises(ollama_guard.AccidentalOllamaCall):
            socket.create_connection(("::1", 11434), timeout=1)

    def test_direct_connect_raises(self):
        sock = socket.socket()
        try:
            with self.assertRaises(ollama_guard.AccidentalOllamaCall):
                sock.connect(("localhost", 11434))
        finally:
            sock.close()

    def test_other_loopback_port_is_not_blocked(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        self.assertNotEqual(port, 11434)
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=1)
            client.close()
        finally:
            server.close()

    def test_urlopen_other_port_still_works(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
        port = httpd.server_address[1]
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/health" % port, timeout=2
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body, {"ok": True})


class OllamaUrlopenStandInTests(unittest.TestCase):
    def test_down_is_urlerror_and_records_the_url(self):
        calls = []
        opener = ollama_guard.ollama_urlopen("down", calls)
        with self.assertRaises(urllib.error.URLError):
            opener("http://127.0.0.1:11434/api/tags")
        self.assertEqual(calls, ["http://127.0.0.1:11434/api/tags"])

    def test_up_returns_tags_and_embed_json(self):
        calls = []
        opener = ollama_guard.ollama_urlopen("up", calls)
        with opener(urllib.request.Request("http://127.0.0.1:11434/api/tags")) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 200)
        self.assertIn("models", tags)
        with opener(urllib.request.Request("http://127.0.0.1:11434/api/embed")) as resp:
            embed = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(len(embed["embeddings"][0]), 1024)
        self.assertEqual(
            calls,
            [
                "http://127.0.0.1:11434/api/tags",
                "http://127.0.0.1:11434/api/embed",
            ],
        )


if __name__ == "__main__":
    unittest.main()
