#!/usr/bin/env python3
"""Q1: curl custom-request (-X) cannot return a FETCH {n} literal intact.

Loopback only. TLS verification stays on. No Keychain and no host other
than 127.0.0.1. The assertion is the drop itself: a future curl that
returns the literal intact should fail this test.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import hermetic_binaries

CURL_BIN = "/usr/bin/curl"
SECRET = "example-secret-token"
USER = "fixture-user"


def _literal(tag: str) -> bytes:
    live = ("%s OK" % tag).encode("ascii")
    parts = [
        b"\x00\x01line-one",
        b"* 1 FETCH (FLAGS (\\Seen))",
        b"A003 OK",
        live,
        b"\xff\xfe tail",
    ]
    return b"\r\n".join(parts)


def _verb(body: str) -> str:
    parts = body.upper().split()
    if not parts:
        return ""
    if parts[0] == "UID" and len(parts) > 1:
        return "UID " + parts[1]
    return parts[0]


class _Stub:
    def __init__(self, certfile: str, keyfile: str) -> None:
        self._certfile = certfile
        self._keyfile = keyfile
        self.commands: list[str] = []
        self.served = b""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
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
            threading.Thread(
                target=self._handle, args=(raw, context), daemon=True
            ).start()

    def _handle(self, raw: socket.socket, context: ssl.SSLContext) -> None:
        try:
            tls = context.wrap_socket(raw, server_side=True)
        except ssl.SSLError:
            raw.close()
            return
        try:
            tls.settimeout(5)
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
        if body.upper().startswith("LOGIN"):
            body = "LOGIN"
        with self._lock:
            self.commands.append(body)
        verb = _verb(body)
        raw_tag = tag.encode("ascii", "replace")
        if verb == "CAPABILITY":
            tls.sendall(b"* CAPABILITY IMAP4rev1\r\n" + raw_tag + b" OK CAPABILITY\r\n")
            return
        if verb == "LOGIN":
            tls.sendall(raw_tag + b" OK LOGIN\r\n")
            return
        if verb == "UID FETCH":
            payload = _literal(tag)
            with self._lock:
                self.served = payload
            header = ("* 1 FETCH (BODY[1] {%d}\r\n" % len(payload)).encode("ascii")
            tls.sendall(header + payload + b")\r\n" + raw_tag + b" OK FETCH\r\n")
            return
        if verb == "LOGOUT":
            tls.sendall(b"* BYE\r\n" + raw_tag + b" OK LOGOUT\r\n")
            return
        tls.sendall(raw_tag + b" BAD unknown\r\n")


def _mint(root: Path) -> tuple[Path, Path]:
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
    if minted.returncode != 0:
        raise AssertionError(
            "test cert failed: %s" % (minted.stderr or b"").decode("utf-8", "replace")
        )
    return cert, key


class CurlCustomRequestLiteralTests(unittest.TestCase):
    def test_custom_request_drops_the_literal(self) -> None:
        if not os.path.exists(CURL_BIN):
            self.skipTest("pinned curl binary /usr/bin/curl is absent")
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = _mint(Path(tmp))
            server = _Stub(str(cert), str(key))
            self.assertEqual(server._sock.getsockname()[0], "127.0.0.1")
            url = "imaps://127.0.0.1:%s/" % server.port
            config = "\n".join(
                [
                    "silent",
                    "show-error",
                    'cacert = "%s"' % cert,
                    'user = "%s:%s"' % (USER, SECRET),
                    'request = "UID FETCH 9 (BODY.PEEK[1]<0.1048576>)"',
                    'url = "%s"' % url,
                    "",
                ]
            )
            try:
                with hermetic_binaries.allow_real_curl("127.0.0.1"):
                    proc = subprocess.run(
                        [CURL_BIN, "--silent", "--show-error", "-K", "-"],
                        input=config.encode("utf-8"),
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
            finally:
                server.close()
        stdout = proc.stdout or b""
        detail = "rc=%s commands=%r stdout=%r stderr=%r" % (
            proc.returncode,
            server.commands,
            stdout,
            proc.stderr,
        )
        self.assertEqual(proc.returncode, 0, detail)
        self.assertTrue(server.served, detail)
        self.assertIn("UID FETCH", " ".join(server.commands).upper(), detail)
        self.assertNotIn(b"\xff\xfe tail", stdout, detail)
        self.assertNotIn(server.served, stdout, detail)
        self.assertNotIn(b"\x00\x01line-one", stdout, detail)
        self.assertNotIn(SECRET.encode("ascii"), stdout, detail)
        self.assertNotIn(SECRET.encode("ascii"), proc.stderr or b"", detail)


if __name__ == "__main__":
    unittest.main()
