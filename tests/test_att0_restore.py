#!/usr/bin/env python3
"""ATT-0 restore helper. Synthetic temp databases only. No network."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Discover imports this module after sitecustomize has installed the hermetic
# guard. Snapshot the wrapped callables before importing the helper. On macOS
# the writer gate shells out to `ps` (there is no /proc). That path, and the
# wrapper subprocess below, must not leave subprocess.run unwrapped for tests
# that run later. Linux CI never takes the ps fallback, which is why it stayed
# green.
_HERMETIC_SUBPROCESS = {
    "run": subprocess.run,
    "call": subprocess.call,
    "check_call": subprocess.check_call,
    "check_output": subprocess.check_output,
}
_HERMETIC_POPEN_INIT = subprocess.Popen.__init__
_HERMETIC_OS = {
    name: getattr(os, name)
    for name in (
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
    )
    if hasattr(os, name) and getattr(getattr(os, name), "_mailroom_hermetic", False)
}

import attachments.att0_restore as restore  # noqa: E402
from sor_writer_gate import SorWriterRefuse  # noqa: E402


def _rearm_hermetic_guard() -> None:
    """Put the suite guard back if this module's restore path replaced it."""
    for name, saved in _HERMETIC_SUBPROCESS.items():
        if not getattr(saved, "_mailroom_hermetic", False):
            continue
        current = getattr(subprocess, name)
        if current is not saved:
            setattr(subprocess, name, saved)
    if getattr(_HERMETIC_POPEN_INIT, "_mailroom_hermetic", False):
        if subprocess.Popen.__init__ is not _HERMETIC_POPEN_INIT:
            subprocess.Popen.__init__ = _HERMETIC_POPEN_INIT
    for name, saved in _HERMETIC_OS.items():
        current = getattr(os, name, None)
        if current is not saved:
            setattr(os, name, saved)


def tearDownModule() -> None:  # noqa: N802
    _rearm_hermetic_guard()


_rearm_hermetic_guard()

DOC = ROOT / "docs" / "attachments" / "att0-restore.md"
HELPER = SCRIPTS / "attachments" / "att0_restore.py"
WRAPPER = SCRIPTS / "with_writer_lock.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_from_uri(text: str) -> Path | None:
    if not str(text).startswith("file:"):
        return None
    parsed = urlparse(str(text))
    if parsed.scheme != "file" or not parsed.path:
        return None
    return Path(unquote(parsed.path))


def _family_hashes(path: Path) -> dict[str, str | None]:
    found: dict[str, str | None] = {"main": _sha(path)}
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        found[suffix] = _sha(sidecar) if sidecar.is_file() else None
    return found


def _row_digest(path: Path) -> str:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, body FROM notes ORDER BY id").fetchall()
    finally:
        conn.close()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    return len(rows) == 1 and str(rows[0][0]).lower() == "ok"


def _make_db(path: Path, body: str, *, wal: bool = True) -> None:
    conn = sqlite3.connect(str(path))
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES (?)", (body,))
        conn.commit()
    finally:
        conn.close()


def _format_versions(path: Path) -> tuple[int, int]:
    """SQLite header bytes 18-19: 1 = rollback journal, 2 = WAL."""
    blob = path.read_bytes()
    return blob[18], blob[19]


def _wal_without_shm(path: Path) -> bool:
    write_v, read_v = _format_versions(path)
    shm = Path(str(path) + "-shm")
    return (write_v == 2 or read_v == 2) and not shm.is_file()


def _temps(directory: Path) -> list[Path]:
    return [path for path in directory.iterdir() if ".att0-restore-" in path.name]


