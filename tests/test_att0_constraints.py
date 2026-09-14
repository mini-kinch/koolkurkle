#!/usr/bin/env python3
"""KOO-65..70 ATT-0 design contracts. Docs/tests/fail-closed/fixtures only.

No live IMAP. No live SoR writer. No ATT catalog/extract/embed/apply run.
Rem-legacy untouched. ZERO PII.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import att0_constraints as att0  # noqa: E402

ATT0 = ROOT / "docs" / "att0-constraints.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
ASK = ROOT / "docs" / "ask_mail.md"
EMBED = ROOT / "docs" / "embed-backfill.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
SOFT = ROOT / "docs" / "soft-delete.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
FIXTURE = ROOT / "tests" / "fixtures" / "att0_interface_proof.json"
ASK_MAIL_PY = ROOT / "scripts" / "ask_mail.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
CROSS_LINKS = (MAILROOM, ASK, EMBED, TOMBSTONE, SOFT)
OPERATORS = (OPS, README)


def _hay(text: str) -> str:
    return text.replace("/Users/<operator>/", "")


class Koo65Att0SchemaGenerationSkipTests(unittest.TestCase):
    def test_packet_lands_full_heavy_05_contract(self):
        text = ATT0.read_text(encoding="utf-8")
        self.assertIn("# ATT-0 — Attachment lane constraints (design docs)", text)
        self.assertIn("DESIGN ONLY", text)
        self.assertIn("20260914-05-attachment-search-design", text)
        self.assertIn("https://github.com/mini-kinch/koolkurkle", text)
        self.assertIn("ZERO", text)
        self.assertIn("**Not** ATT-1..8 implement", text)
        self.assertIn("qwen3-embedding:8b", text)
        self.assertIn("store dim **1024**", text)
        self.assertIn("Mixed dim = refuse", text)
        self.assertIn("`attachments`", text)
        self.assertIn("`attachment_extracts`", text)
        self.assertIn("`attachment_chunks`", text)
        self.assertIn("`attachment_chunks_fts`", text)
        self.assertIn("`chunk_embedding_meta`", text)
        self.assertIn("`chunk_embeddings`", text)
        self.assertIn("ON DELETE CASCADE", text)
        self.assertIn("skip_reason", text)
        self.assertIn("needs_ocr", text)
        self.assertIn("too_big", text)
        self.assertIn("P0 catalog", text)
        self.assertIn("P1 PDF/plain", text)
        self.assertIn("P2 office", text)
        self.assertIn("P3 OCR later", text)
        self.assertIn("S/M Mini, L MBP, X skip", text)
        self.assertIn("blob > 50 MB", text)
        self.assertIn("ATT-1", text)
        self.assertIn("ATT-8", text)
        self.assertIn("**Not** ATT-1..8 implement", text)
        self.assertIn("source=body|attach", text)
        self.assertIn("`--no-attach`", text)
        hay = _hay(text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_cross_links_from_required_docs(self):
        for path in CROSS_LINKS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("att0-constraints.md", text, msg=path.name)
            hay = _hay(text)
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)

    def test_skip_policy_and_generation_key_fail_closed(self):
        self.assertEqual(
            att0.extract_decision(mime="text/plain")["v1"], att0.EXTRACT_YES
        )
        self.assertEqual(
            att0.extract_decision(mime="application/pdf")["phase"], "P1"
        )
        scanned = att0.extract_decision(
            mime="application/pdf", image_only_pdf=True
        )
        self.assertEqual(scanned["skip_reason"], att0.SKIP_NEEDS_OCR)
        self.assertEqual(scanned["phase"], "P3")
        big = att0.extract_decision(mime="text/plain", blob_bytes=51 * 1024 * 1024)
        self.assertEqual(big["skip_reason"], att0.TOO_BIG)
        office = att0.extract_decision(
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        self.assertEqual(office["phase"], "P2")
        self.assertEqual(att0.size_band_host("S"), "Mini")
        self.assertEqual(att0.size_band_host("M"), "Mini")
        self.assertEqual(att0.size_band_host("L"), "MBP")
        self.assertEqual(att0.size_band_host("X"), "skip")
        att0.refuse_mixed_store_dim(1024)
        with self.assertRaises(att0.Att0Refuse) as ctx:
            att0.refuse_mixed_store_dim(256)
        self.assertIn("mixed dim", str(ctx.exception))
        with self.assertRaises(att0.Att0Refuse):
            att0.refuse_message_embeddings_for_chunks()
        self.assertFalse(att0.on_delete_cascade_allowed())
        self.assertTrue(att0.future_att_in_scope("ATT-0"))
        self.assertFalse(att0.future_att_in_scope("ATT-1"))
        self.assertEqual(att0.EMBED_BATCH_START, 32)

    def test_operators_can_find_att0(self):
        for path in OPERATORS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("att0-constraints.md", text, msg=path.name)
            self.assertIn("ATT-0", text, msg=path.name)
            hay = _hay(text)
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


class Koo66AuthHardGateTests(unittest.TestCase):
    def test_docs_lock_auth_hard_gate(self):
        text = ATT0.read_text(encoding="utf-8")
        self.assertIn("Auth / 2FA hard-gate", text)
        self.assertIn("`lane=auth`", text)
        self.assertIn("extract→FTS/chunk retrieve", text)
        self.assertIn("never-text-codes", text)
        self.assertIn("explicit human ask", text)
        self.assertIn("Use attachment retrieve to bypass auth body-block", text)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("auth hard-gate", ops.lower())
        self.assertIn("lane=auth", ops)

    def test_fail_closed_auth_lane(self):
        with self.assertRaises(att0.Att0Refuse) as ctx:
            att0.auth_hard_gate(lane="auth")
        self.assertIn("lane=auth", str(ctx.exception))
        with self.assertRaises(att0.Att0Refuse):
            att0.auth_hard_gate(lane="2fa")
        lifted = att0.auth_hard_gate(lane="auth", human_ask_lifts=True)
        self.assertTrue(lifted["allowed"])
        money = att0.auth_hard_gate(lane="money")
        self.assertTrue(money["allowed"])
        none = att0.auth_hard_gate(lane=None)
        self.assertTrue(none["allowed"])


class Koo67HistoryVsLiveAttachTests(unittest.TestCase):
    def test_docs_align_ask_mail_history_default(self):
        text = ATT0.read_text(encoding="utf-8")
        self.assertIn("History vs live for tombstoned parents", text)
        self.assertIn("ask_mail default = **history**", text)
        self.assertIn("present_on_server=0", text)
        self.assertIn("`--live`", text)
        self.assertIn("Do **not** permanently hide attach hits", text)
        ask = ASK.read_text(encoding="utf-8")
        self.assertIn("Retrieve default is **history**", ask)
        self.assertIn("att0-constraints.md", ask)
        self.assertIn("tombstoned", ask.lower())

    def test_history_shows_tombstoned_live_hides(self):
        self.assertTrue(
            att0.attach_hit_visible(parent_present_on_server=0, live=False)
        )
        self.assertTrue(
            att0.attach_hit_visible(parent_present_on_server=1, live=False)
        )
        self.assertFalse(
            att0.attach_hit_visible(parent_present_on_server=0, live=True)
        )
        self.assertTrue(
            att0.attach_hit_visible(parent_present_on_server=1, live=True)
        )


class Koo68NoLiveSorCatalogWhileRemTests(unittest.TestCase):
    def test_docs_lock_file_stage_vs_apply(self):
        text = ATT0.read_text(encoding="utf-8")
        self.assertIn("No live SoR catalog/apply while rem holds the lock", text)
        self.assertIn("Stage A (catalog), D (apply text), F (apply vec)", text)
        self.assertIn("file-stage", text)
        self.assertIn("copy DB", text)
        self.assertIn("EXIT 0", text)
        self.assertIn("with_writer_lock", text)
        embed = EMBED.read_text(encoding="utf-8")
        self.assertIn("att0-constraints.md", embed)
        self.assertIn("file-stage", embed)

    def test_fail_closed_live_adf_while_rem(self):
        for stage in att0.SOR_WRITER_STEPS:
            with self.assertRaises(att0.Att0Refuse) as ctx:
                att0.refuse_live_sor_writer_while_rem(
                    stage=stage,
                    rem_holds_lock=True,
                    rem_exit_0=False,
                    target="live_sor",
                )
            self.assertIn("EXIT 0", str(ctx.exception))
        for stage in att0.FILE_STAGE_STEPS:
            allowed = att0.refuse_live_sor_writer_while_rem(
                stage=stage,
                rem_holds_lock=True,
                rem_exit_0=False,
                target="sidecar",
            )
            self.assertTrue(allowed["allowed"])
        copy_ok = att0.refuse_live_sor_writer_while_rem(
            stage="A",
            rem_holds_lock=True,
            rem_exit_0=False,
            target="copy_db",
        )
        self.assertTrue(copy_ok["allowed"])
        after = att0.refuse_live_sor_writer_while_rem(
            stage="A",
            rem_holds_lock=False,
            rem_exit_0=True,
            target="live_sor",
        )
        self.assertTrue(after["allowed"])


class Koo69ImapBrewCurlSeenRailsTests(unittest.TestCase):
    def test_docs_lock_seen_and_brew_curl(self):
        text = ATT0.read_text(encoding="utf-8")
        self.assertIn("IMAP part-fetch rails", text)
        self.assertIn("Homebrew curl **≥ 8.17**", text)
        self.assertIn("`BODY.PEEK`", text)
        self.assertIn("`/usr/bin/curl`", text)
        self.assertIn("Apple curl LS allow ≠ brew curl", text)
        self.assertIn("`\\Seen` restore is not delete", text)
        self.assertIn("`EXPUNGE`", text)
        self.assertIn("`\\Deleted`", text)
        mail = MAILROOM.read_text(encoding="utf-8")
        self.assertIn("att0-constraints.md", mail)
        self.assertIn("\\Seen", mail)

    def test_fail_closed_apple_curl_and_hygiene(self):
        with self.assertRaises(att0.Att0Refuse) as ctx:
            att0.refuse_attachment_curl(
                att0.APPLE_CURL, version_text="curl 8.7.1 (secure transport)"
            )
        self.assertIn("fail-closed", str(ctx.exception))
        with self.assertRaises(att0.Att0Refuse):
            att0.refuse_attachment_curl(
                att0.HOMEBREW_CURL, version_text="curl 8.16.0 (Homebrew)"
            )
        att0.refuse_attachment_curl(
            att0.HOMEBREW_CURL, version_text="curl 8.17.0 (Homebrew)"
        )
        self.assertFalse(att0.seen_restore_is_delete())
        with self.assertRaises(att0.Att0Refuse) as ctx:
            att0.refuse_imap_hygiene_verb("EXPUNGE")
        self.assertIn("\\Seen restore is not delete", str(ctx.exception))
        with self.assertRaises(att0.Att0Refuse):
            att0.refuse_imap_hygiene_verb("DELETED")
        att0.refuse_imap_hygiene_verb("STORE SEEN")


class Koo70NeverPurgeAttachmentDiskCapsTests(unittest.TestCase):
    def test_docs_lock_never_purge_and_caps(self):
        text = ATT0.read_text(encoding="utf-8")
        self.assertIn("Never-purge + disk caps", text)
        self.assertIn("`attachment_*`", text)
        self.assertIn("too_big", text)
        self.assertIn("disk_path", text)
        self.assertIn("extract-tree", text)
        tomb = TOMBSTONE.read_text(encoding="utf-8")
        self.assertIn("att0-constraints.md", tomb)
        self.assertIn("attachment_*", tomb)
        soft = SOFT.read_text(encoding="utf-8")
        self.assertIn("att0-constraints.md", soft)
        self.assertIn("never-purge", soft.lower())

    def test_fail_closed_delete_and_caps(self):
        with self.assertRaises(att0.Att0Refuse) as ctx:
            att0.refuse_attachment_purge_sql("DELETE FROM attachments")
        self.assertIn("never-purge", str(ctx.exception))
        with self.assertRaises(att0.Att0Refuse):
            att0.refuse_attachment_purge_sql("DELETE FROM attachment_chunks")
        with self.assertRaises(att0.Att0Refuse):
            att0.refuse_attachment_purge_sql("delete from chunk_embeddings")
        att0.refuse_attachment_purge_sql("SELECT * FROM attachments")
        att0.refuse_attachment_purge_sql("DELETE FROM messages_ids")
        capped = att0.truncate_extract_text("x" * (2 * 1024 * 1024 + 50))
        self.assertTrue(capped["truncated"])
        self.assertEqual(capped["flag"], "extract_text_capped")
        small = att0.truncate_extract_text("ok")
        self.assertFalse(small["truncated"])


class Att0InterfaceProofAndHardDeckTests(unittest.TestCase):
    def test_interface_proof_fixture(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        proof = att0.interface_proof_fixture()
        self.assertTrue(raw["ok"])
        self.assertTrue(proof["ok"])
        self.assertTrue(raw["ready_is_not_att_implement"])
        self.assertEqual(raw["store_dim"], 1024)
        self.assertEqual(raw["model_tag"], "qwen3-embedding:8b")
        self.assertFalse(raw["seen_restore_is_delete"])
        self.assertIn("auth_hard_gate", raw["negative_smoke"])
        for label in att0.negative_smoke_labels():
            self.assertIn(label, raw["negative_smoke"])

    def test_ask_mail_not_overwritten_with_mcp_stub(self):
        text = ASK_MAIL_PY.read_text(encoding="utf-8")
        self.assertGreater(len(text), 8000)
        self.assertIn("Retrieve default is history (local SoR)", text)
        self.assertNotIn("MCP stub only", text)
        self.assertNotIn("EXAMPLE_USER_LOCAL", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)

    def test_ready_neq_att_implement(self):
        for path in (OPS, README, MAILROOM):
            text = path.read_text(encoding="utf-8")
            self.assertIn("Ready ≠ ATT implement", text, msg=path.name)


if __name__ == "__main__":
    unittest.main()
