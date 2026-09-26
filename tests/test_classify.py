#!/usr/bin/env python3
"""Rules-first classify.py. Temporary sqlite only. No network. No personal data."""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import ollama_guard  # noqa: E402

ollama_guard.install()

import classify  # noqa: E402
import mailroom_daily as daily  # noqa: E402
from refuse_destructive import REFUSE_PREFIX  # noqa: E402

EXAMPLE_RULES = ROOT / "docs" / "classify_rules.example.json"
REQUIRED_RULES = frozenset(
    (
        "folder_newsletters",
        "junk_ops",
        "p10_auth",
        "marketing_domain",
        "property_keep",
        "family",
        "lawsuit_people",
        "p60_ops",
        "money_subj_but_promo",
        "p20_money",
        "p70_bulk",
        "pcn_examplekeyword",
        "folder_junk",
        "p90_unknown",
    )
)


def _rules_payload():
    return {
        "property_keep": ["(?i)example-plaza"],
        "lawsuit_extra": ["(?i)example v\\. sample"],
        "family_addrs": ["family@example.com", "family@counsel.example.com"],
        "lawsuit_domains": ["counsel.example.com"],
        "money_addrs": ["statements@example.com"],
        "marketing_domains": ["promo.example.com"],
        "pcn_keywords": ["examplekeyword"],
    }


