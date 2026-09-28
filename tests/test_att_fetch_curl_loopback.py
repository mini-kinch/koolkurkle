#!/usr/bin/env python3
"""Real /usr/bin/curl against a test-only loopback IMAPS stub.

No Keychain, no iCloud, and no host beyond 127.0.0.1. The stub's TLS
certificate is generated in the test. Production verification stays on;
the client receives that CA through the test port/CA hook.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.fetch_p1 as fetch_p1  # noqa: E402

CURL_BIN = "/usr/bin/curl"
SECRET = "example-secret-token"
USER = "fixture-user"
_FORBIDDEN = frozenset(("SELECT", "STORE", "EXPUNGE"))


def _curl_version_text() -> str:
    proc = subprocess.run(
        [CURL_BIN, "--version"],
        check=False,
        capture_output=True,
    )
    text = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        return "curl --version failed rc=%s\n%s%s" % (proc.returncode, text, err)
    return text


def _literal_payload(tag: str) -> bytes:
    """Binary literal with CRLF, a '* ' line, and a tag-shaped line.

    The live command tag is included so a reader that does not track
    ``{N}`` can mistake that line for the end of the FETCH response.
    ``A003 OK`` is always present as well.
    """
    live = ("%s OK" % tag).encode("ascii")
    parts = [
        b"\x00\x01line-one",
        b"* 1 FETCH (FLAGS (\\Seen))",
        b"A003 OK",
        live,
        b"\xff\xfe tail",
    ]
    return b"\r\n".join(parts)


def _command_body(line: bytes) -> str:
    text = line.decode("ascii", "replace")
    if " " not in text:
        return text
    _tag, rest = text.split(" ", 1)
    if SECRET in rest:
        rest = rest.replace(SECRET, "***")
    if rest.upper().startswith("LOGIN ") or rest.upper() == "LOGIN":
        return "LOGIN"
    return rest


def _verb(command: str) -> str:
    parts = command.upper().split()
    if not parts:
        return ""
    if parts[0] == "UID" and len(parts) > 1:
        return "UID " + parts[1]
    return parts[0]


def _forbidden(command: str) -> bool:
    verb = _verb(command)
    if verb in _FORBIDDEN:
        return True
    return verb in ("UID STORE", "UID EXPUNGE")


def _trio(commands: list) -> bool:
    login_at = -1
    examine_at = -1
    fetch_at = -1
    for index, command in enumerate(commands):
        verb = _verb(command)
        if login_at < 0 and verb in ("LOGIN", "AUTHENTICATE"):
            login_at = index
        elif examine_at < 0 and login_at >= 0 and verb == "EXAMINE":
            examine_at = index
        elif fetch_at < 0 and examine_at >= 0 and verb == "UID FETCH":
            fetch_at = index
    return login_at >= 0 and examine_at > login_at and fetch_at > examine_at


class _LoopbackImap:
    """One-shot IMAPS stub. Speaks just enough for curl's LOGIN/EXAMINE/FETCH."""

    def __init__(self, certfile: str, keyfile: str) -> None:
        self._certfile = certfile
        self._keyfile = keyfile
        self.connections: list = []
        self.served: bytes | None = None
        self.errors: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    def _accept_loop(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self._certfile, self._keyfile)
        while not self._stop.is_set():
            try:
                raw, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            worker = threading.Thread(
                target=self._handle,
                args=(raw, context),
                daemon=True,
            )
            worker.start()

    def _handle(self, raw: socket.socket, context: ssl.SSLContext) -> None:
        commands: list = []
        with self._lock:
            self.connections.append(commands)
        examined = False
        try:
            tls = context.wrap_socket(raw, server_side=True)
        except ssl.SSLError as exc:
            with self._lock:
                self.errors.append("tls: %s" % exc.__class__.__name__)
            raw.close()
            return
        try:
            tls.settimeout(8.0)
            tls.sendall(b"* OK IMAP4rev1 ready\r\n")
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = tls.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    if not line:
                        continue
                    examined = self._on_line(tls, line, commands, examined)
        except (ssl.SSLError, OSError, ConnectionError) as exc:
            with self._lock:
                self.errors.append("conn: %s" % exc.__class__.__name__)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _on_line(
        self,
        tls: ssl.SSLSocket,
        line: bytes,
        commands: list,
        examined: bool,
    ) -> bool:
        body = _command_body(line)
        commands.append(body)
        space = line.find(b" ")
        tag = line[:space].decode("ascii", "replace") if space > 0 else "A000"
        verb = _verb(body)
        if verb == "CAPABILITY":
            tls.sendall(
                b"* CAPABILITY IMAP4rev1\r\n" + tag.encode("ascii") + b" OK CAPABILITY\r\n"
            )
            return examined
        if verb == "LOGIN":
            tls.sendall(tag.encode("ascii") + b" OK LOGIN\r\n")
            return examined
        if verb == "EXAMINE":
            tls.sendall(
                b"* 1 EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n"
                + tag.encode("ascii")
                + b" OK [READ-ONLY] EXAMINE completed\r\n"
            )
            return True
        if verb == "UID FETCH":
            if not examined:
                tls.sendall(tag.encode("ascii") + b" NO not examined\r\n")
                return examined
            payload = _literal_payload(tag)
            with self._lock:
                self.served = payload
            header = ("* 1 FETCH (BODY[1]<0> {%d}\r\n" % len(payload)).encode("ascii")
            tls.sendall(
                header
                + payload
                + b")\r\n"
                + tag.encode("ascii")
                + b" OK FETCH completed\r\n"
            )
            return examined
        if verb == "LOGOUT":
            tls.sendall(b"* BYE\r\n" + tag.encode("ascii") + b" OK LOGOUT\r\n")
            return examined
        if verb in _FORBIDDEN or verb in ("UID STORE", "UID EXPUNGE"):
            tls.sendall(tag.encode("ascii") + b" NO refused\r\n")
            return examined
        tls.sendall(tag.encode("ascii") + b" BAD unknown\r\n")
        return examined


