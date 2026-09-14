#!/usr/bin/env python3
"""KOO-56 Mini MLX embedder path design + holdout fail-closed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mlx_embed_holdout as hold  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
DOC = ROOT / "docs" / "mini-mlx-embedder.md"
OPS = ROOT / "docs" / "ops-terminal.md"
FIXTURE = ROOT / "tests" / "fixtures" / "mlx_holdout_frozen_ids.json"


def _unit(n: int, flip: float = 0.0) -> list[float]:
    vec = [0.0] * n
    vec[-1] = 1.0 - flip
    vec[0] = flip
    return vec


class MiniMlxHoldoutTests(unittest.TestCase):
    def test_docs_require_holdout_and_ram_law(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("Holdout required before cutover", text)
        self.assertIn("Fail-closed if miss", text)
        self.assertIn("message_ids", text)
        self.assertIn("cosine-agreement", text)
        self.assertIn("no co-reside 8B embed + 35B generate", text)
        self.assertIn("No mid-index switch", text)
        self.assertIn("qwen3-embedding:8b", text)
        self.assertIn("1024", text)
        self.assertIn("new `model_version`", text)
        self.assertIn("Docs PR Ready ≠", text)
        self.assertIn("ops-terminal.md", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("Mini MLX embedder path", ops)
        self.assertIn("holdout", ops.lower())

    def test_holdout_pass_and_fail_closed(self):
        ids = hold.load_frozen_ids(FIXTURE)
        self.assertEqual(len(ids), 32)
        pairs = {
            mid: {"ollama": _unit(8), "mlx": _unit(8)}
            for mid in ids
        }
        result = hold.evaluate_holdout(ids, pairs, n_required=32, threshold=0.97)
        self.assertTrue(result["ok"])
        missing = dict(pairs)
        missing.pop(ids[0])
        with self.assertRaises(hold.MlxHoldoutRefuse) as ctx:
            hold.evaluate_holdout(ids, missing, n_required=32)
        self.assertIn("holdout miss", str(ctx.exception))
        bad = dict(pairs)
        bad[ids[0]] = {"ollama": _unit(8), "mlx": _unit(8, flip=1.0)}
        with self.assertRaises(hold.MlxHoldoutRefuse) as ctx:
            hold.evaluate_holdout(ids, bad, n_required=32, threshold=0.97)
        self.assertIn("fail-closed", str(ctx.exception))

    def test_same_family_or_new_generation(self):
        ids = hold.load_frozen_ids(FIXTURE)
        pairs = {mid: {"ollama": _unit(4), "mlx": _unit(4)} for mid in ids}
        with self.assertRaises(hold.MlxHoldoutRefuse):
            hold.evaluate_holdout(
                ids,
                pairs,
                n_required=32,
                model_tag="other-embed:7b",
                same_family=True,
                new_model_version=False,
            )
        ok = hold.evaluate_holdout(
            ids,
            pairs,
            n_required=32,
            model_tag="other-embed:7b",
            same_family=True,
            new_model_version=True,
        )
        self.assertTrue(ok["new_model_version"])


if __name__ == "__main__":
    unittest.main()
