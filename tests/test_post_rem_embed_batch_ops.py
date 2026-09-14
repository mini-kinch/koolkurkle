#!/usr/bin/env python3
"""KOO-55 post-rem same-writer embed batch bump (docs + fail-closed).

Docs/tests/config only. AFTER EXIT 0. Forbid mid-job bump.
First bump 32, then 64 if stable — not 256 first.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embed_lib as el  # noqa: E402
import post_rem_embed_batch as prb  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
DOCS = (
    ROOT / "docs" / "post-rem-embed-batch.md",
    ROOT / "docs" / "embed-backfill.md",
    ROOT / "docs" / "ops-terminal.md",
    ROOT / "docs" / "MAILROOM.md",
    ROOT / "README.md",
)


class PostRemBatchConfigTests(unittest.TestCase):
    def test_next_run_default_is_32_not_256(self):
        self.assertEqual(el.DEFAULT_BATCH_SIZE, 8)
        self.assertEqual(prb.REM_LEGACY_BATCH_SIZE, 8)
        self.assertEqual(prb.POST_REM_NEXT_RUN_BATCH_SIZE, 32)
        self.assertEqual(prb.POST_REM_NEXT_RUN_BATCH_IF_STABLE, 64)
        self.assertEqual(prb.POST_REM_HOLD_256, 256)
        self.assertEqual(el.POST_REM_NEXT_RUN_BATCH_SIZE, 32)
        self.assertTrue(prb.COMMIT_PER_BATCH)
        self.assertEqual(prb.POST_REM_MODEL_TAG, "qwen3-embedding:8b")
        self.assertEqual(prb.POST_REM_STORE_DIM, 1024)
        self.assertEqual(prb.POST_REM_NATIVE_DIM, 4096)

    def test_forbid_mid_job_and_pre_exit(self):
        with self.assertRaises(prb.PostRemBatchRefuse) as ctx:
            prb.validate_post_rem_batch_size(32, mid_job=True, rem_exit_0=True, human_go=True)
        self.assertIn("forbid mid-job", str(ctx.exception))
        with self.assertRaises(prb.PostRemBatchRefuse) as ctx:
            prb.validate_post_rem_batch_size(32, rem_exit_0=False, human_go=True)
        self.assertIn("AFTER EXIT 0", str(ctx.exception))
        with self.assertRaises(prb.PostRemBatchRefuse) as ctx:
            prb.validate_post_rem_batch_size(32, rem_exit_0=True, human_go=False)
        self.assertIn("human go", str(ctx.exception))

    def test_256_is_not_first_bump(self):
        with self.assertRaises(prb.PostRemBatchRefuse) as ctx:
            prb.validate_post_rem_batch_size(
                256, rem_exit_0=True, human_go=True
            )
        self.assertIn("256 is not the first bump", str(ctx.exception))

    def test_64_only_if_32_stable(self):
        with self.assertRaises(prb.PostRemBatchRefuse):
            prb.validate_post_rem_batch_size(
                64, rem_exit_0=True, human_go=True
            )
        self.assertEqual(
            prb.validate_post_rem_batch_size(
                32, rem_exit_0=True, human_go=True
            ),
            32,
        )
        self.assertEqual(
            prb.validate_post_rem_batch_size(
                64,
                rem_exit_0=True,
                human_go=True,
                first_bump_done=True,
                first_bump_stable=True,
            ),
            64,
        )

    def test_docs_lock_after_exit_and_forbid_mid_job(self):
        for path in DOCS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("32", text, msg=path.name)
            self.assertIn("EXIT 0", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        bump = DOCS[0].read_text(encoding="utf-8")
        self.assertIn("Forbid mid-job bump", bump)
        self.assertIn("256 is not the first bump", bump)
        self.assertIn("qwen3-embedding:8b", bump)
        self.assertIn("1024", bump)
        self.assertIn("Commit per batch", bump)
        self.assertIn("Docs PR Ready ≠", bump)
        self.assertNotIn("mid-job hot-swap the live rem batch", bump.lower())


if __name__ == "__main__":
    unittest.main()
