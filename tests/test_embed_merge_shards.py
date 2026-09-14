#!/usr/bin/env python3
"""KOO-6 merge_shards CLI contract. Temp DB / --help only — no live SoR."""

from __future__ import annotations

import io
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embed_lib as el  # noqa: E402
import embed_merge_shards as ems  # noqa: E402

DIMS = 4
MODEL = "qwen3-embedding-8b"
VERSION = "v1"
BLOB = struct.pack("%sf" % DIMS, *([0.0] * (DIMS - 1) + [1.0]))


def _embed_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE embedding_meta (
          message_id TEXT NOT NULL,
          model TEXT NOT NULL,
          model_version TEXT NOT NULL DEFAULT 'v1',
          created_at TEXT NOT NULL,
          text_hash TEXT NOT NULL,
          char_count INTEGER NOT NULL DEFAULT 0,
          dims INTEGER NOT NULL DEFAULT 4,
          PRIMARY KEY (message_id, model, model_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE message_embeddings (
          message_id TEXT PRIMARY KEY,
          embedding float[4]
        )
        """
    )
    conn.commit()


def _insert_embed(conn: sqlite3.Connection, mid: str, *, text_hash: str = "aa") -> None:
    conn.execute(
        "INSERT INTO message_embeddings(message_id, embedding) VALUES (?, ?)",
        (mid, BLOB),
    )
    conn.execute(
        """
        INSERT INTO embedding_meta(
          message_id, model, model_version, created_at,
          text_hash, char_count, dims
        ) VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', ?, 10, ?)
        """,
        (mid, MODEL, VERSION, text_hash, DIMS),
    )
    conn.commit()


def _temp_pair():
    tmp = tempfile.TemporaryDirectory()
    primary = Path(tmp.name) / "primary.sqlite"
    secondary = Path(tmp.name) / "secondary.sqlite"
    pri = sqlite3.connect(str(primary))
    pri.row_factory = sqlite3.Row
    sec = sqlite3.connect(str(secondary))
    sec.row_factory = sqlite3.Row
    _embed_tables(pri)
    _embed_tables(sec)
    return tmp, primary, secondary, pri, sec


class HelpContractTests(unittest.TestCase):
    def test_help_subprocess_lists_supported_path(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "embed_merge_shards.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout
        self.assertIn("--primary", text)
        self.assertIn("--secondary", text)
        self.assertIn("--dry-run", text)
        self.assertIn("missing-only", text.lower())
        self.assertIn("HARD DECK", text)
        self.assertIn("EXIT 0", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("@me.com", text)
        self.assertNotIn("@icloud.com", text)

    def test_help_does_not_permit_same_file_writers(self):
        help_text = " ".join(ems.build_parser().format_help().split())
        self.assertIn("Never two embed writers on one sqlite", help_text)
        self.assertIn("different file", help_text)


class PathGuardTests(unittest.TestCase):
    def test_refuses_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "one.sqlite"
            db.write_bytes(b"")
            with self.assertRaises(el.EmbedError) as ctx:
                ems.resolve_merge_paths(str(db), str(db))
            self.assertIn("HARD DECK", str(ctx.exception))
            self.assertIn("different files", str(ctx.exception))

    def test_refuses_missing_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            sec = Path(tmp) / "secondary.sqlite"
            sec.write_bytes(b"")
            with self.assertRaises(el.EmbedError) as ctx:
                ems.resolve_merge_paths(str(Path(tmp) / "missing.sqlite"), str(sec))
            self.assertIn("primary DB not found", str(ctx.exception))

    def test_main_same_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "one.sqlite"
            db.write_bytes(b"")
            err = io.StringIO()
            with redirect_stderr(err):
                rc = ems.main(["--primary", str(db), "--secondary", str(db)])
            self.assertEqual(rc, 2)
            self.assertIn("HARD DECK", err.getvalue())


class MergeShardsContractTests(unittest.TestCase):
    def test_missing_only_inserts_then_skips(self):
        tmp, _primary, _secondary, pri, sec = _temp_pair()
        try:
            _insert_embed(pri, "already")
            _insert_embed(sec, "already", text_hash="other")
            _insert_embed(sec, "new-from-sec")
            first = el.merge_shards(pri, sec, dry_run=False)
            self.assertEqual(first["examined"], 2)
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(first["skipped_already_present"], 1)
            self.assertEqual(first["errors"], 0)
            kept = pri.execute(
                "SELECT text_hash FROM embedding_meta WHERE message_id='already'"
            ).fetchone()
            self.assertEqual(kept["text_hash"], "aa")
            copied = pri.execute(
                "SELECT 1 FROM embedding_meta WHERE message_id='new-from-sec'"
            ).fetchone()
            self.assertIsNotNone(copied)
            second = el.merge_shards(pri, sec, dry_run=False)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["skipped_already_present"], 2)
        finally:
            pri.close()
            sec.close()
            tmp.cleanup()

    def test_cli_dry_run_does_not_write(self):
        tmp, primary, secondary, pri, sec = _temp_pair()
        try:
            _insert_embed(sec, "only-sec")
            pri.close()
            sec.close()
            err = io.StringIO()
            with redirect_stderr(err):
                rc = ems.main(
                    [
                        "--primary",
                        str(primary),
                        "--secondary",
                        str(secondary),
                        "--dry-run",
                    ]
                )
            self.assertEqual(rc, 0, err.getvalue())
            log = err.getvalue()
            self.assertIn("dry-run", log)
            self.assertIn("inserted=1", log)
            check = sqlite3.connect(str(primary))
            try:
                n = check.execute("SELECT COUNT(*) FROM embedding_meta").fetchone()[0]
                self.assertEqual(n, 0)
            finally:
                check.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
