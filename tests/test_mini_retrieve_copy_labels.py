#!/usr/bin/env python3
"""KOO-59 Mini retrieve db_mode=copy + copy_age. Never imply live/SoR."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _py_compat import import_ask_mail  # noqa: E402

ask_mail = import_ask_mail()
import retrieve_db_labels as labels  # noqa: E402
import semantic_search as ss  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class CopyLabelUnitTests(unittest.TestCase):
    def test_copy_basename_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mailroom-copy.sqlite"
            path.write_bytes(b"sqlite")
            now = datetime(2026, 9, 14, tzinfo=timezone.utc)
            out = labels.retrieve_db_labels(path, now=now)
            self.assertEqual(out["db_mode"], "copy")
            self.assertIsInstance(out["copy_age"], int)
            labels.refuse_copy_implies_live_or_sor(out)

    def test_mini_sor_basename_refused(self):
        with self.assertRaises(labels.RetrieveLabelRefuse):
            labels.retrieve_db_labels(
                Path("/tmp/mailroom.sqlite"), host="Mini"
            )

    def test_copy_must_not_imply_live_or_sor(self):
        with self.assertRaises(labels.RetrieveLabelRefuse):
            labels.refuse_copy_implies_live_or_sor({"db_mode": "copy", "note": "live"})


class AskAndSearchLabelTests(unittest.TestCase):
    def test_ask_mail_labels_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            db.write_bytes(b"x")
            result = ask_mail.ask(
                "invoice",
                db=db,
                generate=False,
                retrieve_fn=lambda *_a, **_k: [
                    {
                        "message_id": "m4",
                        "rrf": 0.02,
                        "rerank": None,
                        "subject": "Invoice",
                    }
                ],
            )
            self.assertEqual(result["db_mode"], "copy")
            self.assertIn("copy_age", result)
            self.assertNotEqual(result["db_mode"], "live")
            self.assertNotEqual(result["db_mode"], "sor")

    def test_semantic_search_meta_labels_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mailroom-copy.sqlite"
            db.write_bytes(b"x")
            fake = [
                {
                    "message_id": "m1",
                    "chunk_id": None,
                    "thread_id": "t1",
                    "date": "2026-09-01T00:00:00Z",
                    "from": "a@x",
                    "subject": "SDGE bill",
                    "snippet": "due",
                    "fts_rank": 1,
                    "vec_rank": 2,
                    "rrf": 0.017,
                    "rerank": None,
                    "lane": "money",
                }
            ]
            import io

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(ss, "retrieve", return_value=fake):
                with mock.patch("sys.stdout", stdout):
                    with mock.patch("sys.stderr", stderr):
                        rc = ss.main(["--json", "--db", str(db), "SDGE bill"])
            self.assertEqual(rc, 0)
            self.assertIn("db_mode", stderr.getvalue())
            self.assertIn("copy", stderr.getvalue())

    def test_docs_require_copy_labels(self):
        for path in (
            ROOT / "docs" / "ask_mail.md",
            ROOT / "docs" / "ops-terminal.md",
            ROOT / "README.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("db_mode=copy", text, msg=path.name)
            self.assertIn("copy_age", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


if __name__ == "__main__":
    unittest.main()
