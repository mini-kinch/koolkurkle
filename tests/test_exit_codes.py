#!/usr/bin/env python3
"""Pin writer-lock, gate, probe, and ops/ATT-0/phase-P exit codes.

Stdlib only. No network, no Keychain, no live SoR. Statuses and stderr
text are observed, not changed.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOC = ROOT / "docs" / "exit-codes.md"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mailroom_daily as daily  # noqa: E402
import sqlite_pragmas  # noqa: E402
import with_writer_lock as wwl  # noqa: E402

CITE_RE = re.compile(r"`(scripts/(?:attachments/)?[A-Za-z0-9_]+\.py):(\d+)`")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Citation -> substring that must still be on that source line.
NEEDLES = {
    ("scripts/with_writer_lock.py", 184): "writer lock token generation failed",
    ("scripts/with_writer_lock.py", 209): "purpose is required",
    ("scripts/with_writer_lock.py", 222): "no steal",
    ("scripts/with_writer_lock.py", 225): "writer lock held:",
    ("scripts/with_writer_lock.py", 246): "search resume +26 drop failed; child not started:",
    ("scripts/with_writer_lock.py", 264): "rc 1",
    ("scripts/with_writer_lock.py", 291): "action-required open",
    ("scripts/with_writer_lock.py", 295): "command is required after --",
    ("scripts/with_writer_lock.py", 308): "writer lock token missing",
    ("scripts/with_writer_lock.py", 314): "return int(completed.returncode)",
    ("scripts/with_writer_lock.py", 362): "parse_args",
    ("scripts/with_writer_lock.py", 378): "WriterLockError",
    ("scripts/with_writer_lock.py", 380): "return 2",
    ("scripts/with_writer_lock.py", 384): "SystemExit",
    ("scripts/sor_writer_gate.py", 47): "CONFLICT_EXIT = 2",
    ("scripts/sor_writer_gate.py", 547): "writer lock absent",
    ("scripts/sor_writer_gate.py", 557): "writer lock held:",
    ("scripts/sor_writer_gate.py", 562): "writer lock free",
    ("scripts/sor_writer_gate.py", 646): "lock not held",
    ("scripts/sor_writer_gate.py", 650): "writer lock held by wrapper",
    ("scripts/sor_writer_gate.py", 785): "parse_args",
    ("scripts/sor_writer_gate.py", 791): "return CONFLICT_EXIT",
    ("scripts/sor_writer_gate.py", 793): "return 0",
    ("scripts/pr5_preflight.py", 245): "parse_args",
    ("scripts/pr5_preflight.py", 250): "return 2",
    ("scripts/pr5_preflight.py", 266): "return 2",
    ("scripts/pr5_preflight.py", 289): "return 2",
    ("scripts/pr5_preflight.py", 292): "return 2",
    ("scripts/pr5_preflight.py", 294): "writer flock held",
    ("scripts/pr5_preflight.py", 295): "return 2",
    ("scripts/pr5_preflight.py", 296): "return 0",
    ("scripts/search_resume_watchdog.py", 412): "lsof exits 1",
    ("scripts/search_resume_watchdog.py", 426): 'return "clear"',
    ("scripts/search_resume_watchdog.py", 569): "return 0",
    ("scripts/search_resume_watchdog.py", 598): "return 2",
    ("scripts/search_resume_watchdog.py", 611): "return 0",
    ("scripts/search_resume_watchdog.py", 618): "return 2",
    ("scripts/search_resume_watchdog.py", 623): "return 1",
    ("scripts/search_resume_watchdog.py", 626): "return 1",
    ("scripts/search_resume_watchdog.py", 627): "return 0",
    ("scripts/search_resume_watchdog.py", 632): "return 0",
    ("scripts/search_resume_watchdog.py", 640): "return 1",
    ("scripts/search_resume_watchdog.py", 643): "return 1",
    ("scripts/search_resume_watchdog.py", 645): "return 0",
    ("scripts/search_resume_watchdog.py", 653): "return 2",
    ("scripts/search_resume_watchdog.py", 670): "return 2",
    ("scripts/attachments/meta_fill.py", 198): "return 1",
    ("scripts/attachments/meta_fill.py", 205): "rc=%s",
    ("scripts/attachments/meta_fill.py", 1275): "return 2",
    ("scripts/attachments/meta_fill.py", 1276): "parse_args",
    ("scripts/attachments/meta_fill.py", 1279): "return 2",
    ("scripts/attachments/meta_fill.py", 1286): "return 2",
    ("scripts/attachments/meta_fill.py", 1289): "return 2",
    ("scripts/attachments/meta_fill.py", 1292): "return 2",
    ("scripts/attachments/meta_fill.py", 1321): "return 2",
    ("scripts/attachments/meta_fill.py", 1324): "return 2",
    ("scripts/attachments/meta_fill.py", 1326): "return 0",
    ("scripts/attachments/migrate_att0_schema.py", 428): "return 2",
    ("scripts/attachments/migrate_att0_schema.py", 429): "parse_args",
    ("scripts/attachments/migrate_att0_schema.py", 436): "return 2",
    ("scripts/attachments/migrate_att0_schema.py", 439): "return 2",
    ("scripts/attachments/migrate_att0_schema.py", 448): "return 2",
    ("scripts/attachments/migrate_att0_schema.py", 451): "return 2",
    ("scripts/attachments/migrate_att0_schema.py", 453): "return 0",
    ("scripts/embed_backfill.py", 434): "return 2",
    ("scripts/embed_backfill.py", 435): "parse_args",
    ("scripts/embed_backfill.py", 480): "return 2",
    ("scripts/embed_backfill.py", 483): "return 2",
    ("scripts/embed_backfill.py", 486): "return 0",
    ("scripts/embed_sidecar_apply.py", 295): "parse_args",
    ("scripts/embed_sidecar_apply.py", 310): "return 2",
    ("scripts/embed_sidecar_apply.py", 312): "return 0",
    ("scripts/embed_merge_shards.py", 141): "parse_args",
    ("scripts/embed_merge_shards.py", 162): "return 2",
    ("scripts/embed_merge_shards.py", 165): "return 2",
    ("scripts/embed_merge_shards.py", 166): "return 0",
    ("scripts/migrate_pr1_schema.py", 397): "parse_args",
    ("scripts/migrate_pr1_schema.py", 404): "return 0",
    ("scripts/migrate_pr1_schema.py", 415): "return _run_locked",
    ("scripts/migrate_pr1_schema.py", 416): "return _go()",
    ("scripts/migrate_pr1_schema.py", 419): "return 2",
    ("scripts/messages_ids.py", 343): "parse_args",
    ("scripts/messages_ids.py", 346): "return 2",
    ("scripts/messages_ids.py", 361): "return 2",
    ("scripts/messages_ids.py", 366): "return 0",
    ("scripts/mailroom_copy_db.py", 167): "def child_main",
    ("scripts/mailroom_copy_db.py", 197): "return 2",
    ("scripts/mailroom_copy_db.py", 201): "return 2",
    ("scripts/mailroom_copy_db.py", 205): "return 2",
    ("scripts/mailroom_copy_db.py", 207): "return 0",
    ("scripts/mailroom_copy_db.py", 286): "return 2",
    ("scripts/mailroom_copy_db.py", 287): "parse_args",
    ("scripts/mailroom_copy_db.py", 301): "return 2",
    ("scripts/mailroom_copy_db.py", 305): "return 2",
    ("scripts/mailroom_copy_db.py", 308): "return 0",
    ("scripts/imap_newmail.py", 24): "return child_main",
    ("scripts/imap_tombstone.py", 29): "return child_main",
    ("scripts/imap_fetch_bodies_fts.py", 141): "return child_main",
    ("scripts/classify.py", 448): "return 2",
    ("scripts/classify.py", 451): "return 0",
    ("scripts/classify.py", 467): "return 1",
    ("scripts/classify.py", 471): "return 0",
    ("scripts/classify.py", 483): "return 2",
    ("scripts/classify.py", 484): "parse_args",
    ("scripts/classify.py", 489): "return 2",
    ("scripts/classify.py", 492): "return 2",
    ("scripts/classify.py", 495): "return rc",
    ("scripts/notify_bills.py", 45): "SystemExit",
    ("scripts/notify_bills.py", 47): "SystemExit",
    ("scripts/notify_bills.py", 63): "SystemExit",
    ("scripts/notify_bills.py", 91): "SystemExit",
    ("scripts/notify_bills.py", 161): "parse_args",
    ("scripts/notify_bills.py", 167): "SystemExit",
    ("scripts/notify_bills.py", 174): "return 0",
    ("scripts/notify_bills.py", 177): "return 1",
    ("scripts/notify_bills.py", 182): "return 0",
    ("scripts/notify_bills.py", 186): "return 0",
    ("scripts/notify_bills.py", 192): "return 0",
    ("scripts/mailroom_daily.py", 503): "return result.returncode",
    ("scripts/mailroom_daily.py", 585): "return 2",
    ("scripts/mailroom_daily.py", 586): "parse_args",
    ("scripts/mailroom_daily.py", 613): "return 2",
    ("scripts/mailroom_daily.py", 617): "return 2",
    ("scripts/mailroom_daily.py", 627): "return 0",
    ("scripts/mailroom_daily.py", 641): "return 0",
    ("scripts/mailroom_daily.py", 648): "return 0",
    ("scripts/mailroom_daily.py", 650): "return 2",
    ("scripts/mailroom_daily.py", 654): "return 0",
    ("scripts/mailroom_daily.py", 709): "return 2",
    ("scripts/mailroom_daily.py", 728): "return rc",
    ("scripts/mailroom_daily.py", 741): "return 0",
    ("scripts/mailroom_daily.py", 751): "return 0",
    ("scripts/mailroom_daily.py", 753): "return 2",
    ("scripts/sqlite_pragmas.py", 124): "parse_args",
    ("scripts/sqlite_pragmas.py", 135): "return 0 if writer_sqlite_ok() else 2",
    ("scripts/sqlite_pragmas.py", 139): "return 2",
    ("scripts/sqlite_pragmas.py", 152): "return 0",
    ("scripts/sqlite_pragmas.py", 154): "return 2",
    ("scripts/refuse_destructive.py", 121): "return 2",
    ("scripts/refuse_destructive.py", 123): "return 0",
    ("scripts/refuse_sql_maintenance.py", 95): "return 2",
    ("scripts/refuse_sql_maintenance.py", 97): "return 0",
    ("scripts/refuse_frozen_jsonl.py", 108): "return 2",
    ("scripts/refuse_frozen_jsonl.py", 110): "return 0",
    ("scripts/sor_health_pack.py", 123): "return 2",
    ("scripts/sor_health_pack.py", 125): "return 1",
    ("scripts/sor_health_pack.py", 126): "return 0",
    ("scripts/sor_health_pack.py", 698): "parse_args",
    ("scripts/sor_health_pack.py", 716): "return report.exit_code()",
}


def _env(extra=None):
    env = os.environ.copy()
    for key in list(env):
        if (
            key.startswith("MAILROOM_")
            or key.startswith("SOR_")
            or key.startswith("SEARCH_")
        ):
            env.pop(key, None)
    env["PYTHONPATH"] = str(SCRIPTS) + os.pathsep + str(SCRIPTS / "attachments")
    if extra:
        env.update(extra)
    return env


def _run(argv, env=None):
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=_env(env),
        check=False,
    )


def _py(*args):
    return [sys.executable, *args]


class DocContractTests(unittest.TestCase):
    def test_citations_match_source_and_are_named_in_the_doc(self):
        text = DOC.read_text(encoding="utf-8")
        found = {(rel, int(line)) for rel, line in CITE_RE.findall(text)}
        self.assertGreaterEqual(len(found), len(NEEDLES))
        for rel, line in found:
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(line, 1, "%s:%s" % (rel, line))
            self.assertLessEqual(line, len(rows), "%s:%s" % (rel, line))
            self.assertTrue(rows[line - 1].strip(), "%s:%s" % (rel, line))
        missing = []
        for (rel, line), needle in sorted(NEEDLES.items()):
            if (rel, line) not in found:
                missing.append("%s:%s" % (rel, line))
                continue
            source = (ROOT / rel).read_text(encoding="utf-8").splitlines()[line - 1]
            if needle not in source:
                missing.append("%s:%s missing %r" % (rel, line, needle))
        self.assertEqual(missing, [])

    def test_proposal_is_opt_in_and_does_not_rename_the_default(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("MAILROOM_WRITER_LOCK_BUSY_EXIT", text)
        self.assertIn("--busy-exit", text)
        self.assertIn("status stays 2", text)
        wrapper = (SCRIPTS / "with_writer_lock.py").read_text(encoding="utf-8")
        self.assertNotIn("MAILROOM_WRITER_LOCK_BUSY_EXIT", wrapper)
        self.assertNotIn("--busy-exit", wrapper)
        self.assertIn("return 2", wrapper)

    def test_doc_has_no_personal_identifiers(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)
        self.assertIsNone(EMAIL_RE.search(text))
        self.assertNotIn("@icloud.com", text)
        self.assertNotIn("@me.com", text)


class WrapperExitTests(unittest.TestCase):
    def test_usage_is_2_with_usage_line(self):
        proc = _run(_py(str(SCRIPTS / "with_writer_lock.py")))
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("usage: with_writer_lock.py"))
        self.assertIn("with_writer_lock.py: error:", proc.stderr)
        self.assertNotIn("writer lock held", proc.stderr)

    def test_bad_max_age_is_usage(self):
        proc = _run(
            _py(
                str(SCRIPTS / "with_writer_lock.py"),
                "--purpose",
                "unit",
                "--max-age-hours",
                "nope",
                "--",
                sys.executable,
                "-c",
                "print('no')",
            )
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage: with_writer_lock.py", proc.stderr)
        self.assertIn("with_writer_lock.py: error:", proc.stderr)

    def test_missing_command_is_writer_lock_error_not_usage(self):
        proc = _run(
            _py(str(SCRIPTS / "with_writer_lock.py"), "--purpose", "unit")
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: command is required after --\n", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)

    def test_blank_purpose_is_writer_lock_error_not_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = _run(
                _py(
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "   ",
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                    "--action-required-file",
                    str(root / "ACTION_REQUIRED"),
                    "--",
                    sys.executable,
                    "-c",
                    "print('no')",
                )
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: purpose is required\n", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)

    def test_success_is_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = _run(
                _py(
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "unit",
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                    "--action-required-file",
                    str(root / "ACTION_REQUIRED"),
                    "--",
                    sys.executable,
                    "-c",
                    "print('ok')",
                )
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "ok\n")
        self.assertEqual(proc.stderr, "")

    def test_child_exit_2_is_not_a_lock_busy_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = _run(
                _py(
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "unit",
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                    "--action-required-file",
                    str(root / "ACTION_REQUIRED"),
                    "--",
                    sys.executable,
                    "-c",
                    "raise SystemExit(2)",
                )
            )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertNotIn("usage:", proc.stderr)
        self.assertNotIn("writer lock held", proc.stderr)

    def test_lock_held_is_2_with_held_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "holder")
            try:
                proc = _run(
                    _py(
                        str(SCRIPTS / "with_writer_lock.py"),
                        "--purpose",
                        "later",
                        "--lock-file",
                        str(lock),
                        "--action-required-file",
                        str(root / "ACTION_REQUIRED"),
                        "--",
                        sys.executable,
                        "-c",
                        "print('no')",
                    )
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: writer lock held:", proc.stderr)
        self.assertIn("purpose=holder", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)
        self.assertNotIn("no steal", proc.stderr)
        self.assertNotIn("writer_token", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_stale_lock_is_2_with_no_steal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "holder")
            try:
                lock.write_text(
                    wwl.format_lock_payload(
                        "holder",
                        datetime(2020, 1, 1, tzinfo=timezone.utc),
                        1,
                        "host",
                    ),
                    encoding="utf-8",
                )
                proc = _run(
                    _py(
                        str(SCRIPTS / "with_writer_lock.py"),
                        "--purpose",
                        "later",
                        "--lock-file",
                        str(lock),
                        "--action-required-file",
                        str(root / "ACTION_REQUIRED"),
                        "--",
                        sys.executable,
                        "-c",
                        "print('no')",
                    )
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no steal", proc.stderr)
        self.assertIn("error: writer lock held >", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)

    def test_action_required_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            action = root / "ACTION_REQUIRED"
            action.write_text("pause\n", encoding="utf-8")
            proc = _run(
                _py(
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "unit",
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                    "--action-required-file",
                    str(action),
                    "--",
                    sys.executable,
                    "-c",
                    "print('no')",
                )
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: action-required open", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_drop_mismatch_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deadline = root / "deadline.epoch"
            proc = _run(
                _py(
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "att0-restore",
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                    "--action-required-file",
                    str(root / "ACTION_REQUIRED"),
                    "--",
                    sys.executable,
                    "-c",
                    "print('child')",
                ),
                {
                    "MAILROOM_SEARCH_RESUME_RUN_ID": "other-id",
                    "SEARCH_RESUME_DEADLINE_FILE": str(deadline),
                    "SEARCH_RESUME_LOG": str(root / "wd.log"),
                },
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn(
            "error: search resume +26 drop failed; child not started:",
            proc.stderr,
        )
        self.assertNotIn("child", proc.stdout)
        self.assertNotIn("usage:", proc.stderr)


class GateAndProbeExitTests(unittest.TestCase):
    def test_gate_usage_is_2(self):
        proc = _run(_py(str(SCRIPTS / "sor_writer_gate.py")))
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("usage: sor_writer_gate.py"))
        self.assertIn("sor_writer_gate.py: error:", proc.stderr)
        self.assertNotIn("CONFLICT", proc.stderr)

    def test_gate_allows_free_live_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom.sqlite"
            db.write_bytes(b"")
            proc = _run(
                _py(
                    str(SCRIPTS / "sor_writer_gate.py"),
                    "--db",
                    str(db),
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                )
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("sor_writer_gate=allow db=", proc.stdout)
        self.assertEqual(proc.stderr, "")

    def test_probe_exits_1_where_gate_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            db = root / "mailroom.sqlite"
            db.write_bytes(b"")
            held_lock = wwl.acquire_writer_lock(lock, "holder")
            try:
                probe_code = "\n".join(
                    [
                        "import sys",
                        "sys.path.insert(0, %r)" % str(SCRIPTS),
                        "import sor_writer_gate as gate",
                        "held, detail = gate._probe_writer_lock(%r)" % str(lock),
                        "sys.stderr.write(detail + '\\n')",
                        "raise SystemExit(held)",
                    ]
                )
                probe = _run([sys.executable, "-c", probe_code])
                gate = _run(
                    _py(
                        str(SCRIPTS / "sor_writer_gate.py"),
                        "--db",
                        str(db),
                        "--lock-file",
                        str(lock),
                    )
                )
                copy = _run(
                    _py(
                        str(SCRIPTS / "sor_writer_gate.py"),
                        "--db",
                        str(root / "mailroom-copy.sqlite"),
                        "--lock-file",
                        str(lock),
                    )
                )
            finally:
                wwl.release_writer_lock(held_lock)
        self.assertEqual(probe.returncode, 1)
        self.assertIn("writer lock held:", probe.stderr)
        self.assertNotIn("CONFLICT", probe.stderr)
        self.assertNotIn("usage:", probe.stderr)
        self.assertEqual(gate.returncode, 2)
        self.assertIn("error: CONFLICT:", gate.stderr)
        self.assertIn("writer lock held:", gate.stderr)
        self.assertNotIn("usage:", gate.stderr)
        self.assertEqual(copy.returncode, 0, copy.stderr)
        self.assertIn("sor_writer_gate=allow", copy.stdout)

    def test_identity_probe_exits_1_where_gate_exits_2(self):
        token = "pin-token"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "absent.write.lock"
            db = root / "mailroom.sqlite"
            db.write_bytes(b"")
            code = "\n".join(
                [
                    "import sys",
                    "sys.path.insert(0, %r)" % str(SCRIPTS),
                    "import sor_writer_gate as gate",
                    "held, detail = gate.writer_lock_held(%r)" % str(lock),
                    "sys.stderr.write(detail + '\\n')",
                    "raise SystemExit(held)",
                ]
            )
            env = {"MAILROOM_WRITER_LOCK_TOKEN": token}
            probe = _run([sys.executable, "-c", code], env)
            gate = _run(
                _py(
                    str(SCRIPTS / "sor_writer_gate.py"),
                    "--db",
                    str(db),
                    "--lock-file",
                    str(lock),
                ),
                env,
            )
        self.assertEqual(probe.returncode, 1)
        self.assertIn("writer lock identity refused: lock not held", probe.stderr)
        self.assertNotIn(token, probe.stderr)
        self.assertNotIn("CONFLICT", probe.stderr)
        self.assertEqual(gate.returncode, 2)
        self.assertIn("error: CONFLICT:", gate.stderr)
        self.assertIn("lock not held", gate.stderr)
        self.assertNotIn(token, gate.stderr)

    def test_preflight_held_flock_is_2_without_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "holder")
            try:
                proc = _run(
                    _py(
                        str(SCRIPTS / "pr5_preflight.py"),
                        "--lock-file",
                        str(lock),
                    )
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(
            "error: writer flock held — flock free gate failed\n",
            proc.stderr,
        )
        self.assertIn("flock_free=false", proc.stdout)
        self.assertIn("enable_verdict=NON-GO", proc.stdout)
        self.assertNotIn("CONFLICT", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)

    def test_preflight_free_lock_is_0_even_with_non_go_stdout(self):
        proc = _run(_py(str(SCRIPTS / "pr5_preflight.py")))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("enable_verdict=NON-GO", proc.stdout)
        self.assertEqual(proc.stderr, "")

    def test_preflight_enable_is_2(self):
        proc = _run(_py(str(SCRIPTS / "pr5_preflight.py"), "--enable"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: NON-GO:", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)


class WatchdogExitTests(unittest.TestCase):
    def _deadline_env(self, root):
        return {
            "SEARCH_RESUME_DEADLINE_FILE": str(root / "deadline.epoch"),
            "SEARCH_RESUME_LOG": str(root / "wd.log"),
        }

    def test_status_missing_is_1_and_usage_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._deadline_env(root)
            status = _run(
                _py(str(SCRIPTS / "search_resume_watchdog.py"), "status"),
                env,
            )
            usage = _run(
                _py(str(SCRIPTS / "search_resume_watchdog.py"), "nope"),
                env,
            )
            watch = _run(
                _py(str(SCRIPTS / "search_resume_watchdog.py"), "watch"),
                env,
            )
        self.assertEqual(status.returncode, 1)
        self.assertEqual(status.stdout, "status=missing\n")
        self.assertEqual(status.stderr, "")
        self.assertEqual(usage.returncode, 2)
        self.assertTrue(usage.stderr.startswith("usage: search_resume_watchdog.py"))
        self.assertEqual(watch.returncode, 0)
        self.assertEqual(watch.stderr, "")

    def test_arm_mismatch_is_1_and_wrapper_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._deadline_env(root)
            wrote = _run(
                _py(
                    str(SCRIPTS / "search_resume_watchdog.py"),
                    "write",
                    "--run-id",
                    "att0-L1-1",
                    "--now",
                    "100",
                ),
                env,
            )
            arm = _run(
                _py(
                    str(SCRIPTS / "search_resume_watchdog.py"),
                    "arm",
                    "--run-id",
                    "other-id",
                ),
                env,
            )
            wrapped = _run(
                _py(
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "att0-restore",
                    "--lock-file",
                    str(root / "mailroom.write.lock"),
                    "--action-required-file",
                    str(root / "ACTION_REQUIRED"),
                    "--",
                    sys.executable,
                    "-c",
                    "print('child')",
                ),
                dict(env, MAILROOM_SEARCH_RESUME_RUN_ID="other-id"),
            )
        self.assertEqual(wrote.returncode, 0, wrote.stderr)
        self.assertEqual(arm.returncode, 1)
        self.assertIn("error: run-id mismatch\n", arm.stderr)
        self.assertNotIn("usage:", arm.stderr)
        self.assertEqual(wrapped.returncode, 2)
        self.assertIn(
            "error: search resume +26 drop failed; child not started:",
            wrapped.stderr,
        )
        self.assertNotIn("child", wrapped.stdout)


class Att0AndOpsExitTests(unittest.TestCase):
    def test_meta_fill_and_migrate_usage_and_success(self):
        migrate = str(SCRIPTS / "attachments" / "migrate_att0_schema.py")
        fill = str(SCRIPTS / "attachments" / "meta_fill.py")
        usage_m = _run(_py(migrate))
        usage_f = _run(_py(fill))
        self.assertEqual(usage_m.returncode, 2)
        self.assertTrue(usage_m.stderr.startswith("usage: migrate_att0_schema.py"))
        self.assertEqual(usage_f.returncode, 2)
        self.assertTrue(usage_f.stderr.startswith("usage: meta_fill.py"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mailroom-copy.sqlite"
            live = root / "mailroom.sqlite"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE messages ("
                "id TEXT PRIMARY KEY, source TEXT NOT NULL, "
                "folder TEXT, uid TEXT, jsonl_offset INTEGER, "
                "jsonl_len INTEGER, has_attachments INTEGER DEFAULT 0)"
            )
            conn.commit()
            conn.close()
            live.write_bytes(b"")
            migrated = _run(_py(migrate, "--db", str(db)))
            missing = _run(_py(migrate, "--db", str(root / "missing.sqlite")))
            live_refuse = _run(_py(fill, "--db", str(live), "--source", "jsonl", "--jsonl", str(root / "a.jsonl"), "--dry-run"))
            both = _run(
                _py(
                    fill,
                    "--db",
                    str(db),
                    "--source",
                    "jsonl",
                    "--jsonl",
                    str(root / "a.jsonl"),
                    "--apply",
                    "--dry-run",
                )
            )
            dump = root / "archive.jsonl"
            dump.write_bytes(b"\n")
            filled = _run(
                _py(
                    fill,
                    "--db",
                    str(db),
                    "--source",
                    "jsonl",
                    "--jsonl",
                    str(dump),
                    "--dry-run",
                    "--max-messages",
                    "0",
                    "--timeout",
                    "0",
                )
            )
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.assertTrue(migrated.stdout.startswith("att0 schema migrate"))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error: database not found\n", missing.stderr)
        self.assertNotIn("usage:", missing.stderr)
        self.assertEqual(live_refuse.returncode, 2)
        self.assertIn("error: refuse: resolved path is the live SoR", live_refuse.stderr)
        self.assertEqual(both.returncode, 2)
        self.assertIn("error: pass only one of --apply and --dry-run\n", both.stderr)
        self.assertEqual(filled.returncode, 0, filled.stderr)
        self.assertIn("att0 meta fill", filled.stdout)

    def test_embed_backfill_conflict_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            db = root / "mailroom.sqlite"
            db.write_bytes(b"")
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                proc = _run(
                    _py(
                        str(SCRIPTS / "embed_backfill.py"),
                        "--db",
                        str(db),
                        "--dry-run",
                    ),
                    {"MAILROOM_WRITE_LOCK": str(lock)},
                )
            finally:
                wwl.release_writer_lock(held)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: CONFLICT:", proc.stderr)
        self.assertIn("writer lock held:", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)

    def test_sidecar_and_merge_usage_are_2(self):
        side = _run(_py(str(SCRIPTS / "embed_sidecar_apply.py")))
        merge = _run(_py(str(SCRIPTS / "embed_merge_shards.py")))
        ids = _run(_py(str(SCRIPTS / "messages_ids.py")))
        self.assertEqual(side.returncode, 2)
        self.assertTrue(side.stderr.startswith("usage: embed_sidecar_apply.py"))
        self.assertEqual(merge.returncode, 2)
        self.assertTrue(merge.stderr.startswith("usage: embed_merge_shards.py"))
        self.assertEqual(ids.returncode, 2)
        self.assertIn("error: pass --backfill", ids.stderr)
        self.assertNotIn("usage:", ids.stderr)

    def test_copy_db_2_and_notify_bills_1_share_the_refuse_text(self):
        copy = _run(_py(str(SCRIPTS / "mailroom_copy_db.py")))
        bills = _run(_py(str(SCRIPTS / "notify_bills.py")))
        child = _run(_py(str(SCRIPTS / "imap_newmail.py")))
        self.assertEqual(copy.returncode, 2)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(bills.returncode, 1)
        for proc in (copy, bills, child):
            self.assertIn("db_mode=refused\n", proc.stderr)
            self.assertIn("error: MAILROOM_DB is unset.", proc.stderr)
            self.assertNotIn("usage:", proc.stderr)
        self.assertEqual(copy.stderr, bills.stderr)
        self.assertEqual(copy.stderr, child.stderr)

    def test_copy_allows_copy_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            proc = _run(_py(str(SCRIPTS / "mailroom_copy_db.py"), "--db", str(db)))
            newmail = _run(_py(str(SCRIPTS / "imap_newmail.py"), "--db", str(db)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("db_mode=copy\n", proc.stderr)
        self.assertEqual(newmail.returncode, 0, newmail.stderr)
        self.assertIn("opened_db=", newmail.stdout)

    def test_classify_sqlite_failure_is_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            sqlite3.connect(str(db)).close()
            proc = _run(_py(str(SCRIPTS / "classify.py"), "--db", str(db)))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("error: classify database write failed\n", proc.stderr)
        self.assertNotIn("usage:", proc.stderr)

    def test_daily_busy_lock_is_0_and_print_plan_is_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            logs = root / "logs"
            db = root / "mailroom-copy.sqlite"
            db.write_bytes(b"")
            plan = _run(
                _py(
                    str(SCRIPTS / "mailroom_daily.py"),
                    "--print-plan",
                    "--archive",
                    str(archive),
                    "--db",
                    str(db),
                    "--logs",
                    str(logs),
                    "--scripts",
                    str(SCRIPTS),
                )
            )
            held = daily.acquire_daily_lock(archive / "mailroom.daily.lock")
            try:
                skip = _run(
                    _py(
                        str(SCRIPTS / "mailroom_daily.py"),
                        "--force",
                        "--archive",
                        str(archive),
                        "--db",
                        str(db),
                        "--logs",
                        str(logs),
                        "--scripts",
                        str(SCRIPTS),
                    )
                )
            finally:
                daily.release_daily_lock(held)
            usage = _run(_py(str(SCRIPTS / "mailroom_daily.py"), "--not-a-flag"))
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("imap_newmail.py", plan.stdout)
        self.assertEqual(skip.returncode, 0, skip.stderr)
        self.assertIn("skip:", skip.stderr)
        self.assertIn("mailroom.daily.lock held", skip.stderr)
        self.assertEqual(usage.returncode, 2)
        self.assertIn("usage: mailroom_daily.py", usage.stderr)

    def test_sqlite_pragmas_and_refuse_clis(self):
        bare = _run(_py(str(SCRIPTS / "sqlite_pragmas.py")))
        version = _run(
            _py(str(SCRIPTS / "sqlite_pragmas.py"), "--check-writer-version")
        )
        ok = _run(_py(str(SCRIPTS / "refuse_destructive.py")))
        denied = _run(_py(str(SCRIPTS / "refuse_destructive.py"), "expunge"))
        sql_ok = _run(_py(str(SCRIPTS / "refuse_sql_maintenance.py")))
        sql_no = _run(
            _py(str(SCRIPTS / "refuse_sql_maintenance.py"), "DELETE FROM messages")
        )
        frozen_ok = _run(_py(str(SCRIPTS / "refuse_frozen_jsonl.py")))
        self.assertEqual(bare.returncode, 2)
        self.assertTrue(bare.stderr.startswith("usage: sqlite_pragmas.py"))
        self.assertNotIn("error: the following arguments", bare.stderr)
        expect = 0 if sqlite_pragmas.writer_sqlite_ok() else 2
        self.assertEqual(version.returncode, expect)
        self.assertTrue(version.stdout.startswith("sqlite "))
        self.assertNotIn("usage:", version.stderr)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("ok: no destructive CLI verbs", ok.stdout)
        self.assertEqual(denied.returncode, 2)
        self.assertIn("error:", denied.stderr)
        self.assertNotIn("usage:", denied.stderr)
        self.assertEqual(sql_ok.returncode, 0, sql_ok.stderr)
        self.assertIn("ok: sql not on maintenance denylist", sql_ok.stdout)
        self.assertEqual(sql_no.returncode, 2)
        self.assertIn("error:", sql_no.stderr)
        self.assertEqual(frozen_ok.returncode, 0, frozen_ok.stderr)
        self.assertIn("ok: frozen jsonl not rewritten", frozen_ok.stdout)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
