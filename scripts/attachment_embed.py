#!/usr/bin/env python3
"""Embed attachment_chunks into the mail SoR and retrieve them like mail.

Same database as messages. Same model as embed_lib: local Ollama
qwen3-embedding:8b, 1024-d Matryoshka, query instruct v1. Does not write
message_embeddings or embedding_meta. Vectors live in attachment_embeddings
(sqlite-vec). One query vector is reused by semantic_search.retrieve, which
folds these hits into the same RRF list. The hit stays a message_id, so the
existing mail link still opens the email. The snippet is the chunk, with a
page prefix.

Apple /usr/bin/python3 cannot load sqlite-vec. Run with the Mailroom venv:

  ~/MailArchive/.venv/bin/python scripts/attachment_embed.py --dry-run
  ~/MailArchive/.venv/bin/python scripts/attachment_embed.py --lock

--dry-run does not call Ollama and does not write vectors.
--lock takes the writer lock per batch, purpose embed_batch (already allowed).
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

import embed_lib as el

DEFAULT_MODEL_VERSION = "att0-v1"
VEC_TABLE = "attachment_embeddings"
META_TABLE = "attachment_embedding_meta"


class AttachmentEmbedError(el.EmbedError):
    """Attachment embed failure. No chunk text, no secrets."""


def _has(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _has(conn, table):
        return set()
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % table)}


def ensure_schema(conn: sqlite3.Connection, dims: int = el.DEFAULT_DIMS) -> None:
    """Create the attachment vector table. Never touches message_embeddings."""
    if dims != el.DEFAULT_DIMS:
        raise AttachmentEmbedError(
            "attachment vectors are %s-d, got %s" % (el.DEFAULT_DIMS, dims)
        )
    if _has(conn, VEC_TABLE):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            (VEC_TABLE,),
        ).fetchone()
        sql = "" if row is None or row[0] is None else str(row[0])
        if "float[%d]" % dims not in sql:
            raise AttachmentEmbedError(
                "%s exists with a different dimension than %s" % (VEC_TABLE, dims)
            )
    else:
        conn.execute(
            "CREATE VIRTUAL TABLE %s USING vec0("
            "chunk_id TEXT PRIMARY KEY, embedding float[%d])" % (VEC_TABLE, dims)
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attachment_embedding_meta (
          chunk_id TEXT NOT NULL,
          model TEXT NOT NULL,
          model_version TEXT NOT NULL,
          created_at TEXT NOT NULL,
          text_hash TEXT NOT NULL,
          char_count INTEGER NOT NULL,
          dims INTEGER NOT NULL,
          message_id TEXT,
          PRIMARY KEY (chunk_id, model, model_version)
        )
        """
    )
    conn.commit()


def _fts_match_query(query: str) -> str:
    """Same token AND as semantic_search.fts_match_query. No circular import."""
    tokens = re.findall(r"[A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*", query or "")
    quoted = []
    for token in tokens:
        cleaned = token.replace('"', " ").strip()
        if cleaned:
            quoted.append('"%s"' % cleaned)
    if not quoted:
        fallback = (query or "").replace('"', " ").strip()
        return '"%s"' % fallback if fallback else '""'
    return " AND ".join(quoted)


def _page_prefix(page_start: object, page_end: object) -> str:
    try:
        start = int(page_start) if page_start is not None else 0
    except (TypeError, ValueError):
        start = 0
    try:
        end = int(page_end) if page_end is not None else start
    except (TypeError, ValueError):
        end = start
    if start < 1:
        return ""
    if end > start:
        return "p.%s-%s " % (start, end)
    return "p.%s " % start


def _snippet(page_start: object, page_end: object, text: object) -> str:
    body = el.snippet("" if text is None else str(text))
    return (_page_prefix(page_start, page_end) + body).strip()


def _date_bounds(after: str | None, before: str | None) -> tuple[str | None, str | None]:
    lo = str(after).strip() if after else None
    hi = None
    if before:
        before_s = str(before).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", before_s):
            hi = before_s + "T23:59:59.999999Z"
        else:
            hi = before_s or None
    return lo or None, hi