def _redact(blob: bytes) -> str:
    text = repr(blob)
    if SECRET in text:
        text = text.replace(SECRET, "***")
    if len(text) > 4000:
        text = text[:4000] + "...(truncated)"
    return text


class CurlLoopbackLiteralTests(unittest.TestCase):
    def test_imaplib_returns_literal_on_one_connection(self) -> None:
        """The part client is imaplib. A tag-like literal comes back intact.

        The stub literal contains CRLF, a ``* `` line, ``A003 OK``, and the
        live tag line. The transcript is LOGIN, EXAMINE, UID FETCH. No
        SELECT, STORE, or EXPUNGE, and curl is not spawned.
        """
        version = "imaplib"
        runs = []
        real_run = subprocess.run

        def spy(*args, **kwargs):
            proc = real_run(*args, **kwargs)
            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
            if isinstance(stdout, str):
                stdout = stdout.encode("utf-8", "replace")
            if isinstance(stderr, str):
                stderr = stderr.encode("utf-8", "replace")
            argv = list(args[0]) if args else list(kwargs.get("argv") or [])
            runs.append((argv, int(proc.returncode), bytes(stdout), bytes(stderr)))
            return proc

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / "key.pem"
            cert = root / "cert.pem"
            minted = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "2",
                    "-nodes",
                    "-subj",
                    "/CN=loopback",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                check=False,
                capture_output=True,
            )
            self.assertEqual(
                minted.returncode,
                0,
                "test cert failed: %s" % (minted.stderr or b"").decode("utf-8", "replace"),
            )
            server = _LoopbackImap(str(cert), str(key))
            self.assertEqual(server._sock.getsockname()[0], "127.0.0.1")
            dest = root / "part"
            error = None
            got = None
            try:
                with mock.patch(
                    "attachments.fetch_p1.subprocess.run",
                    spy,
                ):
                    client = fetch_p1.ImapPartClient(
                        "127.0.0.1",
                        USER,
                        timeout=8,
                        port=server.port,
                        ca_file=str(cert),
                        password_fn=lambda: SECRET,
                    )
                    try:
                        with client:
                            client.fetch_part("INBOX", "9", "1", dest)
                        if dest.is_file():
                            got = dest.read_bytes()
                    except Exception as exc:
                        error = exc
                        if dest.is_file():
                            got = dest.read_bytes()
            finally:
                server.close()

        report = self._report(version, runs, server, got, error)
        trios = [commands for commands in server.connections if _trio(commands)]
        self.assertEqual(len(trios), 1, report)
        for commands in server.connections:
            for command in commands:
                self.assertFalse(_forbidden(command), report)
                self.assertNotIn("SELECT", _verb(command), report)
        self.assertEqual(runs, [], report)
        self.assertIsNone(error, report)
        self.assertIsNotNone(server.served, report)
        self.assertEqual(got, server.served, report)
        self.assertNotIn(SECRET.encode("ascii"), got or b"", report)

    def _report(self, version, runs, server, got, error) -> str:
        lines = [
            "curl --version",
            version.rstrip(),
            "connections %d" % len(server.connections),
            "trace %r" % (server.connections,),
            "server errors %r" % (server.errors,),
            "served %s" % (_redact(server.served) if server.served is not None else "None"),
            "got %s" % (_redact(got) if got is not None else "None"),
            "error %r" % (error,),
        ]
        for index, (argv, rc, stdout, stderr) in enumerate(runs):
            lines.append("run %d rc=%s argv=%r" % (index, rc, argv))
            lines.append("stdout %s" % _redact(stdout))
            lines.append("stderr %s" % _redact(stderr))
        return "\n".join(lines)


if __name__ == "__main__":
    unittest.main()
