#!/usr/bin/env python3
"""ATT-1/ATT-2 P1 byte fetch and out-of-process extract.

Synthetic fixtures only. IMAP is mocked. No network. No Keychain.
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import att0_constraints as att0  # noqa: E402
import attachments.fetch_p1 as fetch_p1  # noqa: E402

FETCH_SRC = SCRIPTS / "attachments" / "fetch_p1.py"
WORKER_SRC = SCRIPTS / "attachments" / "p1_worker.py"
ASK = SCRIPTS / "ask_mail.py"
SECRET = "example-secret-token"

SCHEMA = """
CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  source TEXT,
  folder TEXT,
  lane TEXT,
  uid TEXT
);
CREATE TABLE attachments (
  attachment_id INTEGER PRIMARY KEY,
  message_id TEXT NOT NULL,
  part_id TEXT NOT NULL,
  filename TEXT,
  mime TEXT,
  size INTEGER,
  sha256 TEXT,
  status TEXT NOT NULL,
  content_disposition TEXT
);
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed(path: Path, messages: list[tuple], parts: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO messages (id, source, folder, lane, uid) VALUES (?, ?, ?, ?, ?)",
            messages,
        )
        conn.executemany(
            "INSERT INTO attachments "
            "(attachment_id, message_id, part_id, filename, mime, size, sha256, "
            "status, content_disposition) VALUES (?, ?, ?, ?, ?, ?, NULL, 'meta', ?)",
            parts,
        )
        conn.commit()
    finally:
        conn.close()


def _by_id(report: dict, attachment_id: int) -> dict:
    for record in report["records"]:
        if record["attachment_id"] == attachment_id:
            return record
    raise AssertionError("missing attachment %s" % attachment_id)


def _boom_fetch(*_args, **_kwargs):
    raise AssertionError("fetch is forbidden")


def _boom_runner(*_args, **_kwargs):
    raise AssertionError("extractor is forbidden")


class CopyDbAndDryRunTests(unittest.TestCase):
    def test_refuses_sor_basename_without_override(self):
        source = FETCH_SRC.read_text(encoding="utf-8")
        self.assertNotIn("allow-mailroom-sqlite", source)
        self.assertNotIn("allow_mailroom_sqlite", source)
        help_text = fetch_p1.build_parser().format_help()
        self.assertNotIn("allow-mailroom-sqlite", help_text)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sor = root / "mailroom.sqlite"
            sor.write_bytes(b"not-a-database")
            stage = root / "stage"
            digest = _sha(sor)
            with self.assertRaises(fetch_p1.FetchRefuse) as ctx:
                fetch_p1.run_fetch_extract(
                    sor,
                    stage,
                    dry_run=False,
                    fetch_part=_boom_fetch,
                    runner=_boom_runner,
                )
            self.assertIn("mailroom.sqlite", str(ctx.exception))
            self.assertNotIn("allow-mailroom", str(ctx.exception))
            self.assertEqual(_sha(sor), digest)
            self.assertFalse(stage.exists())
            err = io.StringIO()
            with redirect_stderr(err):
                code = fetch_p1.main(
                    ["--apply", "--db", str(sor), "--stage", str(stage), "--host", "imap.example.com", "--user", "user@example.com"]
                )
            self.assertEqual(code, 2)
            self.assertIn("mailroom.sqlite", err.getvalue())
            self.assertNotIn("allow-mailroom", err.getvalue())
            self.assertFalse(stage.exists())

    def test_refuses_unknown_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "notes.sqlite"
            db.write_bytes(b"")
            with self.assertRaises(fetch_p1.FetchRefuse) as ctx:
                fetch_p1.run_fetch_extract(db, root / "stage", dry_run=True)
            self.assertIn("not a copy database", str(ctx.exception))

    def test_dry_run_writes_nothing_and_does_not_fetch(self):
        cap = fetch_p1.BLOB_CAP
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [
                    ("msg-auth", "imap-live", "INBOX", "auth", "10"),
                    ("msg-big", "imap-live", "INBOX", "bills", "11"),
                    ("msg-zip", "imap-live", "INBOX", "bills", "12"),
                    ("msg-ok", "imap-live", "INBOX", "bills", "13"),
                ],
                [
                    (1, "msg-auth", "1", "note.txt", "text/plain", 10, "attachment"),
                    (2, "msg-big", "1", "huge.pdf", "application/pdf", cap + 1, "attachment"),
                    (3, "msg-zip", "1", "bundle.zip", "application/zip", 20, "attachment"),
                    (4, "msg-ok", "1", "note.txt", "text/plain", 5, "attachment"),
                ],
            )
            digest = _sha(db)
            out = io.StringIO()
            with redirect_stdout(out):
                code = fetch_p1.main(["--dry-run", "--db", str(db), "--stage", str(stage)])
            self.assertEqual(code, 0)
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=True,
                fetch_part=_boom_fetch,
                runner=_boom_runner,
            )
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["db_basename"], "mailroom-copy.sqlite")
            self.assertFalse(stage.exists())
            self.assertEqual(_sha(db), digest)
            self.assertEqual(report["counts"]["excluded_auth"], 1)
            self.assertEqual(report["counts"]["too_big"], 1)
            self.assertEqual(report["counts"]["skipped_archive"], 1)
            self.assertEqual(report["counts"]["planned"], 1)
            self.assertEqual(report["sizes"]["planned_bytes"], 5)
            self.assertEqual(report["sizes"]["too_big_bytes"], cap + 1)
            self.assertEqual(report["sizes"]["fetched_bytes"], 0)
            text = out.getvalue()
            self.assertIn("dry_run=1", text)
            self.assertIn("planned_bytes=5", text)
            self.assertNotIn(str(stage), text)