def _filter_sql(
    conn: sqlite3.Connection,
    *,
    lane: str | None,
    after: str | None,
    before: str | None,
) -> tuple[str, list[Any]]:
    cols = _cols(conn, "messages")
    clauses: list[str] = []
    params: list[Any] = []
    if lane and "lane" in cols:
        clauses.append("LOWER(COALESCE(m.lane, '')) = ?")
        params.append(lane.lower())
    date_col = "date_utc" if "date_utc" in cols else ("date" if "date" in cols else None)
    lo, hi = _date_bounds(after, before)
    if date_col and lo:
        clauses.append("COALESCE(m.%s, '') >= ?" % date_col)
        params.append(lo)
    if date_col and hi:
        clauses.append("COALESCE(m.%s, '') <= ?" % date_col)
        params.append(hi)
    if not clauses:
        return "", params
    return " AND " + " AND ".join(clauses), params


def chunks_ready(conn: sqlite3.Connection) -> bool:
    needed = ("attachment_chunks", "attachment_extracts", "attachments", "messages")
    return all(_has(conn, name) for name in needed)


def _message_passes(
    conn: sqlite3.Connection,
    message_id: str,
    *,
    lane: str | None,
    after: str | None,
    before: str | None,
) -> bool:
    cols = _cols(conn, "messages")
    if "id" not in cols:
        return True
    want = ["id"]
    if "lane" in cols:
        want.append("lane")
    date_col = "date_utc" if "date_utc" in cols else ("date" if "date" in cols else None)
    if date_col:
        want.append(date_col)
    row = conn.execute(
        "SELECT %s FROM messages WHERE id = ?" % ", ".join(want),
        (message_id,),
    ).fetchone()
    if row is None:
        return False
    rec = dict(row)
    if str(rec.get("lane") or "").lower() == "auth":
        return False
    if lane:
        if str(rec.get("lane") or "").lower() != lane.lower():
            return False
    lo, hi = _date_bounds(after, before)
    date_s = str(rec.get(date_col) or "") if date_col else ""
    if lo and date_s < lo:
        return False
    if hi and date_s > hi:
        return False
    return True


