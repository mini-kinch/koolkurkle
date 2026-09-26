#!/usr/bin/env python3
"""Page-aware chunking for ATT-0 attachment text.

Default target is 3000 characters with a 12 percent overlap on splits of
a long page (Heavy 05 asked for about 2-4k characters and 10-15 percent
overlap). Packed short pages do not overlap. No embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    page_start: int
    page_end: int
    text: str


def _split_page(
    page: int,
    text: str,
    target_chars: int,
    overlap_chars: int,
) -> list[tuple[int, str]]:
    parts: list[tuple[int, str]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + target_chars)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind(" "))
            if cut >= target_chars // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            parts.append((page, piece))
        if end >= n:
            break
        nxt = end - overlap_chars
        if nxt <= start:
            nxt = end
        start = nxt
    return parts


def chunk_pages(
    pages: list[tuple[int, str]] | tuple[tuple[int, str], ...],
    *,
    target_chars: int = 3000,
    overlap_chars: int | None = None,
) -> list[TextChunk]:
    """Pack ``(page_number, text)`` into chunks with page ranges.

    Page numbers are the caller's numbers (1-based for extractor output).
    A page longer than ``target_chars`` is split inside that page. The
    next window starts ``overlap_chars`` earlier. Shorter pages pack
    until the next page would exceed the target.
    """
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    if overlap_chars is None:
        overlap_chars = int(target_chars * 0.12)
    if overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("overlap_chars must be >= 0 and < target_chars")

    groups: list[list[tuple[int, str]]] = []
    pack: list[tuple[int, str]] = []
    pack_len = 0

    def flush() -> None:
        nonlocal pack, pack_len
        if pack:
            groups.append(pack)
            pack = []
            pack_len = 0

    for page, raw in pages:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValueError("page numbers must be integers >= 1")
        text = (raw or "").strip()
        if not text:
            continue
        if len(text) > target_chars:
            flush()
            for piece in _split_page(page, text, target_chars, overlap_chars):
                groups.append([piece])
            continue
        extra = len(text) if pack_len == 0 else pack_len + 1 + len(text)
        if pack and extra > target_chars:
            flush()
        if pack:
            pack_len = pack_len + 1 + len(text)
        else:
            pack_len = len(text)
        pack.append((page, text))
    flush()

    chunks: list[TextChunk] = []
    for index, group in enumerate(groups):
        body = "\n".join(part for _, part in group)
        page_nums = [page for page, _ in group]
        chunks.append(
            TextChunk(
                chunk_index=index,
                page_start=min(page_nums),
                page_end=max(page_nums),
                text=body,
            )
        )
    return chunks
