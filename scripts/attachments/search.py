#!/usr/bin/env python3
"""Read-only FTS citations over ATT-0 attachment chunks.

Opens the database mode=ro. Does not migrate and does not write.
No embedding queries.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_TOKEN_RE = re.compile(r"[0-9A-Za-z]+")

_MATCH_SQL = """
SELECT
  a.message_id,
  COALESCE(a.filename, ''),
  c.page_start,
  c.page_end,
  c.text
FROM (
  SELECT rowid AS chunk_id, rank
  FROM attachment_chunks_fts
  WHERE attachment_chunks_fts MATCH ?
  ORDER BY rank
  LIMIT ?
) AS hit
JOIN attachment_chunks AS c ON c.chunk_id = hit.chunk_id
JOIN attachment_extracts AS e ON e.extract_id = c.extract_id
JOIN attachments AS a ON a.attachment_id = e.attachment_id
ORDER BY hit.rank
"""


class SearchError(RuntimeError):
    """Read-only attachment search failed. Never includes secrets."""


@dataclass(frozen=True)
class AttachmentCitation:
    message_id: str
    filename: str
    page_start: int | None
    page_end: int | None
    snippet: str


def _ro_uri(path: Path) -> str:
    posix = path.resolve().as_posix()
    return "file:%s?mode=ro" % quote(posix, safe="/:")


def _snippet(text: str, query: str, window: int = 80) -> str:
    hay = text or ""
    token = ""
    for match in _TOKEN_RE.finditer(query or ""):
        token = match.group(0)
        break
    if not token:
        return hay[:window]
    idx = hay.lower().find(token.lower())
    if idx < 0:
        return hay[:window]
    start = max(0, idx - window // 2)
    end = min(len(hay), idx + len(token) + window // 2)
    return hay[start:end]


def _page(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def search_attachments(
    db: str | Path,
    query: str,
    *,
    limit: int = 10,
) -> list[AttachmentCitation]:
    """Return file and page citations for an FTS query. Read-only."""
    if limit < 1:
        raise ValueError("limit must be positive")
    text = (query or "").strip()
    if not text:
        return []
    path = Path(db)
    if not path.is_file():
        raise SearchError("database is not readable")
    try:
        conn = sqlite3.connect(_ro_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise SearchError("database is not readable") from exc
    try:
        try:
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error:
            pass
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='attachment_chunks_fts' "
            "AND type='table' LIMIT 1"
        ).fetchone()
        if present is None:
            raise SearchError("attachment search schema is missing")
        try:
            rows = conn.execute(_MATCH_SQL, (text, int(limit))).fetchall()
        except sqlite3.OperationalError as exc:
            raise SearchError("fts query failed") from exc
    finally:
        conn.close()
    citations = []
    for message_id, filename, page_start, page_end, chunk_text in rows:
        citations.append(
            AttachmentCitation(
                message_id=str(message_id),
                filename=str(filename or ""),
                page_start=_page(page_start),
                page_end=_page(page_end),
                snippet=_snippet(str(chunk_text or ""), text),
            )
        )
    return citations
