#!/usr/bin/env python3
"""ATT-0 fingerprint, diff, and backup. Synthetic files only.

No network. No Keychain. No live system-of-record file.
Stdlib unittest. Python 3.9+.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import attachments.att0_backup as backup  # noqa: E402
import attachments.att0_fp as fp  # noqa: E402
import attachments.att0_fpdiff as fpdiff  # noqa: E402
from sor_writer_gate import SorWriterRefuse  # noqa: E402

DOC = ROOT / "docs" / "attachments" / "ATT-0-fingerprint.md"
DESIGN = ROOT / "docs" / "attachments" / "ATT-0-design.md"
SCHEMA = SCRIPTS / "attachments" / "schema.sql"
HELPERS = (
    SCRIPTS / "attachments" / "att0_fp.py",
    SCRIPTS / "attachments" / "att0_fpdiff.py",
    SCRIPTS / "attachments" / "att0_backup.py",
)

FOLDER = "FolderName-Zebra-gg"
SUBJECT = "SubjectToken-Zebra-gg"
MSGID = "msgid-Zebra-gg"
DIR_TOKEN = "DIRTOKEN-gg"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _run(func, argv):
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = func(argv)
    return code, out.getvalue(), err.getvalue()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _strings(key)
            yield from _strings(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _strings(item)


def _seed(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE messages (
              id TEXT PRIMARY KEY,
              source TEXT,
              folder TEXT,
              subject TEXT,
              present_on_server INTEGER,
              has_attachments INTEGER
            );
            CREATE TABLE ask_audit (id INTEGER PRIMARY KEY, note TEXT);
            CREATE TABLE drafts (id INTEGER PRIMARY KEY, body TEXT);
            CREATE TABLE bills (id INTEGER PRIMARY KEY, amount INTEGER);
            """
        )
        rows = (
            (MSGID, "imap-live", FOLDER, SUBJECT, 1, 0),
            ("m2", "imap-live", FOLDER, SUBJECT, 0, 0),
            ("m3", "imap-live", None, SUBJECT, 1, 0),
            ("m4", "imap-live", "", SUBJECT, 1, 0),
            ("m5", "other", FOLDER, SUBJECT, 0, 1),
        )
        conn.executemany(
            "INSERT INTO messages "
            "(id, source, folder, subject, present_on_server, has_attachments) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("INSERT INTO ask_audit (note) VALUES ('audit-row')")
        conn.execute("INSERT INTO drafts (body) VALUES ('draft-row')")
        conn.execute("INSERT INTO bills (amount) VALUES (10)")
        conn.commit()
    finally:
        conn.close()


def _mutate(path: Path, sql: str, params=()) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _format_versions(path: Path) -> tuple[int, int]:
    """SQLite header bytes 18-19. 1 is rollback, 2 is WAL."""
    blob = path.read_bytes()
    return blob[18], blob[19]


def _path_from_uri(text: str) -> Path | None:
    if not str(text).startswith("file:"):
        return None
    parsed = urlparse(str(text))
    if parsed.scheme != "file" or not parsed.path:
        return None
    return Path(unquote(parsed.path))


@contextmanager
def _sqlite351_ro_guard(seen=None, staged_inodes=None):
    """Fail a plain mode=ro open of a WAL file that has no -wal and no -shm.

    SQLite 3.51.0 (Apple /usr/bin/python3) returns ``unable to open database
    file`` for that open: a read-only connection cannot create ``-shm``.
    ``immutable=1`` is allowed. A private stage is allowed only after its
    ``-wal`` has been copied, which is the open that can read those frames.

    ``staged_inodes`` records ``(st_dev, st_ino)`` of each ``db.sqlite``
    the guard sees, so a test can prove the stage is not the source inode.
    """
    real = sqlite3.connect

    def guard(database, *args, **kwargs):
        text = str(database)
        if seen is not None and text.startswith("file:"):
            seen.append(text)
        if staged_inodes is not None and text.startswith("file:"):
            opened = _path_from_uri(text)
            if opened is not None and opened.name == "db.sqlite" and opened.is_file():
                info = opened.stat()
                staged_inodes.append((info.st_dev, info.st_ino))
        if (
            text.startswith("file:")
            and "mode=ro" in text
            and "immutable=1" not in text
        ):
            path = _path_from_uri(text)
            if (
                path is not None
                and path.is_file()
                and _format_versions(path) == (2, 2)
                and not Path(str(path) + "-shm").is_file()
                and not Path(str(path) + "-wal").is_file()
            ):
                raise sqlite3.OperationalError("unable to open database file")
        return real(database, *args, **kwargs)

    with mock.patch("sqlite3.connect", side_effect=guard):
        yield


def _drop_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _open_uncheckpointed(path: Path) -> sqlite3.Connection | None:
    """Leave a non-empty ``-wal`` whose latest row is not in the main file.

    Python 3.12+ can close without checkpointing. On 3.9 the connection
    stays open so close does not fold the ``-wal`` into the main file.
    """
    conn = sqlite3.connect(str(path))
    try:
        can_close = hasattr(conn, "setconfig") and hasattr(
            sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE"
        )
        if can_close:
            conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.execute("INSERT INTO bills (amount) VALUES (1)")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("INSERT INTO bills (amount) VALUES (2)")
        conn.commit()
        wal = Path(str(path) + "-wal")
        if not (wal.is_file() and wal.stat().st_size > 0):
            raise AssertionError("expected a non-empty wal")
        if can_close:
            return None
        kept = conn
        conn = None
        return kept
    finally:
        if conn is not None:
            conn.close()


def _family_hashes(path: Path) -> dict:
    found = {"main": _sha(path)}
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        found[suffix] = _sha(sidecar) if sidecar.is_file() else None
    return found


class _DbCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / DIR_TOKEN
        self.root.mkdir()
        self.db = self.root / "sample.sqlite"

    def _logical(self, doc):
        return {
            "exclusions": doc["exclusions"],
            "tables": doc["tables"],
            "skipped_virtual_tables": doc["skipped_virtual_tables"],
            "journal_mode": doc["journal_mode"],
            "imap_live_total": doc["imap_live_total"],
            "imap_live_null_folder": doc["imap_live_null_folder"],
            "imap_live_folders_n": doc["imap_live_folders_n"],
            "imap_live_not_on_server": doc["imap_live_not_on_server"],
        }


class FingerprintTests(_DbCase):
    def setUp(self) -> None:
        super().setUp()
        _seed(self.db)

    def test_two_runs_match_and_do_not_change_the_file(self):
        before = _sha(self.db)
        first = fp.fingerprint(self.db)
        second = fp.fingerprint(self.db)
        after = _sha(self.db)
        self.assertEqual(first, second)
        self.assertEqual(fp.render_json(first), fp.render_json(second))
        self.assertEqual(before, after)
        self.assertEqual(first["main_sha256"], before)
        self.assertNotIn(DIR_TOKEN, fp.render_json(first))
        for text in _strings(first):
            self.assertNotIn("/", text)
            self.assertNotIn(FOLDER, text)
            self.assertNotIn(SUBJECT, text)
            self.assertNotIn(MSGID, text)
        self.assertNotIn("imap_live_by_folder", first)

    def test_imap_counts_have_no_folder_names(self):
        doc = fp.fingerprint(self.db)
        self.assertEqual(doc["imap_live_total"], 4)
        self.assertEqual(doc["imap_live_null_folder"], 2)
        self.assertEqual(doc["imap_live_folders_n"], 1)
        self.assertEqual(doc["imap_live_not_on_server"], 1)
        self.assertEqual(doc["db_basename"], "sample.sqlite")
        self.assertEqual(doc["stat_main"]["basename"], "sample.sqlite")
        self.assertEqual(doc["journal_mode"], "delete")
        self.assertIsNone(doc["stat_wal"])
        self.assertEqual(
            doc["exclusions"],
            {
                "tables": ["ask_audit", "drafts"],
                "columns": ["messages.has_attachments"],
            },
        )
        self.assertNotIn("ask_audit", doc["tables"])
        self.assertNotIn("drafts", doc["tables"])
        self.assertIn("messages", doc["tables"])
        self.assertIn("bills", doc["tables"])
        self.assertEqual(doc["skipped_virtual_tables"], [])

    def test_excluded_writes_do_not_change_logical_fingerprint(self):
        before = fp.fingerprint(self.db)
        _mutate(
            self.db,
            "UPDATE messages SET has_attachments=1 WHERE id=?",
            (MSGID,),
        )
        _mutate(self.db, "INSERT INTO ask_audit (note) VALUES ('more')")
        _mutate(self.db, "INSERT INTO drafts (body) VALUES ('more')")
        after = fp.fingerprint(self.db)
        self.assertEqual(self._logical(before), self._logical(after))
        self.assertNotEqual(before["main_sha256"], after["main_sha256"])
        code, out, err = _run(
            fpdiff.main,
            [
                self._dump("before.json", before),
                self._dump("after.json", after),
                "--logical",
            ],
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)
        full, full_out, _full_err = _run(
            fpdiff.main,
            [str(self.root / "before.json"), str(self.root / "after.json")],
        )
        self.assertEqual(full, 1)
        self.assertIn("SOR_FINGERPRINT=CHANGED", full_out)

    def test_non_excluded_row_changes_the_table_hash(self):
        before = fp.fingerprint(self.db)
        _mutate(self.db, "UPDATE bills SET amount=11 WHERE amount=10")
        after = fp.fingerprint(self.db)
        self.assertNotEqual(
            before["tables"]["bills"]["sha256"],
            after["tables"]["bills"]["sha256"],
        )
        self.assertEqual(
            before["tables"]["messages"]["sha256"],
            after["tables"]["messages"]["sha256"],
        )
        code, out, _err = _run(
            fpdiff.main,
            [
                self._dump("b.json", before),
                self._dump("a.json", after),
                "--logical",
            ],
        )
        self.assertEqual(code, 1)
        self.assertIn("DIFF table bills sha256", out)
        self.assertIn("SOR_FINGERPRINT=CHANGED", out)

    def test_primary_key_order_is_independent_of_insert_order(self):
        left = self.root / "left.sqlite"
        right = self.root / "right.sqlite"
        for path, order in ((left, (1, 2, 3)), (right, (3, 1, 2))):
            conn = sqlite3.connect(str(path))
            try:
                conn.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY, amount INTEGER)")
                for number in order:
                    conn.execute("INSERT INTO bills (id, amount) VALUES (?, ?)", (number, number))
                conn.commit()
            finally:
                conn.close()
        self.assertEqual(
            fp.fingerprint(left)["tables"]["bills"],
            fp.fingerprint(right)["tables"]["bills"],
        )

    def test_rowid_order_follows_insert_order_without_a_primary_key(self):
        left = self.root / "loose-a.sqlite"
        right = self.root / "loose-b.sqlite"
        for path, order in ((left, ("a", "b")), (right, ("b", "a"))):
            conn = sqlite3.connect(str(path))
            try:
                conn.execute("CREATE TABLE loose (body TEXT)")
                for body in order:
                    conn.execute("INSERT INTO loose (body) VALUES (?)", (body,))
                conn.commit()
            finally:
                conn.close()
        self.assertNotEqual(
            fp.fingerprint(left)["tables"]["loose"]["sha256"],
            fp.fingerprint(right)["tables"]["loose"]["sha256"],
        )

    def test_without_rowid_and_skips_sqlite_sequence(self):
        path = self.root / "special.sqlite"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "CREATE TABLE keyed (id TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID"
            )
            conn.execute("INSERT INTO keyed VALUES ('k', 'v')")
            conn.execute(
                "CREATE TABLE seq (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"
            )
            conn.execute("INSERT INTO seq (v) VALUES ('x')")
            conn.commit()
        finally:
            conn.close()
        doc = fp.fingerprint(path)
        self.assertIn("keyed", doc["tables"])
        self.assertIn("seq", doc["tables"])
        self.assertNotIn("sqlite_sequence", doc["tables"])
        self.assertEqual(doc["tables"]["keyed"]["count"], 1)

    def test_empty_table_hashes_empty_bytes(self):
        path = self.root / "empty.sqlite"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE empty_notes (id INTEGER PRIMARY KEY, body TEXT)")
            conn.commit()
        finally:
            conn.close()
        doc = fp.fingerprint(path)
        self.assertEqual(doc["tables"]["empty_notes"]["count"], 0)
        self.assertEqual(doc["tables"]["empty_notes"]["sha256"], EMPTY_SHA)

    def test_messages_without_source_has_null_imap_counts(self):
        path = self.root / "bare.sqlite"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO messages VALUES ('only')")
            conn.commit()
        finally:
            conn.close()
        doc = fp.fingerprint(path)
        self.assertIsNone(doc["imap_live_total"])
        self.assertIsNone(doc["imap_live_null_folder"])
        self.assertIsNone(doc["imap_live_folders_n"])
        self.assertIsNone(doc["imap_live_not_on_server"])

    def test_extra_exclusion_is_recorded_and_applied(self):
        before = fp.fingerprint(self.db, extra_columns=["messages.subject"])
        self.assertEqual(
            before["exclusions"]["columns"],
            ["messages.has_attachments", "messages.subject"],
        )
        _mutate(self.db, "UPDATE messages SET subject='changed'")
        after = fp.fingerprint(self.db, extra_columns=["Messages.Subject"])
        self.assertEqual(before["tables"], after["tables"])
        _mutate(self.db, "UPDATE bills SET amount=99")
        billed = fp.fingerprint(self.db, extra_tables=["bills"])
        self.assertNotIn("bills", billed["tables"])
        self.assertIn("bills", billed["exclusions"]["tables"])
        self.assertIn("ask_audit", billed["exclusions"]["tables"])

    def test_readonly_connection_cannot_write(self):
        before = _sha(self.db)
        conn = fp.open_readonly(self.db)
        try:
            flag = conn.execute("PRAGMA query_only").fetchone()[0]
            self.assertEqual(int(flag), 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE hack (id INTEGER)")
        finally:
            fp.close_readonly(conn)
        self.assertEqual(_sha(self.db), before)
        uri = fp._ro_uri(self.db)
        self.assertTrue(uri.startswith("file:"))
        self.assertIn("mode=ro", uri)
        self.assertNotIn("immutable=1", uri)
        immutable = fp._immutable_uri(self.db)
        self.assertIn("mode=ro", immutable)
        self.assertIn("immutable=1", immutable)

    def test_cli_stdout_or_out_and_usage_errors(self):
        code, out, err = _run(fp.main, [str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        doc = json.loads(out)
        self.assertEqual(doc["db_basename"], "sample.sqlite")
        self.assertNotIn(DIR_TOKEN, out)
        dest = self.root / "fp.json"
        code, out, err = _run(fp.main, [str(self.db), "--out", str(dest)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(dest.read_text(encoding="utf-8"), fp.render_json(doc))
        missing = self.root / "missing.sqlite"
        code, _out, err = _run(fp.main, [str(missing)])
        self.assertEqual(code, 2)
        self.assertIn("missing", err)
        self.assertNotIn(DIR_TOKEN, err)
        code, _out, err = _run(fp.main, [str(self.db), "--exclude-column", "nocolon"])
        self.assertEqual(code, 2)
        self.assertIn("invalid exclude-column", err)
        code, _out, _err = _run(fp.main, [])
        self.assertEqual(code, 2)

    def test_wal_reader_leaves_main_bytes_and_creates_no_sidecar(self):
        path = self.root / "wal.sqlite"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY, amount INTEGER)")
            conn.execute("INSERT INTO bills (amount) VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        _drop_sidecars(path)
        self.assertEqual(_format_versions(path), (2, 2))
        before_bytes = _sha(path)
        names_before = sorted(item.name for item in self.root.iterdir())
        seen: list[str] = []
        with _sqlite351_ro_guard(seen):
            first = fp.fingerprint(path)
            second = fp.fingerprint(path)
        self.assertEqual(first["journal_mode"], "wal")
        self.assertEqual(first["tables"]["bills"]["count"], 1)
        self.assertIsNone(first["stat_wal"])
        self.assertIsNone(first["stat_shm"])
        self.assertIsNone(second["stat_wal"])
        self.assertIsNone(second["stat_shm"])
        self.assertEqual(first["tables"], second["tables"])
        self.assertEqual(_sha(path), before_bytes)
        self.assertFalse(Path(str(path) + "-wal").exists())
        self.assertFalse(Path(str(path) + "-shm").exists())
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), names_before)
        hits = [
            item
            for item in seen
            if _path_from_uri(item) is not None
            and _path_from_uri(item).resolve() == path.resolve()
        ]
        self.assertTrue(hits)
        for item in hits:
            self.assertIn("immutable=1", item)
            self.assertIn("mode=ro", item)
        self.assertFalse(
            any(
                "mode=ro" in item and "immutable=1" not in item and path.name in item
                for item in seen
            )
        )
        code, out, err = _run(
            fpdiff.main,
            [self._dump("w1.json", first), self._dump("w2.json", second)],
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertNotIn("wal_created", out)
        self.assertNotIn("shm_changed", out)
        self.assertIn("note main_sha256_identical", out)
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)

    def test_nonempty_wal_is_staged_and_wal_only_row_is_visible(self):
        path = self.root / "wal.sqlite"
        held = _open_uncheckpointed(path)
        if held is not None:
            self.addCleanup(held.close)
        wal = Path(str(path) + "-wal")
        self.assertGreater(wal.stat().st_size, 0)
        before = _family_hashes(path)
        names_before = sorted(item.name for item in path.parent.iterdir())
        seen: list[str] = []
        with _sqlite351_ro_guard(seen):
            doc = fp.fingerprint(path)
        self.assertEqual(doc["journal_mode"], "wal")
        self.assertEqual(doc["tables"]["bills"]["count"], 2)
        self.assertEqual(doc["stat_wal"]["basename"], "wal.sqlite-wal")
        self.assertGreater(doc["stat_wal"]["size"], 0)
        imm = sqlite3.connect(fp._immutable_uri(path), uri=True)
        try:
            visible = imm.execute("SELECT amount FROM bills").fetchall()
        finally:
            imm.close()
        self.assertEqual(visible, [(1,)])
        self.assertEqual(_family_hashes(path), before)
        self.assertEqual(sorted(item.name for item in path.parent.iterdir()), names_before)
        self.assertFalse(
            any(name.startswith(".att0-fp-") for name in os.listdir(path.parent))
        )
        self.assertFalse(
            any(
                _path_from_uri(item) is not None
                and _path_from_uri(item).resolve() == path.resolve()
                for item in seen
            )
        )
        self.assertTrue(
            any(
                "mode=ro" in item and "immutable=1" not in item and "db.sqlite" in item
                for item in seen
            )
        )
        self.assertFalse(any("immutable=1" in item for item in seen))

    def test_same_process_holder_does_not_change_source_family(self):
        """A connection that already has the source open must not share -shm.

        Python 3.9 leaves the writer open (it has no
        SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE). The same join happens whenever
        any other connection in this process has the source open. The
        source main, -wal, and -shm bytes must stay identical, and the
        wal-only row must still be visible.
        """
        path = self.root / "wal.sqlite"
        setup = _open_uncheckpointed(path)
        holder = sqlite3.connect(str(path))
        try:
            before = _family_hashes(path)
            names_before = sorted(item.name for item in path.parent.iterdir())
            seen: list[str] = []
            staged_inodes: list[tuple[int, int]] = []
            with _sqlite351_ro_guard(seen, staged_inodes):
                doc = fp.fingerprint(path)
            self.assertEqual(doc["journal_mode"], "wal")
            self.assertEqual(doc["tables"]["bills"]["count"], 2)
            with closing(sqlite3.connect(fp._immutable_uri(path), uri=True)) as imm:
                visible = imm.execute("SELECT amount FROM bills").fetchall()
            self.assertEqual(visible, [(1,)])
            self.assertEqual(_family_hashes(path), before)
            self.assertEqual(sorted(item.name for item in path.parent.iterdir()), names_before)
            self.assertFalse(
                any(name.startswith(".att0-fp-") for name in os.listdir(path.parent))
            )
            self.assertFalse(
                any(
                    _path_from_uri(item) is not None
                    and _path_from_uri(item).resolve() == path.resolve()
                    for item in seen
                )
            )
            self.assertTrue(staged_inodes)
            self.assertNotEqual(staged_inodes[0], (path.stat().st_dev, path.stat().st_ino))
        finally:
            try:
                holder.close()
            finally:
                if setup is not None:
                    setup.close()

    def test_stage_space_is_main_plus_wal_plus_margin(self):
        path = self.root / "wal.sqlite"
        held = _open_uncheckpointed(path)
        if held is not None:
            self.addCleanup(held.close)
        before = _family_hashes(path)
        need = (
            path.stat().st_size
            + Path(str(path) + "-wal").stat().st_size
            + fp._STAGE_FREE_MARGIN
        )
        short = mock.Mock(free=need - 1)
        with mock.patch("attachments.att0_fp.shutil.disk_usage", return_value=short):
            with self.assertRaises(fp.FpRefuse) as ctx:
                fp.fingerprint(path)
            code, out, err = _run(fp.main, [str(path)])
        self.assertEqual(str(ctx.exception), "refuse: not enough free space")
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("not enough free space", err)
        self.assertNotIn(DIR_TOKEN, err)
        self.assertEqual(_family_hashes(path), before)
        self.assertFalse(
            any(name.startswith(".att0-fp-") for name in os.listdir(path.parent))
        )
        exact = mock.Mock(free=need)
        with mock.patch("attachments.att0_fp.shutil.disk_usage", return_value=exact):
            doc = fp.fingerprint(path)
        self.assertEqual(doc["tables"]["bills"]["count"], 2)
        self.assertEqual(_family_hashes(path), before)

    def test_nonempty_wal_stage_failure_exits_2(self):
        path = self.root / "wal.sqlite"
        held = _open_uncheckpointed(path)
        if held is not None:
            self.addCleanup(held.close)
        before = _family_hashes(path)
        names_before = sorted(item.name for item in path.parent.iterdir())
        with mock.patch(
            "attachments.att0_fp._copy_bytes", side_effect=OSError("injected")
        ):
            with self.assertRaises(fp.FpRefuse) as ctx:
                fp.fingerprint(path)
            code, out, err = _run(fp.main, [str(path)])
        self.assertEqual(str(ctx.exception), "refuse: cannot stage source wal")
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("cannot stage source wal", err)
        self.assertNotIn(DIR_TOKEN, err)
        self.assertEqual(_family_hashes(path), before)
        self.assertEqual(sorted(item.name for item in path.parent.iterdir()), names_before)

    def test_fts5_virtual_tables_are_skipped_and_shadow_tables_are_hashed(self):
        path = self.root / "fts.sqlite"
        _seed_fts(path)
        doc = fp.fingerprint(path)
        self.assertEqual(
            doc["skipped_virtual_tables"], ["messages_fts", "messages_ids"]
        )
        self.assertNotIn("messages_fts", doc["tables"])
        self.assertNotIn("messages_ids", doc["tables"])
        self.assertIn("messages", doc["tables"])
        for shadow in (
            "messages_fts_data",
            "messages_fts_idx",
            "messages_fts_content",
            "messages_fts_docsize",
            "messages_fts_config",
            "messages_ids_data",
            "messages_ids_idx",
            "messages_ids_content",
            "messages_ids_docsize",
            "messages_ids_config",
        ):
            info = doc["tables"][shadow]
            self.assertIsInstance(info["count"], int)
            self.assertNotIsInstance(info["count"], bool)
            self.assertEqual(len(info["sha256"]), 64)
        self.assertGreater(doc["tables"]["messages_fts_data"]["count"], 0)
        loaded = json.loads(fp.render_json(doc))
        self.assertEqual(
            loaded["skipped_virtual_tables"], ["messages_fts", "messages_ids"]
        )
        code, out, err = _run(
            fpdiff.main,
            [self._dump("fts-a.json", doc), self._dump("fts-b.json", doc)],
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0, msg=out)
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)

    def test_table_read_error_exits_without_a_hash(self):
        original = fp._row_sha256

        def boom(conn, table, info, skip):
            if table == "bills":
                raise sqlite3.OperationalError("no such module: vec0")
            return original(conn, table, info, skip)

        dest = self.root / "fp.json"
        with mock.patch.object(fp, "_row_sha256", side_effect=boom):
            with self.assertRaises(fp.FpRefuse) as ctx:
                fp.fingerprint(self.db)
            code, out, err = _run(fp.main, [str(self.db), "--out", str(dest)])
        self.assertEqual(str(ctx.exception), "ERROR bills")
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "ERROR bills\n")
        self.assertNotIn(EMPTY_SHA, out)
        self.assertNotIn(EMPTY_SHA, err)
        self.assertNotRegex(out + err, r"[0-9a-f]{64}")
        self.assertNotIn("bills", out)
        self.assertFalse(dest.exists())

    def _dump(self, name, doc) -> str:
        path = self.root / name
        path.write_text(fp.render_json(doc), encoding="utf-8")
        return str(path)


class FpdiffTests(_DbCase):
    def _pair(self, before, after, *args):
        left = self.root / "before.json"
        right = self.root / "after.json"
        left.write_text(json.dumps(before), encoding="utf-8")
        right.write_text(json.dumps(after), encoding="utf-8")
        return _run(fpdiff.main, [str(left), str(right), *args])

    def _doc(self, **overrides):
        doc = {
            "db_basename": "sample.sqlite",
            "journal_mode": "delete",
            "main_sha256": "ab" * 32,
            "stat_main": {"basename": "sample.sqlite", "size": 8, "mtime_ns": 1},
            "stat_wal": None,
            "stat_shm": None,
            "exclusions": {
                "tables": ["ask_audit", "drafts"],
                "columns": ["messages.has_attachments"],
            },
            "tables": {"messages": {"count": 1, "sha256": "cd" * 32}},
            "imap_live_total": 1,
            "imap_live_null_folder": 0,
            "imap_live_folders_n": 1,
            "imap_live_not_on_server": 0,
        }
        doc.update(overrides)
        return doc

    def test_identical_is_zero(self):
        doc = self._doc()
        code, out, err = self._pair(doc, json.loads(json.dumps(doc)))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)
        self.assertIn("note main_sha256_identical", out)

    def test_changed_row_is_one(self):
        before = self._doc()
        after = self._doc(
            tables={"messages": {"count": 1, "sha256": "ef" * 32}}
        )
        code, out, _err = self._pair(before, after)
        self.assertEqual(code, 1)
        self.assertIn("DIFF table messages sha256", out)
        self.assertIn("SOR_FINGERPRINT=CHANGED", out)

    def test_mismatched_exclusions_are_exit_two(self):
        before = self._doc()
        after = self._doc(
            exclusions={
                "tables": ["ask_audit", "drafts", "bills"],
                "columns": ["messages.has_attachments"],
            }
        )
        code, out, err = self._pair(before, after)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("exclusion sets differ", err)
        self.assertNotIn("SOR_FINGERPRINT", err)

    def test_added_table_is_refused_unless_allowed(self):
        before = self._doc()
        after = self._doc(
            tables={
                "messages": {"count": 1, "sha256": "cd" * 32},
                "attachments": {"count": 0, "sha256": EMPTY_SHA},
            }
        )
        code, out, _err = self._pair(before, after, "--logical")
        self.assertEqual(code, 1)
        self.assertIn("DIFF table attachments added", out)
        code, out, err = self._pair(
            before, after, "--logical", "--allow-added-table", "attachments"
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("note added_table attachments allowed", out)
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)
        self.assertNotIn("DIFF", out)

    def test_removed_table_stays_a_diff_when_allow_added_is_set(self):
        before = self._doc(
            tables={
                "messages": {"count": 1, "sha256": "cd" * 32},
                "attachments": {"count": 0, "sha256": EMPTY_SHA},
            }
        )
        after = self._doc()
        code, out, _err = self._pair(
            before, after, "--logical", "--allow-added-table", "attachments"
        )
        self.assertEqual(code, 1)
        self.assertIn("DIFF table attachments removed", out)

    def test_logical_ignores_file_bytes_and_stat(self):
        before = self._doc()
        after = self._doc(
            main_sha256="11" * 32,
            stat_main={"basename": "sample.sqlite", "size": 99, "mtime_ns": 5},
            db_basename="other.sqlite",
            journal_mode="wal",
        )
        code, out, _err = self._pair(before, after, "--logical")
        self.assertEqual(code, 0)
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)
        self.assertNotIn("DIFF", out)
        code, out, _err = self._pair(before, after)
        self.assertEqual(code, 1)
        self.assertIn("DIFF stat_main", out)
        self.assertIn("DIFF field journal_mode", out)
        self.assertNotIn("DIFF main_sha256", out)

    def test_main_sha_diff_when_stat_matches(self):
        before = self._doc()
        after = self._doc(main_sha256="22" * 32)
        code, out, _err = self._pair(before, after)
        self.assertEqual(code, 1)
        self.assertIn("DIFF main_sha256", out)
        self.assertNotIn("DIFF stat_main", out)

    def test_new_nonempty_wal_is_a_diff(self):
        before = self._doc()
        after = self._doc(
            stat_wal={"basename": "sample.sqlite-wal", "size": 24, "mtime_ns": 3}
        )
        code, out, _err = self._pair(before, after)
        self.assertEqual(code, 1)
        self.assertIn("DIFF stat_wal_created_nonempty", out)

    def test_new_empty_wal_is_a_note(self):
        before = self._doc()
        after = self._doc(
            stat_wal={"basename": "sample.sqlite-wal", "size": 0, "mtime_ns": 3},
            stat_shm={"basename": "sample.sqlite-shm", "size": 32768, "mtime_ns": 4},
        )
        code, out, err = self._pair(before, after)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("note wal_created_empty_by_ro_reader", out)
        self.assertIn("note shm_changed", out)
        self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)

    def test_malformed_fingerprint_is_exit_two(self):
        path = self.root / "bad.json"
        path.write_text("{", encoding="utf-8")
        other = self.root / "other.json"
        other.write_text("{}", encoding="utf-8")
        code, _out, err = _run(fpdiff.main, [str(path), str(other)])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)
        code, _out, err = _run(fpdiff.main, [str(other), str(other)])
        self.assertEqual(code, 2)
        self.assertIn("no exclusions", err)
        code, _out, _err = _run(fpdiff.main, [])
        self.assertEqual(code, 2)
        help_text = fpdiff.build_parser().format_help()
        self.assertIn("--logical", help_text)
        self.assertIn("table counts and hashes", help_text)

    def test_help_documents_logical(self):
        text = Path(fpdiff.__file__).read_text(encoding="utf-8")
        self.assertIn("--logical", text)
        self.assertIn("table counts and hashes", text)

    def test_skipped_virtual_tables_are_ignored_consistently(self):
        base = self._doc()
        listed = self._doc(
            skipped_virtual_tables=["messages_fts", "messages_ids"]
        )
        reordered = self._doc(
            skipped_virtual_tables=["messages_ids", "messages_fts"]
        )
        wrong_type = self._doc(skipped_virtual_tables="messages_fts")
        for args in ((), ("--logical",)):
            for other in (listed, reordered, wrong_type):
                code, out, err = self._pair(base, other, *args)
                self.assertEqual(err, "")
                self.assertEqual(code, 0, msg=out)
                self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)
                self.assertNotIn("DIFF", out)
                self.assertNotIn("skipped_virtual_tables", out)
            code, out, err = self._pair(
                listed, json.loads(json.dumps(reordered)), *args
            )
            self.assertEqual(err, "")
            self.assertEqual(code, 0, msg=out)
            self.assertIn("SOR_FINGERPRINT=IDENTICAL", out)
        changed = self._doc(
            skipped_virtual_tables=["message_embeddings"],
            tables={"messages": {"count": 1, "sha256": "ef" * 32}},
        )
        code, out, _err = self._pair(listed, changed, "--logical")
        self.assertEqual(code, 1)
        self.assertIn("DIFF table messages sha256", out)
        self.assertNotIn("skipped_virtual_tables", out)


