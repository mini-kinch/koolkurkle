#!/usr/bin/env python3
"""KOO-54: --embed-live-only flag. Temp DB only. No rem-legacy run."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embed_backfill as eb  # noqa: E402
import embed_lib as el  # noqa: E402
from test_embed_incremental import (  # noqa: E402
    fake_embed,
    incremental_conn,
    insert_meta,
    insert_msg,
)

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
EMBED = ROOT / "docs" / "embed-backfill.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
OPS = ROOT / "docs" / "ops-terminal.md"
ONEPAGER = ROOT / "docs" / "soft-delete.md"


class EmbedLiveOnlyFlagTests(unittest.TestCase):
    def test_parser_default_off(self):
        parser = eb.build_parser()
        option_strings = {
            opt
            for action in parser._actions
            for opt in (action.option_strings or ())
        }
        self.assertIn("--embed-live-only", option_strings)
        bare = parser.parse_args([])
        self.assertFalse(bare.embed_live_only)
        flagged = parser.parse_args(["--quote-strip", "--embed-live-only", "--dry-run"])
        self.assertTrue(flagged.embed_live_only)
        self.assertTrue(flagged.quote_strip)
        self.assertTrue(flagged.dry_run)

    def test_skips_tombstone_and_keeps_existing_embed(self):
        conn = incremental_conn()
        insert_msg(conn, "live-1")
        insert_msg(conn, "gone-1")
        conn.execute("UPDATE messages SET present_on_server = 1 WHERE id = 'live-1'")
        conn.execute("UPDATE messages SET present_on_server = 0 WHERE id = 'gone-1'")
        insert_meta(conn, "gone-1", quote_stripped=0, content_hash=None)
        conn.execute(
            "INSERT INTO message_embeddings(message_id, embedding) VALUES ('gone-1', X'00')"
        )
        conn.commit()
        rows = el.iter_incremental_candidates(conn, embed_live_only=True)
        self.assertEqual([r["id"] for r in rows], ["live-1"])
        counts = el.incremental_candidate_counts(conn, embed_live_only=True)
        self.assertEqual(counts["candidates"], 1)
        self.assertEqual(counts["skipped_tombstone"], 1)
        logs: list[str] = []
        out = el.backfill(
            conn,
            quote_strip=True,
            embed_live_only=True,
            embed_fn=fake_embed,
            log=logs.append,
        )
        self.assertEqual(out["embedded"], 1)
        self.assertEqual(out.get("skipped_tombstone"), 1)
        rem_vec = conn.execute(
            "SELECT embedding FROM message_embeddings WHERE message_id='gone-1'"
        ).fetchone()
        self.assertEqual(bytes(rem_vec["embedding"]), b"\x00")
        rem_meta = conn.execute(
            "SELECT quote_stripped, content_hash FROM embedding_meta WHERE message_id='gone-1'"
        ).fetchone()
        self.assertEqual(int(rem_meta["quote_stripped"] or 0), 0)
        self.assertIsNone(rem_meta["content_hash"])
        self.assertTrue(any("embed_live_only=1" in line for line in logs))
        with self.assertRaises(el.EmbedError) as ctx:
            el.upsert_embedding(
                conn,
                message_id="gone-1",
                vector=[0.0] * 1024,
                model=el.DEFAULT_MODEL,
                model_version=el.DEFAULT_MODEL_VERSION,
                text_hash="aa",
                char_count=1,
                embed_live_only=True,
            )
        self.assertIn("must not delete existing tombstone embeds", str(ctx.exception))
        rem_vec2 = conn.execute(
            "SELECT embedding FROM message_embeddings WHERE message_id='gone-1'"
        ).fetchone()
        self.assertEqual(bytes(rem_vec2["embedding"]), b"\x00")

    def test_default_still_includes_tombstone_candidates(self):
        conn = incremental_conn()
        insert_msg(conn, "gone-1")
        conn.execute("UPDATE messages SET present_on_server = 0 WHERE id = 'gone-1'")
        conn.commit()
        rows = el.iter_incremental_candidates(conn)
        self.assertEqual([r["id"] for r in rows], ["gone-1"])

    def test_docs_ship_flag_not_a_run(self):
        for path in (EMBED, MAILROOM, OPS, ONEPAGER, SCRIPTS / "embed_backfill.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("--embed-live-only", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        embed = EMBED.read_text(encoding="utf-8")
        self.assertIn("must not delete existing tombstone embeds", embed)
        self.assertIn("Shipping the flag ≠ starting a job", embed)
        self.assertIn("Guard ≠ run", embed)
        self.assertIn("rem-legacy", embed)
        self.assertNotIn("--embed-live-only --skip-auth --quote-strip --lock", embed)
        daily = (ROOT / "scripts" / "README.mailroom-daily.md").read_text(encoding="utf-8")
        self.assertIn("--skip-auth --quote-strip --lock", daily)
        self.assertNotIn("embed_backfill.py --skip-auth --quote-strip --lock --embed-live-only", daily)


if __name__ == "__main__":
    unittest.main()
