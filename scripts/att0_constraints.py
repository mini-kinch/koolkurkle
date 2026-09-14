#!/usr/bin/env python3
"""ATT-0 fail-closed helpers (KOO-65..70). Docs/tests/fixtures only.

Schema / generation / skip / hard-deck language from
docs/att0-constraints.md. No catalog/extract/chunk/embed/apply run.
No live IMAP. No live SoR writer. Rem-legacy untouched.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

HOMEBREW_CURL = "/opt/homebrew/opt/curl/bin/curl"
APPLE_CURL = "/usr/bin/curl"
MIN_CURL_VERSION = (8, 17, 0)
STORE_DIM = 1024
MODEL_TAG = "qwen3-embedding:8b"
BLOB_TOO_BIG_BYTES = 50 * 1024 * 1024
EXTRACT_TEXT_CAP_BYTES = 2 * 1024 * 1024
ZIP_UNCOMPRESSED_SKIP_BYTES = 200 * 1024 * 1024
EMBED_BATCH_START = 32

ATTACHMENT_TABLES = (
    "attachments",
    "attachment_extracts",
    "attachment_chunks",
    "attachment_chunks_fts",
    "chunk_embedding_meta",
    "chunk_embeddings",
)
BODY_LANE_TABLES = ("messages", "message_embeddings")
LOCKED_TABLES = ATTACHMENT_TABLES + BODY_LANE_TABLES
MIME_PHASES = ("P0", "P1", "P2", "P3")
SIZE_BANDS = ("S", "M", "L", "X")
FILE_STAGE_STEPS = ("B", "C", "E")
SOR_WRITER_STEPS = ("A", "D", "F")
FUTURE_ATT_IDS = ("ATT-1", "ATT-2", "ATT-3", "ATT-4", "ATT-5", "ATT-6", "ATT-7", "ATT-8")

# v1 extract policy — MIME skip table (docs/att0-constraints.md §5).
EXTRACT_YES = "yes"
SKIP_NEEDS_OCR = "needs_ocr"
SKIP_IMAGE = "skip_image"
SKIP_NEVER_EXECUTE = "skip_never_execute"
SKIP_AV = "skip_av"
NAMES_ONLY = "names_only"
TOO_BIG = "too_big"

_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_DELETE_ATTACHMENT = re.compile(
    r"(?i)\bDELETE\s+FROM\s+"
    r"(?:(?:[\"`\[])?\w+(?:[\"`\]])?\s*\.\s*)?"
    r"(?:[\"`\[])?(attachment_\w+|attachments|chunk_embeddings|chunk_embedding_meta)"
    r"(?:[\"`\]])?"
)


class Att0Refuse(RuntimeError):
    """Fail-closed ATT-0 contract refuse. Never includes secrets."""


def parse_curl_version(text: str | None) -> tuple[int, int, int] | None:
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def version_at_least(version: tuple[int, int, int] | None) -> bool:
    if version is None:
        return False
    return version >= MIN_CURL_VERSION


def refuse_apple_curl_for_body_peek(curl_bin: str) -> None:
    if curl_bin == APPLE_CURL:
        raise Att0Refuse(
            "Apple /usr/bin/curl fail-closed for streaming literals / BODY.PEEK; "
            "use Homebrew curl >= 8.17 at %s" % HOMEBREW_CURL
        )


def refuse_attachment_curl(
    curl_bin: str,
    *,
    version_text: str | None = None,
) -> None:
    """Same bodies-fts rails: brew curl ≥ 8.17; Apple fail-closed."""
    refuse_apple_curl_for_body_peek(curl_bin)
    if curl_bin != HOMEBREW_CURL:
        raise Att0Refuse(
            "IMAP part-fetch requires Homebrew curl >= 8.17 at %s (got %s)"
            % (HOMEBREW_CURL, curl_bin)
        )
    if not version_at_least(parse_curl_version(version_text)):
        raise Att0Refuse(
            "BODY.PEEK requires Homebrew curl >= 8.17 (got %s)" % (version_text or "unknown")
        )


def seen_restore_is_delete() -> bool:
    """\\Seen restore is not delete. Never EXPUNGE / \\Deleted for hygiene."""
    return False


def refuse_imap_hygiene_verb(verb: str) -> None:
    name = (verb or "").strip().lstrip("-").split("=", 1)[0].upper()
    if name in {"EXPUNGE", "DELETED", "STORE \\DELETED", r"STORE \DELETED"}:
        raise Att0Refuse(
            "never EXPUNGE / \\Deleted for attachment hygiene; "
            "\\Seen restore is not delete"
        )
    if name.replace("\\", "") in {"SEEN", "STORE SEEN"}:
        return


def extract_decision(
    *,
    mime: str,
    blob_bytes: int = 0,
    zip_uncompressed_bytes: int | None = None,
    encrypted: bool = False,
    image_only_pdf: bool = False,
) -> dict[str, Any]:
    """v1 extract policy. Caps beat silent ballooning. No live extract."""
    kind = (mime or "").strip().lower()
    if blob_bytes > BLOB_TOO_BIG_BYTES:
        return {"v1": "skip", "skip_reason": TOO_BIG, "phase": "P0"}
    if encrypted or kind in {"application/pdf+encrypted"}:
        return {"v1": "skip", "skip_reason": SKIP_NEVER_EXECUTE, "phase": "P1"}
    if zip_uncompressed_bytes is not None and zip_uncompressed_bytes > ZIP_UNCOMPRESSED_SKIP_BYTES:
        return {"v1": "skip", "skip_reason": "zip_too_big", "phase": "P0"}
    if kind.startswith("image/"):
        return {"v1": "skip", "skip_reason": SKIP_IMAGE, "phase": "P3"}
    if kind.startswith("audio/") or kind.startswith("video/"):
        return {"v1": "skip", "skip_reason": SKIP_AV, "phase": "P3"}
    if kind in {"application/x-msdownload", "application/x-apple-diskimage", "application/x-newton-compatible-pkg"} or kind.endswith("/x-msdownload"):
        return {"v1": "skip", "skip_reason": SKIP_NEVER_EXECUTE, "phase": "P0"}
    if kind in {"application/zip", "application/x-rar-compressed", "application/x-7z-compressed"}:
        return {"v1": NAMES_ONLY, "skip_reason": None, "phase": "P0"}
    if kind in {"application/vnd.ms-cab-compressed"}:
        return {"v1": "skip", "skip_reason": SKIP_NEVER_EXECUTE, "phase": "P0"}
    if kind in {"application/x-dosexec", "application/vnd.microsoft.portable-executable"}:
        return {"v1": "skip", "skip_reason": SKIP_NEVER_EXECUTE, "phase": "P0"}
    if any(token in kind for token in ("exe", "dmg", "x-apple-diskimage", "vnd.apple.installer")):
        return {"v1": "skip", "skip_reason": SKIP_NEVER_EXECUTE, "phase": "P0"}
    if kind == "application/pdf" and image_only_pdf:
        return {"v1": "skip", "skip_reason": SKIP_NEEDS_OCR, "phase": "P3"}
    if kind == "application/pdf":
        return {"v1": EXTRACT_YES, "skip_reason": None, "phase": "P1"}
    if kind in {"text/plain", "text/csv", "application/csv"}:
        return {"v1": EXTRACT_YES, "skip_reason": None, "phase": "P1"}
    if kind in {"text/html", "application/xhtml+xml"}:
        return {"v1": EXTRACT_YES, "skip_reason": None, "phase": "P1"}
    if kind in {"message/rfc822", "message/rfc822-headers"}:
        return {"v1": EXTRACT_YES, "skip_reason": None, "phase": "P1"}
    if kind in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/msword",
        "application/vnd.ms-powerpoint",
    }:
        return {"v1": EXTRACT_YES, "skip_reason": None, "phase": "P2"}
    if kind.endswith(".exe") or kind in {"application/x-mach-binary"}:
        return {"v1": "skip", "skip_reason": SKIP_NEVER_EXECUTE, "phase": "P0"}
    return {"v1": "skip", "skip_reason": "unknown_mime", "phase": "P0"}


def size_band_host(band: str) -> str:
    """S/M Mini, L MBP, X skip. No invented byte cutoffs for S/M/L."""
    key = (band or "").strip().upper()
    if key in {"S", "M"}:
        return "Mini"
    if key == "L":
        return "MBP"
    if key == "X":
        return "skip"
    raise Att0Refuse("unknown size band %s (locked S/M/L/X)" % band)


def refuse_mixed_store_dim(store_dim: int) -> None:
    if int(store_dim) != STORE_DIM:
        raise Att0Refuse("mixed dim = refuse (store dim must be 1024, got %s)" % store_dim)


def refuse_message_embeddings_for_chunks() -> None:
    raise Att0Refuse(
        "do not put attachment vectors in message_embeddings; "
        "use chunk_embeddings vec0(float[1024])"
    )


def auth_hard_gate(
    *,
    lane: str | None,
    human_ask_lifts: bool = False,
) -> dict[str, Any]:
    """lane=auth must not enter extract→FTS/chunk retrieve by default."""
    name = (lane or "").strip().lower()
    blocked = name in {"auth", "2fa", "lane=auth"}
    if blocked and not human_ask_lifts:
        raise Att0Refuse(
            "auth hard-gate: lane=auth must not enter extract→FTS/chunk retrieve "
            "by default; never-text-codes extend to attachment text"
        )
    return {"allowed": True, "lane": name or None, "lifted": bool(human_ask_lifts and blocked)}


def attach_hit_visible(
    *,
    parent_present_on_server: int,
    live: bool = False,
) -> bool:
    """History default includes tombstoned parents; --live hides them."""
    if live:
        return int(parent_present_on_server) == 1
    return True


def refuse_live_sor_writer_while_rem(
    *,
    stage: str,
    rem_holds_lock: bool,
    rem_exit_0: bool = False,
    target: str = "live_sor",
) -> dict[str, Any]:
    """A/D/F wait rem EXIT 0 + with_writer_lock. B/C/E file-stage or copy OK."""
    step = (stage or "").strip().upper()
    dest = (target or "").strip().lower()
    file_or_copy = dest in {"file-stage", "copy", "copy_db", "sidecar"}
    if step in FILE_STAGE_STEPS or file_or_copy:
        return {"allowed": True, "stage": step, "target": dest}
    if step in SOR_WRITER_STEPS and dest in {"live_sor", "live", "mailroom.sqlite", "sor"}:
        if rem_holds_lock or not rem_exit_0:
            raise Att0Refuse(
                "no live SoR catalog/apply while rem holds the lock; "
                "stage %s waits rem EXIT 0 + with_writer_lock" % step
            )
    return {"allowed": True, "stage": step, "target": dest}


def normalize_sql(sql: str) -> str:
    text = _COMMENT_RE.sub(" ", sql or "")
    return _WS_RE.sub(" ", text).strip()


def refuse_attachment_purge_sql(sql: str | None) -> None:
    """Never-purge attachment_* / chunk_* cleanup DELETE."""
    text = normalize_sql(sql or "")
    if not text:
        return
    if _DELETE_ATTACHMENT.search(text):
        raise Att0Refuse(
            "never-purge: do not DELETE from attachment_* as cleanup "
            "(same never-purge spirit as messages); skip/too_big beats silent ballooning"
        )


def truncate_extract_text(text: str) -> dict[str, Any]:
    raw = text.encode("utf-8")
    if len(raw) <= EXTRACT_TEXT_CAP_BYTES:
        return {"text": text, "truncated": False, "flag": None}
    clipped = raw[:EXTRACT_TEXT_CAP_BYTES].decode("utf-8", errors="ignore")
    return {"text": clipped, "truncated": True, "flag": "extract_text_capped"}


def on_delete_cascade_allowed() -> bool:
    """message_id is a logical FK without ON DELETE CASCADE."""
    return False


def future_att_in_scope(att_id: str) -> bool:
    return (att_id or "").strip().upper() == "ATT-0"


def interface_proof_fixture(path_values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Positive contract labels. Ready ≠ ATT implement permission."""
    proof = {
        "ok": True,
        "lane": "att0",
        "status": "DESIGN ONLY",
        "store_dim": STORE_DIM,
        "model_tag": MODEL_TAG,
        "homebrew_curl": HOMEBREW_CURL,
        "apple_curl_fail_closed": True,
        "seen_restore_is_delete": False,
        "history_default": True,
        "ready_is_not_att_implement": True,
        "rem_untouched": True,
        "no_live_imap": True,
        "no_live_sor_writer": True,
        "att_1_to_8": "FUTURE / out of scope",
    }
    if path_values:
        proof.update(dict(path_values))
    return proof


def negative_smoke_labels() -> tuple[str, ...]:
    return (
        "auth_hard_gate",
        "rem_holds_lock_catalog",
        "apple_curl_body_peek",
        "mixed_dim",
        "delete_attachment_star",
        "expunge_hygiene",
        "message_embeddings_for_chunks",
    )
