#!/usr/bin/env python3
"""Q5: imaplib safeguards on a loopback IMAPS stub. No live host."""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.fetch_p1 as fetch_p1  # noqa: E402
import attachments.imaplib_part as part  # noqa: E402

SECRET = "example-secret-token"
USER = "fixture-user"
HOST = "127.0.0.1"


def _mint(directory: Path) -> tuple[Path, Path]:
    key = directory / "loopback.key"
    cert = directory / "loopback.crt"
    proc = subprocess.run(
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
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace"))
    return key, cert


class _Stub:
    """Loopback IMAPS. Records commands. Optional per-verb delay."""

    def __init__(self, cert: Path, key: Path, *, login_ok: bool = True, delays=None, on_fetch=None):
        self.login_ok = login_ok
        self.delays = delays or {}
        self.on_fetch = on_fetch
        self.commands: list[str] = []
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((HOST, 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
        self._cert = str(cert)
        self._key = str(key)
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
        context.load_cert_chain(self._cert, self._key)
        while not self._stop.is_set():
            try:
                raw, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(raw, context), daemon=True).start()

    def _handle(self, raw: socket.socket, context: ssl.SSLContext) -> None:
        try:
            tls = context.wrap_socket(raw, server_side=True)
        except ssl.SSLError:
            raw.close()
            return
        try:
            tls.settimeout(2)
            tls.sendall(b"* OK ready\r\n")
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
            return
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _pause(self, verb: str) -> None:
        delay = float(self.delays.get(verb) or 0)
        if delay <= 0:
            return
        end = time.monotonic() + delay
        while time.monotonic() < end and not self._stop.is_set():
            time.sleep(0.02)

    def _on_line(self, tls: ssl.SSLSocket, line: bytes) -> None:
        text = line.decode("ascii", "replace")
        tag, _, body = text.partition(" ")
        recorded = "LOGIN" if body.upper().startswith("LOGIN") else body
        self.commands.append(recorded)
        verb = recorded.split(" ", 1)[0].upper()
        if verb == "UID" and recorded.upper().startswith("UID "):
            verb = "UID"
        self._pause(verb if verb != "UID" else "UID")
        if body.upper().startswith("CAPABILITY"):
            tls.sendall(b"* CAPABILITY IMAP4rev1\r\n" + tag.encode() + b" OK CAPABILITY\r\n")
            return
        if body.upper().startswith("LOGIN"):
            if self.login_ok:
                tls.sendall(tag.encode() + b" OK LOGIN\r\n")
            else:
                tls.sendall(tag.encode() + b" NO denied " + SECRET.encode() + b"\r\n")
            return
        if body.upper().startswith("EXAMINE"):
            tls.sendall(
                b"* 1 EXISTS\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n"
                + tag.encode()
                + b" OK [READ-ONLY] EXAMINE completed\r\n"
            )
            return
        if body.upper().startswith("UID FETCH"):
            if self.on_fetch is not None:
                self.on_fetch(tls, tag, body)
                return
            tls.sendall(tag.encode() + b" OK FETCH\r\n")
            return
        if body.upper().startswith("LOGOUT"):
            tls.sendall(b"* BYE\r\n" + tag.encode() + b" OK LOGOUT\r\n")
            return
        tls.sendall(tag.encode() + b" BAD\r\n")


def _send_literal(tls, tag: str, section: str, payload: bytes) -> None:
    header = ("* 1 FETCH (%s {%d}\r\n" % (section, len(payload))).encode("ascii")
    tls.sendall(header + payload + b")\r\n" + tag.encode("ascii") + b" OK FETCH\r\n")


class SafeguardTests(unittest.TestCase):
    def test_module_has_no_curl_fallback(self) -> None:
        text = (SCRIPTS / "attachments" / "imaplib_part.py").read_text(encoding="utf-8")
        self.assertIn("ssl.create_default_context()", text)
        self.assertIn("imaplib.IMAP4_SSL", text)
        self.assertNotIn("IMAP4(", text)
        self.assertNotIn("subprocess", text)
        self.assertNotIn("CURL_BIN", text)
        self.assertNotIn('"-X"', text)
        self.assertNotIn("CERT_NONE", text)
        fetch = (SCRIPTS / "attachments" / "fetch_p1.py").read_text(encoding="utf-8")
        enter = fetch.split("def __enter__", 1)[1].split("def __exit__", 1)[0]
        self.assertIn("imaplib_part", enter)
        self.assertNotIn("_CurlPartConn", enter)

    def test_sslkeylogfile_is_cleared_unless_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            keylog = str(Path(tmp) / "keys.log")
            env = {"SSLKEYLOGFILE": keylog}
            blocked = part.verified_context(env=env)
            self.assertFalse(blocked.keylog_filename)
            allowed = part.verified_context(
                env={"SSLKEYLOGFILE": keylog, "MAILROOM_SSLKEYLOGFILE_ALLOW": "1"}
            )
            self.assertEqual(allowed.keylog_filename, keylog)
            self.assertEqual(blocked.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(blocked.check_hostname)
            self.assertNotIn("SSLKEYLOGFILE", os.environ)

    def test_errno_9_refuses_and_stores_nothing(self) -> None:
        def opener(*_args, **_kwargs):
            raise OSError(9, "Bad file descriptor")

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "part.bin"
            try:
                conn = part.ImaplibPartConn(HOST, 1, 2, opener=opener)
                typ, data = conn.uid("FETCH", "9", "(BODY.PEEK[1]<0.8>)")
                blob = part.interpret_peek(data, 0)
                dest.write_bytes(blob)
                self.fail("dial should have refused: %s %s" % (typ, blob))
            except fetch_p1.FetchRefuse as exc:
                self.assertEqual(str(exc), "part fetch failed")
            self.assertFalse(dest.exists())

    def test_password_is_not_in_the_login_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key, cert = _mint(Path(tmp))
            server = _Stub(cert, key, login_ok=False)
            try:
                conn = part.ImaplibPartConn(HOST, server.port, 5, ca_file=str(cert))
                with self.assertRaises(fetch_p1.FetchRefuse) as ctx:
                    conn.login(USER, SECRET)
            finally:
                server.close()
        self.assertEqual(str(ctx.exception), "imap login failed")
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertIn("LOGIN", server.commands)
        self.assertNotIn(SECRET, " ".join(server.commands))

    def test_overall_deadline_wraps_the_socket_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key, cert = _mint(Path(tmp))
            server = _Stub(cert, key, delays={"LOGIN": 1.2, "EXAMINE": 5.0})
            started = time.monotonic()
            try:
                conn = part.ImaplibPartConn(HOST, server.port, 2.0, ca_file=str(cert))
                conn.login(USER, SECRET)
                with self.assertRaises(fetch_p1.FetchRefuse) as ctx:
                    conn.select("INBOX", readonly=True)
            finally:
                elapsed = time.monotonic() - started
                server.close()
        self.assertEqual(str(ctx.exception), "part fetch failed")
        self.assertGreater(elapsed, 1.2)
        self.assertLess(elapsed, 2.8)
        self.assertNotIn(SECRET, " ".join(server.commands))

    def test_r4_nil_bytes_differ_from_the_nil_atom(self) -> None:
        payload = b"NIL\r\n* 1 FETCH\r\n\x00\xff"

        def on_fetch(tls, tag, body):
            if body.endswith("(BODY.PEEK[1]<0.3>)"):
                _send_literal(tls, tag, "BODY[1]<0>", b"NIL")
                return
            if body.endswith("(BODY.PEEK[1]<0.8>)"):
                tls.sendall(
                    b"* 1 FETCH (BODY[1]<0> NIL)\r\n" + tag.encode("ascii") + b" OK FETCH\r\n"
                )
                return
            if body.endswith("(BODY.PEEK[1]<0.1>)"):
                _send_literal(tls, tag, "BODY[1]<0>", b"")
                return
            if body.endswith("(BODY.PEEK[1]<4.8>)"):
                _send_literal(tls, tag, "BODY[1]<4>", b"")
                return
            _send_literal(tls, tag, "BODY[1]<0>", payload)

        with tempfile.TemporaryDirectory() as tmp:
            key, cert = _mint(Path(tmp))
            server = _Stub(cert, key, on_fetch=on_fetch)
            try:
                conn = part.ImaplibPartConn(HOST, server.port, 8, ca_file=str(cert))
                conn.login(USER, SECRET)
                conn.select("INBOX", readonly=True)
                _typ, nil_bytes = conn.uid("FETCH", "9", "(BODY.PEEK[1]<0.3>)")
                self.assertEqual(part.interpret_peek(nil_bytes, 0), b"NIL")
                _typ, nil_atom = conn.uid("FETCH", "9", "(BODY.PEEK[1]<0.8>)")
                with self.assertRaises(fetch_p1.FetchRefuse) as ctx:
                    part.interpret_peek(nil_atom, 0)
                self.assertEqual(str(ctx.exception), "empty part fetch")
                _typ, empty = conn.uid("FETCH", "9", "(BODY.PEEK[1]<0.1>)")
                with self.assertRaises(fetch_p1.FetchRefuse):
                    part.interpret_peek(empty, 0)
                _typ, later = conn.uid("FETCH", "9", "(BODY.PEEK[1]<4.8>)")
                self.assertEqual(part.interpret_peek(later, 4), b"")
                _typ, binary = conn.uid("FETCH", "9", "(BODY.PEEK[1]<0.32>)")
                self.assertEqual(part.interpret_peek(binary, 0), payload)
            finally:
                server.close()
        joined = " ".join(server.commands)
        self.assertIn("EXAMINE", joined)
        self.assertIn("BODY.PEEK[1]", joined)
        self.assertNotIn("SELECT", joined)
        self.assertNotIn("STORE", joined)
        self.assertNotIn("EXPUNGE", joined)
        self.assertNotIn(SECRET, joined)


if __name__ == "__main__":
    unittest.main()
