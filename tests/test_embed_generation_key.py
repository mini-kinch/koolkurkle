#!/usr/bin/env python3
"""KOO-58 embed generation key + refuse embedding_meta mismatch."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embed_generation_key as gk  # noqa: E402
import embed_lib as el  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class GenerationKeyTests(unittest.TestCase):
    def test_locked_v1_matches_embed_lib(self):
        key = gk.locked_v1_key()
        self.assertEqual(
            tuple(gk.GENERATION_KEY_FIELDS),
            (
                "model_tag",
                "embed_runtime",
                "native_dim",
                "store_dim",
                "instruction_prefix",
            ),
        )
        self.assertEqual(key["model_tag"], "qwen3-embedding:8b")
        self.assertEqual(key["embed_runtime"], "ollama")
        self.assertEqual(key["native_dim"], 4096)
        self.assertEqual(key["store_dim"], 1024)
        self.assertEqual(key["instruction_prefix"], el.QUERY_INSTRUCT)
        self.assertEqual(el.NATIVE_DIMS, 4096)
        self.assertEqual(el.DEFAULT_DIMS, 1024)

    def test_refuse_mismatch(self):
        locked = gk.locked_v1_key()
        other = gk.embed_generation_key(store_dim=256)
        with self.assertRaises(gk.GenerationKeyRefuse):
            gk.refuse_generation_mismatch(None, other)
        gk.refuse_generation_mismatch(None, locked)
        with self.assertRaises(gk.GenerationKeyRefuse) as ctx:
            gk.refuse_generation_mismatch(locked, other)
        self.assertIn("mismatches embedding_meta", str(ctx.exception))

    def test_upsert_refuses_store_dim_mismatch(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE embedding_meta (
              message_id TEXT NOT NULL,
              model TEXT NOT NULL,
              model_version TEXT NOT NULL DEFAULT 'v1',
              created_at TEXT NOT NULL,
              text_hash TEXT NOT NULL,
              char_count INTEGER NOT NULL DEFAULT 0,
              dims INTEGER NOT NULL DEFAULT 1024,
              PRIMARY KEY (message_id, model, model_version)
            );
            CREATE TABLE message_embeddings (
              message_id TEXT PRIMARY KEY, embedding BLOB
            );
            """
        )
        vec = [0.0] * 1023 + [1.0]
        el.upsert_embedding(
            conn,
            message_id="m1",
            vector=vec,
            model="qwen3-embedding:8b",
            model_version="v1",
            text_hash="aa",
            char_count=1,
            dims=1024,
        )
        with self.assertRaises(el.EmbedError):
            el.upsert_embedding(
                conn,
                message_id="m2",
                vector=[0.0, 1.0],
                model="qwen3-embedding:8b",
                model_version="v1",
                text_hash="bb",
                char_count=1,
                dims=2,
            )

    def test_docs_align_4096_1024(self):
        for path in (
            ROOT / "docs" / "embed-generation-key.md",
            ROOT / "docs" / "MAILROOM.md",
            ROOT / "docs" / "embed-backfill.md",
            ROOT / "README.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("4096", text, msg=path.name)
            self.assertIn("1024", text, msg=path.name)
            self.assertIn("model_tag", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


if __name__ == "__main__":
    unittest.main()