class CapAuthArchiveTests(unittest.TestCase):
    def test_too_big_at_cap_boundary(self):
        cap = fetch_p1.BLOB_CAP
        self.assertEqual(cap, 50 * 1024 * 1024)
        self.assertEqual(cap, att0.BLOB_TOO_BIG_BYTES)
        calls = []

        def fetch(folder, uid, part):
            calls.append((folder, uid, part))
            return b"borderline-text"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [
                    ("msg-at", "imap-live", "INBOX", "bills", "21"),
                    ("msg-over", "imap-live", "INBOX", "bills", "22"),
                ],
                [
                    (1, "msg-at", "1", "at.txt", "text/plain", cap, "attachment"),
                    (2, "msg-over", "1", "over.txt", "text/plain", cap + 1, "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
            )
            self.assertEqual(calls, [("INBOX", "21", "1")])
            at_row = _by_id(report, 1)
            over = _by_id(report, 2)
            self.assertEqual(at_row["status"], "ok")
            self.assertEqual(over["status"], "too_big")
            self.assertTrue((stage / "bytes" / "1").is_file())
            self.assertFalse((stage / "bytes" / "2").exists())
            self.assertEqual((stage / "text" / "1.txt").read_text(encoding="utf-8"), "borderline-text")

    def test_payload_over_cap_is_not_written(self):
        calls = {"runner": 0}

        def fetch(folder, uid, part):
            del folder, uid
            if part == "1":
                return b"01234567"
            if part == "2":
                return b"012345678"
            raise AssertionError(part)

        def runner(argv, timeout, env):
            calls["runner"] += 1
            return fetch_p1._default_runner(argv, timeout, env)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-daily-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-cap", "imap-live", "INBOX", "bills", "30")],
                [
                    (1, "msg-cap", "1", "eight.txt", "text/plain", 8, "attachment"),
                    (2, "msg-cap", "2", "nine.txt", "text/plain", 8, "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
                max_bytes=8,
                runner=runner,
            )
            self.assertEqual(_by_id(report, 1)["status"], "ok")
            self.assertEqual(_by_id(report, 2)["status"], "too_big")
            self.assertEqual(calls["runner"], 1)
            self.assertTrue((stage / "bytes" / "1").is_file())
            self.assertEqual((stage / "bytes" / "1").read_bytes(), b"01234567")
            self.assertFalse((stage / "bytes" / "2").exists())

    def test_lane_auth_excluded(self):
        calls = []

        def fetch(folder, uid, part):
            calls.append((folder, uid, part))
            return b"visible text"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [
                    ("msg-auth", "imap-live", "INBOX", "auth", "40"),
                    ("msg-bill", "imap-live", "INBOX", "bills", "41"),
                ],
                [
                    (1, "msg-auth", "1", "code.txt", "text/plain", 4, "attachment"),
                    (2, "msg-bill", "1", "note.txt", "text/plain", 12, "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
            )
            self.assertEqual(calls, [("INBOX", "41", "1")])
            self.assertEqual(_by_id(report, 1)["status"], "excluded_auth")
            self.assertEqual(_by_id(report, 2)["status"], "ok")
            self.assertFalse((stage / "bytes" / "1").exists())
            self.assertFalse((stage / "text" / "1.txt").exists())
            self.assertTrue((stage / "text" / "2.txt").is_file())

    def test_archive_skipped_not_unpacked(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("secret.txt", "ARCHIVE_MEMBER_TOKEN")
        payload = buf.getvalue()
        calls = []

        def fetch(folder, uid, part):
            calls.append(part)
            if part == "2":
                return payload
            raise AssertionError("archive row was fetched")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-zip", "imap-live", "INBOX", "bills", "50")],
                [
                    (1, "msg-zip", "1", "bundle.zip", "application/zip", len(payload), "attachment"),
                    (2, "msg-zip", "2", "note.txt", "text/plain", len(payload), "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
            )
            self.assertEqual(calls, ["2"])
            self.assertEqual(_by_id(report, 1)["status"], "skipped_archive")
            self.assertEqual(_by_id(report, 2)["status"], "skipped_archive")
            self.assertFalse((stage / "bytes" / "1").exists())
            self.assertFalse((stage / "bytes" / "2").exists())
            self.assertFalse((stage / "secret.txt").exists())
            for path in stage.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"ARCHIVE_MEMBER_TOKEN", path.read_bytes())
            for name in ("fetch_p1.py", "p1_worker.py"):
                text = (SCRIPTS / "attachments" / name).read_text(encoding="utf-8")
                self.assertNotIn("zipfile", text, name)
                self.assertNotIn("tarfile", text, name)
                self.assertNotIn("shell=True", text, name)


class ExtractorTests(unittest.TestCase):
    def test_truncation_flag_at_two_megabytes(self):
        text_cap = att0.EXTRACT_TEXT_CAP_BYTES
        self.assertEqual(text_cap, 2 * 1024 * 1024)

        def fetch(folder, uid, part):
            del folder, uid
            if part == "1":
                return b"A" * text_cap
            if part == "2":
                return b"B" * (text_cap + 1)
            raise AssertionError(part)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-text", "imap-live", "INBOX", "bills", "60")],
                [
                    (1, "msg-text", "1", "exact.txt", "text/plain", text_cap, "attachment"),
                    (2, "msg-text", "2", "over.txt", "text/plain", text_cap + 1, "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
            )
            exact = stage / "text" / "1.txt"
            over = stage / "text" / "2.txt"
            self.assertEqual(exact.stat().st_size, text_cap)
            self.assertFalse(_by_id(report, 1)["truncated"])
            self.assertEqual(over.stat().st_size, text_cap)
            self.assertTrue(_by_id(report, 2)["truncated"])
            self.assertEqual(_by_id(report, 2)["status"], "ok")
            status = (stage / "status" / "2.json").read_text(encoding="utf-8")
            self.assertIn('"truncated": true', status)

    def test_html_sidecar_produced(self):
        raw = (
            b"<html><body><script>not-in-sidecar</script>"
            b"<p>Hello synthetic</p></body></html>"
        )

        def fetch(folder, uid, part):
            del folder, uid, part
            return raw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-html", "imap-live", "INBOX", "bills", "70")],
                [
                    (1, "msg-html", "1", "page.html", "text/html", len(raw), "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
            )
            sidecar = stage / "text" / "1.txt"
            self.assertTrue(sidecar.is_file())
            text = sidecar.read_text(encoding="utf-8")
            self.assertIn("Hello synthetic", text)
            self.assertNotIn("not-in-sidecar", text)
            self.assertNotIn("<p>", text)
            self.assertEqual(_by_id(report, 1)["status"], "ok")
            self.assertEqual(_by_id(report, 1)["text_path"], "text/1.txt")

    def test_extractor_timeout_and_failure_are_recorded(self):
        def fetch(folder, uid, part):
            del folder, uid
            if part == "1":
                return b"%PDF-1.4\n"
            if part == "2":
                return b"kept going"
            if part == "3":
                return b"a,b\n1,2\n"
            return b"plain note"

        def worker_argv(kind, src, dest, timeout_s):
            if kind == "pdf":
                return [sys.executable, "-c", "import time; time.sleep(3)"]
            if kind == "csv":
                return [sys.executable, "-c", "import sys; sys.exit(3)"]
            return fetch_p1._default_worker_argv(kind, src, dest, timeout_s)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-mix", "imap-live", "INBOX", "bills", "80")],
                [
                    (1, "msg-mix", "1", "scan.pdf", "application/pdf", 9, "attachment"),
                    (2, "msg-mix", "2", "note.txt", "text/plain", 11, "attachment"),
                    (3, "msg-mix", "3", "rows.csv", "text/csv", 8, "attachment"),
                    (4, "msg-mix", "4", "other.txt", "text/plain", 10, "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
                timeout_s=0.3,
                worker_argv=worker_argv,
            )
            self.assertEqual(_by_id(report, 1)["status"], "timeout")
            self.assertEqual(_by_id(report, 3)["status"], "error")
            self.assertEqual(_by_id(report, 3)["error"], "worker_exit_3")
            self.assertEqual(_by_id(report, 2)["status"], "ok")
            self.assertEqual(_by_id(report, 4)["status"], "ok")
            self.assertEqual((stage / "text" / "2.txt").read_text(encoding="utf-8"), "kept going")
            self.assertFalse((stage / "text" / "1.txt").exists())
            self.assertFalse((stage / "text" / "3.txt").exists())

    def test_pdftotext_missing_records_extractor_missing(self):
        def fetch(folder, uid, part):
            del folder, uid
            if part == "1":
                return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
            return b"<p>Hello synthetic</p>"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty-bin"
            empty.mkdir()
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-pdf", "imap-live", "INBOX", "bills", "90")],
                [
                    (1, "msg-pdf", "1", "scan.pdf", "application/pdf", 32, "attachment"),
                    (2, "msg-pdf", "2", "page.html", "text/html", 24, "attachment"),
                ],
            )
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
                env={"PATH": str(empty), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(_by_id(report, 1)["status"], "extractor_missing")
            self.assertEqual(_by_id(report, 1)["extractor"], "pdftotext")
            self.assertEqual(_by_id(report, 2)["status"], "ok")
            self.assertIn("Hello synthetic", (stage / "text" / "2.txt").read_text(encoding="utf-8"))
            self.assertFalse((stage / "text" / "1.txt").exists())


class ImapPartClientTests(unittest.TestCase):
    def test_fetch_uses_body_peek_and_does_not_dial(self):
        seen = {}

        class FakeSSL:
            def __init__(self, host, port, timeout=None, ssl_context=None):
                seen["init"] = (host, port, timeout, ssl_context)

            def login(self, user, password):
                seen["login"] = (user, password)
                return "OK", [b""]

            def select(self, mailbox, readonly=False):
                seen["select"] = (mailbox, readonly)
                return "OK", [b"1"]

            def uid(self, cmd, uid, item):
                seen["uid"] = (cmd, uid, item)
                meta = b"9 (BODY[2] {5}"
                return "OK", [(meta, b"hello"), b")"]

            def logout(self):
                seen["logout"] = True
                return "BYE", [b""]

        def boom(*_args, **_kwargs):
            raise AssertionError("network is forbidden")

        class IMAP4:
            def __init__(self, *args, **kwargs):
                raise AssertionError("plain IMAP")

        source = FETCH_SRC.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?m)^\s*(?:import\s+imaplib\b|from\s+imaplib\b)")
        self.assertNotIn("IMAP4_SSL", source)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "part"
            with mock.patch("socket.create_connection", boom):
                client = fetch_p1.ImapPartClient(
                    "imap.example.com",
                    "user@example.com",
                    timeout=5,
                    imap_factory=FakeSSL,
                    password_fn=lambda: SECRET,
                )
                with client:
                    nbytes = client.fetch_part("INBOX", "9", "2", dest)
            self.assertEqual(nbytes, 5)
            self.assertEqual(dest.read_bytes(), b"hello")
            blob = dest.read_bytes()
        self.assertEqual(seen["init"][:3], ("imap.example.com", 993, 5))
        self.assertEqual(seen["init"][3].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(seen["init"][3].check_hostname)
        self.assertEqual(seen["select"], ('"INBOX"', True))
        self.assertEqual(
            seen["uid"],
            ("FETCH", "9", "(BODY.PEEK[2]<0.%d>)" % fetch_p1.CHUNK_BYTES),
        )
        self.assertNotIn(SECRET, blob.decode("ascii"))
        self.assertTrue(seen["logout"])
        with self.assertRaises(fetch_p1.FetchRefuse):
            fetch_p1.ImapPartClient(
                "imap.example.com",
                "user@example.com",
                imap_factory=IMAP4,
                password_fn=lambda: SECRET,
            )

    def test_default_transport_is_pinned_curl_imaps(self):
        seen = {}

        def fake_run(argv, input=None, capture_output=None, env=None, timeout=None, check=False):
            del capture_output, env, timeout, check
            seen.setdefault("argv", argv)
            seen.setdefault("inputs", []).append(input)
            text = input or b""
            if b"UID FETCH" in text:
                body = b"hello"
                stdout = b"* 1 FETCH (BODY[2]<0> {5}\r\n" + body + b")\r\nA OK\r\n"
            else:
                stdout = b"* OK [READ-ONLY] EXAMINE completed\r\nA OK\r\n"
            return subprocess.CompletedProcess(argv, 0, stdout, b"")

        def boom(*_args, **_kwargs):
            raise AssertionError("network is forbidden")

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "part"
            with mock.patch("socket.create_connection", boom), mock.patch(
                "attachments.fetch_p1.subprocess.run", fake_run
            ):
                client = fetch_p1.ImapPartClient(
                    "imap.example.com",
                    "user@example.com",
                    timeout=5,
                    password_fn=lambda: SECRET,
                )
                with client:
                    nbytes = client.fetch_part("INBOX", "9", "2", dest)
            self.assertEqual(nbytes, 5)
            self.assertEqual(dest.read_bytes(), b"hello")
        self.assertEqual(seen["argv"][0], "/usr/bin/curl")
        self.assertNotIn("--user", seen["argv"])
        self.assertNotIn("-k", seen["argv"])
        joined = b"\n".join(seen["inputs"])
        self.assertIn(b"imaps://imap.example.com:993/", joined)
        self.assertIn(b"EXAMINE", joined)
        self.assertIn(b"UID FETCH 9 (BODY.PEEK[2]<0.", joined)
        for arg in seen["argv"]:
            self.assertNotIn(SECRET, str(arg))


def _assert_pid_gone(testcase: unittest.TestCase, pid: int) -> None:
    """Portable liveness check. ProcessLookupError means the pid is gone."""
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            testcase.fail("pid %s is still running" % pid)
        time.sleep(0.05)


class PartialFetchCapTests(unittest.TestCase):
    def test_catalog_small_body_over_cap_is_chunked_and_not_stored(self):
        cap = 250
        chunk = 100
        body = b"B" * 5000
        fetches = []

        class FakeSSL:
            def __init__(self, host, port, timeout=None, ssl_context=None):
                del host, port, timeout, ssl_context

            def login(self, user, password):
                del user, password
                return "OK", [b""]

            def select(self, mailbox, readonly=False):
                fetches.append(("SELECT", mailbox, readonly))
                return "OK", [b"1"]

            def uid(self, cmd, uid, item):
                fetches.append((cmd, uid, item))
                marker = "<"
                start = item.find(marker)
                end = item.find(">", start)
                if start < 0 or end < 0 or "." not in item[start:end]:
                    chunk_bytes = body
                    offset = 0
                else:
                    spec = item[start + 1:end]
                    offset_text, count_text = spec.split(".", 1)
                    offset = int(offset_text)
                    count = int(count_text)
                    chunk_bytes = body[offset:offset + count]
                meta = ("1 (BODY[1]<%d> {%d}" % (offset, len(chunk_bytes))).encode("ascii")
                return "OK", [(meta, chunk_bytes), b")"]

            def logout(self):
                return "BYE", [b""]

        def boom(*_args, **_kwargs):
            raise AssertionError("network is forbidden")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [
                    ("msg-lie", "imap-live", "INBOX", "bills", "21"),
                    ("msg-catalog", "imap-live", "INBOX", "bills", "22"),
                ],
                [
                    (1, "msg-lie", "1", "note.txt", "text/plain", 10, "attachment"),
                    (2, "msg-catalog", "1", "huge.txt", "text/plain", cap + 1, "attachment"),
                ],
            )
            with mock.patch("socket.create_connection", boom):
                client = fetch_p1.ImapPartClient(
                    "imap.example.com",
                    "user@example.com",
                    timeout=5,
                    imap_factory=FakeSSL,
                    password_fn=lambda: SECRET,
                )
                with client:
                    report = fetch_p1.run_fetch_extract(
                        db,
                        stage,
                        dry_run=False,
                        part_client=client,
                        max_bytes=cap,
                        chunk_size=chunk,
                    )
            self.assertEqual(_by_id(report, 1)["status"], "too_big")
            self.assertEqual(_by_id(report, 1)["fetched_bytes"], 0)
            self.assertEqual(_by_id(report, 2)["status"], "too_big")
            self.assertFalse((stage / "bytes" / "1").exists())
            self.assertFalse((stage / "bytes" / "2").exists())
            self.assertFalse((stage / "text" / "1.txt").exists())
            self.assertEqual(list((stage / "bytes").iterdir()), [])
            commands = [item for item in fetches if item[0] == "FETCH"]
            full_fetches = (len(body) + chunk - 1) // chunk
            self.assertEqual(len(commands), (cap // chunk) + 1)
            self.assertGreater(len(commands), 1)
            self.assertLess(len(commands), full_fetches)
            self.assertEqual(
                [item[2] for item in commands],
                [
                    "(BODY.PEEK[1]<0.100>)",
                    "(BODY.PEEK[1]<100.100>)",
                    "(BODY.PEEK[1]<200.51>)",
                ],
            )
            self.assertEqual([item[1] for item in commands], ["21", "21", "21"])
            self.assertIn(("SELECT", '"INBOX"', True), fetches)
            for path in stage.rglob("*"):
                if path.is_file():
                    self.assertNotIn(SECRET.encode("ascii"), path.read_bytes())


class WorkerKillTests(unittest.TestCase):
    def test_timeout_kills_grandchild(self):
        script = (
            "import os, subprocess, sys, time\n"
            "pidfile = sys.argv[1]\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "handle = open(pidfile, 'w', encoding='ascii')\n"
            "handle.write(str(child.pid))\n"
            "handle.flush()\n"
            "os.fsync(handle.fileno())\n"
            "handle.close()\n"
            "time.sleep(60)\n"
        )

        def fetch(folder, uid, part):
            del folder, uid, part
            return b"note"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / "grand.pid"
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-sleep", "imap-live", "INBOX", "bills", "31")],
                [(1, "msg-sleep", "1", "note.txt", "text/plain", 4, "attachment")],
            )

            def worker_argv(kind, src, dest, timeout_s):
                del kind, src, dest, timeout_s
                return [sys.executable, "-c", script, str(pidfile)]

            started = time.monotonic()
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
                timeout_s=2.0,
                worker_argv=worker_argv,
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 10.0)
            self.assertEqual(_by_id(report, 1)["status"], "timeout")
            self.assertTrue(pidfile.is_file())
            pid = int(pidfile.read_text(encoding="ascii"))
            _assert_pid_gone(self, pid)
            self.assertFalse((stage / "text" / "1.txt").exists())

    def test_output_flood_is_bounded_error(self):
        script = (
            "import os, sys, time\n"
            "handle = open(sys.argv[1], 'w', encoding='ascii')\n"
            "handle.write(str(os.getpid()))\n"
            "handle.flush()\n"
            "os.fsync(handle.fileno())\n"
            "handle.close()\n"
            "chunk = b'x' * 65536\n"
            "for _ in range(32):\n"
            "    sys.stdout.buffer.write(chunk)\n"
            "    sys.stderr.buffer.write(chunk)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.flush()\n"
            "time.sleep(60)\n"
        )
        self.assertEqual(fetch_p1.OUTPUT_CAP, 64 * 1024)

        def fetch(folder, uid, part):
            del folder, uid, part
            return b"note"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / "flood.pid"
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-flood", "imap-live", "INBOX", "bills", "32")],
                [(1, "msg-flood", "1", "note.txt", "text/plain", 4, "attachment")],
            )

            def worker_argv(kind, src, dest, timeout_s):
                del kind, src, dest, timeout_s
                return [sys.executable, "-c", script, str(pidfile)]

            started = time.monotonic()
            report = fetch_p1.run_fetch_extract(
                db,
                stage,
                dry_run=False,
                fetch_part=fetch,
                timeout_s=8.0,
                worker_argv=worker_argv,
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 4.0)
            row = _by_id(report, 1)
            self.assertEqual(row["status"], "error")
            self.assertEqual(row["error"], "output_overflow")
            self.assertTrue(pidfile.is_file())
            _assert_pid_gone(self, int(pidfile.read_text(encoding="ascii")))
            self.assertFalse((stage / "text" / "1.txt").exists())
            for path in stage.rglob("*"):
                if path.is_file():
                    self.assertLess(path.stat().st_size, fetch_p1.OUTPUT_CAP)


class StagePermTests(unittest.TestCase):
    def test_dirs_0700_files_0600_and_symlink_not_followed(self):
        def fetch(folder, uid, part):
            del folder, uid, part
            return b"hello-bytes"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.txt"
            outside.write_bytes(b"OUTSIDE_ORIGINAL")
            db = root / "mailroom-copy.sqlite"
            stage = root / "stage"
            _seed(
                db,
                [("msg-ok", "imap-live", "INBOX", "bills", "41")],
                [(1, "msg-ok", "1", "note.txt", "text/plain", 11, "attachment")],
            )
            old_mask = os.umask(0)
            try:
                bytes_dir = stage / "bytes"
                bytes_dir.mkdir(parents=True)
                link = bytes_dir / "1"
                link.symlink_to(outside)
                report = fetch_p1.run_fetch_extract(
                    db,
                    stage,
                    dry_run=False,
                    fetch_part=fetch,
                )
            finally:
                os.umask(old_mask)
            self.assertEqual(_by_id(report, 1)["status"], "ok")
            self.assertEqual(outside.read_bytes(), b"OUTSIDE_ORIGINAL")
            stored = stage / "bytes" / "1"
            self.assertTrue(stored.is_file())
            self.assertFalse(stored.is_symlink())
            self.assertEqual(stored.read_bytes(), b"hello-bytes")
            text = stage / "text" / "1.txt"
            status = stage / "status" / "1.json"
            self.assertEqual(text.read_text(encoding="utf-8"), "hello-bytes")
            self.assertIn('"status": "ok"', status.read_text(encoding="utf-8"))
            for directory in (stage, stage / "bytes", stage / "text", stage / "status"):
                mode = stat.S_IMODE(directory.stat().st_mode)
                self.assertEqual(mode, 0o700, directory.name)
            for path in (stored, text, status):
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o600, path.name)


class AskMailUntouchedTests(unittest.TestCase):
    def test_ask_mail_is_not_imported_by_the_new_modules(self):
        for path in (FETCH_SRC, WORKER_SRC):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ask_mail", text, path.name)
        self.assertIn("def ", ASK.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
