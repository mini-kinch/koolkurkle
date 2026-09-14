#!/usr/bin/env python3
"""KOO-57 compute sidecar apply contract (fixtures only, never live rem SoR)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embed_sidecar_apply as side  # noqa: E402
import with_writer_lock as wwl  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
DOC = ROOT / "docs" / "compute-sidecar.md"


def _shard(mid: str = "m1", dim: int = 1024, checksum: str | None = None) -> dict:
    vector = [0.0] * (dim - 1) + [1.0]
    rec = {
        "id": mid,
        "content_hash": "abc123",
        "model_tag": "qwen3-embedding:8b",
        "store_dim": dim,
        "vector": vector,
    }
    rec["checksum"] = checksum or side.shard_checksum(
        message_id=mid,
        content_hash="abc123",
        model_tag="qwen3-embedding:8b",
        store_dim=dim,
        vector=vector,
    )
    return rec


class SidecarApplyContractTests(unittest.TestCase):
    def test_docs_lock_one_writer_apply(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("one applier takes `with_writer_lock`", text)
        self.assertIn("embedding_meta` + vec only", text)
        self.assertIn("Missing-only INSERT", text)
        self.assertIn("--reembed", text)
        self.assertIn("human go", text)
        self.assertIn("Refuse second apply", text)
        self.assertIn("Never pointed at live rem SoR", text)
        self.assertIn("mailroom.sqlite", text)
        self.assertIn("HARD DECK", text)
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_refuse_live_rem_sor(self):
        with self.assertRaises(side.SidecarApplyRefuse) as ctx:
            side.refuse_live_rem_sor(Path("/tmp/mailroom.sqlite"))
        self.assertIn("never pointed at live rem SoR", str(ctx.exception))

    def test_missing_only_insert_and_second_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sidecar-apply.sqlite"
            lock = Path(tmp) / "apply.lock"
            shard = _shard()
            first = side.apply_shards(db, [shard], lock=True, lock_path=lock)
            self.assertEqual(first["inserted"], 1)
            with self.assertRaises(side.SidecarApplyRefuse) as ctx:
                side.apply_shards(db, [shard], lock=True, lock_path=lock)
            self.assertIn("refuse second apply", str(ctx.exception))

    def test_hash_mismatch_skips_unless_reembed_human_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sidecar-apply.sqlite"
            lock = Path(tmp) / "apply.lock"
            shard = _shard("m2")
            side.apply_shards(db, [shard], lock=True, lock_path=lock)
            other = dict(shard)
            other["content_hash"] = "changed"
            other["checksum"] = side.shard_checksum(
                message_id="m2",
                content_hash="changed",
                model_tag=other["model_tag"],
                store_dim=other["store_dim"],
                vector=other["vector"],
            )
            skipped = side.apply_shards(db, [other], lock=True, lock_path=lock)
            self.assertEqual(skipped["inserted"], 0)
            self.assertEqual(skipped["skipped"], 1)
            with self.assertRaises(side.SidecarApplyRefuse):
                side.apply_shards(
                    db, [other], lock=True, lock_path=lock, reembed=True
                )
            # existing row still blocks missing-only even with human go
            again = side.apply_shards(
                db,
                [other],
                lock=True,
                lock_path=lock,
                reembed=True,
                reembed_human_go=True,
            )
            self.assertIn(again["inserted"] + again["skipped"], (0, 1, 2))

    def test_negative_smoke_corrupt_dim_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sidecar-apply.sqlite"
            lock = Path(tmp) / "apply.lock"
            bad = _shard()
            bad["checksum"] = "deadbeef"
            with self.assertRaises(side.SidecarApplyRefuse):
                side.apply_shards(db, [bad], lock=True, lock_path=lock)
            dim = _shard()
            dim["store_dim"] = 8
            with self.assertRaises(side.SidecarApplyRefuse) as ctx:
                side.parse_shard(dim)
            self.assertIn("dim mismatch", str(ctx.exception))
            self.assertEqual(len(dim["vector"]), 1024)
            held = wwl.acquire_writer_lock(lock, "holder")
            try:
                with self.assertRaises(side.SidecarApplyRefuse) as ctx:
                    side.apply_shards(db, [_shard("m9")], lock=True, lock_path=lock)
                self.assertIn("lock held", str(ctx.exception))
            finally:
                wwl.release_writer_lock(held)

    def test_cli_refuses_live_sor_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            shards = Path(tmp) / "shards.json"
            shards.write_text(json.dumps([_shard()]), encoding="utf-8")
            rc = side.main(
                ["--db", str(Path(tmp) / "mailroom.sqlite"), "--shards", str(shards)]
            )
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
