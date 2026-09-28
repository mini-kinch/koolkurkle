#!/usr/bin/env python3
"""Real imaplib against a test-only loopback IMAPS stub.

No mocks of imaplib, ssl, or socket. No Keychain and no host beyond
127.0.0.1. The certificate is generated in the test. ``subprocess`` is
patched only around the dial so a child process cannot receive the
password; the IMAP stack itself is the stdlib client.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.fetch_p1 as fetch_p1  # noqa: E402
import attachments.imaplib_part as imaplib_part  # noqa: E402
from test_att_fetch_curl_loopback import (  # noqa: E402
    SECRET,
    USER,
    _LoopbackImap,
    _literal_payload,
    _verb,
)

_FIXED = _literal_payload("A004")
_MIB = _FIXED * ((1024 * 1024) // len(_FIXED))
_RANGE = re.compile(r"<(\d+)\.(\d+)>")
_PINNED = re.compile(
    r"^UID FETCH [1-9]\d* \(BODY\.PEEK\[[1-9]\d*(?:\.[1-9]\d*)*\]<\d+\.\d+>\)$"
)
_ALLOWED = frozenset(("CAPABILITY", "LOGIN", "EXAMINE", "UID FETCH", "LOGOUT"))
_BANNED = frozenset(
    ("SELECT", "STORE", "EXPUNGE", "APPEND", "UID STORE", "UID EXPUNGE")
)


def _mint(directory: Path, name: str, san: str) -> tuple:
    key = directory / ("%s.key" % name)
    cert = directory / ("%s.crt" % name)
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
            san,
        ],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError("test cert failed: %s" % detail)
    return key, cert


def _send_literal(tls: ssl.SSLSocket, tag: str, section: str, payload: bytes) -> None:
    header = ("* 1 FETCH (%s {%d}\r\n" % (section, len(payload))).encode("ascii")
    tls.sendall(
        header
        + payload
        + b")\r\n"
        + tag.encode("ascii")
        + b" OK FETCH completed\r\n"
    )


def _ranged(payload: bytes):
    def handler(server: _LoopbackImap, tls: ssl.SSLSocket, tag: str, body: str) -> None:
        match = _RANGE.search(body)
        if match is None:
            tls.sendall(tag.encode("ascii") + b" BAD parse\r\n")
            return
        offset = int(match.group(1))
        count = int(match.group(2))
        chunk = payload[offset : offset + count]
        server.served = payload
        _send_literal(tls, tag, "BODY[1]<%d>" % offset, chunk)

    return handler


def _forbid_subprocess(*_args, **_kwargs):
    raise AssertionError("subprocess is forbidden")


class ImaplibInvariantTests(unittest.TestCase):
    def test_pinned_regex_rejects_non_peek(self) -> None:
        self.assertEqual(
            imaplib_part.pinned_fetch_command("9", "(BODY.PEEK[1]<0.1048576>)"),
            "UID FETCH 9 (BODY.PEEK[1]<0.1048576>)",
        )
        self.assertEqual(
            imaplib_part.pinned_fetch_command("12", "(BODY.PEEK[1.2]<10.20>)"),
            "UID FETCH 12 (BODY.PEEK[1.2]<10.20>)",
        )
        rejected = (
            ("9", "(BODY[1]<0.10>)"),
            ("9", "(BODY.PEEK[1])"),
            ("9", "BODY.PEEK[1]<0.10>"),
            ("0", "(BODY.PEEK[1]<0.1>)"),
            ("9", "(BODY.PEEK[1]<0.1>) extra"),
            ("9", "(BODY.PEEK[1]<0>)"),
        )
        for uid, item in rejected:
            with self.assertRaises(fetch_p1.FetchRefuse):
                imaplib_part.pinned_fetch_command(uid, item)

    def test_context_is_verified_and_source_has_no_insecure_option(self) -> None:
        context = imaplib_part.verified_context(None)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        text = (SCRIPTS / "attachments" / "imaplib_part.py").read_text(encoding="utf-8")
        self.assertIn("ssl.create_default_context()", text)
        self.assertIn("imaplib.IMAP4_SSL", text)
        self.assertNotIn("imaplib.IMAP4(", text)
        self.assertNotIn("CERT_NONE", text)
        self.assertNotIn("check_hostname=False", text)
        self.assertNotIn("check_hostname = False", text)
        self.assertNotIn("os.environ", text)
        self.assertNotIn("subprocess", text)
        self.assertNotIn("keychain", text.lower())
        fetch_text = (SCRIPTS / "attachments" / "fetch_p1.py").read_text(encoding="utf-8")
        self.assertNotRegex(
            fetch_text,
            r"(?m)^\s*(?:import\s+imaplib\b|from\s+imaplib\b)",
        )
        self.assertNotIn("IMAP4_SSL", fetch_text)

    def test_default_port_is_993_without_dialing(self) -> None:
        client = fetch_p1.ImapPartClient(
            "imap.example.invalid",
            "fixture-user",
            transport="imaplib",
            password_fn=lambda: SECRET,
        )
        self.assertEqual(client.port, 993)
        self.assertEqual(client._transport, "imaplib")
        self.assertIsNone(client._conn)

    def test_sor_basename_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mailroom.sqlite"
            path.write_bytes(b"")
            with self.assertRaises(fetch_p1.FetchRefuse) as ctx:
                fetch_p1.refuse_sor_basename(path)
            self.assertIn("mailroom.sqlite", str(ctx.exception))

    def test_copy_open_stays_query_only(self) -> None:
        source = Path(fetch_p1.__file__).read_text(encoding="utf-8")
        self.assertIn('"%s?mode=ro" % path.resolve().as_uri()', source)
        self.assertIn("PRAGMA query_only = ON", source)
        self.assertEqual(fetch_p1.CHUNK_BYTES, 1024 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            conn = sqlite3.connect(str(db))
            try:
                conn.execute("CREATE TABLE t (id INTEGER)")
                conn.commit()
            finally:
                conn.close()
            ro = fetch_p1._connect_ro(db)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    ro.execute("INSERT INTO t (id) VALUES (1)")
            finally:
                ro.close()


class ImaplibLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._root = Path(tempfile.mkdtemp(prefix="imaplib-probe-"))
        cls._key, cls._cert = _mint(
            cls._root,
            "good",
            "subjectAltName=IP:127.0.0.1",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def _server(self, handler=None, greet: bool = True, cert: Path | None = None, key: Path | None = None):
        chosen_cert = self._cert if cert is None else cert
        chosen_key = self._key if key is None else key
        server = _LoopbackImap(
            str(chosen_cert),
            str(chosen_key),
            fetch_handler=handler,
            greet=greet,
        )
        self.assertEqual(server._sock.getsockname()[0], "127.0.0.1")
        return server

    def _fetch(self, server, dest: Path, ca_file: Path, timeout: float = 8, probe_rejects: bool = False) -> int:
        before = dict(os.environ)
        for value in before.values():
            self.assertNotIn(SECRET, str(value))
        self.assertNotIn(SECRET, " ".join(sys.argv))
        try:
            with mock.patch("subprocess.run", _forbid_subprocess), mock.patch(
                "subprocess.Popen", _forbid_subprocess
            ):
                client = fetch_p1.ImapPartClient(
                    "127.0.0.1",
                    USER,
                    timeout=timeout,
                    port=server.port,
                    ca_file=str(ca_file),
                    password_fn=lambda: SECRET,
                    transport="imaplib",
                )
                with client:
                    if probe_rejects:
                        self.assertEqual(client._conn.socket_timeout, timeout)
                        with self.assertRaises(fetch_p1.FetchRefuse):
                            client._conn.select('"INBOX"', readonly=False)
                        with self.assertRaises(fetch_p1.FetchRefuse):
                            client._conn.uid("STORE", "9", "+FLAGS (\\Seen)")
                        self.assertFalse(hasattr(client._conn, "append"))
                        self.assertFalse(hasattr(client._conn, "expunge"))
                        self.assertFalse(hasattr(client._conn, "store"))
                    return client.fetch_part("INBOX", "9", "1", dest)
        finally:
            self.assertEqual(dict(os.environ), before)
            for value in os.environ.values():
                self.assertNotIn(SECRET, str(value))
            self.assertNotIn(SECRET, " ".join(sys.argv))
            server.close()

    def _commands(self, server) -> list:
        self.assertEqual(len(server.connections), 1, server.connections)
        return list(server.connections[0])

    def _assert_readonly(self, commands: list, fetches: int) -> None:
        verbs = [_verb(command) for command in commands]
        for verb in verbs:
            self.assertNotIn(verb, _BANNED, commands)
            self.assertIn(verb, _ALLOWED, commands)
        self.assertIn("EXAMINE", verbs, commands)
        self.assertNotIn("SELECT", verbs, commands)
        got = [command for command in commands if _verb(command) == "UID FETCH"]
        self.assertEqual(len(got), fetches, commands)
        for command in got:
            self.assertRegex(command, _PINNED)
        self.assertNotIn(SECRET, "\n".join(commands))

    def _assert_nothing(self, dest: Path) -> None:
        self.assertFalse(dest.exists())
        if dest.parent.exists():
            self.assertEqual(list(dest.parent.iterdir()), [])

    def test_literal_bytes_survive_on_one_connection(self) -> None:
        self.assertEqual(len(_FIXED), 64)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=_ranged(_FIXED))
            nbytes = self._fetch(server, dest, self._cert, probe_rejects=True)
            got = dest.read_bytes()
            self.assertEqual([path.name for path in dest.parent.iterdir()], ["part"])
        self.assertEqual(nbytes, 64)
        self.assertEqual(got, _FIXED)
        self.assertEqual(
            got,
            b"\x00\x01line-one\r\n* 1 FETCH (FLAGS (\\Seen))\r\nA003 OK\r\nA004 OK\r\n\xff\xfe tail",
        )
        commands = self._commands(server)
        self._assert_readonly(commands, 1)
        self.assertEqual(
            [command for command in commands if _verb(command) == "UID FETCH"],
            ["UID FETCH 9 (BODY.PEEK[1]<0.1048576>)"],
        )

    def test_exact_one_mib_ends_on_empty_literal(self) -> None:
        self.assertEqual(len(_MIB), 1024 * 1024)
        self.assertEqual(fetch_p1.CHUNK_BYTES, 1024 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=_ranged(_MIB))
            nbytes = self._fetch(server, dest, self._cert)
            got = dest.read_bytes()
        self.assertEqual(nbytes, 1024 * 1024)
        self.assertEqual(got, _MIB)
        commands = self._commands(server)
        self._assert_readonly(commands, 2)
        self.assertEqual(
            [command for command in commands if _verb(command) == "UID FETCH"],
            [
                "UID FETCH 9 (BODY.PEEK[1]<0.1048576>)",
                "UID FETCH 9 (BODY.PEEK[1]<1048576.1048576>)",
            ],
        )

    def test_nil_at_offset_zero_stores_nothing(self) -> None:
        def handler(_server, tls, tag, _body):
            tls.sendall(
                b"* 1 FETCH (BODY[1]<0> NIL)\r\n"
                + tag.encode("ascii")
                + b" OK FETCH completed\r\n"
            )

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=handler)
            with self.assertRaises(fetch_p1.FetchRefuse):
                self._fetch(server, dest, self._cert)
            self._assert_nothing(dest)
        self._assert_readonly(self._commands(server), 1)

    def test_empty_literal_at_offset_zero_stores_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=_ranged(b""))
            with self.assertRaises(fetch_p1.FetchRefuse):
                self._fetch(server, dest, self._cert)
            self._assert_nothing(dest)
        self._assert_readonly(self._commands(server), 1)

    def test_short_literal_stores_nothing(self) -> None:
        def handler(_server, tls, _tag, _body):
            tls.sendall(b"* 1 FETCH (BODY[1]<0> {64}\r\n\x00\x01short")
            try:
                tls.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            tls.close()

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=handler)
            with self.assertRaises(fetch_p1.FetchRefuse):
                self._fetch(server, dest, self._cert)
            self._assert_nothing(dest)
        commands = self._commands(server)
        verbs = [_verb(command) for command in commands]
        self.assertIn("EXAMINE", verbs)
        self.assertNotIn("SELECT", verbs)
        for verb in verbs:
            self.assertNotIn(verb, _BANNED)

    def test_no_response_stores_nothing(self) -> None:
        def handler(_server, tls, tag, _body):
            tls.sendall(tag.encode("ascii") + b" NO no such message\r\n")

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=handler)
            with self.assertRaises(fetch_p1.FetchRefuse):
                self._fetch(server, dest, self._cert)
            self._assert_nothing(dest)
        self._assert_readonly(self._commands(server), 1)

    def test_bad_response_stores_nothing(self) -> None:
        def handler(_server, tls, tag, _body):
            tls.sendall(tag.encode("ascii") + b" BAD parse\r\n")

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(handler=handler)
            with self.assertRaises(fetch_p1.FetchRefuse):
                self._fetch(server, dest, self._cert)
            self._assert_nothing(dest)
        self._assert_readonly(self._commands(server), 1)

    def test_socket_timeout_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bytes" / "part"
            server = self._server(greet=False)
            started = time.monotonic()
            with self.assertRaises(fetch_p1.FetchRefuse):
                self._fetch(server, dest, self._cert, timeout=0.8)
            elapsed = time.monotonic() - started
            self._assert_nothing(dest)
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 2.5)
        self.assertEqual(len(server.connections), 1)
        self.assertEqual(server.connections[0], [])

    def test_wrong_ca_stores_nothing(self) -> None:
        _other_key, other_cert = _mint(
            Path(tempfile.mkdtemp(prefix="imaplib-probe-ca-")),
            "other",
            "subjectAltName=IP:127.0.0.1",
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "bytes" / "part"
                server = self._server(handler=_ranged(_FIXED))
                with self.assertRaises(fetch_p1.FetchRefuse):
                    self._fetch(server, dest, other_cert)
                self._assert_nothing(dest)
        finally:
            shutil.rmtree(other_cert.parent, ignore_errors=True)

    def test_hostname_mismatch_stores_nothing(self) -> None:
        host_key, host_cert = _mint(
            Path(tempfile.mkdtemp(prefix="imaplib-probe-name-")),
            "name",
            "subjectAltName=DNS:loopback.invalid",
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "bytes" / "part"
                server = self._server(
                    handler=_ranged(_FIXED),
                    cert=host_cert,
                    key=host_key,
                )
                with self.assertRaises(fetch_p1.FetchRefuse):
                    self._fetch(server, dest, host_cert)
                self._assert_nothing(dest)
        finally:
            shutil.rmtree(host_cert.parent, ignore_errors=True)

    def test_ca_hook_keeps_verification_on(self) -> None:
        context = imaplib_part.verified_context(str(self._cert))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


if __name__ == "__main__":
    unittest.main()
