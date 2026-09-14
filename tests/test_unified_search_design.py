#!/usr/bin/env python3
"""KOO-71..82 Heavy-06 + 06b design contracts. Docs/tests/fail-closed/fixtures only.

No live IMAP. No live SoR writer. No MSG/NOTE implement.
No chat.db / NoteStore open. No FDA grant. No mailroom.sqlite dump.
Rem-legacy untouched. ZERO PII. No sample OTP codes / phones / handles.
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

import unified_search_constraints as uni  # noqa: E402

DESIGN = ROOT / "docs" / "unified-search-design.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
ASK = ROOT / "docs" / "ask_mail.md"
ATT0 = ROOT / "docs" / "att0-constraints.md"
EMBED = ROOT / "docs" / "embed-backfill.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
SOFT = ROOT / "docs" / "soft-delete.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
FIXTURE = ROOT / "tests" / "fixtures" / "unified_search_interface_proof.json"
ASK_MAIL_PY = ROOT / "scripts" / "ask_mail.py"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
CROSS_LINKS = (MAILROOM, ASK, ATT0, TOMBSTONE, SOFT)
OPERATORS = (OPS, README)


def _hay(text: str) -> str:
    return text.replace("/Users/<operator>/", "")


def _no_pii(text: str, label: str) -> None:
    hay = _hay(text)
    for needle in PRIVACY_NEEDLES:
        if needle in hay:
            raise AssertionError("%s contains privacy needle %r" % (label, needle))


class Koo71SpineTests(unittest.TestCase):
    def test_packet_lands_full_heavy_06_contract(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("# Unified-search / ask_all (Heavy-06 + Mailroom 06b)", text)
        self.assertIn("DESIGN ONLY", text)
        self.assertIn("20260914-06-unified-search-design", text)
        self.assertIn("20260914-06b-mailroom-gaps-accepted", text)
        self.assertIn("https://github.com/mini-kinch/koolkurkle", text)
        self.assertIn("ZERO", text)
        self.assertIn("**Not** MSG/NOTE implement", text)
        self.assertIn("Ready ≠ MSG/NOTE enable", text)
        self.assertIn("KOO-65..70 Done (#36 merged)", text)
        self.assertIn("replicate then", text)
        self.assertIn("ask_all", text)
        self.assertIn("Hard decks", text)
        self.assertIn("What not to do", text)
        self.assertIn("never overwrite `scripts/ask_mail.py`", text)
        _no_pii(text, "unified-search-design.md")

    def test_cross_links_from_required_docs(self):
        for path in CROSS_LINKS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("unified-search-design.md", text, msg=path.name)
            _no_pii(text, path.name)

    def test_operators_can_find_unified_search(self):
        for path in OPERATORS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("unified-search-design.md", text, msg=path.name)
            self.assertIn("Ready ≠ MSG/NOTE enable", text, msg=path.name)
            _no_pii(text, path.name)


class Koo72SeparateSorsTests(unittest.TestCase):
    def test_docs_lock_three_sors_locks_backups(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("mailroom.sqlite", text)
        self.assertIn("msgroom.sqlite", text)
        self.assertIn("noteroom.sqlite", text)
        self.assertIn("three SoRs / three locks / three backup sets", text)
        self.assertIn("Mail rem lock ≠ msgroom/noteroom locks", text)
        self.assertIn("Never dump Messages/Notes into `mailroom.sqlite`", text)
        self.assertIn("INSERT INTO mailroom.sqlite", text)
        mail = MAILROOM.read_text(encoding="utf-8")
        self.assertIn("three SoRs", mail)
        self.assertIn("mailroom.sqlite", mail)

    def test_fail_closed_dump_and_shared_lock(self):
        contract = uni.three_sors_contract()
        self.assertEqual(contract["backup_sets"], 3)
        self.assertEqual(len(contract["sors"]), 3)
        self.assertFalse(contract["mail_rem_lock_equals_msgroom"])
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_mailroom_dump("chat.db")
        self.assertIn("three SoRs", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_mailroom_dump("notes")
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_shared_lock("mailroom", "msgroom")
        self.assertIn("different locks", str(ctx.exception))
        uni.refuse_shared_lock("mailroom", "mailroom")


class Koo73OneTaggedRrfTests(unittest.TestCase):
    def test_docs_lock_one_tagged_rrf(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("source=body|attach|imsg|note", text)
        self.assertIn("ONE RRF", text)
        self.assertIn("Do not invent a second RRF", text)
        self.assertIn("Not two RRF then merge", text)
        att = ATT0.read_text(encoding="utf-8")
        self.assertIn("source=body|attach", att)
        self.assertIn("unified-search-design.md", att)

    def test_fail_closed_one_rrf(self):
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_second_rrf("per-corpus-then-merge")
        self.assertIn("second RRF", str(ctx.exception))
        hits = [
            uni.tag_hit("body", 1, "m1"),
            uni.tag_hit("attach", 1, "a1"),
            uni.tag_hit("imsg", 1, "i1"),
            uni.tag_hit("note", 1, "n1"),
        ]
        fused = uni.one_tagged_rrf(hits)
        self.assertEqual(len(fused), 4)
        self.assertEqual({row["source"] for row in fused}, set(uni.RRF_TAGS))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.tag_hit("sms", 1, "x")
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.one_tagged_rrf([{"source": "mail", "rank": 1, "id": "x"}])


class Koo74BlobTreeTests(unittest.TestCase):
    def test_docs_lock_blob_paths(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("$MAILARCHIVE/att-blobs/<sha256[:2]>/<sha256>", text)
        self.assertIn("$MAILARCHIVE/imsg-blobs/<sha256[:2]>/<sha256>", text)
        self.assertIn("$MAILARCHIVE/note-blobs/<sha256[:2]>/<sha256>", text)
        self.assertIn("$MAILARCHIVE/att-extract/<lane>/<sha256>.txt", text)
        self.assertIn("$MAILARCHIVE/att-shards/", text)
        self.assertIn("Messages/Attachments", text)
        self.assertIn("copy-out by sha only", text)
        self.assertIn("extract_status=blob_missing", text)
        self.assertIn("MailArchive-mini/", text)

    def test_fail_closed_copy_out_only(self):
        digest = "ab" + ("cd" * 15)
        self.assertEqual(
            uni.blob_relpath("att", digest),
            "att-blobs/ab/%s" % digest,
        )
        self.assertEqual(
            uni.blob_relpath("imsg", digest),
            "imsg-blobs/ab/%s" % digest,
        )
        self.assertEqual(
            uni.blob_relpath("note", digest),
            "note-blobs/ab/%s" % digest,
        )
        self.assertEqual(
            uni.blob_relpath("imsg", digest, extract=True),
            "att-extract/imsg/%s.txt" % digest,
        )
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.blob_relpath("att", "not-a-sha")
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_write_into_apple_tree("~/Library/Messages/Attachments/x")
        self.assertIn("copy-out by sha only", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_write_into_apple_tree(
                "~/Library/Group Containers/group.com.apple.notes/Media"
            )
        self.assertEqual(uni.missing_blob_status(), "blob_missing")


class Koo75MsgLaneOutlineTests(unittest.TestCase):
    def test_docs_lock_msg_0_2_future(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("MSG-0..2 FUTURE docs only", text)
        self.assertIn("**NO MSG implement in this PR.**", text)
        self.assertIn("MSG-0", text)
        self.assertIn("MSG-1", text)
        self.assertIn("MSG-2", text)
        self.assertIn("msgroom.sqlite", text)
        self.assertIn("handle_hash", text)
        self.assertIn("attributedBody", text)
        self.assertIn("replica_complete=unknown|partial|local", text)
        self.assertIn("FUTURE / out of scope", text)
        self.assertFalse(uni.future_lane_in_scope("MSG-0"))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_msg_note_implement("MSG-0")
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_msg_note_implement("OPEN_CHAT_DB")


class Koo76NoteLaneOutlineTests(unittest.TestCase):
    def test_docs_lock_note_0_2_future(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("NOTE-0..2 FUTURE docs only", text)
        self.assertIn("**NO NOTE implement in this PR.**", text)
        self.assertIn("NOTE-0", text)
        self.assertIn("NOTE-1", text)
        self.assertIn("NOTE-2", text)
        self.assertIn("noteroom.sqlite", text)
        self.assertIn("gzip + protobuf", text)
        self.assertIn("ZICNOTEDATA.ZDATA", text)
        self.assertIn("Never write NoteStore", text)
        self.assertFalse(uni.future_lane_in_scope("NOTE-0"))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_msg_note_implement("NOTE-2")
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_msg_note_implement("OPEN_NOTESTORE")


class Koo77Fed0AskAllTests(unittest.TestCase):
    def test_docs_lock_fed0_shape_and_caps(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("FED-0", text)
        self.assertIn("No new writer", text)
        self.assertIn("ONE RRF (k=60)", text)
        self.assertIn("cap per corpus (e.g. 8 mail, 8 imsg, 6 notes)", text)
        self.assertIn("--corpus mail,att,imsg,note", text)
        self.assertIn("Cap 20 messages of context per cited chat", text)
        ask = ASK.read_text(encoding="utf-8")
        self.assertIn("ask_all", ask)
        self.assertIn("per-corpus caps", ask)

    def test_fail_closed_caps_and_mixed_dim(self):
        hits = (
            [{"source": "body", "id": "m%s" % i} for i in range(10)]
            + [{"source": "imsg", "id": "i%s" % i} for i in range(10)]
            + [{"source": "note", "id": "n%s" % i} for i in range(10)]
        )
        capped = uni.apply_per_corpus_caps(hits)
        self.assertEqual(sum(1 for h in capped if h["source"] == "body"), 8)
        self.assertEqual(sum(1 for h in capped if h["source"] == "imsg"), 8)
        self.assertEqual(sum(1 for h in capped if h["source"] == "note"), 6)
        uni.refuse_mixed_store_dim(1024)
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_mixed_store_dim(256)


class Koo78FdaBotBoundaryTests(unittest.TestCase):
    def test_docs_lock_fda_boundary(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("FDA / bot boundary", text)
        self.assertIn("human** grant", text)
        self.assertIn("Grok Bot.app", text)
        self.assertIn("Linux box", text)
        self.assertIn("retrieve-only against **our** replicas", text)
        self.assertIn("never a path that opens live `chat.db`", text)
        self.assertIn("Mini embeds only from copied replicas", text)
        ops = OPS.read_text(encoding="utf-8")
        self.assertIn("FDA", ops)
        self.assertIn("Grok Bot.app", ops)

    def test_fail_closed_fda_and_live_stores(self):
        ok = uni.refuse_fda_to_bot("Terminal")
        self.assertTrue(ok["human_grant"])
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_fda_to_bot("Grok Bot.app")
        self.assertIn("never Grok Bot.app", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_fda_to_bot("Linux box")
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_live_apple_store(store="chat.db", actor="box")
        self.assertIn("retrieve-only", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_live_apple_store(store="NoteStore.sqlite", actor="ask_all")
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.mini_embed_from_copied_replicas_only(host="Mini", source="chat.db")
        copied = uni.mini_embed_from_copied_replicas_only(
            host="Mini", source="msgroom-copy.sqlite"
        )
        self.assertTrue(copied["allowed"])


class Koo79FreshnessSplitTests(unittest.TestCase):
    def test_docs_lock_live_vs_replica_age(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("present_on_server", text)
        self.assertIn("`replica_age` only", text)
        self.assertIn("unified “live” means fresh iMessage", text)
        ask = ASK.read_text(encoding="utf-8")
        self.assertIn("replica_age", ask)
        self.assertIn("`--live`", ask)

    def test_fail_closed_no_unified_live_lie(self):
        mail_live = uni.freshness_label(corpus="mail", live=True)
        self.assertEqual(mail_live["freshness"], "present_on_server")
        self.assertTrue(mail_live["live_means_imap"])
        imsg = uni.freshness_label(corpus="imsg", live=False)
        self.assertEqual(imsg["freshness"], "replica_age")
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.freshness_label(corpus="imsg", live=True)
        self.assertIn("replica_age only", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.freshness_label(corpus="note", live=True)


class Koo80NotifyBillsIsolationTests(unittest.TestCase):
    def test_docs_lock_notify_bills_exception(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("notify_bills", text)
        self.assertIn("ops exception", text)
        self.assertIn("not an `ask_all` capability", text)
        self.assertIn("not a precedent for agent send", text)
        self.assertIn("Keychain item **name** only", text)
        mail = MAILROOM.read_text(encoding="utf-8")
        self.assertIn("notify_bills", mail)
        self.assertIn("ask_all", mail)

    def test_fail_closed_ask_all_send(self):
        bills = uni.refuse_ask_all_send(capability="notify_bills", notify_bills=True)
        self.assertTrue(bills["ops_exception"])
        self.assertFalse(bills["ask_all_capability"])
        self.assertFalse(bills["agent_send_precedent"])
        self.assertTrue(bills["keychain_name_only"])
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_ask_all_send(capability="ask_all_send")
        self.assertIn("ops exception", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.refuse_ask_all_send(capability="agent_send")


class Koo81RemSafeSequenceTests(unittest.TestCase):
    def test_docs_lock_sequence_and_ready(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("Rem-safe sequence", text)
        self.assertIn("Docs/tests during rem are OK", text)
        self.assertIn("SoR apply waits rem", text)
        self.assertIn("Do not start Messages/Notes during rem-legacy", text)
        self.assertIn("Ready ≠ MSG/NOTE enable", text)
        self.assertIn("att0-constraints.md", text)
        self.assertIn("MAILROOM.md", text)
        self.assertIn("ask_mail.md", text)
        self.assertIn("KOO-55..64", text)
        self.assertIn("ATT-0", text)
        embed = EMBED.read_text(encoding="utf-8")
        self.assertIn("unified-search-design.md", embed)

    def test_fail_closed_rem_safe_and_ready(self):
        docs_ok = uni.rem_safe_sequence_allows(
            step="UNIFIED-SEARCH-DOCS", rem_holds_lock=True, rem_exit_0=False
        )
        self.assertTrue(docs_ok["docs_during_rem"])
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.rem_safe_sequence_allows(
                step="MSG-0", rem_holds_lock=True, rem_exit_0=False
            )
        self.assertIn("EXIT 0", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.rem_safe_sequence_allows(
                step="ATT-APPLY", rem_holds_lock=True, rem_exit_0=False
            )
        after = uni.rem_safe_sequence_allows(
            step="FED-0", rem_holds_lock=False, rem_exit_0=True
        )
        self.assertTrue(after["allowed"])
        self.assertFalse(uni.ready_is_msg_note_enable())


class Koo82MsgOtpHardGateTests(unittest.TestCase):
    def test_docs_lock_otp_gate_no_sample_codes(self):
        text = DESIGN.read_text(encoding="utf-8")
        self.assertIn("MSG OTP / auth hard-gate", text)
        self.assertIn("cannot become a code dump", text)
        self.assertIn("Never put codes in citations / `ask_audit`", text)
        self.assertIn("No sample codes in this repo", text)
        self.assertNotIn("123456", text)
        self.assertNotIn("one-time password is", text.lower())
        att = ATT0.read_text(encoding="utf-8")
        self.assertIn("lane=auth", att)

    def test_fail_closed_otp_without_sample_codes(self):
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.msg_otp_hard_gate(lane="otp")
        self.assertIn("code dump", str(ctx.exception))
        with self.assertRaises(uni.UnifiedSearchRefuse):
            uni.msg_otp_hard_gate(auth_like=True)
        lifted = uni.msg_otp_hard_gate(lane="otp", human_ask_lifts=True)
        self.assertTrue(lifted["allowed"])
        money = uni.msg_otp_hard_gate(lane="money")
        self.assertTrue(money["allowed"])
        with self.assertRaises(uni.UnifiedSearchRefuse) as ctx:
            uni.refuse_codes_in_citations_or_audit({"id": "x", "otp": True})
        self.assertIn("ask_audit", str(ctx.exception))
        uni.refuse_codes_in_citations_or_audit({"id": "x", "source": "imsg"})


class UnifiedSearchInterfaceProofTests(unittest.TestCase):
    def test_interface_proof_fixture(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        proof = uni.interface_proof_fixture()
        self.assertTrue(raw["ok"])
        self.assertTrue(proof["ok"])
        self.assertTrue(raw["ready_is_not_msg_note_enable"])
        self.assertEqual(raw["store_dim"], 1024)
        self.assertEqual(raw["rrf_tags"], list(uni.RRF_TAGS))
        self.assertEqual(raw["backup_sets"], 3)
        self.assertEqual(raw["koo_65_70"], "Done (#36 merged)")
        for label in uni.negative_smoke_labels():
            self.assertIn(label, raw["negative_smoke"])

    def test_ask_mail_not_overwritten_with_mcp_stub(self):
        text = ASK_MAIL_PY.read_text(encoding="utf-8")
        self.assertGreater(len(text), 8000)
        self.assertIn("Retrieve default is history (local SoR)", text)
        self.assertNotIn("MCP stub only", text)
        self.assertNotIn("EXAMPLE_USER_LOCAL", text)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, text)

    def test_ready_neq_msg_note_enable(self):
        for path in (OPS, README, MAILROOM, DESIGN):
            text = path.read_text(encoding="utf-8")
            self.assertIn("Ready ≠ MSG/NOTE enable", text, msg=path.name)
            self.assertIn("Ready ≠ ATT implement", text, msg=path.name)


if __name__ == "__main__":
    unittest.main()
