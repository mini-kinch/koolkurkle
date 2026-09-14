#!/usr/bin/env python3
"""KOO-50: frozen icloud_mail_all.jsonl immutability. No dump rewrite."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refuse_frozen_jsonl as rfj  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
FROZEN = "icloud_mail_all.jsonl"


class FrozenJsonlImmutabilityTests(unittest.TestCase):
    def test_identifies_frozen_basename_only(self):
        self.assertTrue(rfj.is_frozen_jsonl(FROZEN))
        self.assertTrue(rfj.is_frozen_jsonl("/tmp/%s" % FROZEN))
        self.assertTrue(rfj.is_frozen_jsonl(Path("dumps") / FROZEN))
        self.assertFalse(rfj.is_frozen_jsonl("icloud_mail_new.jsonl"))
        self.assertFalse(rfj.is_frozen_jsonl("/tmp/other.jsonl"))
        self.assertFalse(rfj.is_frozen_jsonl(None))

    def test_refuse_rewrite_and_reconcile_paths(self):
        for verb in ("rewrite", "reconcile", "overwrite"):
            with self.assertRaises(rfj.FrozenJsonlRefuse) as ctx:
                rfj.refuse_frozen_jsonl_touch("/tmp/%s" % FROZEN, verb)
            msg = str(ctx.exception)
            self.assertIn(rfj.REFUSE_PREFIX, msg)
            self.assertIn(FROZEN, msg)
            self.assertIn("immutable", msg.lower())
            self.assertIn("No dump rewrite", msg)
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, msg)
        rfj.refuse_frozen_jsonl_touch("/tmp/other.jsonl", "rewrite")

    def test_cli_refuses_rewrite_reconcile_argv(self):
        for argv in (
            ["--rewrite", "/tmp/%s" % FROZEN],
            ["--reconcile", FROZEN],
            ["rewrite", FROZEN],
            ["--dump-rewrite", FROZEN],
        ):
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                rc = rfj.main(argv)
            self.assertEqual(rc, 2, msg=argv)
            self.assertIn(rfj.REFUSE_PREFIX, err.getvalue())
            self.assertNotIn("ok:", out.getvalue())

    def test_cli_allows_inspect_without_mutation_verb(self):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = rfj.main(["--show", FROZEN])
        self.assertEqual(rc, 0)
        self.assertIn("ok:", out.getvalue())
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(rfj.main(["invoice.jsonl", "--rewrite"]), 0)

    def test_source_and_docs_name_the_frozen_file(self):
        src = (SCRIPTS / "refuse_frozen_jsonl.py").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "soft-delete.md").read_text(encoding="utf-8")
        self.assertIn(FROZEN, src)
        self.assertIn(FROZEN, docs)
        self.assertIn("No dump rewrite", docs)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, src)
            self.assertNotIn(needle, docs)


if __name__ == "__main__":
    unittest.main()