def _open_uncheckpointed(path: Path, body: str) -> sqlite3.Connection:
    """Hold a WAL connection whose latest commit stays in a non-empty -wal."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes (body) VALUES (?)", (body,))
    conn.commit()
    wal = Path(str(path) + "-wal")
    if not (wal.is_file() and wal.stat().st_size > 0):
        conn.close()
        raise AssertionError("expected a non-empty wal")
    return conn


class RestoreCliTests(unittest.TestCase):
    def test_mailroom_without_flag_exits_2_and_leaves_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            before = _sha(dest)
            src_before = _sha(src)
            rc = restore.main(["--src", str(src), "--dest", str(dest)])
            self.assertEqual(rc, 2)
            self.assertEqual(_sha(dest), before)
            self.assertEqual(_sha(src), src_before)
            self.assertEqual(_temps(root), [])
            self.assertNotIn(str(root), restore.format_report({"src": "a", "dest": "b"}))

    def test_happy_path_mailroom_matches_src_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            src_before = _sha(src)
            src_digest = _row_digest(src)
            report = restore.restore_database(
                src,
                dest,
                allow_mailroom_sqlite=True,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(report["src"], "backup.sqlite")
            self.assertEqual(report["dest"], "mailroom.sqlite")
            self.assertNotIn(str(root), restore.format_report(report))
            self.assertEqual(_temps(root), [])
            self.assertFalse((root / "mailroom.sqlite-wal").exists())
            self.assertFalse((root / "mailroom.sqlite-shm").exists())
            self.assertEqual(_row_digest(dest), src_digest)
            self.assertEqual(_sha(src), src_before)
            self.assertTrue(_integrity_ok(dest))
            self.assertEqual(_row_digest(dest), src_digest)

    def test_cli_happy_path_prints_basenames_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            lock = root / "missing.lock"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {"MAILROOM_WRITE_LOCK": str(lock)}):
                with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                    rc = restore.main(
                        [
                            "--src",
                            str(src),
                            "--dest",
                            str(dest),
                            "--allow-mailroom-sqlite",
                        ]
                    )
            self.assertEqual(rc, 0, stderr.getvalue())
            text = stdout.getvalue()
            self.assertEqual(text, "restored src=backup.sqlite dest=mailroom.sqlite\n")
            self.assertNotIn(str(root), text)
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertEqual(_row_digest(dest), _row_digest(src))
            self.assertEqual(_temps(root), [])

    def test_live_nonempty_wal_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            held = sqlite3.connect(str(dest))
            try:
                held.execute("PRAGMA journal_mode=WAL")
                held.execute("PRAGMA wal_autocheckpoint=0")
                held.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
                held.execute("INSERT INTO notes (body) VALUES ('live-row')")
                held.commit()
                wal = Path(str(dest) + "-wal")
                self.assertTrue(wal.is_file())
                self.assertGreater(wal.stat().st_size, 0)
                src_digest = _row_digest(src)
                src_before = _sha(src)
                report = restore.restore_database(
                    src,
                    dest,
                    allow_mailroom_sqlite=True,
                    cmdlines=[],
                    lock_held=False,
                )
                self.assertEqual(report["dest"], "mailroom.sqlite")
                self.assertEqual(_sha(src), src_before)
                self.assertEqual(_temps(root), [])
                fresh = sqlite3.connect(str(dest))
                try:
                    rows = fresh.execute("SELECT body FROM notes").fetchall()
                    self.assertEqual(rows, [("from-src",)])
                    self.assertTrue(
                        str(fresh.execute("PRAGMA integrity_check").fetchone()[0]).lower()
                        == "ok"
                    )
                finally:
                    fresh.close()
            finally:
                held.close()
            reopened = sqlite3.connect(str(dest))
            try:
                rows = reopened.execute("SELECT body FROM notes").fetchall()
                self.assertEqual(rows, [("from-src",)])
                self.assertEqual(_row_digest(dest), src_digest)
            finally:
                reopened.close()
            wal = Path(str(dest) + "-wal")
            shm = Path(str(dest) + "-shm")
            if wal.exists():
                self.assertEqual(wal.stat().st_size, 0)
            if shm.exists():
                reopened = sqlite3.connect(str(dest))
                try:
                    rows = reopened.execute("SELECT body FROM notes").fetchall()
                finally:
                    reopened.close()
                self.assertEqual(rows, [("from-src",)])

    def test_same_file_missing_src_non_sqlite_and_src_mailroom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "notes.sqlite"
            _make_db(db, "same")
            before = _sha(db)
            with self.assertRaises(restore.RestoreRefuse):
                restore.restore_database(db, db, cmdlines=[], lock_held=False)
            self.assertEqual(_sha(db), before)

            linked = root / "linked.sqlite"
            os.link(str(db), str(linked))
            with self.assertRaises(restore.RestoreRefuse) as ctx:
                restore.restore_database(db, linked, cmdlines=[], lock_held=False)
            self.assertIn("same file", str(ctx.exception))
            self.assertEqual(_sha(db), before)
            self.assertEqual(_sha(linked), before)

            missing = root / "no-such.sqlite"
            dest = root / "dest.sqlite"
            _make_db(dest, "keep")
            dest_before = _sha(dest)
            with self.assertRaises(restore.RestoreRefuse) as ctx:
                restore.restore_database(missing, dest, cmdlines=[], lock_held=False)
            self.assertIn("missing", str(ctx.exception))
            self.assertEqual(_sha(dest), dest_before)

            junk = root / "not-a.db"
            junk.write_bytes(b"this is not sqlite")
            with self.assertRaises(restore.RestoreRefuse) as ctx:
                restore.restore_database(junk, dest, cmdlines=[], lock_held=False)
            self.assertIn("not a sqlite", str(ctx.exception))
            self.assertEqual(_sha(dest), dest_before)
            self.assertEqual(junk.read_bytes(), b"this is not sqlite")

            named = root / "nested" / "mailroom.sqlite"
            named.parent.mkdir()
            _make_db(named, "do-not-use")
            named_before = _sha(named)
            with self.assertRaises(restore.RestoreRefuse) as ctx:
                restore.restore_database(named, dest, cmdlines=[], lock_held=False)
            self.assertIn("mailroom.sqlite", str(ctx.exception))
            self.assertEqual(_sha(named), named_before)
            self.assertEqual(_sha(dest), dest_before)
            self.assertEqual(_temps(root), [])

            rc = restore.main(["--src", str(missing), "--dest", str(dest)])
            self.assertEqual(rc, 2)
            self.assertEqual(_sha(dest), dest_before)

    def test_dest_directory_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            _make_db(src, "from-src")
            dest = root / "missing-dir" / "notes.sqlite"
            with self.assertRaises(restore.RestoreRefuse) as ctx:
                restore.restore_database(src, dest, cmdlines=[], lock_held=False)
            self.assertIn("directory", str(ctx.exception))
            self.assertFalse(dest.parent.exists())
            self.assertEqual(_temps(root), [])

    def test_gate_conflict_refuses_before_any_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            before = _sha(dest)
            opened = []

            def boom(*_args, **_kwargs):
                raise SorWriterRefuse("CONFLICT: injected")

            real_connect = sqlite3.connect

            def track_connect(*args, **kwargs):
                opened.append(args[0] if args else kwargs.get("database"))
                raise AssertionError("sqlite opened")

            with mock.patch.object(restore, "refuse_if_sor_writer_conflict", side_effect=boom):
                with mock.patch("sqlite3.connect", side_effect=track_connect):
                    rc = restore.main(
                        [
                            "--src",
                            str(src),
                            "--dest",
                            str(dest),
                            "--allow-mailroom-sqlite",
                        ]
                    )
            self.assertEqual(rc, 2)
            self.assertEqual(opened, [])
            self.assertEqual(_sha(dest), before)
            self.assertEqual(_temps(root), [])
            self.assertIs(sqlite3.connect, real_connect)

    def test_backup_failure_leaves_dest_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            before = _sha(dest)
            src_before = _sha(src)
            lock = root / "missing.lock"

            def boom(_source, _target):
                raise sqlite3.OperationalError("injected backup failure")

            with mock.patch.dict(os.environ, {"MAILROOM_WRITE_LOCK": str(lock)}):
                with mock.patch.object(restore, "_backup", boom):
                    rc = restore.main(
                        [
                            "--src",
                            str(src),
                            "--dest",
                            str(dest),
                            "--allow-mailroom-sqlite",
                        ]
                    )
            self.assertEqual(rc, 1)
            self.assertEqual(_sha(dest), before)
            self.assertEqual(_sha(src), src_before)
            self.assertEqual(_temps(root), [])
            conn = sqlite3.connect(str(dest))
            try:
                self.assertEqual(
                    conn.execute("SELECT body FROM notes").fetchall(),
                    [("live-dest",)],
                )
            finally:
                conn.close()

    def test_help_and_doc_name_the_wrapper(self):
        help_text = restore.build_parser().format_help()
        self.assertIn(
            "scripts/with_writer_lock.py --purpose att0-restore -- "
            "python3 scripts/attachments/att0_restore.py ...",
            help_text,
        )
        self.assertIn("does not take the writer flock", help_text)
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn(
            "scripts/with_writer_lock.py --purpose att0-restore -- "
            "python3 scripts/attachments/att0_restore.py ...",
            source,
        )
        self.assertEqual(source.count("import subprocess"), 1)
        self.assertIn('"/usr/sbin/lsof"', source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("/usr/bin/curl", source)
        self.assertNotIn("/usr/bin/security", source)
        self.assertNotIn("ask_mail", source)
        self.assertNotIn("os.link(", source)
        self.assertIn("refuse: source changed during stage", source)
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn(
            "scripts/with_writer_lock.py --purpose att0-restore -- "
            "python3 scripts/attachments/att0_restore.py "
            "--src /var/backups/mailroom-backup.sqlite "
            "--dest /var/lib/mailroom/mailroom.sqlite "
            "--allow-mailroom-sqlite",
            doc,
        )
        self.assertIn(
            "The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only).",
            doc,
        )
        self.assertIn("PRAGMA journal_mode=DELETE", doc)
        self.assertIn("bytes 18 and 19", doc)
        self.assertNotIn("$HOME", doc)
        self.assertNotIn("/Users/", doc)
        self.assertNotIn("/home/", doc)
        self.assertNotIn("wal_checkpoint", doc)
        self.assertNotIn("wal_checkpoint", source)
        self.assertIn("immutable=1", doc)
        self.assertIn("casefold", doc)
        self.assertIn("refuse: not enough free space", doc)
        self.assertIn("F_FULLFSYNC", doc)
        self.assertIn("64 MiB", doc)
        self.assertIn("refuse: source changed during stage", doc)
        self.assertIn("SIGKILL", doc)
        self.assertIn(".att0-restore-", doc)
        self.assertIn("safe to delete when no helper is running", doc)
        self.assertIn(
            "no daily/rem/lock holder, ask-mail-serve booted out, and lsof empty first",
            doc,
        )
        self.assertIn("not hardlinks", doc)
        self.assertNotIn('sqlite3 /var/backups/mailroom-backup.sqlite ".backup ', doc)
        self.assertNotIn("mailroom.sqlite.restore-tmp", doc)
        self.assertNotIn("mv /var/lib/mailroom/mailroom.sqlite-wal", doc)

    def test_wrapper_command_restores_and_foreign_lock_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            lock = root / "mailroom.write.lock"
            absent_ar = root / "ACTION_REQUIRED"
            env = os.environ.copy()
            env["MAILROOM_WRITE_LOCK"] = str(lock)
            env.pop("MAILROOM_ACTION_REQUIRED", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(WRAPPER),
                    "--purpose",
                    "att0-restore",
                    "--lock-file",
                    str(lock),
                    "--action-required-file",
                    str(absent_ar),
                    "--",
                    sys.executable,
                    str(HELPER),
                    "--src",
                    str(src),
                    "--dest",
                    str(dest),
                    "--allow-mailroom-sqlite",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("restored src=backup.sqlite dest=mailroom.sqlite", proc.stdout)
            self.assertNotIn(str(root), proc.stdout)
            self.assertEqual(_row_digest(dest), _row_digest(src))
            self.assertEqual(_temps(root), [])

            for suffix in ("", "-wal", "-shm", "-journal"):
                sidecar = Path(str(dest) + suffix) if suffix else dest
                if sidecar.exists():
                    sidecar.unlink()
            _make_db(dest, "live-again")
            before = _sha(dest)
            import fcntl

            lock.write_text(
                "pid=%d\nhostname=placeholder\npurpose=other-job\nacquired_at=2026-09-26T00:00:00+00:00\n"
                % os.getpid(),
                encoding="utf-8",
            )
            handle = open(lock, "r+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                child = subprocess.run(
                    [
                        sys.executable,
                        str(HELPER),
                        "--src",
                        str(src),
                        "--dest",
                        str(dest),
                        "--allow-mailroom-sqlite",
                    ],
                    cwd=str(ROOT),
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self.assertEqual(child.returncode, 2, child.stderr)
            self.assertIn("CONFLICT", child.stderr)
            self.assertEqual(_sha(dest), before)
            self.assertEqual(_temps(root), [])

    def test_restored_dest_is_not_wal_without_shm(self):
        """mode=ro must work after restore on SQLite 3.51.0.

        Header bytes 18-19 are the write/read versions (1 rollback, 2 WAL).
        A WAL header with no -shm file is the shape that raises
        OperationalError: unable to open database file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            src_versions = _format_versions(src)
            self.assertEqual(src_versions, (2, 2))
            src_before = _sha(src)
            src_digest = _row_digest(src)
            restore.restore_database(
                src,
                dest,
                allow_mailroom_sqlite=True,
                cmdlines=[],
                lock_held=False,
            )
            self.assertEqual(_format_versions(src), src_versions)
            self.assertEqual(_sha(src), src_before)
            self.assertEqual(_format_versions(dest), (1, 1))
            self.assertFalse(_wal_without_shm(dest))
            self.assertFalse(Path(str(dest) + "-wal").exists())
            self.assertFalse(Path(str(dest) + "-shm").exists())
            self.assertEqual(_row_digest(dest), src_digest)
            self.assertTrue(_integrity_ok(dest))

            simulated = root / "wal-without-shm.sqlite"
            flipped = bytearray(dest.read_bytes())
            flipped[18] = 2
            flipped[19] = 2
            simulated.write_bytes(flipped)
            self.assertTrue(_wal_without_shm(simulated))
            self.assertNotEqual(_format_versions(simulated), (1, 1))

    def test_rearm_restores_subprocess_run_if_this_module_replaced_it(self):
        replaced = subprocess.run
        subprocess.run = lambda *args, **kwargs: None
        try:
            self.assertFalse(getattr(subprocess.run, "_mailroom_hermetic", False))
            _rearm_hermetic_guard()
            self.assertIs(subprocess.run, _HERMETIC_SUBPROCESS["run"])
            self.assertTrue(getattr(subprocess.run, "_mailroom_hermetic", False))
        finally:
            subprocess.run = replaced
            _rearm_hermetic_guard()


