#!/usr/bin/env python3
"""Offline probe: can any curl invocation fetch one part byte-exact
and stay read-only?

Loopback IMAPS only (127.0.0.1, certificate minted here). No iCloud,
no Keychain, no production transport changes. Password is stdin curl
config (``-K -``), never argv. TLS verification stays on (test CA only).

curl 8.5.0 libcurl templates (this build's libcurl.so) are
``SELECT %s`` and ``UID FETCH %s BODY[%s]`` / ``UID FETCH %s BODY[%s]<%s>``.
There is no ``BODY.PEEK`` string and no ``EXAMINE %s`` command template.
``curl --manual`` says ``-X`` for IMAP "Specifies a custom IMAP command
to use instead of LIST." ``--quote`` is FTP/SFTP only.

A variant meets the invariants only when all of these hold:
the part bytes equal the 64-byte literal, curl's exit status is 0,
the command stream has no SELECT, the fetch is BODY.PEEK (not BODY[]),
and the stub's \\Seen flag stays clear. SELECT or a non-PEEK BODY[]
fails even when the bytes match.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import test_att_fetch_curl_loopback as loop

CURL_BIN = loop.CURL_BIN
SECRET = loop.SECRET
USER = loop.USER
FIXED_LITERAL = loop.FIXED_LITERAL

_LOGIN = "A002 LOGIN %s ***" % USER
_EXAMINE_LINES = (
    b"* 1 EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n"
)
_TRUNC_PEEK = (
    _EXAMINE_LINES
    + b"* 1 FETCH (BODY[1] {64}\r\n"
    + b"* 1 FETCH (FLAGS (\\Seen))\r\n"
)
_TRUNC_PEEK_ONLY = (
    b"* 1 FETCH (BODY[1] {64}\r\n* 1 FETCH (FLAGS (\\Seen))\r\n"
)
_TRUNC_NATIVE_CUSTOM = (
    b"* 1 FETCH (FLAGS (\\Seen) BODY[1] {64}\r\n"
    b"* 1 FETCH (FLAGS (\\Seen))\r\n"
)
_TRUNC_PARTIAL_PEEK = (
    _EXAMINE_LINES
    + b"* 1 FETCH (BODY[1]<0> {64}\r\n"
    + b"* 1 FETCH (FLAGS (\\Seen))\r\n"
)
_LIST_LINE = b'* LIST (\\Unmarked) "/" INBOX\r\n'

# Comparable fields. ``stdout`` is None when the expectation is "do not
# check raw stdout" (the part file is checked via byte_exact instead).
_FIELDS = (
    "rc",
    "commands",
    "nconn",
    "select",
    "examine",
    "specs",
    "nonpeek",
    "peek",
    "seen",
    "access",
    "byte_exact",
    "stderr",
    "stdout",
    "examine_file",
)


def _base_cmds(*rest: str) -> list:
    cmds = ["A001 CAPABILITY", _LOGIN]
    cmds.extend(rest)
    return cmds


def _expected() -> dict:
    """Measured command streams for /usr/bin/curl 8.5.0 on this stub."""
    select_fetch = _base_cmds(
        "A003 SELECT INBOX",
        "A004 UID FETCH 9 BODY[1]",
        "A005 LOGOUT",
    )
    rows = {
        "url_uid_section": {
            "rc": 0,
            "commands": select_fetch,
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_partial": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 UID FETCH 9 BODY[1]<0.1048576>",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]<0.1048576>"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_uidvalidity": {
            "rc": 0,
            "commands": list(select_fetch),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_uidvalidity_partial": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 UID FETCH 9 BODY[1]<0.1048576>",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]<0.1048576>"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_uid_full": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 UID FETCH 9 BODY[]",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_mailindex": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 FETCH 1 BODY[1]",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_uidvalidity_mismatch": {
            "rc": 78,
            "commands": _base_cmds("A003 SELECT INBOX", "A004 LOGOUT"),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": [],
            "nonpeek": False,
            "peek": False,
            "seen": False,
            "access": "read-write",
            "byte_exact": False,
            "stderr": "curl: (78) Mailbox UIDVALIDITY has changed",
            "stdout": b"",
            "examine_file": None,
        },
        "url_section_named_peek": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 UID FETCH 9 BODY[PEEK]",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[PEEK]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_section_dot_peek_rejected": {
            "rc": 3,
            "commands": _base_cmds("A003 LOGOUT"),
            "nconn": 1,
            "select": False,
            "examine": False,
            "specs": [],
            "nonpeek": False,
            "peek": False,
            "seen": False,
            "access": "",
            "byte_exact": False,
            "stderr": "curl: (3) URL using bad/illegal format or missing URL",
            "stdout": b"",
            "examine_file": None,
        },
        "url_head": {
            "rc": 0,
            "commands": list(select_fetch),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "url_quote_examine": {
            "rc": 0,
            "commands": list(select_fetch),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": FIXED_LITERAL,
            "examine_file": None,
        },
        "chain_examine_then_url_fetch": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 EXAMINE INBOX",
                "A004 SELECT INBOX",
                "A005 UID FETCH 9 BODY[1]",
                "A006 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": True,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": True,
            "stderr": "",
            "stdout": b"",
            "examine_file": _EXAMINE_LINES,
        },
        "chain_select_examine_then_url_fetch": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 EXAMINE INBOX",
                "A005 UID FETCH 9 BODY[1]",
                "A006 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": True,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": False,
            "access": "read-only",
            "byte_exact": True,
            "stderr": "",
            "stdout": b"",
            "examine_file": _EXAMINE_LINES,
        },
        "custom_peek_unselected": {
            "rc": 21,
            "commands": _base_cmds(
                "A003 UID FETCH 9 (BODY.PEEK[1])",
                "A004 LOGOUT",
            ),
            "nconn": 1,
            "select": False,
            "examine": False,
            "specs": ["BODY.PEEK[1]"],
            "nonpeek": False,
            "peek": True,
            "seen": False,
            "access": "",
            "byte_exact": False,
            "stderr": "curl: (21) Quote command returned error",
            "stdout": b"",
            "examine_file": None,
        },
        "custom_examine_then_peek": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 EXAMINE INBOX",
                "A004 UID FETCH 9 (BODY.PEEK[1])",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": False,
            "examine": True,
            "specs": ["BODY.PEEK[1]"],
            "nonpeek": False,
            "peek": True,
            "seen": False,
            "access": "read-only",
            "byte_exact": False,
            "stderr": "",
            "stdout": _TRUNC_PEEK,
            "examine_file": None,
        },
        "custom_peek_replaces_url_fetch": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 SELECT INBOX",
                "A004 UID FETCH 9 (BODY.PEEK[1])",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY.PEEK[1]"],
            "nonpeek": False,
            "peek": True,
            "seen": False,
            "access": "read-write",
            "byte_exact": False,
            "stderr": "",
            "stdout": _TRUNC_PEEK_ONLY,
            "examine_file": None,
        },
        "custom_native_text_not_literal_parser": {
            "rc": 0,
            "commands": list(select_fetch),
            "nconn": 1,
            "select": True,
            "examine": False,
            "specs": ["BODY[1]"],
            "nonpeek": True,
            "peek": False,
            "seen": True,
            "access": "read-write",
            "byte_exact": False,
            "stderr": "",
            "stdout": _TRUNC_NATIVE_CUSTOM,
            "examine_file": None,
        },
        "custom_examine_then_partial_peek": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 EXAMINE INBOX",
                "A004 UID FETCH 9 (BODY.PEEK[1]<0.1048576>)",
                "A005 LOGOUT",
            ),
            "nconn": 1,
            "select": False,
            "examine": True,
            "specs": ["BODY.PEEK[1]<0.1048576>"],
            "nonpeek": False,
            "peek": True,
            "seen": False,
            "access": "read-only",
            "byte_exact": False,
            "stderr": "",
            "stdout": _TRUNC_PARTIAL_PEEK,
            "examine_file": None,
        },
        "request_applies_to_every_url": {
            "rc": 0,
            "commands": _base_cmds(
                "A003 EXAMINE INBOX",
                "A004 SELECT INBOX",
                "A005 EXAMINE INBOX",
                "A006 LOGOUT",
            ),
            "nconn": 1,
            "select": True,
            "examine": True,
            "specs": [],
            "nonpeek": False,
            "peek": False,
            "seen": False,
            "access": "read-only",
            "byte_exact": False,
            "stderr": "",
            "stdout": _EXAMINE_LINES + _EXAMINE_LINES,
            "examine_file": None,
        },
        "mailbox_list_not_fetch": {
            "rc": 0,
            "commands": _base_cmds('A003 LIST "INBOX" *', "A004 LOGOUT"),
            "nconn": 1,
            "select": False,
            "examine": False,
            "specs": [],
            "nonpeek": False,
            "peek": False,
            "seen": False,
            "access": "",
            "byte_exact": False,
            "stderr": "",
            "stdout": _LIST_LINE,
            "examine_file": None,
        },
        "append_requests_seen": {
            "rc": 25,
            "commands": _base_cmds(
                "A003 APPEND INBOX (\\Seen) {5}",
                "A004 LOGOUT",
            ),
            "nconn": 1,
            "select": False,
            "examine": False,
            "specs": [],
            "nonpeek": False,
            "peek": False,
            "seen": False,
            "access": "",
            "byte_exact": False,
            "stderr": "curl: (25) Upload failed (at start/before it took off)",
            "stdout": b"",
            "examine_file": None,
        },
    }
    return rows


VARIANT_KEYS = (
    "url_uid_section",
    "url_partial",
    "url_uidvalidity",
    "url_uidvalidity_partial",
    "url_uid_full",
    "url_mailindex",
    "url_uidvalidity_mismatch",
    "url_section_named_peek",
    "url_section_dot_peek_rejected",
    "url_head",
    "url_quote_examine",
    "chain_examine_then_url_fetch",
    "chain_select_examine_then_url_fetch",
    "custom_peek_unselected",
    "custom_examine_then_peek",
    "custom_peek_replaces_url_fetch",
    "custom_native_text_not_literal_parser",
    "custom_examine_then_partial_peek",
    "request_applies_to_every_url",
    "mailbox_list_not_fetch",
    "append_requests_seen",
)


def _meets(row: dict) -> bool:
    return bool(
        row["rc"] == 0
        and row["byte_exact"]
        and not row["select"]
        and not row["nonpeek"]
        and not row["seen"]
        and row["argv_ok"]
    )


def _why(row: dict) -> str:
    reasons = []
    if row["rc"] != 0:
        reasons.append("rc=%s" % row["rc"])
    if not row["byte_exact"]:
        reasons.append("literal not byte-exact")
    if row["select"]:
        reasons.append("SELECT")
    if row["nonpeek"]:
        reasons.append("non-PEEK BODY")
    if row["seen"]:
        reasons.append("Seen set")
    if not row["argv_ok"]:
        reasons.append("argv broke tls or password rule")
    return ", ".join(reasons) or "meets"


def _curl_env() -> dict:
    blocked = {
        "CURL_BIN",
        "IMAP_APP_PASSWORD",
        "MAILROOM_IMAP_PASSWORD",
        "MAILROOM_IMAP_APP_PASSWORD",
    }
    env = {}
    for key, value in os.environ.items():
        if key in blocked or value == SECRET:
            continue
        env[key] = value
    return env


def _config(cert: str, blocks: list) -> bytes:
    parts = []
    for block in blocks:
        lines = [
            "silent",
            "show-error",
            'connect-timeout = "8"',
            'max-time = "8"',
            'user = "%s:%s"' % (USER, SECRET),
            'cacert = "%s"' % cert,
        ]
        lines.extend(block)
        parts.append("\n".join(lines))
    return ("\nnext\n".join(parts) + "\n").encode("utf-8")


def _wait_closed(server, before: int) -> list:
    deadline = time.time() + 3.0
    while time.time() < deadline:
        fresh = server.connections[before:]
        if fresh and all(conn.closed.is_set() for conn in fresh):
            return fresh
        time.sleep(0.01)
    return server.connections[before:]


def _run_variant(server, cert: str, blocks: list, part_path, examine_path) -> dict:
    before = len(server.connections)
    err_before = len(server.errors)
    argv = [CURL_BIN, "--silent", "--show-error", "-K", "-"]
    argv_ok = (
        argv[0] == CURL_BIN
        and "-k" not in argv
        and "--insecure" not in argv
        and argv[-2:] == ["-K", "-"]
        and all(SECRET not in str(item) for item in argv)
    )
    proc = subprocess.run(
        argv,
        input=_config(cert, blocks),
        capture_output=True,
        env=_curl_env(),
        timeout=15,
        check=False,
    )
    conns = _wait_closed(server, before)
    stderr = (proc.stderr or b"").decode("utf-8", "replace").replace(SECRET, "***").strip()
    stdout = proc.stdout or b""
    if SECRET.encode("ascii") in stdout or SECRET.encode("ascii") in (proc.stderr or b""):
        argv_ok = False
    commands = []
    select = False
    examine = False
    specs = []
    seen = False
    nonpeek = False
    peek = False
    access = ""
    for conn in conns:
        for raw in conn.verbatim:
            text = raw.decode("ascii", "replace")
            commands.append(text)
            body = text.split(" ", 1)[1] if " " in text else text
            verb = loop._verb(body)
            if verb == "SELECT":
                select = True
            if verb == "EXAMINE":
                examine = True
        specs.extend(conn.fetch_specs)
        if conn.seen:
            seen = True
        if conn.nonpeek:
            nonpeek = True
        if conn.peek:
            peek = True
        if conn.access:
            access = conn.access
    if part_path is None:
        part = stdout
    else:
        part = Path(part_path).read_bytes() if Path(part_path).is_file() else b""
    examine_file = None
    if examine_path is not None:
        examine_file = Path(examine_path).read_bytes() if Path(examine_path).is_file() else b""
    return {
        "rc": int(proc.returncode),
        "commands": commands,
        "nconn": len(conns),
        "select": select,
        "examine": examine,
        "specs": specs,
        "nonpeek": nonpeek,
        "peek": peek,
        "seen": seen,
        "access": access,
        "byte_exact": part == FIXED_LITERAL,
        "stderr": stderr,
        "stdout": stdout,
        "examine_file": examine_file,
        "argv_ok": argv_ok,
        "stub_errors": server.errors[err_before:],
        "part": part,
    }


def _specs(port: int, root: Path) -> list:
    base = "imaps://127.0.0.1:%d/INBOX" % port
    bare = "imaps://127.0.0.1:%d/" % port
    examine_a = root / "chain-a-examine.bin"
    fetch_a = root / "chain-a-part.bin"
    examine_b = root / "chain-b-examine.bin"
    fetch_b = root / "chain-b-part.bin"
    upload = root / "append.bin"
    upload.write_bytes(b"hello")
    uid = base + ";UID=9;SECTION=1"
    return [
        ("url_uid_section", [['url = "%s"' % uid]], None, None),
        (
            "url_partial",
            [['url = "%s;PARTIAL=0.1048576"' % uid]],
            None,
            None,
        ),
        (
            "url_uidvalidity",
            [['url = "%s;UIDVALIDITY=1;UID=9;SECTION=1"' % base]],
            None,
            None,
        ),
        (
            "url_uidvalidity_partial",
            [['url = "%s;UIDVALIDITY=1;UID=9;SECTION=1;PARTIAL=0.1048576"' % base]],
            None,
            None,
        ),
        ("url_uid_full", [['url = "%s;UID=9"' % base]], None, None),
        (
            "url_mailindex",
            [['url = "%s;MAILINDEX=1;SECTION=1"' % base]],
            None,
            None,
        ),
        (
            "url_uidvalidity_mismatch",
            [['url = "%s;UIDVALIDITY=999;UID=9;SECTION=1"' % base]],
            None,
            None,
        ),
        (
            "url_section_named_peek",
            [['globoff', 'url = "%s;UID=9;SECTION=PEEK"' % base]],
            None,
            None,
        ),
        (
            "url_section_dot_peek_rejected",
            [['globoff', 'url = "%s;UID=9;SECTION=.PEEK[1]"' % base]],
            None,
            None,
        ),
        ("url_head", [["head", 'url = "%s"' % uid]], None, None),
        (
            "url_quote_examine",
            [['quote = "EXAMINE INBOX"', 'url = "%s"' % uid]],
            None,
            None,
        ),
        (
            "chain_examine_then_url_fetch",
            [
                [
                    'request = "EXAMINE INBOX"',
                    'output = "%s"' % examine_a,
                    'url = "%s"' % bare,
                ],
                ['output = "%s"' % fetch_a, 'url = "%s"' % uid],
            ],
            fetch_a,
            examine_a,
        ),
        (
            "chain_select_examine_then_url_fetch",
            [
                [
                    'request = "EXAMINE INBOX"',
                    'output = "%s"' % examine_b,
                    'url = "%s"' % base,
                ],
                ['output = "%s"' % fetch_b, 'url = "%s"' % uid],
            ],
            fetch_b,
            examine_b,
        ),
        (
            "custom_peek_unselected",
            [['request = "UID FETCH 9 (BODY.PEEK[1])"', 'url = "%s"' % bare]],
            None,
            None,
        ),
        (
            "custom_examine_then_peek",
            [
                ['request = "EXAMINE INBOX"', 'url = "%s"' % bare],
                ['request = "UID FETCH 9 (BODY.PEEK[1])"', 'url = "%s"' % bare],
            ],
            None,
            None,
        ),
        (
            "custom_peek_replaces_url_fetch",
            [[
                'request = "UID FETCH 9 (BODY.PEEK[1])"',
                'url = "%s"' % uid,
            ]],
            None,
            None,
        ),
        (
            "custom_native_text_not_literal_parser",
            [[
                'request = "UID FETCH 9 BODY[1]"',
                'url = "%s"' % base,
            ]],
            None,
            None,
        ),
        (
            "custom_examine_then_partial_peek",
            [
                ['request = "EXAMINE INBOX"', 'url = "%s"' % bare],
                [
                    'request = "UID FETCH 9 (BODY.PEEK[1]<0.1048576>)"',
                    'url = "%s"' % bare,
                ],
            ],
            None,
            None,
        ),
        (
            "request_applies_to_every_url",
            [[
                'request = "EXAMINE INBOX"',
                'url = "%s"' % bare,
                'url = "%s"' % uid,
            ]],
            None,
            None,
        ),
        ("mailbox_list_not_fetch", [['url = "%s"' % base]], None, None),
        (
            "append_requests_seen",
            [['upload-file = "%s"' % upload, 'url = "%s"' % base]],
            None,
            None,
        ),
    ]


def _project(row: dict) -> dict:
    return {key: row[key] for key in _FIELDS}


def _libcurl_path() -> str:
    proc = subprocess.run(
        ["ldd", CURL_BIN],
        check=False,
        capture_output=True,
    )
    text = (proc.stdout or b"").decode("utf-8", "replace")
    for line in text.splitlines():
        if "libcurl.so" not in line:
            continue
        parts = line.split()
        for part in parts:
            if part.startswith("/") and "libcurl.so" in part:
                return part
    raise AssertionError("libcurl not found via ldd %s" % CURL_BIN)


class CurlUrlFormProbeTests(unittest.TestCase):
    RESULTS = {}
    VERSION = ""

    @classmethod
    def setUpClass(cls) -> None:
        if not os.path.exists(CURL_BIN):
            raise unittest.SkipTest("pinned curl binary /usr/bin/curl is absent")
        cls.VERSION = loop._curl_version_text()
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
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
        cls.server = loop._LoopbackImap(str(cert), str(key))
        cls.cert = str(cert)
        cls.RESULTS = {}
        for key, blocks, part_path, examine_path in _specs(cls.server.port, root):
            cls.RESULTS[key] = _run_variant(
                cls.server, cls.cert, blocks, part_path, examine_path
            )

    @classmethod
    def tearDownClass(cls) -> None:
        server = getattr(cls, "server", None)
        if server is not None:
            server.close()
        tmp = getattr(cls, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def test_curl_version_and_libcurl_templates(self) -> None:
        version = type(self).VERSION
        self.assertTrue(version.startswith("curl 8.5.0 "), version)
        self.assertIn("imap", version)
        self.assertIn("imaps", version)
        blob = Path(_libcurl_path()).read_bytes()
        self.assertNotIn(b"BODY.PEEK", blob)
        self.assertNotIn(b"EXAMINE %s", blob)
        for needle in (
            b"SELECT %s",
            b"UID FETCH %s BODY[%s]<%s>",
            b"UID FETCH %s BODY[%s]",
            b"Cannot FETCH without a UID.",
            b"Mailbox UIDVALIDITY has changed",
            b"APPEND %s (\\Seen) {%ld}",
            b"UIDVALIDITY",
            b"MAILINDEX",
            b"SECTION",
            b"PARTIAL",
        ):
            self.assertIn(needle, blob, needle)
        manual = subprocess.run(
            [CURL_BIN, "--manual"],
            check=False,
            capture_output=True,
        )
        text = (manual.stdout or b"").decode("utf-8", "replace")
        self.assertIn(
            "Specifies a custom IMAP command to use instead of",
            text,
        )
        quote_at = text.find("-Q, --quote <command>")
        self.assertGreater(quote_at, 0)
        quote = text[quote_at : quote_at + 500]
        self.assertIn("(FTP", quote)
        self.assertIn("SFTP)", quote)
        self.assertNotIn("IMAP", quote)

    def test_stub_seen_flag_follows_rfc3501(self) -> None:
        """Direct client, still loopback TLS with verification on."""
        cases = [
            (
                ["SELECT INBOX", "UID FETCH 9 BODY[1]", "LOGOUT"],
                {"access": "read-write", "seen": True, "nonpeek": True, "peek": False},
            ),
            (
                ["EXAMINE INBOX", "UID FETCH 9 BODY[1]", "LOGOUT"],
                {"access": "read-only", "seen": False, "nonpeek": True, "peek": False},
            ),
            (
                ["SELECT INBOX", "UID FETCH 9 (BODY.PEEK[1])", "LOGOUT"],
                {"access": "read-write", "seen": False, "nonpeek": False, "peek": True},
            ),
            (
                ["SELECT INBOX", "FETCH 1 BODY[1]<0.1048576>", "LOGOUT"],
                {"access": "read-write", "seen": True, "nonpeek": True, "peek": False},
            ),
            (
                ["EXAMINE INBOX", "UID FETCH 9 (BODY.PEEK[1]<0.1048576>)", "LOGOUT"],
                {"access": "read-only", "seen": False, "nonpeek": False, "peek": True},
            ),
        ]
        for commands, expect in cases:
            log = _speak(type(self).server, type(self).cert, commands)
            self.assertEqual(log.access, expect["access"], commands)
            self.assertEqual(log.seen, expect["seen"], commands)
            self.assertEqual(log.nonpeek, expect["nonpeek"], commands)
            self.assertEqual(log.peek, expect["peek"], commands)
            self.assertGreaterEqual(len(log.verbatim), 3, commands)
            joined = b"\n".join(log.verbatim)
            self.assertNotIn(SECRET.encode("ascii"), joined)
            self.assertIn(commands[0].encode("ascii"), joined)
            self.assertIn(commands[1].encode("ascii"), joined)

    def test_observed_command_streams(self) -> None:
        expected = _expected()
        self.assertEqual(tuple(expected), VARIANT_KEYS)
        mismatches = []
        for key in VARIANT_KEYS:
            got = _project(type(self).RESULTS[key])
            want = expected[key]
            if got != want:
                mismatches.append((key, got, want))
            row = type(self).RESULTS[key]
            self.assertEqual(row["stub_errors"], [], key)
            self.assertTrue(row["argv_ok"], key)
            self.assertNotIn(SECRET, " ".join(row["commands"]), key)
        self.assertEqual(mismatches, [])
        met = [key for key in VARIANT_KEYS if _meets(type(self).RESULTS[key])]
        self.assertEqual(met, [])

    def test_literal_bytes(self) -> None:
        self.assertEqual(len(FIXED_LITERAL), 64)
        self.assertEqual(
            FIXED_LITERAL,
            b"\x00\x01line-one\r\n"
            b"* 1 FETCH (FLAGS (\\Seen))\r\n"
            b"A003 OK\r\n"
            b"A004 OK\r\n"
            b"\xff\xfe tail",
        )


def _speak(server, cert: str, commands: list):
    before = len(server.connections)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cert)
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    raw = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    tls = context.wrap_socket(raw, server_hostname="127.0.0.1")
    try:
        buf_box = [b""]
        _read_line(tls, buf_box)
        for index, command in enumerate(commands, 1):
            tag = "T%03d" % index
            tls.sendall(("%s %s\r\n" % (tag, command)).encode("ascii"))
            _read_until_tag(tls, buf_box, tag)
    finally:
        tls.close()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if len(server.connections) > before and server.connections[before].closed.is_set():
            break
        time.sleep(0.01)
    log = server.connections[before]
    log.closed.wait(1.0)
    return log


def _fill(tls, box: list) -> None:
    while b"\r\n" not in box[0]:
        chunk = tls.recv(65536)
        if not chunk:
            raise OSError("closed")
        box[0] += chunk


def _read_line(tls, box: list) -> bytes:
    _fill(tls, box)
    line, rest = box[0].split(b"\r\n", 1)
    box[0] = rest
    return line


def _read_until_tag(tls, box: list, tag: str) -> None:
    tagb = tag.encode("ascii")
    while True:
        line = _read_line(tls, box)
        match = loop._LITERAL_RE.search(line)
        if match:
            need = int(match.group(1))
            while len(box[0]) < need:
                chunk = tls.recv(65536)
                if not chunk:
                    raise OSError("closed")
                box[0] += chunk
            box[0] = box[0][need:]
        if line.startswith(tagb + b" "):
            return


def _bind_invariant_tests() -> None:
    for key in VARIANT_KEYS:

        def test(self, key=key):
            row = type(self).RESULTS[key]
            self.assertTrue(_meets(row), "%s: %s" % (key, _why(row)))

        test.__name__ = "test_invariants_%s" % key
        test.__doc__ = (
            "Expected to fail: %s does not meet read-only byte-exact invariants."
            % key
        )
        setattr(
            CurlUrlFormProbeTests,
            test.__name__,
            unittest.expectedFailure(test),
        )


_bind_invariant_tests()


if __name__ == "__main__":
    unittest.main()
