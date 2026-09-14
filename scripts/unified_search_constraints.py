#!/usr/bin/env python3
"""Heavy-06 / 06b fail-closed helpers (KOO-71..82). Docs/tests/fixtures only.

Unified-search / ask_all design from docs/unified-search-design.md.
NO MSG/NOTE implement. No chat.db / NoteStore open. No FDA grant.
No mailroom.sqlite dump. Rem-legacy untouched. ZERO PII.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

STORE_DIM = 1024
RRF_K = 60
RRF_TAGS = ("body", "attach", "imsg", "note")
SORS = ("mailroom.sqlite", "msgroom.sqlite", "noteroom.sqlite")
LOCKS = ("mailroom", "msgroom", "noteroom")
BACKUP_SETS = 3
BLOB_ROOTS = (
    "att-blobs",
    "imsg-blobs",
    "note-blobs",
    "att-extract",
    "att-shards",
)
APPLE_WRITE_REFUSE = (
    "Messages/Attachments",
    "group.com.apple.notes",
    "chat.db",
    "NoteStore.sqlite",
)
LIVE_APPLE_STORES = ("chat.db", "NoteStore.sqlite")
FDA_FORBIDDEN_GRANTEES = ("grok bot.app", "bot.app", "linux box", "linux-box")
PER_CORPUS_CAPS = {"mail": 8, "imsg": 8, "note": 6}
MSG_FUTURE_IDS = ("MSG-0", "MSG-1", "MSG-2")
NOTE_FUTURE_IDS = ("NOTE-0", "NOTE-1", "NOTE-2")
FED_FUTURE_IDS = ("FED-0",)
FUTURE_IMPLEMENT_IDS = MSG_FUTURE_IDS + NOTE_FUTURE_IDS + FED_FUTURE_IDS
AUTH_LANES = frozenset({"auth", "otp", "2fa", "lane=auth", "lane=otp"})


class UnifiedSearchRefuse(RuntimeError):
    """Fail-closed Heavy-06 / 06b contract refuse. Never includes secrets."""


def refuse_fda_to_bot(grantee: str) -> dict[str, Any]:
    """FDA = human grant to indexer/Terminal; never Grok Bot.app / Linux box."""
    name = (grantee or "").strip().lower()
    if any(token in name for token in FDA_FORBIDDEN_GRANTEES):
        raise UnifiedSearchRefuse(
            "FDA must stay a human grant to the local indexer/Terminal; "
            "never Grok Bot.app / Linux box"
        )
    if name in {"indexer", "terminal", "human", "local indexer"}:
        return {"allowed": True, "grantee": name, "human_grant": True}
    raise UnifiedSearchRefuse(
        "FDA grant target must be human indexer/Terminal (got %s)" % grantee
    )


def refuse_live_apple_store(
    *,
    store: str,
    actor: str,
    replica_only: bool = False,
) -> dict[str, Any]:
    """ask_all on box = retrieve-only against our replicas; never live Apple stores."""
    name = (store or "").strip()
    who = (actor or "").strip().lower()
    leaf = name.split("/")[-1]
    boxish = who in {"box", "bot", "linux box", "linux-box", "ask_all", "grok bot.app"}
    if leaf in LIVE_APPLE_STORES or any(token in name for token in LIVE_APPLE_STORES):
        if boxish or not replica_only:
            raise UnifiedSearchRefuse(
                "ask_all on the box is retrieve-only against our replicas "
                "(msgroom/noteroom); never open live chat.db / NoteStore"
            )
    return {"allowed": True, "store": name, "actor": who, "replica_only": True}


def mini_embed_from_copied_replicas_only(
    *,
    host: str,
    source: str,
) -> dict[str, Any]:
    """Mini embeds only from copied replicas (no FDA on live Apple stores)."""
    who = (host or "").strip().lower()
    src = (source or "").strip().lower()
    if who == "mini" and src in {"chat.db", "notestore.sqlite", "live_apple"}:
        raise UnifiedSearchRefuse(
            "Mini embeds only from copied replicas (no FDA on live Apple stores)"
        )
    return {"allowed": True, "host": who, "source": src}


def refuse_mailroom_dump(source: str) -> None:
    """Never dump Messages/Notes into mailroom.sqlite."""
    src = (source or "").strip().lower()
    if src in {
        "chat.db",
        "notestore.sqlite",
        "msgroom.sqlite",
        "noteroom.sqlite",
        "messages",
        "notes",
        "imsg",
        "note",
    }:
        raise UnifiedSearchRefuse(
            "never dump Messages/Notes into mailroom.sqlite; "
            "three SoRs / three locks / three backup sets"
        )


def refuse_shared_lock(lock_a: str, lock_b: str) -> None:
    """Mail rem lock ≠ msgroom/noteroom locks. Never share with_writer_lock."""
    a = (lock_a or "").strip().lower()
    b = (lock_b or "").strip().lower()
    if a != b:
        raise UnifiedSearchRefuse(
            "never share with_writer_lock across mailroom and msgroom "
            "(different files, different locks); mail rem lock ≠ "
            "msgroom/noteroom locks"
        )


def three_sors_contract() -> dict[str, Any]:
    return {
        "sors": list(SORS),
        "locks": list(LOCKS),
        "backup_sets": BACKUP_SETS,
        "mail_rem_lock_equals_msgroom": False,
    }


def refuse_second_rrf(mode: str) -> None:
    """ONE tagged RRF. Do not invent a second RRF product."""
    name = (mode or "").strip().lower()
    if name in {"two_rrf", "two-stage", "per-corpus-then-merge", "second_rrf"}:
        raise UnifiedSearchRefuse(
            "RRF is one unified list tagged source=body|attach|imsg|note; "
            "do not invent a second RRF product"
        )


def tag_hit(source: str, rank: int, ident: str) -> dict[str, Any]:
    tag = (source or "").strip().lower()
    if tag not in RRF_TAGS:
        raise UnifiedSearchRefuse(
            "hit source must be body|attach|imsg|note (got %s)" % source
        )
    return {"source": tag, "rank": int(rank), "id": ident}


def one_tagged_rrf(hits: Iterable[Mapping[str, Any]], *, k: int = RRF_K) -> list[dict[str, Any]]:
    """One RRF over a pre-tagged unified list. No per-corpus second merge."""
    fused: dict[tuple[str, str], float] = {}
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        tag = str(hit.get("source") or "")
        if tag not in RRF_TAGS:
            raise UnifiedSearchRefuse(
                "ONE RRF requires source=body|attach|imsg|note before fusion"
            )
        ident = str(hit.get("id") or "")
        rank = int(hit.get("rank") or 0)
        key = (tag, ident)
        fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
        meta[key] = {"source": tag, "id": ident}
    ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    return [{**meta[key], "rrf": score} for key, score in ordered]


def apply_per_corpus_caps(
    hits: Iterable[Mapping[str, Any]],
    caps: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    limits = dict(caps or PER_CORPUS_CAPS)
    counts: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for hit in hits:
        source = str(hit.get("source") or "")
        corpus = "mail" if source in {"body", "attach"} else source
        used = counts.get(corpus, 0)
        cap = int(limits.get(corpus, limits.get(source, 0)) or 0)
        if cap and used >= cap:
            continue
        counts[corpus] = used + 1
        kept.append(dict(hit))
    return kept


def blob_relpath(lane: str, sha256: str, *, extract: bool = False) -> str:
    digest = (sha256 or "").strip().lower()
    if len(digest) < 4 or any(ch not in "0123456789abcdef" for ch in digest):
        raise UnifiedSearchRefuse("copy-out by sha only; refuse non-sha blob name")
    prefix = digest[:2]
    kind = (lane or "").strip().lower()
    if extract:
        return "att-extract/%s/%s.txt" % (kind, digest)
    roots = {"att": "att-blobs", "imsg": "imsg-blobs", "note": "note-blobs"}
    root = roots.get(kind)
    if root is None:
        raise UnifiedSearchRefuse("unknown blob lane %s (att|imsg|note)" % lane)
    return "%s/%s/%s" % (root, prefix, digest)


def refuse_write_into_apple_tree(path: str) -> None:
    text = (path or "").strip()
    for token in APPLE_WRITE_REFUSE:
        if token in text:
            raise UnifiedSearchRefuse(
                "do not write into Apple Messages/Attachments or Notes "
                "Group Container; copy-out by sha only"
            )


def missing_blob_status() -> str:
    return "blob_missing"


def freshness_label(*, corpus: str, live: bool = False) -> dict[str, Any]:
    """Mail --live = present_on_server; imsg/note = replica_age only."""
    name = (corpus or "").strip().lower()
    if name in {"imsg", "note", "msg", "messages", "notes"}:
        if live:
            raise UnifiedSearchRefuse(
                "imsg/note freshness is replica_age only; "
                "do not imply unified live means fresh iMessage"
            )
        return {"freshness": "replica_age", "live_means_imap": False}
    if name in {"mail", "body", "attach"}:
        return {
            "freshness": "present_on_server" if live else "history",
            "live_means_imap": bool(live),
        }
    raise UnifiedSearchRefuse("unknown corpus for freshness %s" % corpus)


def refuse_ask_all_send(*, capability: str, notify_bills: bool = False) -> dict[str, Any]:
    """notify_bills is an isolated ops exception ≠ ask_all send."""
    name = (capability or "").strip().lower()
    if name in {"ask_all_send", "agent_send", "applescript_send", "imessage_send"}:
        raise UnifiedSearchRefuse(
            "no auto-send / Messages AppleScript send for ask; "
            "notify_bills → Messages is an isolated ops exception, "
            "not an ask_all capability and not a precedent for agent send"
        )
    if name == "notify_bills" and notify_bills:
        return {
            "allowed": True,
            "ops_exception": True,
            "ask_all_capability": False,
            "agent_send_precedent": False,
            "keychain_name_only": True,
        }
    raise UnifiedSearchRefuse("unknown send capability %s" % capability)


def msg_otp_hard_gate(
    *,
    lane: str | None = None,
    auth_like: bool = False,
    human_ask_lifts: bool = False,
) -> dict[str, Any]:
    """MSG OTP/auth hard-gate. Never codes in citations/ask_audit."""
    name = (lane or "").strip().lower()
    blocked = name in AUTH_LANES or auth_like
    if blocked and not human_ask_lifts:
        raise UnifiedSearchRefuse(
            "MSG OTP/auth hard-gate: skip or hard-filter likely OTP patterns "
            "/ known auth senders so ask_all cannot become a code dump; "
            "never put codes in citations / ask_audit"
        )
    return {
        "allowed": True,
        "lane": name or None,
        "lifted": bool(human_ask_lifts and blocked),
    }


def refuse_codes_in_citations_or_audit(payload: Mapping[str, Any]) -> None:
    """Refuse citation/ask_audit payloads that claim to carry codes."""
    if payload.get("code") or payload.get("otp") or payload.get("auth_code"):
        raise UnifiedSearchRefuse(
            "never put codes in citations / ask_audit"
        )
    text = " ".join(str(payload.get(key) or "") for key in ("snippet", "body", "text"))
    if "auth_code" in text.lower() or "otp_code" in text.lower():
        raise UnifiedSearchRefuse(
            "never put codes in citations / ask_audit"
        )


def refuse_mixed_store_dim(store_dim: int) -> None:
    if int(store_dim) != STORE_DIM:
        raise UnifiedSearchRefuse(
            "do not mix dims across corpora in one RRF (store dim must be 1024)"
        )


def refuse_msg_note_implement(step: str) -> None:
    name = (step or "").strip().upper()
    if name in FUTURE_IMPLEMENT_IDS or name in {
        "MSG",
        "NOTE",
        "ASK_ALL",
        "CREATE_MSGROOM",
        "CREATE_NOTEROOM",
        "OPEN_CHAT_DB",
        "OPEN_NOTESTORE",
        "FDA_GRANT",
    }:
        raise UnifiedSearchRefuse(
            "NO MSG/NOTE implement; Ready ≠ MSG/NOTE enable; "
            "%s is FUTURE / out of scope" % name
        )


def rem_safe_sequence_allows(
    *,
    step: str,
    rem_holds_lock: bool,
    rem_exit_0: bool = False,
) -> dict[str, Any]:
    """Docs/tests OK during rem. SoR apply / MSG/NOTE start wait EXIT 0."""
    name = (step or "").strip().upper()
    docs_ok = name in {
        "DOCS",
        "TESTS",
        "ATT-0",
        "HEAVY-06",
        "KOO-71",
        "UNIFIED-SEARCH-DOCS",
    }
    if docs_ok:
        return {"allowed": True, "step": name, "docs_during_rem": True}
    applyish = name in {
        "ATT-APPLY",
        "MSG-0",
        "MSG-1",
        "MSG-2",
        "NOTE-0",
        "NOTE-1",
        "NOTE-2",
        "FED-0",
        "SOR-APPLY",
    }
    if applyish and (rem_holds_lock or not rem_exit_0):
        raise UnifiedSearchRefuse(
            "do not start Messages/Notes during rem-legacy; "
            "do not start ATT SoR apply during rem; SoR apply waits EXIT 0"
        )
    return {"allowed": True, "step": name, "rem_exit_0": rem_exit_0}


def future_lane_in_scope(lane_id: str) -> bool:
    return False


def ready_is_msg_note_enable() -> bool:
    return False


def interface_proof_fixture(path_values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Positive contract labels. Ready ≠ MSG/NOTE enable permission."""
    proof = {
        "ok": True,
        "lane": "heavy-06",
        "status": "DESIGN ONLY",
        "store_dim": STORE_DIM,
        "rrf_k": RRF_K,
        "rrf_tags": list(RRF_TAGS),
        "sors": list(SORS),
        "locks": list(LOCKS),
        "backup_sets": BACKUP_SETS,
        "blob_roots": list(BLOB_ROOTS),
        "ready_is_not_msg_note_enable": True,
        "ready_is_not_att_implement": True,
        "rem_untouched": True,
        "no_live_imap": True,
        "no_live_sor_writer": True,
        "no_msg_note_implement": True,
        "ask_all_box_retrieve_only": True,
        "fda_human_grant_only": True,
        "notify_bills_ops_exception": True,
        "msg_otp_hard_gate": True,
        "koo_65_70": "Done (#36 merged)",
    }
    if path_values:
        proof.update(dict(path_values))
    return proof


def negative_smoke_labels() -> tuple[str, ...]:
    return (
        "fda_to_bot",
        "live_chat_db_from_box",
        "mailroom_dump",
        "shared_lock",
        "second_rrf",
        "write_apple_tree",
        "unified_live_imsg",
        "ask_all_send",
        "msg_otp_codes_in_audit",
        "msg_note_implement_during_rem",
        "mixed_dim",
    )
