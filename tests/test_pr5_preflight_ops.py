#!/usr/bin/env python3
"""KOO-86 PR-5 prep: cutover docs + dry preflight (no enable).

Docs/tests/verify only. No RunAtLoad enable. No SoR host flip.
No rem restart. No live IMAP. No live SoR writes. ZERO PII.
"""

from __future__ import annotations

import io
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pr5_preflight as pre  # noqa: E402
import sor_writer_gate as gate  # noqa: E402
import with_writer_lock as wwl  # noqa: E402

CHECKLIST = ROOT / "docs" / "pr5-cutover.md"
CATCHUP = ROOT / "docs" / "post-exit-catchup.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
EMBED = ROOT / "docs" / "embed-backfill.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
FREEZE = ROOT / "docs" / "rem-window-freeze.md"
HELPER = ROOT / "scripts" / "pr5_preflight.py"
REPO_PLIST = ROOT / "launchd" / "com.mailroom.daily.plist"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")

DOC_PATHS = (CHECKLIST, CATCHUP, OPS, README, MAILROOM, EMBED, DAILY, FREEZE)


class Pr5PrepDocNeedleTests(unittest.TestCase):
    def test_checklist_has_integrity_rollback_preflight_gates_non_go(self):
        raw = CHECKLIST.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("Integrity pack", text)
        self.assertIn("Rollback procedure", text)
        self.assertIn("Operator-facing preflight", text)
        self.assertIn("Post-rem gates", text)
        self.assertIn("NON-GO", text)
        self.assertIn("does **not** enable PR-5", text)
        self.assertIn("Does not enable RunAtLoad", text)
        self.assertIn("does not enable PR-5 / RunAtLoad / flip SoR host", text)
        self.assertIn("EXIT ≠ cutover GO", text)
        self.assertIn(
            "EXIT → gate ALLOW → catch-up → later copy+integrity → then checklist",
            text,
        )
        self.assertIn("not enable with catch-up", text.lower())
        self.assertIn("17223/17223", text)
        self.assertIn("#40", text)
        self.assertIn("sor_writer_gate", text)
        self.assertIn("catch-up Done", text)
        self.assertIn("HARD DECK", text)
        self.assertIn("flock free", text)
        self.assertIn("stale dead-PID lock", text)
        self.assertIn("false", text)
        self.assertIn("CONFLICT", text)
        self.assertIn("promote GO", text)
        self.assertIn("db_mode=refused", text)
        self.assertIn("history", text)
        self.assertIn("opt-in", text)
        self.assertIn("purge", text)
        self.assertIn("EXPUNGE", text)
        self.assertIn("never-purge", text)
        self.assertIn("icloud_mail_all.jsonl", text)
        self.assertIn("immutable", text)
        self.assertIn("BODY.PEEK", text)
        self.assertIn("8.17", text)
        self.assertIn("mailroom.imap.app-password", text)
        self.assertIn("lane=auth", text)
        self.assertIn("One cutover + one rollback", text)
        self.assertIn("SoR=MBP until CoS says", text)
        self.assertIn("mlx", text)
        self.assertIn("pr5_preflight.py", text)
        self.assertIn("gated on rem-legacy EXIT 0", text)
        self.assertIn("Mini SoR switch steps", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_cross_links_from_mailroom_ops_embed_readme(self):
        for path in (MAILROOM, OPS, EMBED, README, DAILY, CATCHUP):
            raw = path.read_text(encoding="utf-8")
            text = " ".join(raw.split())
            self.assertIn("pr5-cutover.md", text, msg=path.name)
            self.assertIn("NON-GO", text, msg=path.name)
            self.assertIn("do not enable", text.lower(), msg=path.name)
            hay = raw.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

    def test_mailroom_needles_folded_into_ops_and_mailroom(self):
        for path in (OPS, MAILROOM):
            text = " ".join(path.read_text(encoding="utf-8").split())
            self.assertIn("EXIT ≠ cutover GO", text, msg=path.name)
            self.assertIn("gate ALLOW", text, msg=path.name)
            self.assertIn("not enable with catch-up", text.lower(), msg=path.name)
            self.assertIn("stale dead-PID lock", text, msg=path.name)
            self.assertIn("promote GO", text, msg=path.name)
            self.assertIn("SoR=MBP until CoS says", text, msg=path.name)
            self.assertIn("17223/17223", text, msg=path.name)

    def test_ops_keeps_standing_do_not_enable_runatload(self):
        raw = OPS.read_text(encoding="utf-8")
        low = " ".join(raw.split()).lower()
        self.assertIn("do not enable runatload in this change", low)
        self.assertIn("does **not** enable pr-5 cutover", low)
        self.assertIn("mini sor switch steps", low)
        self.assertNotIn("cutover is not gated", low)
        self.assertNotIn("this gate starts rem-legacy", low)

    def test_docs_do_not_enable_or_implement_att(self):
        for path in DOC_PATHS:
            low = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("enable pr-5 cutover in this change", low, msg=path.name)
            self.assertNotIn("this change enables runatload", low, msg=path.name)
            self.assertNotIn("this pr implements att", low, msg=path.name)
            self.assertNotIn("this pr implements msg", low, msg=path.name)


class Pr5PreflightHelperTests(unittest.TestCase):
    def test_plist_absent_is_not_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "com.mailroom.daily.plist"
            info = pre.inspect_plist(missing)
        self.assertEqual(info["state"], "absent")
        self.assertFalse(info["run_at_load"])
        self.assertTrue(pre.installed_not_enabled(info))

    def test_plist_disabled_run_at_load_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed.plist"
            path.write_bytes(
                plistlib.dumps(
                    {
                        "Label": "com.mailroom.daily",
                        "RunAtLoad": False,
                        "EnvironmentVariables": {
                            "MAILROOM_DB": "__HOME__/MailArchive/mailroom-copy.sqlite"
                        },
                    }
                )
            )
            info = pre.inspect_plist(path)
        self.assertEqual(info["state"], "disabled")
        self.assertFalse(info["run_at_load"])
        self.assertTrue(pre.installed_not_enabled(info))

    def test_installed_run_at_load_true_is_not_hold_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed.plist"
            path.write_bytes(plistlib.dumps({"RunAtLoad": True}))
            info = pre.inspect_plist(path)
        self.assertEqual(info["state"], "enabled_run_at_load")
        self.assertFalse(pre.installed_not_enabled(info))

    def test_path_existence_mocks_fail_closed(self):
        seen: list[Path] = []

        def exists_fn(path: Path) -> bool:
            seen.append(path)
            return path.name == "mailroom-copy.sqlite"

        self.assertTrue(
            pre.path_exists("/tmp/mailroom-copy.sqlite", exists_fn=exists_fn)
        )
        self.assertFalse(
            pre.path_exists("/tmp/missing-copy.sqlite", exists_fn=exists_fn)
        )
        self.assertEqual(len(seen), 2)
        self.assertTrue(pre.is_copy_basename("/tmp/mailroom-copy.sqlite"))
        self.assertFalse(pre.is_copy_basename("/tmp/mailroom.sqlite"))

    def test_repo_template_stays_copy_only(self):
        raw = pre.repo_template_copy_only(REPO_PLIST)
        self.assertIn("copy", Path(raw).name)
        self.assertNotEqual(Path(raw).name, "mailroom.sqlite")
        data = plistlib.loads(REPO_PLIST.read_bytes())
        # Template RunAtLoad stays as previously checked in — not an enable.
        self.assertTrue(data["RunAtLoad"])

    def test_post_rem_gates_fail_closed_without_evidence(self):
        ok, missing = pre.post_rem_gates_pass({})
        self.assertFalse(ok)
        self.assertEqual(list(pre.REQUIRED_POST_REM_GATES), missing)
        ok, missing = pre.post_rem_gates_pass(
            {
                "rem_exit": True,
                "gate_40_live": True,
                "catchup_done": True,
                "single_writer_hard_deck": True,
                # flock_free omitted
            }
        )
        self.assertFalse(ok)
        self.assertEqual(missing, ["flock_free"])
        ok, missing = pre.post_rem_gates_pass(
            {
                "rem_exit": True,
                "gate_40_live": True,
                "catchup_done": True,
                "single_writer_hard_deck": True,
                "flock_free": True,
            }
        )
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_stale_dead_pid_lock_is_not_false_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            lock.write_text(
                wwl.format_lock_payload(
                    "rem-legacy",
                    wwl.utcnow(),
                    999001,
                    "test-host",
                ),
                encoding="utf-8",
            )
            label = pre.probe_stale_dead_pid_lock(lock, db, cmdlines=())
            self.assertEqual(label, "stale_dead_pid_lock_not_conflict")
            self.assertTrue(pre.flock_is_free(lock))
            gate.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)

    def test_live_rem_refuses_future_sor_writes(self):
        db = Path("/tmp/mailroom.sqlite")
        rem = ((42, "python3 rem-legacy --db %s" % db),)
        with self.assertRaises(gate.SorWriterRefuse) as ctx:
            pre.probe_live_rem_refuses(db, cmdlines=rem, lock_held=False)
        self.assertIn(gate.CONFLICT_TOKEN, str(ctx.exception))
        # Copy DB stays allowed while rem is live.
        gate.refuse_if_sor_writer_conflict(
            Path("/tmp/mailroom-copy.sqlite"),
            cmdlines=rem,
            lock_held=True,
        )

    def test_held_flock_is_not_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                self.assertFalse(pre.flock_is_free(lock))
                with self.assertRaises(gate.SorWriterRefuse) as ctx:
                    gate.refuse_if_sor_writer_conflict(
                        db, cmdlines=(), lock_path=lock
                    )
                self.assertIn(gate.CONFLICT_TOKEN, str(ctx.exception))
            finally:
                wwl.release_writer_lock(held)

    def test_read_only_integrity_and_hard_deck_needles(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        needles = pre.read_only_integrity_needles(text)
        self.assertTrue(needles["integrity_pack"])
        self.assertTrue(needles["copy_freshness"])
        self.assertTrue(needles["embed_key"])
        embed = EMBED.read_text(encoding="utf-8")
        self.assertTrue(pre.hard_deck_survives(embed))
        self.assertTrue(pre.hard_deck_survives(text))

    def test_enable_flag_is_refused(self):
        with self.assertRaises(pre.PreflightRefuse) as ctx:
            pre.refuse_enable_flag(True)
        self.assertIn("NON-GO", str(ctx.exception))
        pre.refuse_enable_flag(False)

    def test_cli_dry_run_absent_plist_and_mocked_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.plist"
            copy = Path(tmp) / "mailroom-copy.sqlite"
            copy.write_bytes(b"")
            lock = Path(tmp) / "mailroom.write.lock"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "--installed-plist",
                    str(missing),
                    "--repo-plist",
                    str(REPO_PLIST),
                    "--copy-db",
                    str(copy),
                    "--lock-file",
                    str(lock),
                    "--sor-db",
                    str(Path(tmp) / "mailroom.sqlite"),
                    "--cite-standing",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("enable_verdict=NON-GO", proc.stdout)
        self.assertIn("installed_plist_state=absent", proc.stdout)
        self.assertIn("installed_not_enabled=true", proc.stdout)
        self.assertIn("copy_path_ok=true", proc.stdout)
        self.assertIn("flock_free=true", proc.stdout)
        self.assertIn("sor_written=false", proc.stdout)
        self.assertIn("17223/17223", proc.stdout)
        self.assertIn("lane=auth", proc.stdout)
        self.assertIn("mailroom.imap.app-password", proc.stdout)
        self.assertNotIn("launchctl", proc.stdout)
        hay = proc.stdout + proc.stderr
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_cli_refuses_enable_and_run_at_load_true(self):
        enable = subprocess.run(
            [sys.executable, str(HELPER), "--enable"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(enable.returncode, 2)
        self.assertIn("NON-GO", enable.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.plist"
            path.write_bytes(plistlib.dumps({"RunAtLoad": True}))
            proc = subprocess.run(
                [sys.executable, str(HELPER), "--installed-plist", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("NON-GO", proc.stdout + proc.stderr)

    def test_helper_source_never_enables_launchagents_or_writes_sor(self):
        src = HELPER.read_text(encoding="utf-8")
        for verb in pre.FORBIDDEN_LAUNCHCTL_SUBCOMMANDS:
            self.assertNotRegex(
                src,
                r"launchctl\s+" + verb,
                msg="helper must not invoke launchctl %s" % verb,
            )
        self.assertNotIn("subprocess", src)
        self.assertNotIn("Popen", src)
        self.assertIn("Never enables PR-5", src)
        self.assertIn("stale dead-PID", src)
        # Do not open ask_mail or overwrite it.
        self.assertNotIn("ask_mail.py", src)

    def test_no_live_sqlite_write_from_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom.sqlite"
            self.assertFalse(db.exists())
            buf = io.StringIO()
            with patch.object(sys, "stdout", buf):
                rc = pre.main(
                    [
                        "--sor-db",
                        str(db),
                        "--installed-plist",
                        str(Path(tmp) / "no.plist"),
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertFalse(db.exists())
            self.assertIn("sor_written=false", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