def fts_hits(
    conn: sqlite3.Connection,
    query: str,
    *,
    k: int = 50,
    lane: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """Best attachment chunk per message. Rank 1 is best. Read-only."""
    if not _has(conn, "attachment_chunks_fts") or not chunks_ready(conn):
        return []
    extra, params = _filter_sql(conn, lane=lane, after=after, before=before)
    sql = (
        "SELECT c.chunk_id AS chunk_id, a.message_id AS message_id, "
        "c.page_start AS page_start, c.page_end AS page_end, c.text AS text "
        "FROM attachment_chunks_fts AS fts "
        "JOIN attachment_chunks AS c ON c.chunk_id = fts.rowid "
        "JOIN attachment_extracts AS e ON e.extract_id = c.extract_id "
        "JOIN attachments AS a ON a.attachment_id = e.attachment_id "
        "JOIN messages AS m ON m.id = a.message_id "
        "WHERE attachment_chunks_fts MATCH ? "
        "AND lower(coalesce(m.lane, '')) != 'auth'%s "
        "ORDER BY bm25(attachment_chunks_fts) "
        "LIMIT ?" % extra
    )
    try:
        rows = conn.execute(sql, (_fts_match_query(query), *params, max(1, int(k)) * 4)).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        rec = dict(row)
        mid = str(rec.get("message_id") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(
            {
                "message_id": mid,
                "chunk_id": str(rec.get("chunk_id")),
                "fts_rank": len(out) + 1,
                "snippet": _snippet(rec.get("page_start"), rec.get("page_end"), rec.get("text")),
            }
        )
        if len(out) >= k:
            break
    return out


def vec_hits(
    conn: sqlite3.Connection,
    query_vector: Sequence[float] | None,
    *,
    k: int = 50,
    dims: int = el.DEFAULT_DIMS,
    lane: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """KNN on attachment_embeddings. Read-only. Empty if the table is missing."""
    if query_vector is None or not _has(conn, VEC_TABLE) or not chunks_ready(conn):
        return []
    if len(query_vector) != dims:
        return []
    try:
        rows = conn.execute(
            "SELECT v.chunk_id AS chunk_id, v.distance AS distance "
            "FROM attachment_embeddings AS v "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (el.serialize_f32(query_vector), max(1, int(k)) * 4),
        ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        rec = dict(row)
        chunk_id = str(rec.get("chunk_id") or "")
        if not chunk_id:
            continue
        meta = conn.execute(
            "SELECT a.message_id AS message_id, c.page_start AS page_start, "
            "c.page_end AS page_end, c.text AS text "
            "FROM attachment_chunks AS c "
            "JOIN attachment_extracts AS e ON e.extract_id = c.extract_id "
            "JOIN attachments AS a ON a.attachment_id = e.attachment_id "
            "WHERE c.chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if meta is None:
            continue
        info = dict(meta)
        mid = str(info.get("message_id") or "")
        if not mid or mid in seen:
            continue
        if not _message_passes(conn, mid, lane=lane, after=after, before=before):
            continue
        seen.add(mid)
        out.append(
            {
                "message_id": mid,
                "chunk_id": chunk_id,
                "vec_rank": len(out) + 1,
                "snippet": _snippet(info.get("page_start"), info.get("page_end"), info.get("text")),
            }
        )
        if len(out) >= k:
            break
    return out


def evidence(
    conn: sqlite3.Connection,
    query: str,
    *,
    query_vector: Sequence[float] | None = None,
    k: int = 50,
    lane: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """One row per message. Missing side has no rank key. Read-only."""
    if not chunks_ready(conn):
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for item in fts_hits(conn, query, k=k, lane=lane, after=after, before=before):
        by_id[item["message_id"]] = {
            "message_id": item["message_id"],
            "chunk_id": item["chunk_id"],
            "fts_rank": item["fts_rank"],
            "snippet": item["snippet"],
        }
    for item in vec_hits(
        conn,
        query_vector,
        k=k,
        lane=lane,
        after=after,
        before=before,
    ):
        rec = by_id.get(item["message_id"])
        if rec is None:
            by_id[item["message_id"]] = {
                "message_id": item["message_id"],
                "chunk_id": item["chunk_id"],
                "vec_rank": item["vec_rank"],
                "snippet": item["snippet"],
            }
            continue
        rec["vec_rank"] = item["vec_rank"]
        rec["chunk_id"] = item["chunk_id"]
        rec["snippet"] = item["snippet"]
    return list(by_id.values())


def _candidate_rows(
    conn: sqlite3.Connection,
    *,
    model: str,
    model_version: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    if not chunks_ready(conn):
        raise AttachmentEmbedError("attachment tables are missing")
    stored = el.model_id(model)
    rows = conn.execute(
        """
        SELECT c.chunk_id AS chunk_id,
               a.message_id AS message_id,
               m.subject AS subject,
               c.text AS text
        FROM attachment_chunks AS c
        JOIN attachment_extracts AS e ON e.extract_id = c.extract_id
        JOIN attachments AS a ON a.attachment_id = e.attachment_id
        JOIN messages AS m ON m.id = a.message_id
        WHERE c.text IS NOT NULL AND length(trim(c.text)) > 0
          AND lower(coalesce(m.lane, '')) != 'auth'
        ORDER BY c.chunk_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        text = el.embed_text(rec.get("subject"), rec.get("text"))
        if not text.strip():
            continue
        digest = el.sha256_text(text)
        already = conn.execute(
            "SELECT 1 FROM attachment_embedding_meta "
            "WHERE chunk_id = ? AND model = ? AND model_version = ? AND text_hash = ?",
            (str(rec["chunk_id"]), stored, model_version, digest),
        ).fetchone() if _has(conn, META_TABLE) else None
        if already:
            continue
        out.append(
            {
                "chunk_id": str(rec["chunk_id"]),
                "message_id": str(rec["message_id"]),
                "text": text,
                "text_hash": digest,
            }
        )
        if limit is not None and len(out) >= int(limit):
            break
    return out


def backfill(
    conn: sqlite3.Connection,
    *,
    model: str = el.DEFAULT_MODEL,
    model_version: str = DEFAULT_MODEL_VERSION,
    limit: int | None = None,
    batch_size: int = el.DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    ollama_url: str = el.DEFAULT_OLLAMA_URL,
    dims: int = el.DEFAULT_DIMS,
    embed_fn: el.EmbedFn | None = None,
    lock: bool = False,
) -> dict[str, int]:
    """Embed new or changed chunks. Resume-safe. Dry-run does not call Ollama."""
    if not dry_run:
        def _schema() -> None:
            ensure_schema(conn, dims=dims)

        el._run_locked_batch(lock, "embed_batch", _schema)
    elif dims != el.DEFAULT_DIMS:
        raise AttachmentEmbedError(
            "attachment vectors are %s-d, got %s" % (el.DEFAULT_DIMS, dims)
        )
    rows = _candidate_rows(conn, model=model, model_version=model_version, limit=limit)
    stored = el.model_id(model)
    print(
        "candidates=%s model=%s dims=%s dry_run=%s" % (len(rows), stored, dims, int(dry_run)),
        file=sys.stderr,
    )
    if dry_run:
        for row in rows[:20]:
            print("  %s chars=%s" % (row["chunk_id"], len(row["text"])), file=sys.stderr)
        if len(rows) > 20:
            print("  … %s more" % (len(rows) - 20), file=sys.stderr)
        return {"candidates": len(rows), "embedded": 0}
    if not rows:
        return {"candidates": 0, "embedded": 0}

    worker = embed_fn or el.make_ollama_embed_fn(ollama_url, dims)
    embedded = 0
    size = max(1, int(batch_size))
    for start in range(0, len(rows), size):
        batch = rows[start : start + size]
        vectors = worker([row["text"] for row in batch], model)
        if len(vectors) != len(batch):
            raise AttachmentEmbedError("embed function returned the wrong number of vectors")

        def _commit(batch_rows: list[dict[str, Any]] = batch, batch_vectors: list[list[float]] = vectors) -> None:
            nonlocal embedded
            created = el.utc_now()
            for row, vector in zip(batch_rows, batch_vectors):
                if len(vector) != dims:
                    raise AttachmentEmbedError(
                        "vector dim %s != %s" % (len(vector), dims)
                    )
                blob = el.serialize_f32(vector)
                conn.execute(
                    "DELETE FROM attachment_embeddings WHERE chunk_id = ?",
                    (row["chunk_id"],),
                )
                conn.execute(
                    "INSERT INTO attachment_embeddings(chunk_id, embedding) VALUES (?, ?)",
                    (row["chunk_id"], blob),
                )
                conn.execute(
                    """
                    INSERT INTO attachment_embedding_meta(
                      chunk_id, model, model_version, created_at, text_hash,
                      char_count, dims, message_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, model, model_version) DO UPDATE SET
                      created_at = excluded.created_at,
                      text_hash = excluded.text_hash,
                      char_count = excluded.char_count,
                      dims = excluded.dims,
                      message_id = excluded.message_id
                    """,
                    (
                        row["chunk_id"],
                        stored,
                        model_version,
                        created,
                        row["text_hash"],
                        len(row["text"]),
                        dims,
                        row["message_id"],
                    ),
                )
                embedded += 1
            conn.commit()

        el._run_locked_batch(lock, "embed_batch", _commit)
        print("committed %s/%s" % (embedded, len(rows)), file=sys.stderr)
    return {"candidates": len(rows), "embedded": embedded}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Embed attachment chunk text with local Ollama "
            "%s into attachment_embeddings (same SoR, %s-d)."
            % (el.DEFAULT_MODEL, el.DEFAULT_DIMS)
        )
    )
    parser.add_argument("--db", default=str(el.default_db_path()))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=el.DEFAULT_MODEL)
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION)
    parser.add_argument("--batch-size", type=int, default=el.DEFAULT_BATCH_SIZE)
    parser.add_argument("--ollama-url", default=el.DEFAULT_OLLAMA_URL)
    parser.add_argument("--dims", type=int, default=el.DEFAULT_DIMS)
    parser.add_argument("--vec-extension", default=None)
    parser.add_argument(
        "--lock",
        action="store_true",
        help="Writer lock per batch, purpose embed_batch.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Path(args.db).expanduser()
    try:
        if args.dry_run:
            conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
            conn.row_factory = sqlite3.Row
        else:
            conn = el.connect_db(db, args.vec_extension)
        try:
            backfill(
                conn,
                model=args.model,
                model_version=args.model_version,
                limit=args.limit,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                ollama_url=args.ollama_url,
                dims=args.dims,
                lock=args.lock,
            )
        finally:
            conn.close()
    except el.EmbedError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
