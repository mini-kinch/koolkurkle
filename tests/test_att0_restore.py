#!/usr/bin/env python3
"""ATT-0 restore helper. Synthetic temp databases only. No network."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_live_nonempty_wal_checkpoints_and_does_not_replay(self):
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
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("ask_mail", source)
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn(
            "scripts/with_writer_lock.py --purpose att0-restore -- "
            "python3 scripts/attachments/att0_restore.py "
            "--src /var/backups/mailroom-backup.sqlite "
            "--dest /var/lib/mailroom/mailroom.sqlite "
            "--allow-mailroom-sqlite",
            doc,
        )
        self.assertIn('sqlite3 /var/backups/mailroom-backup.sqlite ".backup ', doc)
        self.assertIn(
            "The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only).",
            doc,
        )
        self.assertIn("PRAGMA journal_mode=DELETE", doc)
        self.assertIn("bytes 18 and 19", doc)
        self.assertNotIn("$HOME", doc)
        self.assertNotIn("/Users/", doc)
        self.assertNotIn("/home/", doc)

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


if __name__ == "__main__":
    unittest.main()
