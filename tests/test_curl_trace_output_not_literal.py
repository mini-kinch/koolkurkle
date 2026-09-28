#!/usr/bin/env python3
"""Q4: --output and --trace-ascii do not recover a curl -X FETCH literal.

Loopback only. The trace fixture contains the login secret on purpose so
the test can prove the leak. The secret is a fixture, not a credential.
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

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import hermetic_binaries  # noqa: E402
import imap_curl  # noqa: E402

CURL_BIN = "/usr/bin/curl"
SECRET = "example-secret-token"
USER = "fixture-user"


def _literal(tag: str) -> bytes:
    live = ("%s OK" % tag).encode("ascii")
    return b"\r\n".join(
        [
            b"\x00\x01line-one",
            b"* 1 FETCH (FLAGS (\\Seen))",
            b"A003 OK",
            live,
            b"\xff\xfe tail",
        ]
    )


def _verb(body: str) -> str:
    parts = body.upper().split()
    if parts[:1] == ["UID"] and len(parts) > 1:
        return "UID " + parts[1]
    return parts[0] if parts else ""


class _Stub:
    def __init__(self, certfile: str, keyfile: str) -> None:
        self.served = b""
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile, keyfile)
        self._context = context
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                raw, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw: socket.socket) -> None:
        try:
            tls = self._context.wrap_socket(raw, server_side=True)
        except ssl.SSLError:
            raw.close()
            return
        try:
            tls.settimeout(5)
            tls.sendall(b"* OK IMAP4rev1 ready\r\n")
            buf = b""
            while not self._stop.is_set():
                chunk = tls.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    if line:
                        self._on_line(tls, line)
        except (ssl.SSLError, OSError, ConnectionError):
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _on_line(self, tls: ssl.SSLSocket, line: bytes) -> None:
        text = line.decode("ascii", "replace")
        space = text.find(" ")
        tag = text[:space] if space > 0 else "A000"
        body = text[space + 1 :] if space > 0 else text
        verb = _verb("LOGIN" if body.upper().startswith("LOGIN") else body)
        raw_tag = tag.encode("ascii", "replace")
        if verb == "CAPABILITY":
            tls.sendall(b"* CAPABILITY IMAP4rev1\r\n" + raw_tag + b" OK CAPABILITY\r\n")
            return
        if verb == "LOGIN":
            tls.sendall(raw_tag + b" OK LOGIN\r\n")
            return
        if verb == "UID FETCH":
            payload = _literal(tag)
            self.served = payload
            header = ("* 1 FETCH (BODY[1] {%d}\r\n" % len(payload)).encode("ascii")
            tls.sendall(header + payload + b")\r\n" + raw_tag + b" OK FETCH\r\n")
            return
        if verb == "LOGOUT":
            tls.sendall(b"* BYE\r\n" + raw_tag + b" OK LOGOUT\r\n")
            return
        tls.sendall(raw_tag + b" BAD unknown\r\n")


def _mint(root: Path) -> Path:
    key = root / "key.pem"
    cert = root / "cert.pem"
    minted = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "2", "-nodes", "-subj", "/CN=loopback",
            "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=False,
        capture_output=True,
    )
    if minted.returncode != 0:
        raise AssertionError((minted.stderr or b"").decode("utf-8", "replace"))
    return cert


def _config(cert: Path, port: int) -> str:
    return "\n".join(
        [
            "silent",
            "show-error",
            'cacert = "%s"' % cert,
            'user = "%s:%s"' % (USER, SECRET),
            'request = "UID FETCH 9 (BODY.PEEK[1]<0.1048576>)"',
            'url = "imaps://127.0.0.1:%s/"' % port,
            "",
        ]
    )


class CurlTraceOutputNotLiteralTests(unittest.TestCase):
    def test_output_and_trace_see_the_truncated_read(self) -> None:
        if not os.path.exists(CURL_BIN):
            self.skipTest("pinned curl binary /usr/bin/curl is absent")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cert = _mint(root)
            server = _Stub(str(cert), str(root / "key.pem"))
            out_path = root / "body.bin"
            trace_path = root / "trace.txt"
            try:
                with hermetic_binaries.allow_real_curl("127.0.0.1"):
                    saved = subprocess.run(
                        [
                            CURL_BIN, "--silent", "--show-error",
                            "--output", str(out_path), "-K", "-",
                        ],
                        input=_config(cert, server.port).encode("utf-8"),
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                    traced = subprocess.run(
                        [
                            CURL_BIN, "--silent", "--show-error",
                            "--trace-ascii", str(trace_path), "-K", "-",
                        ],
                        input=_config(cert, server.port).encode("utf-8"),
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
            finally:
                server.close()
            body = out_path.read_bytes() if out_path.is_file() else b""
            trace = trace_path.read_bytes() if trace_path.is_file() else b""
            self.assertEqual(saved.returncode, 0, saved.stderr)
            self.assertEqual(traced.returncode, 0, traced.stderr)
            self.assertTrue(server.served)
            self.assertNotIn(server.served, body)
            self.assertNotIn(b"\xff\xfe tail", body)
            self.assertNotIn(b"\xff\xfe tail", traced.stdout or b"")
            self.assertNotIn(b"\xff\xfe tail", trace)
            self.assertIn(SECRET.encode("ascii"), trace)
            self.assertNotIn(SECRET.encode("ascii"), body)

    def test_production_argv_rejects_trace_and_output(self) -> None:
        argv = imap_curl.curl_argv()
        self.assertNotIn("--trace-ascii", argv)
        self.assertNotIn("--trace", argv)
        self.assertNotIn("--output", argv)
        for flag in ("--trace-ascii", "--trace", "--output", "-o"):
            with self.assertRaises(imap_curl.CurlImapError):
                imap_curl.guard_curl_argv([CURL_BIN, flag, "sink", "-K", "-"])
        for line in ('trace-ascii = "sink"\n', 'output = "sink"\n', 'trace = "sink"\n'):
            with self.assertRaises(imap_curl.CurlImapError):
                imap_curl.guard_curl_config(line)


if __name__ == "__main__":
    unittest.main()
