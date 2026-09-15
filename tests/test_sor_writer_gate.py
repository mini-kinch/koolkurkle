#!/usr/bin/env python3
"""KOO-85 rem-aware SoR writer gate + look-ahead contract.

No live SoR. No IMAP. No rem start. No MBP. ZERO PII.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import classify  # noqa: E402
import embed_sidecar_apply as side  # noqa: E402
import imap_fetch_bodies_fts  # noqa: E402
import imap_newmail  # noqa: E402
import imap_tombstone  # noqa: E402
import mailroom_copy_db as copy_db  # noqa: E402
import notify_bills  # noqa: E402
import post_rem_embed_batch as prb  # noqa: E402
import sor_writer_gate as gate  # noqa: E402
import with_writer_lock as wwl  # noqa: E402

MAILROOM = ROOT / "docs" / "MAILROOM.md"
OPS = ROOT / "docs" / "ops-terminal.md"
EMBED = ROOT / "docs" / "embed-backfill.md"
README = ROOT / "README.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
ASK_MAIL = ROOT / "scripts" / "ask_mail.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")

CHILD_MODULES = (
    imap_newmail,
    imap_tombstone,
    imap_fetch_bodies_fts,
    classify,
    notify_bills,
)

WIRED_NEEDLES = (
    (ROOT / "scripts" / "mailroom_copy_db.py", "refuse_intended_sor_writer"),
    (ROOT / "scripts" / "mailroom_daily.py", "refuse_intended_sor_writer"),
    (ROOT / "scripts" / "embed_backfill.py", "refuse_if_sor_writer_conflict"),
    (ROOT / "scripts" / "embed_merge_shards.py", "refuse_if_sor_writer_conflict"),
    (ROOT / "scripts" / "embed_sidecar_apply.py", "refuse_if_sor_writer_conflict"),
    (ROOT / "scripts" / "migrate_pr1_schema.py", "refuse_if_sor_writer_conflict"),
    (ROOT / "scripts" / "messages_ids.py", "refuse_if_sor_writer_conflict"),
    (ROOT / "scripts" / "post_rem_embed_batch.py", "refuse_if_sor_writer_conflict"),
)

DOC_PATHS = (MAILROOM, OPS, EMBED)


class RemAndLockRefuseTests(unittest.TestCase):
    def test_rem_process_refuses_live_sor(self):
        db = Path("/tmp/mailroom.sqlite")
        cmdlines = (
            (
                111,
                "/opt/homebrew/bin/python3 embed_backfill.py --reembed-legacy "
                "--quote-strip --db %s" % db,
            ),
        )
        with self.assertRaises(gate.SorWriterRefuse) as ctx:
            gate.refuse_if_sor_writer_conflict(
                db, self_pid=999, cmdlines=cmdlines, lock_held=False
            )
        msg = str(ctx.exception)
        self.assertIn(gate.CONFLICT_TOKEN, msg)
        self.assertIn("MAILROOM_DB=copy", msg)
        self.assertIn("111", msg)

    def test_rem_legacy_needle_refuses_live_sor(self):
        db = Path("/tmp/mailroom.sqlite")
        cmdlines = ((42, "python3 rem-legacy --db %s" % db),)
        with self.assertRaises(gate.SorWriterRefuse) as ctx:
            gate.refuse_if_sor_writer_conflict(
                db, self_pid=1, cmdlines=cmdlines, lock_held=False
            )
        self.assertIn(gate.CONFLICT_TOKEN, str(ctx.exception))

    def test_embed_backfill_on_sor_counts_as_rem(self):
        db = Path("/tmp/mailroom.sqlite")
        cmdlines = ((7, "embed_backfill.py --db /var/mailroom.sqlite"),)
        with self.assertRaises(gate.SorWriterRefuse):
            gate.refuse_if_sor_writer_conflict(
                db, self_pid=1, cmdlines=cmdlines, lock_held=False
            )

    def test_lock_held_refuses_live_sor(self):
        db = Path("/tmp/mailroom.sqlite")
        with self.assertRaises(gate.SorWriterRefuse) as ctx:
            gate.refuse_if_sor_writer_conflict(
                db, cmdlines=(), lock_held=True
            )
        msg = str(ctx.exception)
        self.assertIn(gate.CONFLICT_TOKEN, msg)
        self.assertIn("writer lock held", msg)
        self.assertIn("MAILROOM_DB=copy", msg)

    def test_real_flock_refuses_live_sor(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                with self.assertRaises(gate.SorWriterRefuse) as ctx:
                    gate.refuse_if_sor_writer_conflict(
                        db, cmdlines=(), lock_path=lock
                    )
                self.assertIn(gate.CONFLICT_TOKEN, str(ctx.exception))
                self.assertIn("purpose=rem-legacy", str(ctx.exception))
            finally:
                wwl.release_writer_lock(held)

    def test_copy_db_allowed_while_rem_and_lock(self):
        for name in (
            "mailroom-copy.sqlite",
            "mailroom-daily-copy.sqlite",
            "other-copy.sqlite",
            "shard.sqlite",
        ):
            gate.refuse_if_sor_writer_conflict(
                Path("/tmp") / name,
                cmdlines=((9, "embed_backfill.py --reembed-legacy --db /x/mailroom.sqlite"),),
                lock_held=True,
            )

    def test_live_sor_allowed_when_no_rem_no_lock(self):
        gate.refuse_if_sor_writer_conflict(
            Path("/tmp/mailroom.sqlite"),
            cmdlines=(),
            lock_held=False,
        )

    def test_lock_purpose_needles_match_rem(self):
        self.assertTrue(gate.lock_purpose_is_rem("rem-legacy"))
        self.assertTrue(gate.lock_purpose_is_rem("embed-rem"))
        self.assertFalse(gate.lock_purpose_is_rem("embed_batch"))

    def test_self_pid_is_ignored(self):
        db = Path("/tmp/mailroom.sqlite")
        cmdlines = ((42, "embed_backfill.py --reembed-legacy --db %s" % db),)
        gate.refuse_if_sor_writer_conflict(
            db, self_pid=42, cmdlines=cmdlines, lock_held=False
        )

    def test_copy_from_live_sor_refuses_while_rem(self):
        with self.assertRaises(gate.SorWriterRefuse) as ctx:
            gate.refuse_copy_from_live_sor(
                Path("/tmp/mailroom.sqlite"),
                cmdlines=((3, "embed-rem --db /tmp/mailroom.sqlite"),),
                lock_held=False,
            )
        self.assertIn(gate.CONFLICT_TOKEN, str(ctx.exception))


class WiredWriterTests(unittest.TestCase):
    def test_child_main_refuses_sor_when_rem_present(self):
        rem = ((88, "embed_backfill.py --reembed-legacy --db /tmp/mailroom.sqlite"),)
        for mod in CHILD_MODULES:
            rc = copy_db.child_main(
                ["--db", "/tmp/mailroom.sqlite"],
                name=mod.__name__,
                cmdlines=rem,
                lock_held=False,
            )
            self.assertEqual(rc, gate.CONFLICT_EXIT, msg=mod.__name__)

    def test_child_main_allows_copy_when_rem_present(self):
        rem = ((88, "embed_backfill.py --reembed-legacy --db /tmp/mailroom.sqlite"),)
        rc = copy_db.child_main(
            ["--db", "/tmp/mailroom-copy.sqlite"],
            name="imap_newmail",
            cmdlines=rem,
            lock_held=True,
        )
        self.assertEqual(rc, 0)

    def test_imap_newmail_cli_lock_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / "imap_newmail.py"), "--db", str(db)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**os.environ, "MAILROOM_WRITE_LOCK": str(lock)},
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(gate.CONFLICT_TOKEN, proc.stderr)
        self.assertIn("MAILROOM_DB=copy", proc.stderr)
        self.assertIn("db_mode=refused", proc.stderr)

    def test_imap_newmail_cli_copy_ok_with_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom-copy.sqlite"
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / "imap_newmail.py"), "--db", str(db)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**os.environ, "MAILROOM_WRITE_LOCK": str(lock)},
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("db_mode=copy", proc.stderr)

    def test_gate_cli_refuses_sor_when_lock_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            held = wwl.acquire_writer_lock(lock, "embed-rem")
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "sor_writer_gate.py"),
                        "--db",
                        str(db),
                        "--lock-file",
                        str(lock),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(proc.returncode, gate.CONFLICT_EXIT)
        self.assertIn(gate.CONFLICT_TOKEN, proc.stderr)

    def test_gate_cli_allows_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom-copy.sqlite"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "sor_writer_gate.py"),
                    "--db",
                    str(db),
                    "--lock-file",
                    str(lock),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("sor_writer_gate=allow", proc.stdout)

    def test_merge_cli_refuses_sor_primary_when_lock_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "mailroom.sqlite"
            secondary = Path(tmp) / "shard.sqlite"
            primary.write_bytes(b"")
            secondary.write_bytes(b"")
            lock = Path(tmp) / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "embed_merge_shards.py"),
                        "--primary",
                        str(primary),
                        "--secondary",
                        str(secondary),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**os.environ, "MAILROOM_WRITE_LOCK": str(lock)},
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(gate.CONFLICT_TOKEN, proc.stderr)
        self.assertIn("MAILROOM_DB=copy", proc.stderr)

    def test_sidecar_still_refuses_live_sor_basename(self):
        with self.assertRaises(side.SidecarApplyRefuse):
            side.refuse_live_rem_sor(Path("/tmp/mailroom.sqlite"))

    def test_post_rem_lever_refuses_live_sor_while_rem(self):
        with self.assertRaises(gate.SorWriterRefuse):
            prb.refuse_post_rem_against_live_rem_sor(
                Path("/tmp/mailroom.sqlite"),
                cmdlines=((1, "rem-legacy"),),
                lock_held=False,
            )

    def test_scripts_wire_the_gate(self):
        for path, needle in WIRED_NEEDLES:
            text = path.read_text(encoding="utf-8")
            self.assertIn(needle, text, msg=path.name)
        for name in (
            "imap_newmail.py",
            "imap_tombstone.py",
            "imap_fetch_bodies_fts.py",
            "classify.py",
            "notify_bills.py",
        ):
            src = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("child_main", src, msg=name)

    def test_ask_mail_not_replaced_with_mcp_stub(self):
        text = ASK_MAIL.read_text(encoding="utf-8")
        self.assertNotIn("MCP stub", text)
        self.assertIn("def main", text)


class LookAheadDocTests(unittest.TestCase):
    def test_docs_cite_refuse_allow_and_lookahead(self):
        for path in DOC_PATHS:
            raw = path.read_text(encoding="utf-8")
            text = " ".join(raw.split())
            self.assertIn(gate.LOOKAHEAD_NEEDLE, text, msg=path.name)
            self.assertIn("look-ahead calendar jobs", raw.lower(), msg=path.name)
            self.assertIn("classic MBP SoR 8pm", raw, msg=path.name)
            self.assertIn("same-sqlite dual-writer HARD DECK", raw, msg=path.name)
            self.assertIn("MAILROOM_DB=copy", raw, msg=path.name)
            self.assertIn("CONFLICT", raw, msg=path.name)
            self.assertIn("Mini/copy until rem EXIT 0", raw, msg=path.name)
            for item in gate.REFUSE_WHILE_REM_ON_LIVE_SOR:
                self.assertIn(item, raw, msg="%s missing %s" % (path.name, item))
            for item in gate.ALLOW_WHILE_REM:
                self.assertIn(item, raw, msg="%s missing allow %s" % (path.name, item))
            hay = raw.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

    def test_readme_and_daily_name_lookahead(self):
        for path in (README, DAILY):
            raw = path.read_text(encoding="utf-8")
            self.assertIn("look-ahead", raw.lower(), msg=path.name)
            self.assertIn("before the clock", raw, msg=path.name)
            self.assertIn("classic MBP SoR 8pm", raw, msg=path.name)
            hay = raw.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

    def test_gate_module_has_zero_pii(self):
        raw = (SCRIPTS / "sor_writer_gate.py").read_text(encoding="utf-8")
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertIn("does not start rem-legacy", raw)
        self.assertIn("does not restart rem", raw)


if __name__ == "__main__":
    unittest.main()
