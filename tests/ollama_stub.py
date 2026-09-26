#!/usr/bin/env python3
"""Throwaway listener on 127.0.0.1:11434 for the hermetic-suite proof.

Returns 200 JSON for /api/tags (and the other local Ollama routes) so a
real server can sit on the default port while tests run. Not used by the
tests themselves; they mock Ollama and the guard refuses this port.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 11434


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):  # noqa: N802
        path = self._path()
        if path == "/api/tags":
            self._json({"models": [{"name": "qwen3-embedding:8b"}]})
            return
        self._json({"ok": True})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        path = self._path()
        if path == "/api/embed":
            self._json({"embeddings": [[0.1, 0.0, 0.0, 0.0]]})
            return
        if path == "/api/embeddings":
            self._json({"embedding": [0.1, 0.0, 0.0, 0.0]})
            return
        if path == "/api/generate":
            self._json({"response": "yes"})
            return
        if path == "/api/chat":
            self._json({"message": {"content": "yes"}})
            return
        self._json({"ok": True})


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
