"""Block accidental test traffic to local Ollama.

Python 3.9 compatible. ``install()`` patches ``urllib.request.urlopen``,
``socket.create_connection``, and ``socket.socket.connect``. A connection to
loopback port 11434 (127.0.0.1, localhost, or ::1) raises
``AccidentalOllamaCall``.

That type subclasses ``BaseException`` so production fail-open handlers
(``except Exception`` / ``except URLError``) cannot hide a real call.
``unittest`` still records it as an error.

``ollama_urlopen(mode)`` is a stand-in for tests that need both states.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

_LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1", "0.0.0.0"))
_OLLAMA_PORT = 11434

_INSTALLED = False
_ORIG_URL_OPEN = urllib.request.urlopen
_ORIG_CREATE = socket.create_connection
_ORIG_CONNECT = socket.socket.connect


class AccidentalOllamaCall(BaseException):
    """A test reached local Ollama instead of a mock."""


def _port_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _host_port(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return None, None
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return None, None
    return host.strip("[]").lower(), _port_number(address[1])


def _url_host_port(url_or_request):
    if isinstance(url_or_request, urllib.request.Request):
        raw = url_or_request.full_url
    else:
        raw = url_or_request
    if not isinstance(raw, str):
        return None, None, ""
    parsed = urlparse(raw)
    host = parsed.hostname
    if isinstance(host, str):
        host = host.strip("[]").lower()
    port = parsed.port
    if port is None:
        if parsed.scheme == "https":
            port = 443
        elif parsed.scheme == "http":
            port = 80
    return host, port, raw


def is_ollama_target(host, port):
    if port != _OLLAMA_PORT or not isinstance(host, str):
        return False
    return host.strip("[]").lower() in _LOOPBACK


def _raise_for(host, port, detail):
    raise AccidentalOllamaCall(
        "test reached local Ollama at %s:%s (%s); mock urllib/socket instead"
        % (host, port, detail)
    )


def install():
    """Install the guard once. Safe to call from sitecustomize and tests.

    Two copies of this module can exist (``ollama_guard`` and
    ``tests.ollama_guard``). A marker on the socket hook keeps the first
    installer in place so both copies share one exception type.
    """
    global _INSTALLED
    if _INSTALLED or getattr(socket.create_connection, "_mailroom_ollama_guard", False):
        _INSTALLED = True
        return

    def _guarded_urlopen(url, *args, **kwargs):
        host, port, raw = _url_host_port(url)
        if is_ollama_target(host, port):
            _raise_for(host, port, raw or "urlopen")
        return _ORIG_URL_OPEN(url, *args, **kwargs)

    def _guarded_create(address, *args, **kwargs):
        host, port = _host_port(address)
        if is_ollama_target(host, port):
            _raise_for(host, port, "create_connection")
        return _ORIG_CREATE(address, *args, **kwargs)

    def _guarded_connect(self, address):
        host, port = _host_port(address)
        if is_ollama_target(host, port):
            _raise_for(host, port, "connect")
        return _ORIG_CONNECT(self, address)

    _guarded_urlopen._mailroom_ollama_guard = True
    _guarded_create._mailroom_ollama_guard = True
    _guarded_connect._mailroom_ollama_guard = True
    urllib.request.urlopen = _guarded_urlopen
    socket.create_connection = _guarded_create
    socket.socket.connect = _guarded_connect
    _INSTALLED = True


class FakeHTTPResponse(object):
    """Minimal context-manager body for a mocked urlopen."""

    def __init__(self, status, body):
        self.status = int(status)
        self.code = self.status
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def ollama_urlopen(mode, calls=None):
    """Return a urlopen stand-in.

    mode ``down`` raises ``URLError`` (connection refused).
    mode ``up`` returns 200 JSON for the local Ollama routes.
    ``calls`` collects the URL of each attempt.
    """
    if mode not in ("up", "down"):
        raise ValueError("mode must be 'up' or 'down'")

    def _open(url, *args, **kwargs):
        del args, kwargs
        if isinstance(url, urllib.request.Request):
            raw = url.full_url
        else:
            raw = str(url)
        if calls is not None:
            calls.append(raw)
        if mode == "down":
            raise urllib.error.URLError("connection refused")
        path = urlparse(raw).path
        if path == "/api/tags":
            body = b'{"models":[{"name":"qwen3-embedding:8b"}]}'
        elif path == "/api/embed":
            body = json.dumps({"embeddings": [[0.1] * 1024]}).encode("utf-8")
        elif path == "/api/embeddings":
            body = json.dumps({"embedding": [0.1] * 1024}).encode("utf-8")
        elif path == "/api/generate":
            body = b'{"response":"yes"}'
        elif path == "/api/chat":
            body = b'{"message":{"content":"yes"}}'
        else:
            body = b'{"ok":true}'
        return FakeHTTPResponse(200, body)

    return _open