class RestoreReviewTests(unittest.TestCase):
    """R1 residual, R4-R9, and R11. Synthetic files only."""

    def _allow(self, src: Path, dest: Path) -> None:
        restore.restore_database(
            src,
            dest,
            allow_mailroom_sqlite=True,
            cmdlines=[],
            lock_held=False,
        )

    def test_mailroom_basename_casefold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            _make_db(src, "from-src")
            for name in ("Mailroom.sqlite", "MAILROOM.SQLITE"):
                dest = root / name
                if dest.exists():
                    dest.unlink()
                _make_db(dest, "live-dest")
                before = _sha(dest)
                ino = dest.stat().st_ino
                stderr = io.StringIO()
                opened: list[str] = []
                real_connect = sqlite3.connect

                def track(database, *args, **kwargs):
                    opened.append(str(database))
                    return real_connect(database, *args, **kwargs)

                with mock.patch("sqlite3.connect", side_effect=track):
                    with mock.patch("sys.stderr", stderr):
                        rc = restore.main(["--src", str(src), "--dest", str(dest)])
                self.assertEqual(rc, 2, stderr.getvalue())
                self.assertEqual(opened, [])
                self.assertEqual(_sha(dest), before)
                self.assertEqual(dest.stat().st_ino, ino)
                self.assertNotIn(str(root), stderr.getvalue())
                self.assertIn("mailroom.sqlite", stderr.getvalue())
                dest.unlink()

            varied = root / "Mailroom.sqlite"
            _make_db(varied, "live-dest")
            self._allow(src, varied)
            self.assertEqual(_row_digest(varied), _row_digest(src))
            self.assertEqual(_format_versions(varied), (1, 1))

            named = root / "nested" / "Mailroom.sqlite"
            named.parent.mkdir()
            _make_db(named, "do-not-copy")
            named_before = _sha(named)
            dest = root / "dest.sqlite"
            _make_db(dest, "keep")
            dest_before = _sha(dest)
            with self.assertRaises(restore.RestoreRefuse) as ctx:
                restore.restore_database(named, dest, cmdlines=[], lock_held=False)
            self.assertIn("mailroom.sqlite", str(ctx.exception))
            self.assertEqual(_sha(named), named_before)
            self.assertEqual(_sha(dest), dest_before)
            self.assertEqual(_temps(root), [])

    def test_symlink_basename_mailroom_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            real = root / "real.sqlite"
            link = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(real, "live-dest")
            link.symlink_to(real.name)
            before = _sha(real)
            ino = real.stat().st_ino
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                rc = restore.main(["--src", str(src), "--dest", str(link)])
            self.assertEqual(rc, 2, stderr.getvalue())
            self.assertTrue(link.is_symlink())
            self.assertEqual(_sha(real), before)
            self.assertEqual(real.stat().st_ino, ino)
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertEqual(_temps(root), [])

    def test_checkpointed_source_gains_no_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            src_dir.mkdir()
            src = src_dir / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            conn = sqlite3.connect(str(src))
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                rows = conn.execute("SELECT body FROM notes").fetchall()
            finally:
                conn.close()
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(src) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            src_hash = _sha(src)
            seen: list[str] = []
            real_connect = sqlite3.connect

            def track(database, *args, **kwargs):
                seen.append(str(database))
                return real_connect(database, *args, **kwargs)

            with mock.patch("sqlite3.connect", side_effect=track):
                restore.restore_database(src, dest, cmdlines=[], lock_held=False)
            src_uri = src.resolve().as_uri()
            self.assertTrue(any("immutable=1" in item and src_uri in item for item in seen))
            self.assertFalse(any("mode=ro" in item and src_uri in item for item in seen))
            self.assertEqual(sorted(path.name for path in src_dir.iterdir()), ["backup.sqlite"])
            self.assertEqual(_sha(src), src_hash)
            self.assertFalse(Path(str(src) + "-wal").exists())
            self.assertFalse(Path(str(src) + "-shm").exists())
            self.assertEqual(_temps(root), [])
            got = sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                self.assertEqual(got.execute("SELECT body FROM notes").fetchall(), rows)
            finally:
                got.close()

    def test_readonly_source_dir_creates_no_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            src_dir.mkdir()
            src = src_dir / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            conn = sqlite3.connect(str(src))
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                src_rows = conn.execute("SELECT body FROM notes").fetchall()
            finally:
                conn.close()
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(src) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            src_hash = _sha(src)
            os.chmod(src_dir, 0o555)
            try:
                restore.restore_database(src, dest, cmdlines=[], lock_held=False)
                self.assertEqual(
                    sorted(path.name for path in src_dir.iterdir()),
                    ["backup.sqlite"],
                )
                self.assertEqual(_sha(src), src_hash)
                got = sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    self.assertEqual(
                        got.execute("SELECT body FROM notes").fetchall(),
                        src_rows,
                    )
                finally:
                    got.close()
            finally:
                os.chmod(src_dir, 0o755)

    def test_uncheckpointed_source_wal_row_is_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            dst_dir = root / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()
            src = src_dir / "backup.sqlite"
            dest = dst_dir / "dest.sqlite"
            _make_db(dest, "live-dest")
            held = sqlite3.connect(str(src))
            # The holder stays open on every Python version. A hardlink stage
            # would share this connection's inode and rewrite source -shm.
            try:
                held.execute("PRAGMA journal_mode=WAL")
                held.execute("PRAGMA wal_autocheckpoint=0")
                held.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
                held.execute("INSERT INTO notes (body) VALUES ('checkpointed')")
                held.commit()
                held.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                held.execute("INSERT INTO notes (body) VALUES ('wal-only')")
                held.commit()
                wal = Path(str(src) + "-wal")
                shm = Path(str(src) + "-shm")
                self.assertGreater(wal.stat().st_size, 0)
                self.assertTrue(shm.is_file())
                imm = sqlite3.connect(src.resolve().as_uri() + "?immutable=1", uri=True)
                try:
                    self.assertEqual(
                        imm.execute("SELECT body FROM notes").fetchall(),
                        [("checkpointed",)],
                    )
                finally:
                    imm.close()
                before = _family_hashes(src)
                self.assertIsNotNone(before["-shm"])
                names_before = sorted(path.name for path in src_dir.iterdir())
                seen: list[str] = []
                staged_inodes: list[tuple[int, int]] = []
                real_connect = sqlite3.connect

                def track(database, *args, **kwargs):
                    text = str(database)
                    seen.append(text)
                    opened = _path_from_uri(text)
                    if (
                        opened is not None
                        and opened.name == "db.sqlite"
                        and opened.is_file()
                    ):
                        info = opened.stat()
                        staged_inodes.append((info.st_dev, info.st_ino))
                    return real_connect(database, *args, **kwargs)

                with mock.patch("sqlite3.connect", side_effect=track):
                    restore.restore_database(src, dest, cmdlines=[], lock_held=False)
                src_uri = src.resolve().as_uri()
                self.assertTrue(any("mode=ro" in item and "db.sqlite" in item for item in seen))
                self.assertFalse(any("immutable=1" in item for item in seen))
                self.assertFalse(any(src_uri in item for item in seen))
                self.assertEqual(_family_hashes(src), before)
                self.assertEqual(_sha(src), before["main"])
                self.assertEqual(_sha(wal), before["-wal"])
                self.assertEqual(_sha(shm), before["-shm"])
                self.assertTrue(staged_inodes)
                self.assertNotEqual(
                    staged_inodes[0], (src.stat().st_dev, src.stat().st_ino)
                )
                self.assertEqual(sorted(path.name for path in src_dir.iterdir()), names_before)
                self.assertEqual(_temps(src_dir), [])
                self.assertEqual(_temps(dst_dir), [])
                got = sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    rows = got.execute(
                        "SELECT body FROM notes ORDER BY id"
                    ).fetchall()
                finally:
                    got.close()
                self.assertEqual(rows, [("checkpointed",), ("wal-only",)])
            finally:
                held.close()

    def test_source_stage_failure_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(dest, "live-dest")
            held = _open_uncheckpointed(src, "from-src")
            try:
                before = _sha(dest)
                src_before = _sha(src)
                wal_before = _sha(Path(str(src) + "-wal"))
                stderr = io.StringIO()
                with mock.patch.object(
                    restore, "_copy_bytes", side_effect=OSError("injected copy")
                ):
                    with mock.patch("sys.stderr", stderr):
                        rc = restore.main(["--src", str(src), "--dest", str(dest)])
                self.assertEqual(rc, 2, stderr.getvalue())
                self.assertIn("cannot stage source wal", stderr.getvalue())
                self.assertNotIn(str(root), stderr.getvalue())
                self.assertEqual(_sha(dest), before)
                self.assertEqual(_sha(src), src_before)
                self.assertEqual(_sha(Path(str(src) + "-wal")), wal_before)
                self.assertEqual(_temps(root), [])
            finally:
                held.close()

    def test_source_changed_during_stage_removes_stage(self):
        real_copy = restore._copy_stage

        def append_main(source, staged):
            real_copy(source, staged)
            with open(source, "ab") as handle:
                handle.write(b"x")

        def drop_wal(source, staged):
            real_copy(source, staged)
            Path(str(source) + "-wal").unlink()

        def replace_wal(source, staged):
            real_copy(source, staged)
            wal = Path(str(source) + "-wal")
            wal.unlink()
            wal.write_bytes(b"replacement-wal")

        cases = (
            ("append", append_main),
            ("wal-disappears", drop_wal),
            ("wal-appears", replace_wal),
        )
        for label, hook in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    src = root / "backup.sqlite"
                    dest = root / "dest.sqlite"
                    _make_db(dest, "live-dest")
                    held = _open_uncheckpointed(src, "from-src")
                    try:
                        before = _sha(dest)
                        ino = dest.stat().st_ino
                        stderr = io.StringIO()
                        with mock.patch.object(restore, "_copy_stage", side_effect=hook):
                            with mock.patch("sys.stderr", stderr):
                                rc = restore.main(
                                    ["--src", str(src), "--dest", str(dest)]
                                )
                        self.assertEqual(rc, 2, stderr.getvalue())
                        self.assertIn(
                            "refuse: source changed during stage", stderr.getvalue()
                        )
                        self.assertNotIn(str(root), stderr.getvalue())
                        self.assertEqual(_sha(dest), before)
                        self.assertEqual(dest.stat().st_ino, ino)
                        self.assertEqual(_temps(root), [])
                        self.assertEqual(list(root.glob(".att0-restore-*")), [])
                    finally:
                        held.close()

    def test_dest_path_is_never_passed_to_sqlite_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            held = _open_uncheckpointed(dest, "live-row")
            try:
                seen: list[str] = []
                real_connect = sqlite3.connect

                def track(database, *args, **kwargs):
                    seen.append(str(database))
                    return real_connect(database, *args, **kwargs)

                with mock.patch("sqlite3.connect", side_effect=track):
                    self._allow(src, dest)
                plain = str(dest.resolve())
                uri = dest.resolve().as_uri()
                for item in seen:
                    self.assertNotIn(plain, item)
                    self.assertNotIn(uri, item)
                got = sqlite3.connect(uri + "?mode=ro", uri=True)
                try:
                    self.assertEqual(
                        got.execute("SELECT body FROM notes").fetchall(),
                        [("from-src",)],
                    )
                finally:
                    got.close()
            finally:
                held.close()

    def test_fsync_and_replace_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            held = _open_uncheckpointed(dest, "live-row")
            try:
                self.assertTrue(Path(str(dest) + "-shm").is_file())
                events: list[str] = []
                real_file = restore._fsync_file
                real_dir = restore._fsync_dir
                real_replace = os.replace

                def fsync_file(path):
                    events.append("fsync-file")
                    return real_file(path)

                def fsync_dir(path):
                    events.append("fsync-dir")
                    return real_dir(path)

                def replace(src_path, dst_path):
                    src_name = os.path.basename(src_path)
                    dst_name = os.path.basename(dst_path)
                    if dst_name == dest.name and src_name.endswith(".sqlite"):
                        events.append("replace-main")
                    elif src_name.endswith("-wal"):
                        events.append("replace-wal")
                    elif src_name.endswith("-shm"):
                        events.append("replace-shm")
                    else:
                        events.append("replace-other")
                    return real_replace(src_path, dst_path)

                with mock.patch.object(restore, "_fsync_file", fsync_file):
                    with mock.patch.object(restore, "_fsync_dir", fsync_dir):
                        with mock.patch("os.replace", side_effect=replace):
                            self._allow(src, dest)
                self.assertEqual(
                    events,
                    [
                        "fsync-file",
                        "fsync-dir",
                        "replace-wal",
                        "replace-shm",
                        "replace-main",
                        "fsync-dir",
                    ],
                )
            finally:
                held.close()

    def test_fullfsync_on_darwin_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.sqlite"
            path.write_bytes(b"x")
            calls: list[int] = []

            class FakeFcntl:
                F_FULLFSYNC = 99

                @staticmethod
                def fcntl(fd, op, *args):
                    calls.append(op)
                    return 0

            with mock.patch.object(restore, "fcntl", FakeFcntl):
                with mock.patch.object(restore.sys, "platform", "darwin"):
                    restore._fsync_file(path)
                    restore._fsync_dir(Path(tmp))
            self.assertEqual(calls, [99, 99])
            calls.clear()
            with mock.patch.object(restore, "fcntl", FakeFcntl):
                with mock.patch.object(restore.sys, "platform", "linux"):
                    restore._fsync_file(path)
            self.assertEqual(calls, [])

    def test_reader_or_writer_refuse_keeps_dest_main_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            _make_db(src, "from-src")
            for label, begin_reader in (("writer", False), ("reader", True)):
                with self.subTest(label=label):
                    dest = root / ("%s.sqlite" % label)
                    holder = _open_uncheckpointed(dest, "live-row")
                    reader = None
                    try:
                        if begin_reader:
                            reader = sqlite3.connect(str(dest))
                            reader.execute("BEGIN")
                            reader.execute("SELECT body FROM notes").fetchall()
                        before = _sha(dest)
                        ino = dest.stat().st_ino
                        wal = Path(str(dest) + "-wal")
                        wal_hash = _sha(wal)

                        def boom(_conn):
                            raise restore.RestoreRefuse("refuse: integrity_check failed")

                        with mock.patch.object(restore, "_integrity_ok", boom):
                            with self.assertRaises(restore.RestoreRefuse):
                                restore.restore_database(
                                    src, dest, cmdlines=[], lock_held=False
                                )
                        self.assertEqual(_sha(dest), before)
                        self.assertEqual(dest.stat().st_ino, ino)
                        self.assertTrue(wal.is_file())
                        self.assertEqual(_sha(wal), wal_hash)
                        self.assertEqual(_temps(root), [])
                    finally:
                        if reader is not None:
                            reader.close()
                        holder.close()

    def test_fsync_replace_integrity_failures_keep_wal(self):
        real_replace = os.replace

        def integrity_boom(_conn):
            raise restore.RestoreRefuse("refuse: integrity_check failed")

        def fsync_boom(_path):
            raise OSError("injected fsync")

        def replace_boom(src_path, dst_path, *, dest_name):
            src_name = os.path.basename(src_path)
            dst_name = os.path.basename(dst_path)
            if dst_name == dest_name and src_name.endswith(".sqlite"):
                raise OSError("injected replace")
            return real_replace(src_path, dst_path)

        cases = (
            ("integrity", integrity_boom, None, 2),
            ("fsync", None, "fsync", 1),
            ("replace", None, "replace", 1),
        )
        for label, integrity, kind, expect_rc in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    src = root / "backup.sqlite"
                    dest = root / "mailroom.sqlite"
                    _make_db(src, "from-src")
                    holder = _open_uncheckpointed(dest, "live-row")
                    try:
                        before = _sha(dest)
                        ino = dest.stat().st_ino
                        wal = Path(str(dest) + "-wal")
                        wal_hash = _sha(wal)
                        wal_ino = wal.stat().st_ino
                        stderr = io.StringIO()
                        patches = []
                        if integrity is not None:
                            patches.append(
                                mock.patch.object(restore, "_integrity_ok", integrity)
                            )
                        if kind == "fsync":
                            patches.append(
                                mock.patch.object(restore, "_fsync_file", fsync_boom)
                            )
                        if kind == "replace":
                            patches.append(
                                mock.patch(
                                    "os.replace",
                                    side_effect=lambda src_path, dst_path: replace_boom(
                                        src_path, dst_path, dest_name=dest.name
                                    ),
                                )
                            )
                        with mock.patch("sys.stderr", stderr):
                            for patch in patches:
                                patch.start()
                            try:
                                rc = restore.main(
                                    [
                                        "--src",
                                        str(src),
                                        "--dest",
                                        str(dest),
                                        "--allow-mailroom-sqlite",
                                    ]
                                )
                            finally:
                                for patch in reversed(patches):
                                    patch.stop()
                        self.assertEqual(rc, expect_rc, stderr.getvalue())
                        self.assertNotIn(str(root), stderr.getvalue())
                        self.assertEqual(_sha(dest), before)
                        self.assertEqual(dest.stat().st_ino, ino)
                        self.assertTrue(wal.is_file())
                        self.assertGreater(wal.stat().st_size, 0)
                        self.assertEqual(_sha(wal), wal_hash)
                        self.assertEqual(wal.stat().st_ino, wal_ino)
                        self.assertEqual(_temps(root), [])
                    finally:
                        holder.close()

    def test_recreated_dest_wal_refuses_and_restores_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            holder = _open_uncheckpointed(dest, "live-row")
            try:
                before = _sha(dest)
                ino = dest.stat().st_ino
                wal = Path(str(dest) + "-wal")
                wal_hash = _sha(wal)
                wal_ino = wal.stat().st_ino
                real_park = restore._park_sidecars

                def park_and_recreate(target):
                    parked = real_park(target)
                    Path(str(target) + "-wal").write_bytes(b"recreated-nonempty-wal")
                    return parked

                stderr = io.StringIO()
                with mock.patch.object(restore, "_park_sidecars", park_and_recreate):
                    with mock.patch("sys.stderr", stderr):
                        rc = restore.main(
                            [
                                "--src",
                                str(src),
                                "--dest",
                                str(dest),
                                "--allow-mailroom-sqlite",
                            ]
                        )
                self.assertEqual(rc, 2, stderr.getvalue())
                self.assertIn("dest wal recreated", stderr.getvalue())
                self.assertNotIn(str(root), stderr.getvalue())
                self.assertEqual(_sha(dest), before)
                self.assertEqual(dest.stat().st_ino, ino)
                self.assertEqual(_sha(wal), wal_hash)
                self.assertEqual(wal.stat().st_ino, wal_ino)
                self.assertEqual(_temps(root), [])
            finally:
                holder.close()

    def test_free_space_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(dest, "live-dest")
            holder = _open_uncheckpointed(src, "from-src")
            try:
                before = _sha(dest)
                usage = shutil.disk_usage(root)
                margin = 64 * 1024 * 1024
                wal_size = Path(str(src) + "-wal").stat().st_size
                self.assertGreater(wal_size, 0)
                need = src.stat().st_size + wal_size + margin
                short_of_wal = type(usage)(usage.total, usage.used, src.stat().st_size + margin)
                zero = type(usage)(usage.total, usage.used, 0)
                exact = type(usage)(usage.total, usage.used, need)
                one_short = type(usage)(usage.total, usage.used, need - 1)
                stderr = io.StringIO()
                seen: list[Path] = []

                def fake_usage(path):
                    seen.append(Path(path))
                    return zero

                with mock.patch.object(restore.shutil, "disk_usage", side_effect=fake_usage):
                    with mock.patch(
                        "tempfile.mkstemp", side_effect=AssertionError("mkstemp")
                    ):
                        with mock.patch("sys.stderr", stderr):
                            rc = restore.main(["--src", str(src), "--dest", str(dest)])
                self.assertEqual(rc, 2, stderr.getvalue())
                self.assertIn("refuse: not enough free space", stderr.getvalue())
                self.assertNotIn(str(root), stderr.getvalue())
                self.assertEqual(seen, [dest.parent])
                self.assertEqual(_sha(dest), before)
                self.assertEqual(_temps(root), [])

                with mock.patch.object(
                    restore.shutil, "disk_usage", return_value=short_of_wal
                ):
                    with self.assertRaises(restore.RestoreRefuse) as ctx:
                        restore.restore_database(src, dest, cmdlines=[], lock_held=False)
                self.assertIn("not enough free space", str(ctx.exception))
                self.assertEqual(_sha(dest), before)

                with mock.patch.object(
                    restore.shutil, "disk_usage", return_value=one_short
                ):
                    with self.assertRaises(restore.RestoreRefuse):
                        restore.restore_database(src, dest, cmdlines=[], lock_held=False)
                self.assertEqual(_sha(dest), before)
                self.assertEqual(_temps(root), [])

                with mock.patch.object(restore.shutil, "disk_usage", return_value=exact):
                    restore.restore_database(src, dest, cmdlines=[], lock_held=False)
                self.assertEqual(_row_digest(dest), _row_digest(src))
            finally:
                holder.close()

    def test_preserves_dest_mode_and_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            os.chmod(dest, 0o640)
            before = dest.stat()
            restore.restore_database(src, dest, cmdlines=[], lock_held=False)
            got = dest.stat()
            self.assertEqual(stat.S_IMODE(got.st_mode), 0o640)
            self.assertEqual(got.st_uid, before.st_uid)
            self.assertEqual(got.st_gid, before.st_gid)
            self.assertNotEqual(got.st_ino, before.st_ino)
            self.assertEqual(_row_digest(dest), _row_digest(src))

    def test_permission_error_preserving_mode_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            before = _sha(dest)
            ino = dest.stat().st_ino
            stderr = io.StringIO()

            def boom(*_args, **_kwargs):
                raise PermissionError("injected chown")

            with mock.patch("os.chown", side_effect=boom):
                with mock.patch("sys.stderr", stderr):
                    rc = restore.main(["--src", str(src), "--dest", str(dest)])
            self.assertEqual(rc, 2, stderr.getvalue())
            self.assertIn("cannot preserve dest mode", stderr.getvalue())
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertEqual(_sha(dest), before)
            self.assertEqual(dest.stat().st_ino, ino)
            self.assertEqual(_temps(root), [])

    def test_post_restore_mode_ro_open_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            restore.restore_database(src, dest, cmdlines=[], lock_held=False)
            self.assertEqual(_format_versions(dest), (1, 1))
            self.assertFalse(Path(str(dest) + "-wal").exists())
            self.assertFalse(Path(str(dest) + "-shm").exists())
            conn = sqlite3.connect(dest.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                self.assertEqual(
                    conn.execute("SELECT body FROM notes").fetchall(),
                    [("from-src",)],
                )
                self.assertEqual(
                    str(conn.execute("PRAGMA integrity_check").fetchone()[0]).lower(),
                    "ok",
                )
            finally:
                conn.close()

    def test_doc_records_reader_precondition_aside_and_no_recipe(self):
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("launchctl bootout gui/<uid>/com.mailroom.ask-mail-serve", doc)
        self.assertIn(
            "lsof /var/lib/mailroom/mailroom.sqlite "
            "/var/lib/mailroom/mailroom.sqlite-wal "
            "/var/lib/mailroom/mailroom.sqlite-shm",
            doc,
        )
        self.assertIn("must print no process", doc)
        self.assertIn("/usr/sbin/lsof", doc)
        self.assertIn("refuse: dest file is open", doc)
        self.assertIn("exit 2", doc)
        self.assertIn(".att0-restore-aside-<hex>-<name>-wal", doc)
        self.assertIn("rename that `-wal` aside back", doc)
        self.assertIn("rerun this helper", doc)
        self.assertIn("## No hand-rolled swap", doc)
        self.assertIn("no fsync step", doc)
        self.assertIn("scripts/attachments/att0_restore.py", doc)
        self.assertNotIn("$HOME", doc)
        self.assertNotIn("/Users/", doc)
        self.assertNotIn("/home/", doc)

    def test_lsof_holder_exits_2_and_self_pid_or_missing_binary_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "dest.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            before = _sha(dest)
            ino = dest.stat().st_ino
            seen: list[list[str]] = []

            def foreign(argv):
                seen.append(list(argv))
                return "424242\n"

            stderr = io.StringIO()
            with mock.patch.object(restore, "_query_lsof", side_effect=foreign):
                with mock.patch("sys.stderr", stderr):
                    rc = restore.main(["--src", str(src), "--dest", str(dest)])
            self.assertEqual(rc, 2, stderr.getvalue())
            self.assertIn("refuse: dest file is open", stderr.getvalue())
            self.assertNotIn("warn", stderr.getvalue().lower())
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertEqual(seen[0][0], "/usr/sbin/lsof")
            self.assertEqual(seen[0][1:4], ["-n", "-P", "-t"])
            self.assertTrue(any(arg.endswith("dest.sqlite") for arg in seen[0]))
            self.assertTrue(any(arg.endswith("dest.sqlite-wal") for arg in seen[0]))
            self.assertTrue(any(arg.endswith("dest.sqlite-shm") for arg in seen[0]))
            self.assertEqual(_sha(dest), before)
            self.assertEqual(dest.stat().st_ino, ino)
            self.assertEqual(_temps(root), [])

            def only_self(_argv):
                return "%d\n" % os.getpid()

            with mock.patch.object(restore, "_query_lsof", side_effect=only_self):
                restore.restore_database(src, dest, cmdlines=[], lock_held=False)
            self.assertEqual(_row_digest(dest), _row_digest(src))
            self.assertEqual(_temps(root), [])

            for suffix in ("", "-wal", "-shm", "-journal"):
                leftover = Path(str(dest) + suffix) if suffix else dest
                if leftover.exists():
                    leftover.unlink()
            _make_db(dest, "live-again")
            again = _sha(dest)
            calls = {"n": 0}

            def second_call_holds(argv):
                calls["n"] += 1
                seen.append(list(argv))
                if calls["n"] == 1:
                    return ""
                return "424242\n"

            stderr = io.StringIO()
            with mock.patch.object(restore, "_query_lsof", side_effect=second_call_holds):
                with mock.patch("sys.stderr", stderr):
                    rc = restore.main(["--src", str(src), "--dest", str(dest)])
            self.assertEqual(rc, 2, stderr.getvalue())
            self.assertIn("refuse: dest file is open", stderr.getvalue())
            self.assertGreaterEqual(calls["n"], 2)
            self.assertEqual(_sha(dest), again)
            self.assertEqual(_temps(root), [])

            def missing(_argv):
                raise OSError("injected missing lsof")

            with mock.patch.object(restore, "_query_lsof", side_effect=missing):
                restore.restore_database(src, dest, cmdlines=[], lock_held=False)
            self.assertEqual(_row_digest(dest), _row_digest(src))
            self.assertEqual(_temps(root), [])

    def test_unpark_exception_still_removes_temp_and_keeps_original(self):
        real_replace = os.replace

        def replace_boom(src_path, dst_path, *, dest_name):
            src_name = os.path.basename(src_path)
            dst_name = os.path.basename(dst_path)
            if dst_name == dest_name and src_name.endswith(".sqlite"):
                raise OSError("injected replace")
            return real_replace(src_path, dst_path)

        def unpark_boom(_parked):
            raise restore.RestoreRefuse("refuse: injected unpark")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backup.sqlite"
            dest = root / "mailroom.sqlite"
            _make_db(src, "from-src")
            _make_db(dest, "live-dest")
            Path(str(dest) + "-wal").write_bytes(b"parked-wal-bytes")
            before = _sha(dest)
            ino = dest.stat().st_ino
            stderr = io.StringIO()
            with mock.patch(
                "os.replace",
                side_effect=lambda src_path, dst_path: replace_boom(
                    src_path, dst_path, dest_name=dest.name
                ),
            ):
                with mock.patch.object(restore, "_unpark", side_effect=unpark_boom):
                    with mock.patch("sys.stderr", stderr):
                        rc = restore.main(
                            [
                                "--src",
                                str(src),
                                "--dest",
                                str(dest),
                                "--allow-mailroom-sqlite",
                            ]
                        )
            self.assertEqual(rc, 1, stderr.getvalue())
            self.assertIn("restore failed (OSError)", stderr.getvalue())
            self.assertNotIn("injected unpark", stderr.getvalue())
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertEqual(_sha(dest), before)
            self.assertEqual(dest.stat().st_ino, ino)
            temps = [
                path
                for path in root.iterdir()
                if path.name.startswith(".att0-restore-") and "aside-" not in path.name
            ]
            self.assertEqual(temps, [])
            asides = [
                path
                for path in root.iterdir()
                if ".att0-restore-aside-" in path.name
            ]
            self.assertTrue(asides)

    def test_free_space_checks_source_dir_for_staged_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            dest_dir = root / "dest"
            src_dir.mkdir()
            dest_dir.mkdir()
            src = src_dir / "backup.sqlite"
            dest = dest_dir / "dest.sqlite"
            holder = _open_uncheckpointed(src, "from-src")
            try:
                _make_db(dest, "live-dest")
                before = _sha(dest)
                wal_size = Path(str(src) + "-wal").stat().st_size
                self.assertGreater(wal_size, 0)
                margin = 64 * 1024 * 1024
                src_need = src.stat().st_size + wal_size + margin
                dest_need = src.stat().st_size + wal_size + margin
                self.assertEqual(src_need, dest_need)
                self.assertGreater(src_need, wal_size + margin)
                usage = shutil.disk_usage(root)
                plenty = type(usage)(usage.total, usage.used, dest_need + src_need)
                one_short = type(usage)(usage.total, usage.used, src_need - 1)
                exact_src = type(usage)(usage.total, usage.used, src_need)
                seen: list[Path] = []

                def short_source(path):
                    seen.append(Path(path))
                    if Path(path) == src_dir:
                        return one_short
                    return plenty

                with mock.patch.object(restore.shutil, "disk_usage", side_effect=short_source):
                    with mock.patch(
                        "tempfile.mkstemp", side_effect=AssertionError("mkstemp")
                    ):
                        with self.assertRaises(restore.RestoreRefuse) as ctx:
                            restore.restore_database(
                                src, dest, cmdlines=[], lock_held=False
                            )
                self.assertIn("not enough free space", str(ctx.exception))
                self.assertEqual(seen, [dest_dir, src_dir])
                self.assertEqual(_sha(dest), before)
                self.assertEqual(_temps(root), [])
                self.assertEqual(list(src_dir.glob(".att0-restore-*")), [])

                def enough(path):
                    if Path(path) == src_dir:
                        return exact_src
                    return plenty

                with mock.patch.object(restore.shutil, "disk_usage", side_effect=enough):
                    restore.restore_database(src, dest, cmdlines=[], lock_held=False)
                self.assertEqual(_row_digest(dest), _row_digest(src))
                self.assertEqual(_temps(root), [])
            finally:
                holder.close()

            empty_src = src_dir / "checkpointed.sqlite"
            empty_dest = dest_dir / "other.sqlite"
            _make_db(empty_src, "from-src")
            _make_db(empty_dest, "live-dest")
            for suffix in ("-wal", "-shm", "-journal"):
                side = Path(str(empty_src) + suffix)
                if side.exists():
                    side.unlink()
            self.assertFalse(Path(str(empty_src) + "-wal").is_file())
            zero = type(usage)(usage.total, usage.used, 0)
            plenty_empty = type(usage)(
                usage.total,
                usage.used,
                empty_src.stat().st_size + margin,
            )
            checked: list[Path] = []

            def dest_only(path):
                checked.append(Path(path))
                if Path(path) == src_dir:
                    return zero
                return plenty_empty

            with mock.patch.object(restore.shutil, "disk_usage", side_effect=dest_only):
                restore.restore_database(
                    empty_src, empty_dest, cmdlines=[], lock_held=False
                )
            self.assertEqual(checked, [dest_dir])
            self.assertEqual(_row_digest(empty_dest), _row_digest(empty_src))


if __name__ == "__main__":
    unittest.main()