def _write_rules(folder, payload):
    path = Path(folder) / "rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class RuleOrderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.rules_path = _write_rules(self._tmp.name, _rules_payload())
        self.rules, status, count = classify.load_rules(self.rules_path)
        self.assertEqual(status, "loaded")
        self.assertEqual(count, 8)

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_rule_name_and_order(self):
        cases = (
            (
                "folder_newsletters",
                "Person <a@example.com>",
                "verification code",
                "Newsletters",
                ("bulk", 1, 0, "folder_newsletters"),
            ),
            (
                "junk_ops",
                "Person <a@example.com>",
                "package shipped",
                "Junk",
                ("ops", 0, 0, "junk_ops"),
            ),
            (
                "junk_ops_recall",
                "Person <a@example.com>",
                "safety recall notice",
                "Junk",
                ("ops", 0, 1, "junk_ops"),
            ),
            (
                "auth_beats_junk_ops",
                "verification code <a@example.com>",
                "package shipped",
                "Junk",
                ("auth", 0, 0, "p10_auth"),
            ),
            (
                "p10_auth",
                "Person <a@example.com>",
                "Your verification code",
                "INBOX",
                ("auth", 0, 0, "p10_auth"),
            ),
            (
                "marketing_domain",
                "Ads <x@promo.example.com>",
                "package shipped",
                "INBOX",
                ("bulk", 1, 0, "marketing_domain"),
            ),
            (
                "auth_beats_marketing",
                "Ads <x@promo.example.com>",
                "verification code",
                "INBOX",
                ("auth", 0, 0, "p10_auth"),
            ),
            (
                "property_keep",
                "Person <a@example.com>",
                "visit example-plaza",
                "INBOX",
                ("people", 0, 0, "property_keep"),
            ),
            (
                "property_keep_lawsuit",
                "Person <a@example.com>",
                "example-plaza lawsuit",
                "INBOX",
                ("people", 0, 1, "property_keep"),
            ),
            (
                "property_keep_from",
                "example-plaza desk <a@example.com>",
                "hello",
                "Archive",
                ("people", 0, 0, "property_keep"),
            ),
            (
                "family",
                "Kin <family@example.com>",
                "hello",
                "INBOX",
                ("people", 0, 0, "family"),
            ),
            (
                "family_before_lawsuit_domain",
                "Kin <family@counsel.example.com>",
                "hello",
                "INBOX",
                ("people", 0, 0, "family"),
            ),
            (
                "lawsuit_people_subject",
                "Person <a@example.com>",
                "written discovery served",
                "INBOX",
                ("people", 0, 1, "lawsuit_people"),
            ),
            (
                "lawsuit_people_domain",
                "Atty <a@counsel.example.com>",
                "hello",
                "INBOX",
                ("people", 0, 1, "lawsuit_people"),
            ),
            (
                "lawsuit_people_extra",
                "Person <a@example.com>",
                "Example v. Sample hearing",
                "INBOX",
                ("people", 0, 1, "lawsuit_people"),
            ),
            (
                "p60_ops",
                "Person <a@example.com>",
                "boarding pass attached",
                "INBOX",
                ("ops", 0, 0, "p60_ops"),
            ),
            (
                "p60_ops_recall",
                "Person <a@example.com>",
                "safety recall",
                "INBOX",
                ("ops", 0, 1, "p60_ops"),
            ),
            (
                "ops_before_money",
                "Person <a@example.com>",
                "shipped invoice",
                "INBOX",
                ("ops", 0, 0, "p60_ops"),
            ),
            (
                "money_subj_but_promo",
                "Person <a@example.com>",
                "invoice 15% off today",
                "INBOX",
                ("bulk", 1, 0, "money_subj_but_promo"),
            ),
            (
                "money_addr_but_promo",
                "Billing <statements@example.com>",
                "15% off ends tomorrow",
                "INBOX",
                ("bulk", 1, 0, "money_subj_but_promo"),
            ),
            (
                "p20_money",
                "Person <a@example.com>",
                "your bill is ready",
                "INBOX",
                ("money", 0, 0, "p20_money"),
            ),
            (
                "p20_money_addr",
                "Billing <statements@example.com>",
                "hello",
                "Drafts",
                ("money", 0, 0, "p20_money"),
            ),
            (
                "junk_money_not_folder_junk",
                "Person <a@example.com>",
                "invoice",
                "Junk",
                ("money", 0, 0, "p20_money"),
            ),
            (
                "p70_bulk",
                "Person <a@example.com>",
                "please unsubscribe",
                "INBOX",
                ("bulk", 1, 0, "p70_bulk"),
            ),
            (
                "pcn_examplekeyword",
                "Person <a@example.com>",
                "pcn - examplekeyword update",
                "INBOX",
                ("people", 0, 0, "pcn_examplekeyword"),
            ),
            (
                "pcn_before_folder_junk",
                "Person <a@example.com>",
                "pcn - examplekeyword",
                "Junk",
                ("people", 0, 0, "pcn_examplekeyword"),
            ),
            (
                "pcn_prefix_without_keyword",
                "Person <a@example.com>",
                "pcn - other topic",
                "INBOX",
                ("unknown", 0, 0, "p90_unknown"),
            ),
            (
                "keyword_without_pcn_prefix",
                "Person <a@example.com>",
                "see examplekeyword",
                "INBOX",
                ("unknown", 0, 0, "p90_unknown"),
            ),
            (
                "pcn_prefix_is_case_insensitive",
                "Person <a@example.com>",
                "PCN - examplekeyword",
                "INBOX",
                ("people", 0, 0, "pcn_examplekeyword"),
            ),
            (
                "folder_junk",
                "Person <a@example.com>",
                "hello there",
                "Junk",
                ("bulk", 1, 0, "folder_junk"),
            ),
            (
                "p90_unknown",
                "Person <a@example.com>",
                "hello there",
                "INBOX",
                ("unknown", 0, 0, "p90_unknown"),
            ),
        )
        seen = set()
        for name, frm, subj, folder, expected in cases:
            with self.subTest(name=name):
                got = classify.classify(frm, subj, folder, self.rules)
                self.assertEqual(got, expected)
                seen.add(got[3])
        self.assertTrue(REQUIRED_RULES <= seen)

    def test_pcn_prefix_matches_any_case(self):
        r, count = classify.parse_rules({"pcn_keywords": ["death"]})
        self.assertEqual(count, 1)
        expected = ("people", 0, 0, "pcn_death")
        for subject in ("PCN - Death notice", "pcn - death notice", "Pcn- death"):
            with self.subTest(subject=subject):
                self.assertEqual(
                    classify.classify("a@example.com", subject, "INBOX", rules=r),
                    expected,
                )
        self.assertEqual(
            classify.classify("a@example.com", "PCN - Birthday", "INBOX", rules=r),
            ("unknown", 0, 0, "p90_unknown"),
        )


class SqliteFixtureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "mailroom-copy.sqlite"
        conn = sqlite3.connect(self.db)
        conn.execute(
            """CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                folder TEXT,
                date_utc TEXT,
                from_addr TEXT,
                subject TEXT,
                lane TEXT,
                urgent INTEGER DEFAULT 0,
                junk INTEGER DEFAULT 0
            )"""
        )
        conn.commit()
        conn.close()
        self._saved_rules = os.environ.pop("MAILROOM_CLASSIFY_RULES", None)

    def tearDown(self):
        if self._saved_rules is None:
            os.environ.pop("MAILROOM_CLASSIFY_RULES", None)
        else:
            os.environ["MAILROOM_CLASSIFY_RULES"] = self._saved_rules
        self._tmp.cleanup()

    def _insert(self, row_id, folder, subject, lane=None, source="imap-live", urgent=0, frm=None):
        if frm is None:
            frm = "Person <a@example.com>"
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO messages (id, source, folder, date_utc, from_addr, subject, lane, urgent, junk) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            (row_id, source, folder, "2026-01-01T00:00:%02dZ" % (len(row_id) % 60), frm, subject, lane, urgent),
        )
        conn.commit()
        conn.close()

    def _rows(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM messages ORDER BY id")]
        conn.close()
        return rows

    def _audits(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute("SELECT * FROM audit ORDER BY message_id")]
        except sqlite3.OperationalError:
            rows = []
        conn.close()
        return rows

    def _run(self, extra=None, rules=None):
        argv = ["--db", str(self.db)]
        if extra:
            argv.extend(extra)
        if rules is not None:
            os.environ["MAILROOM_CLASSIFY_RULES"] = str(rules)
        else:
            os.environ.pop("MAILROOM_CLASSIFY_RULES", None)
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = classify.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_null_lanes_in_every_folder_non_null_untouched(self):
        self._insert("inb", "INBOX", "Your verification code")
        self._insert("arc", "Archive", "hello there")
        self._insert("drf", "Drafts", "your bill is ready")
        self._insert("prj", "Projects", "please unsubscribe")
        self._insert("news", "Newsletters", "hello there")
        self._insert("junk", "Junk", "hello there")
        self._insert("del", "Deleted Messages", "hello there")
        self._insert("keep", "INBOX", "please unsubscribe", lane="people", urgent=1)
        self._insert("jsonl", "Archive", "invoice", source="jsonl")
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertIn("db_mode=copy", err)
        self.assertIn("opened_db=mailroom-copy.sqlite", out)
        self.assertNotIn(str(self.db), out)
        self.assertIn("classify_rules=none", out)
        self.assertIn("mode null_only", out)
        self.assertNotIn("verification code", out)
        self.assertNotIn("@", out)
        self.assertNotIn("example.com", out)
        by_id = {row["id"]: row for row in self._rows()}
        self.assertEqual(by_id["inb"]["lane"], "auth")
        self.assertEqual(by_id["inb"]["urgent"], 0)
        self.assertEqual(by_id["arc"]["lane"], "unknown")
        self.assertEqual(by_id["drf"]["lane"], "money")
        self.assertEqual(by_id["prj"]["lane"], "bulk")
        self.assertEqual(by_id["news"]["lane"], "bulk")
        self.assertEqual(by_id["news"]["junk"], 1)
        self.assertEqual(by_id["junk"]["lane"], "bulk")
        self.assertEqual(by_id["del"]["lane"], "unknown")
        self.assertEqual(by_id["keep"]["lane"], "people")
        self.assertEqual(by_id["keep"]["urgent"], 1)
        self.assertIsNone(by_id["jsonl"]["lane"])
        audits = {row["message_id"]: json.loads(row["detail"]) for row in self._audits()}
        self.assertEqual(audits["inb"]["rule"], "p10_auth")
        self.assertEqual(audits["news"]["rule"], "folder_newsletters")
        self.assertEqual(audits["prj"]["rule"], "p70_bulk")
        self.assertNotIn("keep", audits)
        self.assertNotIn("jsonl", audits)
        self.assertIn("folder=Archive n=1 unknown=1", out)
        self.assertIn("folder=Projects n=1 bulk=1", out)
        self.assertIn("folder=INBOX n=1 auth=1", out)
        rc2, out2, err2 = self._run()
        self.assertEqual(rc2, 0, err2)
        self.assertIn("counts {} n=0", out2)
        self.assertEqual(len(self._audits()), 7)

    def test_folder_flag_narrows_to_one_folder(self):
        self._insert("inb", "INBOX", "hello there")
        self._insert("arc", "Archive", "hello there")
        rc, out, err = self._run(extra=["--folder", "Archive"])
        self.assertEqual(rc, 0, err)
        by_id = {row["id"]: row for row in self._rows()}
        self.assertIsNone(by_id["inb"]["lane"])
        self.assertEqual(by_id["arc"]["lane"], "unknown")
        self.assertIn("folder=Archive n=1", out)
        self.assertNotIn("folder=INBOX", out)
        self.assertIn("mode null_only", out)

    def test_sent_urgent_suppressed_other_folders_kept(self):
        self._insert("sentm", "Sent Messages", "safety recall")
        self._insert("sent", "Sent", "lawsuit filed")
        self._insert("inbox", "INBOX", "safety recall")
        self._insert("plain", "Sent Messages", "hello there")
        self._insert("kept", "Sent Messages", "safety recall", lane="people", urgent=1)
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        by_id = {row["id"]: row for row in self._rows()}
        self.assertEqual(by_id["sentm"]["lane"], "ops")
        self.assertEqual(by_id["sentm"]["urgent"], 0)
        self.assertEqual(by_id["sent"]["lane"], "people")
        self.assertEqual(by_id["sent"]["urgent"], 0)
        self.assertEqual(by_id["inbox"]["urgent"], 1)
        self.assertEqual(by_id["inbox"]["lane"], "ops")
        self.assertEqual(by_id["plain"]["urgent"], 0)
        self.assertEqual(by_id["plain"]["lane"], "unknown")
        self.assertEqual(by_id["kept"]["urgent"], 1)
        self.assertEqual(by_id["kept"]["lane"], "people")
        audits = {row["message_id"]: json.loads(row["detail"]) for row in self._audits()}
        self.assertEqual(audits["sentm"]["sent_urgent_suppressed"], True)
        self.assertEqual(audits["sentm"]["urgent"], 0)
        self.assertEqual(audits["sentm"]["rule"], "p60_ops")
        self.assertEqual(audits["sent"]["sent_urgent_suppressed"], True)
        self.assertEqual(audits["sent"]["rule"], "lawsuit_people")
        self.assertNotIn("sent_urgent_suppressed", audits["inbox"])
        self.assertNotIn("sent_urgent_suppressed", audits["plain"])
        self.assertNotIn("kept", audits)
        self.assertIn("urgent 1", out)
        self.assertNotIn("safety recall", out)
        self.assertNotIn("lawsuit", out)

    def test_all_without_folder_refuses_and_writes_nothing(self):
        self._insert("inb", "INBOX", "hello there")
        before = self._rows()
        rc, out, err = self._run(extra=["--all"])
        self.assertEqual(rc, 2)
        self.assertIn("--all requires an explicit --folder", err)
        self.assertNotIn("opened_db=", out)
        self.assertNotIn("db_mode=copy", err)
        self.assertEqual(self._rows(), before)
        self.assertEqual(self._audits(), [])

    def test_all_with_folder_reclassifies_only_that_folder(self):
        self._insert("inb", "INBOX", "please unsubscribe", lane="people")
        self._insert("arc", "Archive", "please unsubscribe", lane="people")
        rc, out, err = self._run(extra=["--all", "--folder", "INBOX"])
        self.assertEqual(rc, 0, err)
        by_id = {row["id"]: row for row in self._rows()}
        self.assertEqual(by_id["inb"]["lane"], "bulk")
        self.assertEqual(by_id["arc"]["lane"], "people")
        self.assertIn("mode all", out)

    def test_non_imap_live_source_refuses_and_writes_nothing(self):
        self._insert("inb", "INBOX", "hello there")
        before = self._rows()
        rc, out, err = self._run(extra=["--source", "jsonl"])
        self.assertEqual(rc, 2)
        self.assertIn("imap-live", err)
        self.assertIn("refuse --source", err)
        self.assertNotIn("opened_db=", out)
        self.assertEqual(self._rows(), before)
        self.assertEqual(self._audits(), [])

    def test_rules_missing_loaded_and_malformed(self):
        self._insert("auth", "INBOX", "Your verification code", frm="Person <a@example.com>")
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertIn("classify_rules=none", out)
        self.assertEqual(self._rows()[0]["lane"], "auth")

        rules = _write_rules(
            self.tmp,
            {"marketing_domains": ["promo.example.com"], "pcn_keywords": ["examplekeyword"]},
        )
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM audit")
        conn.commit()
        conn.close()
        self._insert(
            "ad",
            "INBOX",
            "hello there",
            frm="Ads <x@promo.example.com>",
        )
        rc, out, err = self._run(rules=rules)
        self.assertEqual(rc, 0, err)
        self.assertIn("classify_rules=loaded n=2", out)
        self.assertNotIn("promo.example.com", out)
        self.assertNotIn("examplekeyword", out)
        self.assertNotIn("@", out)
        self.assertEqual(self._rows()[0]["lane"], "bulk")
        detail = json.loads(self._audits()[0]["detail"])
        self.assertEqual(detail["rule"], "marketing_domain")

        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE messages SET lane=NULL, junk=0, urgent=0")
        conn.commit()
        conn.close()
        before = self._rows()
        audit_n = len(self._audits())
        bad = self.tmp / "bad.json"
        bad.write_text("{", encoding="utf-8")
        rc, out, err = self._run(rules=bad)
        self.assertEqual(rc, 2)
        self.assertIn("classify_rules: invalid JSON", err)
        self.assertNotIn("{", err)
        self.assertEqual(self._rows(), before)
        self.assertEqual(len(self._audits()), audit_n)

        bad.write_text(
            json.dumps({"family_addrs": "sentinel-value-xyz@example.com"}),
            encoding="utf-8",
        )
        rc, out, err = self._run(rules=bad)
        self.assertEqual(rc, 2)
        self.assertIn("family_addrs must be a list of strings", err)
        self.assertNotIn("sentinel-value-xyz", err)
        self.assertNotIn("sentinel-value-xyz", out)
        self.assertEqual(self._rows(), before)

        bad.write_text(json.dumps({"nope": ["x"]}), encoding="utf-8")
        rc, _out, err = self._run(rules=bad)
        self.assertEqual(rc, 2)
        self.assertIn("unknown key nope", err)
        self.assertEqual(self._rows(), before)

        bad.write_text(json.dumps({"property_keep": ["("]}), encoding="utf-8")
        rc, out, err = self._run(rules=bad)
        self.assertEqual(rc, 2)
        self.assertIn("property_keep[0] is not a valid regex", err)
        self.assertNotIn("(", err)
        self.assertEqual(self._rows(), before)
        self.assertEqual(len(self._audits()), audit_n)

    def test_empty_rules_object_counts_zero(self):
        self._insert("inb", "INBOX", "hello there")
        rules = _write_rules(self.tmp, {})
        rc, out, err = self._run(rules=rules)
        self.assertEqual(rc, 0, err)
        self.assertIn("classify_rules=loaded n=0", out)
        self.assertEqual(self._rows()[0]["lane"], "unknown")

    def test_destructive_verb_refuses_before_write(self):
        self._insert("inb", "INBOX", "hello there")
        before = self._rows()
        rc, out, err = self._run(extra=["--expunge"])
        self.assertEqual(rc, 2)
        self.assertIn(REFUSE_PREFIX, err)
        self.assertNotIn("opened_db=", out)
        self.assertEqual(self._rows(), before)


class RulesFileContractTests(unittest.TestCase):
    def test_example_file_loads_with_fake_values_only(self):
        rules, status, count = classify.load_rules(EXAMPLE_RULES)
        self.assertEqual(status, "loaded")
        self.assertEqual(count, 7)
        self.assertIn("family@example.com", rules.family_addrs)
        self.assertIn("promo.example.com", rules.marketing_domains)
        text = EXAMPLE_RULES.read_text(encoding="utf-8")
        self.assertNotIn("/Users/", text)
        self.assertNotIn("@me.com", text)
        self.assertNotIn("@icloud.com", text)
        for match in re.finditer(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
            self.assertTrue(match.group(0).endswith("@example.com"))

    def test_missing_path_is_none_status(self):
        missing = Path(tempfile.gettempdir()) / "classify-rules-missing-does-not-exist.json"
        if missing.exists():
            missing.unlink()
        rules, status, count = classify.load_rules(missing)
        self.assertEqual(status, "none")
        self.assertEqual(count, 0)
        self.assertEqual(rules.family_addrs, frozenset())
        got = classify.classify("Person <a@example.com>", "Your verification code", "INBOX", rules)
        self.assertEqual(got, ("auth", 0, 0, "p10_auth"))

    def test_local_rules_filename_is_gitignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("classify_rules.local.json", text)
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "classify_rules.local.json"],
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(ignored.returncode, 0)
        example = subprocess.run(
            ["git", "check-ignore", "-q", "docs/classify_rules.example.json"],
            cwd=str(ROOT),
            check=False,
        )
        self.assertNotEqual(example.returncode, 0)

    def test_folder_comparisons_stay_generic(self):
        src = Path(classify.__file__).read_text(encoding="utf-8")
        for name in re.findall(r'folder == "([^"]*)"', src):
            self.assertIn(name, classify.GENERIC_FOLDERS)
        self.assertTrue(set(classify.SENT_FOLDERS) <= set(classify.GENERIC_FOLDERS))
        self.assertEqual(
            set(classify.GENERIC_FOLDERS),
            {
                "INBOX",
                "Junk",
                "Newsletters",
                "Sent Messages",
                "Sent",
                "Deleted Messages",
                "Drafts",
                "Archive",
            },
        )

    def test_classifier_source_has_no_network_hooks(self):
        text = Path(classify.__file__).read_text(encoding="utf-8")
        self.assertNotIn("11434", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("socket", text)
        self.assertIn("fail closed", text)
        self.assertIn("bind_copy_db", text)
        self.assertIn("child_main", text)


class DailyPlanTests(unittest.TestCase):
    def test_classify_argv_has_no_all_and_no_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "MailArchive"
            scripts = archive / "scripts"
            scripts.mkdir(parents=True)
            for name in (
                "imap_newmail.py",
                "imap_tombstone.py",
                "imap_fetch_bodies_fts.py",
                "classify.py",
                "notify_bills.py",
                "embed_backfill.py",
            ):
                (scripts / name).write_text("# fake %s\n" % name, encoding="utf-8")
            venv_py = archive / ".venv" / "bin" / "python"
            venv_py.parent.mkdir(parents=True)
            venv_py.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            venv_py.chmod(0o755)
            db = archive / "mailroom-copy.sqlite"
            with patch.dict(os.environ, {"MAILROOM_VENV_PY": str(venv_py)}, clear=False):
                items = daily.build_plan(archive, scripts, db)
        classify_items = [item for item in items if item.step == "classify"]
        self.assertEqual(len(classify_items), 1)
        argv = classify_items[0].argv
        self.assertNotIn("--all", argv)
        self.assertNotIn("--folder", argv)
        self.assertIn("--db", argv)
        self.assertIn(str(db), argv)


if __name__ == "__main__":
    unittest.main()
