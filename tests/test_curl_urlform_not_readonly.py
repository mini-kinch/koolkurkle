#!/usr/bin/env python3
"""Q3: curl native IMAP URL-form cannot stay read-only.

Loopback only. TLS verification stays on. The transcript is the veto:
URL-form sends SELECT and BODY[], not EXAMINE and not BODY.PEEK.
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
PAYLOAD = b"\x00\x01line-one\r\n* 1 FE\r\n\xff\xfe tail-twelve"


def _verb(body: str) -> str:
    parts = body.upper().split()
    if not parts:
        return ""
    if parts[0] == "UID" and len(parts) > 1:
        return "UID " + parts[1]
    return parts[0]


class _Stub:
    def __init__(self, certfile: str, keyfile: str) -> None:
        self.commands: list[str] = []
        self._lock = threading.Lock()
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
            selected = False
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
                        selected = self._on_line(tls, line, selected)
        except (ssl.SSLError, OSError, ConnectionError):
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _on_line(self, tls: ssl.SSLSocket, line: bytes, selected: bool) -> bool:
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
            return selected
        if verb == "LOGIN":
            tls.sendall(raw_tag + b" OK LOGIN\r\n")
            return selected
        if verb in ("SELECT", "EXAMINE"):
            flags = b"* FLAGS (\\Seen)\r\n* OK [PERMANENTFLAGS (\\Seen)] Flags\r\n"
            banner = b"* 1 EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n"
            kind = b"SELECT" if verb == "SELECT" else b"EXAMINE"
            mode = b"READ-WRITE" if verb == "SELECT" else b"READ-ONLY"
            tls.sendall(banner + flags + raw_tag + b" OK [" + mode + b"] " + kind + b"\r\n")
            return True
        if verb == "UID FETCH":
            if not selected:
                tls.sendall(raw_tag + b" NO not selected\r\n")
                return selected
            blob = PAYLOAD
            upper = body.upper()
            if "<" in body and ">" in body:
                spec = body[body.find("<") + 1 : body.find(">")]
                if "." in spec:
                    start_s, length_s = spec.split(".", 1)
                    start = int(start_s)
                    length = int(length_s)
                    blob = PAYLOAD[start : start + length]
            section = "1"
            header = ("* 1 FETCH (BODY[%s] {%d}\r\n" % (section, len(blob))).encode("ascii")
            tls.sendall(header + blob + b")\r\n" + raw_tag + b" OK FETCH\r\n")
            return selected
        if verb == "LOGOUT":
            tls.sendall(b"* BYE\r\n" + raw_tag + b" OK LOGOUT\r\n")
            return selected
        tls.sendall(raw_tag + b" BAD unknown\r\n")
        return selected


def _mint(root: Path) -> tuple[Path, Path]:
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
    return cert, key


def _curl(server: _Stub, cert: Path, config: str) -> subprocess.CompletedProcess:
    with hermetic_binaries.allow_real_curl("127.0.0.1"):
        return subprocess.run(
            [CURL_BIN, "--silent", "--show-error", "-K", "-"],
            input=config.encode("utf-8"),
            capture_output=True,
            timeout=15,
            check=False,
        )


class CurlUrlFormNotReadonlyTests(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.exists(CURL_BIN):
            self.skipTest("pinned curl binary /usr/bin/curl is absent")
        self._tmp = tempfile.TemporaryDirectory()
        self.cert, key = _mint(Path(self._tmp.name))
        self.server = _Stub(str(self.cert), str(key))
        self.assertEqual(self.server._sock.getsockname()[0], "127.0.0.1")

    def tearDown(self) -> None:
        if hasattr(self, "server"):
            self.server.close()
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def _base(self, extra: str) -> str:
        return "\n".join(
            [
                "silent",
                "show-error",
                'cacert = "%s"' % self.cert,
                'user = "%s:%s"' % (USER, SECRET),
                extra,
                "",
            ]
        )

    def test_url_form_selects_and_fetches_body_not_peek(self) -> None:
        url = "imaps://127.0.0.1:%s/INBOX;UID=9;SECTION=1" % self.server.port
        proc = _curl(self.server, self.cert, self._base('url = "%s"' % url))
        commands = list(self.server.commands)
        detail = "rc=%s commands=%r stdout=%r stderr=%r" % (
            proc.returncode, commands, proc.stdout, proc.stderr
        )
        self.assertEqual(proc.returncode, 0, detail)
        joined = "\n".join(commands)
        upper = joined.upper()
        self.assertIn("SELECT", upper, detail)
        self.assertNotIn("EXAMINE", upper, detail)
        self.assertIn("BODY[", upper, detail)
        self.assertNotIn("BODY.PEEK", upper, detail)
        self.assertEqual(proc.stdout, PAYLOAD, detail)

    def test_partial_sends_body_slice_not_peek(self) -> None:
        url = (
            "imaps://127.0.0.1:%s/INBOX;UID=9;SECTION=1;PARTIAL=0.8" % self.server.port
        )
        proc = _curl(self.server, self.cert, self._base('url = "%s"' % url))
        commands = list(self.server.commands)
        detail = "rc=%s commands=%r stdout=%r stderr=%r" % (
            proc.returncode, commands, proc.stdout, proc.stderr
        )
        self.assertEqual(proc.returncode, 0, detail)
        fetch = [item for item in commands if "FETCH" in item.upper()]
        self.assertTrue(fetch, detail)
        self.assertIn("<0.8>", fetch[-1], detail)
        self.assertNotIn("BODY.PEEK", fetch[-1].upper(), detail)
        self.assertIn("SELECT", "\n".join(commands).upper(), detail)
        self.assertEqual(proc.stdout, PAYLOAD[:8], detail)

    def test_prior_examine_does_not_stick(self) -> None:
        bare = "imaps://127.0.0.1:%s/" % self.server.port
        section = "imaps://127.0.0.1:%s/INBOX;UID=9;SECTION=1" % self.server.port
        repeated = "\n".join(
            [
                'cacert = "%s"' % self.cert,
                'user = "%s:%s"' % (USER, SECRET),
            ]
        )
        config = self._base(
            "\n".join(
                [
                    'request = "EXAMINE INBOX"',
                    'url = "%s"' % bare,
                    "next",
                    repeated,
                    'url = "%s"' % section,
                ]
            )
        )
        proc = _curl(self.server, self.cert, config)
        commands = list(self.server.commands)
        detail = "rc=%s commands=%r stdout=%r stderr=%r" % (
            proc.returncode, commands, proc.stdout, proc.stderr
        )
        upper = [item.upper() for item in commands]
        self.assertEqual(proc.returncode, 0, detail)
        self.assertTrue(any(item.startswith("EXAMINE") for item in upper), detail)
        select_at = [i for i, item in enumerate(upper) if item.startswith("SELECT")]
        examine_at = [i for i, item in enumerate(upper) if item.startswith("EXAMINE")]
        self.assertTrue(select_at, detail)
        self.assertGreater(select_at[-1], examine_at[0], detail)
        fetch = [item for item in upper if "FETCH" in item]
        self.assertTrue(fetch, detail)
        self.assertIn("BODY[", fetch[-1])
        self.assertNotIn("BODY.PEEK", fetch[-1])


if __name__ == "__main__":
    unittest.main()
