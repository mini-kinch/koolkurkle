#!/usr/bin/env python3
"""KOO-57 compute sidecar apply (one-writer, fixtures only).

HARD DECK one-writer apply. Never pointed at live rem SoR
(mailroom.sqlite basename is refused). Design + contract tests only.
Does not start rem-legacy. Docs PR Ready ≠ permission to apply
against live rem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from embed_generation_key import (  # noqa: E402
    DEFAULT_MODEL_TAG,
    DEFAULT_STORE_DIM,
    GenerationKeyRefuse,
    embed_generation_key,
    refuse_generation_mismatch,
)

LIVE_REM_SOR_BASENAME = "mailroom.sqlite"
APPLIED_SHARDS_TABLE = "sidecar_applied_shards"
DEFAULT_PURPOSE = "sidecar_apply"


class SidecarApplyRefuse(RuntimeError):
    """Fail-closed sidecar apply. Never includes secrets."""


def shard_checksum(
    *,
    message_id: str,
    content_hash: str,
    model_tag: str,
    store_dim: int,
    vector: Sequence[float],
) -> str:
    payload = json.dumps(
        {
            "id": message_id,
            "content_hash": content_hash,
            "model_tag": model_tag,
            "store_dim": int(store_dim),
            "vector": [float(x) for x in vector],
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_shard(raw: Mapping[str, Any]) -> dict[str, Any]:
    message_id = str(raw.get("id") or raw.get("message_id") or "").strip()
    content_hash = str(raw.get("content_hash") or "").strip()
    model_tag = str(raw.get("model_tag") or DEFAULT_MODEL_TAG).strip()
    store_dim = int(raw.get("store_dim") or DEFAULT_STORE_DIM)
    vector = raw.get("vector")
    checksum = str(raw.get("checksum") or "").strip()
    if not message_id or not content_hash or not isinstance(vector, (list, tuple)):
        raise SidecarApplyRefuse("shard missing id+content_hash+model_tag+store_dim+vector")
    if len(vector) != store_dim:
        raise SidecarApplyRefuse(
            "dim mismatch: vector=%s store_dim=%s" % (len(vector), store_dim)
        )
    expect = shard_checksum(
        message_id=message_id,
        content_hash=content_hash,
        model_tag=model_tag,
        store_dim=store_dim,
        vector=vector,
    )
    if checksum and checksum != expect:
        raise SidecarApplyRefuse("corrupt shard checksum")
    return {
        "id": message_id,
        "content_hash": content_hash,
        "model_tag": model_tag,
        "store_dim": store_dim,
        "vector": [float(x) for x in vector],
        "checksum": checksum or expect,
    }


def refuse_live_rem_sor(db: str | Path) -> Path:
    path = Path(db)
    if path.name == LIVE_REM_SOR_BASENAME:
        raise SidecarApplyRefuse(
            "never pointed at live rem SoR; refuse basename %s" % LIVE_REM_SOR_BASENAME
        )
    return path


def _serialize_f32(vector: Sequence[float]) -> bytes:
    return struct.pack("%sf" % len(vector), *vector)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(r[0]) for r in rows}


def _ensure_apply_tables(conn: sqlite3.Connection, store_dim: int) -> None:
    names = _table_names(conn)
    if "embedding_meta" not in names:
        conn.execute(
            """
            CREATE TABLE embedding_meta (
              message_id TEXT NOT NULL,
              model TEXT NOT NULL,
              model_version TEXT NOT NULL DEFAULT 'v1',
              created_at TEXT NOT NULL,
              text_hash TEXT NOT NULL,
              char_count INTEGER NOT NULL DEFAULT 0,
              dims INTEGER NOT NULL DEFAULT 1024,
              content_hash TEXT,
              embed_model TEXT,
              embed_dim INTEGER,
              instruct_version TEXT,
              PRIMARY KEY (message_id, model, model_version)
            )
            """
        )
    if "message_embeddings" not in names:
        conn.execute(
            "CREATE TABLE message_embeddings ("
            "message_id TEXT PRIMARY KEY, embedding BLOB)"
        )
    if APPLIED_SHARDS_TABLE not in names:
        conn.execute(
            "CREATE TABLE %s (shard_checksum TEXT PRIMARY KEY, message_id TEXT NOT NULL)"
            % APPLIED_SHARDS_TABLE
        )
    del store_dim


def _forbidden_write(sql: str) -> None:
    low = " ".join(sql.lower().split())
    forbidden = (
        "insert into messages",
        "update messages",
        "delete from messages",
        "insert into messages_fts",
        "update messages_fts",
        "imap",
    )
    for needle in forbidden:
        if needle in low:
            raise SidecarApplyRefuse(
                "sidecar writes embedding_meta+vec only — never messages/FTS/IMAP"
            )


def apply_shards(
    db: str | Path,
    shards: Iterable[Mapping[str, Any]],
    *,
    lock: bool = True,
    lock_path: Path | None = None,
    action_required_path: Path | None = None,
    reembed: bool = False,
    reembed_human_go: bool = False,
    purpose: str = DEFAULT_PURPOSE,
    now: str = "2026-09-14T00:00:00+00:00",
) -> dict[str, int]:
    """Missing-only apply under with_writer_lock. Fixtures / temp DBs only."""
    path = refuse_live_rem_sor(db)
    parsed = [parse_shard(item) for item in shards]
    if reembed and not reembed_human_go:
        raise SidecarApplyRefuse(
            "hash mismatch skip unless explicit --reembed human go"
        )

    def _apply() -> dict[str, int]:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        inserted = 0
        skipped = 0
        try:
            if parsed:
                _ensure_apply_tables(conn, parsed[0]["store_dim"])
            for shard in parsed:
                refuse_generation_mismatch(
                    None,
                    embed_generation_key(
                        model_tag=shard["model_tag"],
                        store_dim=shard["store_dim"],
                    ),
                )
                existing_applied = conn.execute(
                    "SELECT 1 FROM %s WHERE shard_checksum = ?" % APPLIED_SHARDS_TABLE,
                    (shard["checksum"],),
                ).fetchone()
                if existing_applied:
                    raise SidecarApplyRefuse("refuse second apply on same shard")
                meta = conn.execute(
                    "SELECT content_hash, dims, embed_model, model FROM embedding_meta "
                    "WHERE message_id = ?",
                    (shard["id"],),
                ).fetchone()
                if meta is not None:
                    stored_hash = meta["content_hash"] if "content_hash" in meta.keys() else None
                    if stored_hash and stored_hash != shard["content_hash"]:
                        if reembed and not reembed_human_go:
                            raise SidecarApplyRefuse(
                                "hash mismatch skip unless explicit --reembed human go"
                            )
                        skipped += 1
                        continue
                    skipped += 1
                    continue
                _forbidden_write("INSERT INTO embedding_meta")
                conn.execute(
                    """
                    INSERT INTO embedding_meta(
                      message_id, model, model_version, created_at, text_hash,
                      char_count, dims, content_hash, embed_model, embed_dim
                    ) VALUES (?, ?, 'v1', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shard["id"],
                        "qwen3-embedding-8b",
                        now,
                        shard["content_hash"],
                        0,
                        shard["store_dim"],
                        shard["content_hash"],
                        shard["model_tag"],
                        shard["store_dim"],
                    ),
                )
                conn.execute(
                    "INSERT INTO message_embeddings(message_id, embedding) VALUES (?, ?)",
                    (shard["id"], _serialize_f32(shard["vector"])),
                )
                conn.execute(
                    "INSERT INTO %s(shard_checksum, message_id) VALUES (?, ?)"
                    % APPLIED_SHARDS_TABLE,
                    (shard["checksum"], shard["id"]),
                )
                inserted += 1
            conn.commit()
        finally:
            conn.close()
        return {"inserted": inserted, "skipped": skipped}

    if not lock:
        return _apply()
    try:
        import with_writer_lock as wwl
    except ImportError as exc:
        raise SidecarApplyRefuse("with_writer_lock missing") from exc
    try:
        held = wwl.acquire_writer_lock(
            lock_path or wwl.default_lock_path(),
            purpose,
        )
    except Exception as exc:
        raise SidecarApplyRefuse("lock held; refuse second applier") from exc
    try:
        return _apply()
    finally:
        wwl.release_writer_lock(held)


def load_shard_file(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "shards" in raw:
        return list(raw["shards"])
    if isinstance(raw, list):
        return list(raw)
    return [dict(raw)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sidecar apply (fixtures only)")
    parser.add_argument("--db", required=True)
    parser.add_argument("--shards", required=True)
    parser.add_argument("--lock-file")
    parser.add_argument("--reembed", action="store_true")
    parser.add_argument("--reembed-human-go", action="store_true")
    parser.add_argument("--no-lock", action="store_true")
    args = parser.parse_args(argv)
    try:
        refuse_live_rem_sor(args.db)
        shards = load_shard_file(args.shards)
        result = apply_shards(
            args.db,
            shards,
            lock=not args.no_lock,
            lock_path=Path(args.lock_file) if args.lock_file else None,
            reembed=args.reembed,
            reembed_human_go=args.reembed_human_go,
        )
    except (SidecarApplyRefuse, GenerationKeyRefuse) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