class BackupTests(_DbCase):
    def setUp(self) -> None:
        super().setUp()
        _seed(self.db)

    def test_happy_path_digests_match_and_quick_check_ok(self):
        dest = self.root / "copy.sqlite"
        before = _sha(self.db)
        code, out, err = _run(backup.main, [str(self.db), str(dest)])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(err, "")
        self.assertIn("backup_ok", out)
        self.assertIn("quick_check=ok", out)
        self.assertIn("journal_mode=delete", out)
        self.assertEqual(_format_versions(dest), (1, 1))
        self.assertIn("src=sample.sqlite", out)
        self.assertIn("dest=copy.sqlite", out)
        self.assertNotIn(DIR_TOKEN, out)
        self.assertEqual(_sha(self.db), before)
        self.assertTrue(dest.is_file())
        check = sqlite3.connect(str(dest))
        try:
            qc = check.execute("PRAGMA quick_check").fetchone()[0]
            amount = check.execute("SELECT amount FROM bills").fetchone()[0]
        finally:
            check.close()
        self.assertEqual(qc, "ok")
        self.assertEqual(amount, 10)
        src_fp = fp.fingerprint(self.db)
        dst_fp = fp.fingerprint(dest)
        self.assertEqual(src_fp["tables"], dst_fp["tables"])
        code, diff_out, diff_err = _run(
            fpdiff.main,
            [
                self._dump("src.json", src_fp),
                self._dump("dst.json", dst_fp),
                "--logical",
            ],
        )
        self.assertEqual(diff_err, "")
        self.assertEqual(code, 0, msg=diff_out)
        self.assertEqual(dest.stat().st_size, int(out.split("size=")[1].split()[0]))
        leftovers = [
            name
            for name in os.listdir(self.root)
            if name.startswith(".att0-backup-")
        ]
        self.assertEqual(leftovers, [])

    def test_wal_source_round_trip(self):
        src = self.root / "wal-src.sqlite"
        conn = sqlite3.connect(str(src))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY, amount INTEGER)")
            conn.execute("INSERT INTO bills (amount) VALUES (7)")
            conn.commit()
        finally:
            conn.close()
        _drop_sidecars(src)
        self.assertEqual(_format_versions(src), (2, 2))
        before = _family_hashes(src)
        names_before = sorted(item.name for item in src.parent.iterdir())
        dest = self.root / "wal-copy.sqlite"
        seen: list[str] = []
        with _sqlite351_ro_guard(seen):
            line = backup.backup_database(src, dest, cmdlines=[], lock_held=False)
        self.assertIn("quick_check=ok", line)
        self.assertIn("journal_mode=delete", line)
        self.assertEqual(_format_versions(dest), (1, 1))
        self.assertEqual(_family_hashes(src), before)
        self.assertFalse(Path(str(src) + "-wal").exists())
        self.assertFalse(Path(str(src) + "-shm").exists())
        self.assertFalse(Path(str(dest) + "-wal").exists())
        self.assertFalse(Path(str(dest) + "-shm").exists())
        names_after = sorted(item.name for item in src.parent.iterdir())
        self.assertEqual(
            [name for name in names_after if name != dest.name],
            names_before,
        )
        hits = [
            item
            for item in seen
            if _path_from_uri(item) is not None
            and _path_from_uri(item).resolve() == src.resolve()
        ]
        self.assertTrue(hits)
        for item in hits:
            self.assertIn("immutable=1", item)
            self.assertIn("mode=ro", item)
        check = sqlite3.connect(str(dest))
        try:
            self.assertEqual(
                check.execute("SELECT amount FROM bills").fetchone()[0], 7
            )
            self.assertEqual(check.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            check.close()

    def test_uncheckpointed_wal_source_keeps_wal_only_row(self):
        src = self.root / "wal-src.sqlite"
        held = _open_uncheckpointed(src)
        if held is not None:
            self.addCleanup(held.close)
        before = _family_hashes(src)
        names_before = sorted(item.name for item in src.parent.iterdir())
        dest = self.root / "wal-copy.sqlite"
        seen: list[str] = []
        with _sqlite351_ro_guard(seen):
            line = backup.backup_database(src, dest, cmdlines=[], lock_held=False)
        self.assertIn("quick_check=ok", line)
        self.assertIn("journal_mode=delete", line)
        self.assertEqual(_format_versions(dest), (1, 1))
        self.assertEqual(_family_hashes(src), before)
        self.assertEqual(
            sorted(
                item.name
                for item in src.parent.iterdir()
                if item.name != dest.name
            ),
            names_before,
        )
        self.assertFalse(
            any(name.startswith(".att0-backup-") for name in os.listdir(self.root))
        )
        self.assertFalse(
            any(
                _path_from_uri(item) is not None
                and _path_from_uri(item).resolve() == src.resolve()
                for item in seen
            )
        )
        self.assertTrue(
            any(
                "mode=ro" in item and "immutable=1" not in item and "db.sqlite" in item
                for item in seen
            )
        )
        check = sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = check.execute("SELECT amount FROM bills ORDER BY id").fetchall()
            self.assertEqual(check.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            check.close()
        self.assertEqual(rows, [(1,), (2,)])

    def test_same_process_holder_does_not_change_source_family(self):
        """A connection that already has the source open must not share -shm."""
        src = self.root / "wal-src.sqlite"
        setup = _open_uncheckpointed(src)
        holder = sqlite3.connect(str(src))
        try:
            before = _family_hashes(src)
            names_before = sorted(item.name for item in src.parent.iterdir())
            dest = self.root / "wal-copy.sqlite"
            seen: list[str] = []
            staged_inodes: list[tuple[int, int]] = []
            with _sqlite351_ro_guard(seen, staged_inodes):
                line = backup.backup_database(src, dest, cmdlines=[], lock_held=False)
            self.assertIn("quick_check=ok", line)
            self.assertIn("journal_mode=delete", line)
            self.assertEqual(_format_versions(dest), (1, 1))
            self.assertEqual(_family_hashes(src), before)
            self.assertEqual(
                sorted(
                    item.name
                    for item in src.parent.iterdir()
                    if item.name != dest.name
                ),
                names_before,
            )
            self.assertFalse(
                any(name.startswith(".att0-backup-") for name in os.listdir(self.root))
            )
            self.assertFalse(
                any(
                    _path_from_uri(item) is not None
                    and _path_from_uri(item).resolve() == src.resolve()
                    for item in seen
                )
            )
            self.assertTrue(staged_inodes)
            self.assertNotEqual(staged_inodes[0], (src.stat().st_dev, src.stat().st_ino))
            with closing(
                sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)
            ) as check:
                rows = check.execute("SELECT amount FROM bills ORDER BY id").fetchall()
                self.assertEqual(check.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(rows, [(1,), (2,)])
        finally:
            try:
                holder.close()
            finally:
                if setup is not None:
                    setup.close()

    def test_stage_space_refusal_leaves_no_dest(self):
        src = self.root / "wal-src.sqlite"
        held = _open_uncheckpointed(src)
        if held is not None:
            self.addCleanup(held.close)
        before = _family_hashes(src)
        dest = self.root / "wal-copy.sqlite"
        need = (
            src.stat().st_size
            + Path(str(src) + "-wal").stat().st_size
            + fp._STAGE_FREE_MARGIN
        )
        short = mock.Mock(free=need - 1)
        with mock.patch("attachments.att0_fp.shutil.disk_usage", return_value=short):
            with self.assertRaises(backup.BackupRefuse) as ctx:
                backup.backup_database(src, dest, cmdlines=[], lock_held=False)
            code, _out, err = _run(backup.main, [str(src), str(dest)])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(str(ctx.exception), "refuse: not enough free space")
        self.assertEqual(code, 2)
        self.assertIn("not enough free space", err)
        self.assertNotIn(DIR_TOKEN, err)
        self.assertFalse(dest.exists())
        self.assertEqual(_family_hashes(src), before)
        self.assertFalse(
            any(name.startswith(".att0-backup-") for name in os.listdir(self.root))
        )

    def test_wal_stage_failure_exits_2_and_leaves_no_dest(self):
        src = self.root / "wal-src.sqlite"
        held = _open_uncheckpointed(src)
        if held is not None:
            self.addCleanup(held.close)
        before = _family_hashes(src)
        dest = self.root / "wal-copy.sqlite"
        with mock.patch(
            "attachments.att0_fp._copy_bytes", side_effect=OSError("injected")
        ):
            with self.assertRaises(backup.BackupRefuse) as ctx:
                backup.backup_database(src, dest, cmdlines=[], lock_held=False)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("cannot stage source wal", str(ctx.exception))
        self.assertFalse(dest.exists())
        self.assertEqual(_family_hashes(src), before)
        self.assertFalse(
            any(name.startswith(".att0-backup-") for name in os.listdir(self.root))
        )

    def test_refuses_existing_dest_mailroom_name_same_file_and_missing_src(self):
        dest = self.root / "copy.sqlite"
        dest.write_bytes(b"keep")
        code, _out, err = _run(backup.main, [str(self.db), str(dest)])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertNotIn(DIR_TOKEN, err)
        self.assertEqual(dest.read_bytes(), b"keep")
        forbidden = self.root / "mailroom.sqlite"
        code, _out, err = _run(backup.main, [str(self.db), str(forbidden)])
        self.assertEqual(code, 2)
        self.assertIn("mailroom.sqlite", err)
        self.assertFalse(forbidden.exists())
        code, _out, err = _run(backup.main, [str(self.db), str(self.db)])
        self.assertEqual(code, 2)
        self.assertIn("same file", err)
        self.assertTrue(self.db.is_file())
        missing = self.root / "no-such.sqlite"
        code, _out, err = _run(backup.main, [str(missing), str(self.root / "out.sqlite")])
        self.assertEqual(code, 2)
        self.assertIn("src is missing", err)
        self.assertFalse((self.root / "out.sqlite").exists())

    def test_refuses_missing_directory_and_non_sqlite(self):
        code, _out, err = _run(
            backup.main,
            [str(self.db), str(self.root / "missing-dir" / "out.sqlite")],
        )
        self.assertEqual(code, 2)
        self.assertIn("dest directory is missing", err)
        text = self.root / "notes.txt"
        text.write_text("hello", encoding="utf-8")
        dest = self.root / "from-text.sqlite"
        code, _out, err = _run(backup.main, [str(text), str(dest)])
        self.assertEqual(code, 2)
        self.assertIn("not sqlite", err)
        self.assertFalse(dest.exists())
        sidecar = self.root / "side.sqlite-wal"
        sidecar.write_bytes(b"")
        code, _out, err = _run(
            backup.main, [str(self.db), str(self.root / "side.sqlite")]
        )
        self.assertEqual(code, 2)
        self.assertIn("sidecar", err)
        self.assertFalse((self.root / "side.sqlite").exists())

    def test_mid_copy_failure_leaves_no_dest_or_temp(self):
        dest = self.root / "partial.sqlite"

        def boom(_src_conn, dst_conn):
            dst_conn.execute("CREATE TABLE partial (id INTEGER)")
            dst_conn.commit()
            raise sqlite3.OperationalError("mid-copy")

        with mock.patch.object(backup, "_backup_pages", boom):
            with self.assertRaises(backup.BackupRefuse):
                backup.backup_database(self.db, dest)
        self.assertFalse(dest.exists())
        names = os.listdir(self.root)
        self.assertFalse(any(name.startswith(".att0-backup-") for name in names))

    def test_quick_check_failure_leaves_no_dest(self):
        dest = self.root / "bad-check.sqlite"
        with mock.patch.object(backup, "_quick_check", return_value=("malformed", "delete")):
            with self.assertRaises(backup.BackupRefuse) as ctx:
                backup.backup_database(self.db, dest)
        self.assertEqual(ctx.exception.code, 1)
        self.assertFalse(dest.exists())
        self.assertFalse(any(name.startswith(".att0-backup-") for name in os.listdir(self.root)))

    def test_writer_gate_conflict_is_sanitized_and_allow_reads_sor_name(self):
        src = self.root / "mailroom.sqlite"
        _seed(src)
        dest = self.root / "snapshot.sqlite"
        with self.assertRaises(backup.BackupRefuse) as ctx:
            backup.backup_database(src, dest, cmdlines=[], lock_held=True)
        self.assertEqual(ctx.exception.code, 2)
        self.assertNotIn("NOT-A-NAME-gg", str(ctx.exception))
        self.assertIn("mailroom.sqlite", str(ctx.exception))
        self.assertFalse(dest.exists())
        with mock.patch(
            "attachments.att0_backup.refuse_if_sor_writer_conflict",
            side_effect=SorWriterRefuse("detail NOT-A-NAME-gg"),
        ):
            code, _out, err = _run(backup.main, [str(src), str(dest)])
        self.assertEqual(code, 2)
        self.assertNotIn("NOT-A-NAME-gg", err)
        self.assertNotIn(DIR_TOKEN, err)
        self.assertFalse(dest.exists())
        line = backup.backup_database(src, dest, cmdlines=[], lock_held=False)
        self.assertIn("quick_check=ok", line)
        self.assertIn("src=mailroom.sqlite", line)
        self.assertTrue(dest.is_file())

    def test_docstring_mentions_reader_and_writer_lock(self):
        text = Path(backup.__file__).read_text(encoding="utf-8")
        self.assertIn("with_writer_lock.py", text)
        self.assertIn("refuse_if_sor_writer_conflict", text)
        self.assertIn("Reading the system of record is allowed", text)
        self.assertIn("journal_mode=DELETE", text)
        self.assertIn("sqlite_pragmas.py", text)
        fp_text = Path(fp.__file__).read_text(encoding="utf-8")
        self.assertIn("immutable=1", fp_text)
        self.assertIn("Does not create a ``-wal`` or a ``-shm``", fp_text)
        help_text = backup.build_parser().format_help()
        self.assertIn("with_writer_lock.py", help_text)

    def _dump(self, name, doc) -> str:
        path = self.root / name
        path.write_text(fp.render_json(doc), encoding="utf-8")
        return str(path)


class HelperContractTests(unittest.TestCase):
    def test_docs_cover_usage_and_the_shell_recipe(self):
        text = DOC.read_text(encoding="utf-8")
        design = DESIGN.read_text(encoding="utf-8")
        for needle in (
            "att0_fp.py",
            "att0_fpdiff.py",
            "att0_backup.py",
            "--logical",
            "--allow-added-table",
            "--exclude-table",
            "--exclude-column",
            "ask_audit",
            "drafts",
            "has_attachments",
            "imap_live_by_folder",
            "SOR_FINGERPRINT",
            "with_writer_lock.py",
            "mailroom.sqlite",
            "/path/to/db.sqlite",
            "shasum -a 256",
            "mode=ro",
            "immutable=1",
            "journal_mode=DELETE",
            "sqlite_pragmas.py",
            "cannot stage source wal",
            "not enough free space",
            "not a hardlink",
            "quick_check",
            "skipped_virtual_tables",
            "pipefail",
            "CREATE VIRTUAL TABLE",
            "ERROR",
        ):
            self.assertIn(needle, text, msg=needle)
        self.assertIn("ATT-0-fingerprint.md", design)
        self.assertIn("not restore a backup", text.lower())
        schema = SCHEMA.read_text(encoding="utf-8")
        for name in (
            "attachments",
            "attachment_extracts",
            "attachment_chunks",
            "attachment_meta_scans",
            "attachment_folder_uidvalidity",
            "attachment_chunks_fts",
        ):
            self.assertIn(name, schema)
            self.assertIn("`%s`" % name, text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in ("/Users/", "@me.com", "@icloud.com"):
            self.assertNotIn(needle, hay)

    def test_helpers_stay_stdlib_and_do_not_touch_frozen_modules(self):
        for path in HELPERS:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("import subprocess", source)
            self.assertNotIn("imaplib", source)
            self.assertNotIn("import socket", source)
            self.assertNotIn("/Users/", source)
            self.assertNotIn("@me.com", source)
            self.assertNotIn("@icloud.com", source)
        self.assertNotIn("att0_restore", "\n".join(p.name for p in HELPERS))


def _seed_fts(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)"
        )
        conn.execute("INSERT INTO messages (body) VALUES ('alpha')")
        conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(body)")
        conn.execute("INSERT INTO messages_fts (body) VALUES ('alpha')")
        conn.execute("CREATE VIRTUAL TABLE messages_ids USING fts5(body)")
        conn.commit()
    finally:
        conn.close()


def _shell_recipe() -> str:
    text = DOC.read_text(encoding="utf-8")
    marker = "```bash\n"
    start = text.index(marker) + len(marker)
    end = text.index("\n```", start)
    return text[start:end] + "\n"


def _shell_tools() -> bool:
    return all(
        shutil.which(name) for name in ("bash", "sqlite3", "shasum", "grep", "paste")
    )


class ShellRecipeTests(_DbCase):
    def _run_recipe(self, db: Path, extra_env=None):
        script = _shell_recipe().replace("/path/to/db.sqlite", str(db))
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=str(self.root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_recipe_skips_virtual_tables_and_hashes_shadow_tables(self):
        if not _shell_tools():
            self.skipTest("bash, sqlite3, or shasum is missing")
        path = self.root / "fts.sqlite"
        _seed_fts(path)
        proc = self._run_recipe(path)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stderr, "")
        names = [line.split("\t", 1)[0] for line in proc.stdout.splitlines() if line]
        self.assertIn("messages_fts_data", names)
        self.assertNotIn("messages_fts", names)
        self.assertNotIn("messages_ids", names)
        doc = fp.fingerprint(path)
        self.assertEqual(names, list(doc["tables"]))
        for name in names:
            count, digest = proc.stdout.splitlines()[names.index(name)].split("\t")[1:]
            self.assertRegex(count, r"^[0-9]+$")
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_recipe_fails_loud_when_stderr_is_set_on_a_numeric_count(self):
        if not _shell_tools():
            self.skipTest("bash, sqlite3, or shasum is missing")
        _seed(self.db)
        real = shutil.which("sqlite3")
        marker = self.root / "hash-reached"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "sqlite3"
        wrapper.write_text(
            """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    *'FROM "bills"'*)
      case "$arg" in
        *'COUNT(*)'*)
          echo "no such module: vec0" >&2
          printf '0\\n'
          exit 0
          ;;
      esac
      if [ -n "$ATT0_HASH_MARKER" ]; then
        printf 'reached\\n' > "$ATT0_HASH_MARKER"
      fi
      echo "no such module: vec0" >&2
      exit 0
      ;;
  esac
done
exec %s "$@"
"""
            % real,
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = {
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "ATT0_HASH_MARKER": str(marker),
        }
        proc = self._run_recipe(self.db, extra_env=env)
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "ERROR bills\n")
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("bills", proc.stdout)
        self.assertNotIn(EMPTY_SHA, proc.stdout)
        self.assertNotRegex(proc.stdout, r"[0-9a-f]{64}")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
