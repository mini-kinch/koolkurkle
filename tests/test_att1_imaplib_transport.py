#!/usr/bin/env python3
"""Q6: ImapPartClient speaks imaplib on loopback. No curl fallback."""

from __future__ import annotations

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

SECRET = "example-secret-token"
USER = "fixture-user"
HOST = "127.0.0.1"
_BANNED = ("SELECT", "STORE", "EXPUNGE")


def _mint(directory: Path) -> tuple[Path, Path]:
    key = directory / "loopback.key"
    cert = directory / "loopback.crt"
    proc = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "2", "-nodes", "-subj", "/CN=loopback",
            "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace"))
    return key, cert


def _send_literal(tls, tag: str, section: str, payload: bytes) -> None:
    header = ("* 1 FETCH (%s {%d}\r\n" % (section, len(payload))).encode("ascii")
    tls.sendall(header + payload + b")\r\n" + tag.encode("ascii") + b" OK FETCH\r\n")


class _Stub:
    def __init__(self, cert: Path, key: Path, on_fetch) -> None:
        self.on_fetch = on_fetch
        self.commands: list[str] = []
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((HOST, 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
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
            tls.settimeout(3)
            tls.sendall(b"* OK ready\r\n")
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
            return
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _on_line(self, tls, line: bytes) -> None:
        text = line.decode("ascii", "replace")
        tag, _, body = text.partition(" ")
        recorded = "LOGIN" if body.upper().startswith("LOGIN") else body
        self.commands.append(recorded)
        upper = body.upper()
        if upper.startswith("CAPABILITY"):
            tls.sendall(b"* CAPABILITY IMAP4rev1\r\n" + tag.encode() + b" OK CAPABILITY\r\n")
        elif upper.startswith("LOGIN"):
            tls.sendall(tag.encode() + b" OK LOGIN\r\n")
        elif upper.startswith("EXAMINE"):
            tls.sendall(
                b"* 1 EXISTS\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n"
                + tag.encode()
                + b" OK [READ-ONLY] EXAMINE completed\r\n"
            )
        elif upper.startswith("UID FETCH"):
            self.on_fetch(tls, tag, body)
        elif upper.startswith("LOGOUT"):
            tls.sendall(b"* BYE\r\n" + tag.encode() + b" OK LOGOUT\r\n")
        else:
            tls.sendall(tag.encode() + b" BAD\r\n")


def _range(body: str) -> tuple[int, int]:
    start = body.rfind("<")
    end = body.rfind(">")
    offset_text, count_text = body[start + 1:end].split(".", 1)
    return int(offset_text), int(count_text)


def _forbid_subprocess(*_args, **_kwargs):
    raise AssertionError("curl subprocess is forbidden")


class TransportTests(unittest.TestCase):
    def test_default_client_keeps_bytes_and_stays_readonly(self) -> None:
        note = (ROOT / "docs" / "att" / "att1-imaplib-exception.md").read_text(encoding="utf-8")
        self.assertIn("imaplib_part.py", note)
        self.assertIn("<keychain-item>", note)
        self.assertIn("no curl", note.lower())
        binary = b"\x00\x01" + b"\r\n* 1 FETCH (FLAGS)\r\n" + b"A004 OK\r\n" + b"\xff\xfe"
        plain = b"no-crlf-payload"
        payloads = {"1": binary, "2": plain, "3": b"NIL"}

        def on_fetch(tls, tag, body):
            offset, count = _range(body)
            if "(BODY.PEEK[1]" in body and offset == 0 and count == 1:
                _send_literal(tls, tag, "BODY[1]<0>", b"")
                return
            if "(BODY.PEEK[4]" in body:
                tls.sendall(
                    b"* 1 FETCH (BODY[4]<0> NIL)\r\n" + tag.encode("ascii") + b" OK FETCH\r\n"
                )
                return
            part_id = "1"
            for candidate in ("2", "3"):
                if "(BODY.PEEK[%s]" % candidate in body:
                    part_id = candidate
            blob = payloads[part_id]
            _send_literal(tls, tag, "BODY[%s]<%d>" % (part_id, offset), blob[offset:offset + count])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key, cert = _mint(root)
            server = _Stub(cert, key, on_fetch)
            try:
                with mock.patch("subprocess.run", _forbid_subprocess), mock.patch(
                    "subprocess.Popen", _forbid_subprocess
                ):
                    client = fetch_p1.ImapPartClient(
                        HOST,
                        USER,
                        timeout=8,
                        port=server.port,
                        ca_file=str(cert),
                        password_fn=lambda: SECRET,
                    )
                    with client:
                        dest = root / "binary"
                        size = client.fetch_part("INBOX", "9", "1", dest, chunk_size=8)
                        self.assertEqual(size, len(binary))
                        self.assertEqual(dest.read_bytes(), binary)
                        plain_dest = root / "plain"
                        client.fetch_part("INBOX", "9", "2", plain_dest, chunk_size=4)
                        self.assertEqual(plain_dest.read_bytes(), plain)
                        nil_dest = root / "nil-bytes"
                        client.fetch_part("INBOX", "9", "3", nil_dest, chunk_size=8)
                        self.assertEqual(nil_dest.read_bytes(), b"NIL")
                        empty_dest = root / "empty"
                        with self.assertRaises(fetch_p1.FetchRefuse) as empty_ctx:
                            client.fetch_part("INBOX", "9", "1", empty_dest, chunk_size=1)
                        nil_atom = root / "nil-atom"
                        with self.assertRaises(fetch_p1.FetchRefuse) as nil_ctx:
                            client.fetch_part("INBOX", "8", "4", nil_atom, chunk_size=8)
                        self.assertEqual(str(empty_ctx.exception), "empty part fetch")
                        self.assertFalse(empty_dest.exists())
                        self.assertEqual(str(nil_ctx.exception), "empty part fetch")
                        self.assertFalse(nil_atom.exists())
            finally:
                server.close()
        joined = "\n".join(server.commands)
        self.assertIn("EXAMINE", joined)
        self.assertIn("BODY.PEEK[", joined)
        for banned in _BANNED:
            self.assertNotIn(banned, joined)
        self.assertNotIn(SECRET, joined)
        self.assertNotIn(SECRET.encode("ascii"), binary)

    def test_later_empty_ends_cleanly(self) -> None:
        body = b"abcdefgh"

        def on_fetch(tls, tag, command):
            offset, count = _range(command)
            _send_literal(tls, tag, "BODY[1]<%d>" % offset, body[offset:offset + count])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key, cert = _mint(root)
            server = _Stub(cert, key, on_fetch)
            try:
                client = fetch_p1.ImapPartClient(
                    HOST,
                    USER,
                    timeout=8,
                    port=server.port,
                    ca_file=str(cert),
                    password_fn=lambda: SECRET,
                )
                with client:
                    dest = root / "part"
                    size = client.fetch_part("INBOX", "9", "1", dest, chunk_size=8)
                    stored = dest.read_bytes()
            finally:
                server.close()
        self.assertEqual(size, len(body))
        self.assertEqual(stored, body)
        fetches = [line for line in server.commands if line.startswith("UID FETCH")]
        self.assertGreaterEqual(len(fetches), 2)


if __name__ == "__main__":
    unittest.main()
