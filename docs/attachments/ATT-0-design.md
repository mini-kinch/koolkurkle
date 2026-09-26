# ATT-0 design refresh — attachment search (FTS)

**Status:** design + code and tests. The migration is not applied to any database by this change.
**As-of:** 2026-09-26
**Built from:** [att0-constraints.md](../att0-constraints.md), the repo copy of Heavy `20260914-05-attachment-search-design` (as-of 2026-09-14, about 2:32 PM PT).
**PII:** none. Fixtures use example.com and made-up names only.

This refresh is the ATT-0 implementation spec for schema, file-only extract, chunk page ranges, and FTS citations. It does not replace the 2026-09-14 contract. That file stays the Heavy 05 record. `scripts/ask_mail.py` is unchanged.

---

## What changed from Heavy 05, and why

### 1. One Mac mini is the sole SoR writer

Heavy 05 hard deck 8 and the stage machine assumed two machines: the MBP held the live SQLite system of record, the Mac mini was copy-only until PR-5, and rem-legacy was the sole writer on basename `mailroom.sqlite` until EXIT 0.

Today one Mac mini is the sole writer of that SQLite system of record. There is no second machine writing the file. Attachment work still must not become a second writer on it. Scope B (below) is how this packet keeps that rule.

### 2. Search is FTS-only. There is no embedding service. Ollama is down.

Heavy 05 retrieve was body FTS + body KNN union chunk FTS + chunk KNN, one RRF list tagged `source=body|attach`, then optional rerank. The data model added `chunk_embedding_meta` and `chunk_embeddings` vec0 `float[1024]` for `qwen3-embedding:8b`. Stages E (embed workers) and F (apply vec) were part of the product spine (ATT-4).

Today there is no embedding service, and Ollama is down. An embedding code path would have nothing to call. This packet does not create `chunk_embeddings`, `chunk_embedding_meta`, or any vec0 table. It does not read or write `message_embeddings`. Attachment retrieve is FTS5 over `attachment_chunks` only. A hit cites the parent message, the filename, and the chunk page range. Embed stages stay future work outside this packet.

### 3. The daily pipeline uses Scope B

Scope B means attachment writes target an **explicit copy database**, set beside the system of record, and every write is gated by the existing helpers:

- `sor_writer_gate.refuse_if_sor_writer_conflict`
- `refuse_destructive.refuse_destructive_cli`

The copy basenames the daily pipeline already allows are `mailroom-copy.sqlite` and `mailroom-daily-copy.sqlite`.

Heavy 05 already forbade live SoR catalog/apply while a writer held the lock, and it allowed file-stage work against a copy DB. The gate alone still **allows** basename `mailroom.sqlite` when no rem process is running and the writer lock is free. That is the wrong default now that the mini is the sole SoR writer: a quiet lock is not permission to DDL the system of record. The ATT-0 migration therefore **refuses a database named `mailroom.sqlite` unless `--allow-mailroom-sqlite` is passed**, and it still calls the writer gate after that flag so a held lock or a rem process remains a CONFLICT. Destructive CLI verbs (`purge`, `expunge`, and the rest of the existing set) refuse before any connection is opened.

This change does not run the migration. Tests use temporary files only.

---

## What stayed from Heavy 05

- Attachment bytes stay out of sqlite. Rows hold metadata, extracted text, and hashes.
- Attachment chunks must not be stored in `message_embeddings`. A mismatched pre-existing table is a refuse, not an `ALTER` or a `DROP`.
- `message_id` is a logical foreign key **without** `ON DELETE CASCADE`.
- Extract failure does not tombstone the parent message. The attachment row has its own status.
- Extracted text is untrusted data.
- v1 skips OCR, images, video, audio, archives, and executables. Encrypted PDFs skip as well (Heavy 05 "never execute").
- Caps: file bytes (default 50 MiB), extracted text (default 2,000,000 chars, the Heavy 05 2 MB text cap counted as characters), plus a page cap and a per-file timeout.
- Extractors do not shell out and do not execute anything found in an attachment.
- Citations keep the parent `message_id`, the filename, and the page range.
- Auth hard-gate, history versus live, never-purge, and IMAP part-fetch rails in the 2026-09-14 contract are unchanged and are not implemented here.

---

## Schema

Script: `scripts/attachments/migrate_att0_schema.py`  
SQL: `scripts/attachments/schema.sql`

The script loads that SQL. It is idempotent (`IF NOT EXISTS`, triggers created only when missing, FTS `rebuild` to match the content table). It does not bump `user_version`. It does not enable a writer pragma profile. It does not call `ask_mail`.

| Table | Columns |
| --- | --- |
| `attachments` | `attachment_id` INTEGER PK, `message_id` FK → `messages(id)`, `part_id`, `filename`, `mime`, `size`, `sha256`, `status` |
| `attachment_extracts` | `extract_id` INTEGER PK, `attachment_id` FK, `extractor`, `extractor_version`, `text`, `page_count`, `status`, `error`, `timings` |
| `attachment_chunks` | `chunk_id` INTEGER PK, `extract_id` FK, `chunk_index`, `page_start`, `page_end`, `text` |
| `attachment_chunks_fts` | FTS5 `text`, external content `attachment_chunks`, `content_rowid=chunk_id`, `tokenize=unicode61` (no porter, so identifiers are not stemmed) |

`timings` is a JSON text blob (`elapsed_ms`, and `bytes` when known).

If `attachments`, `attachment_extracts`, or `attachment_chunks` already exists with a different column list, the script raises and does not `ALTER` or `DROP`. The PR-1 sketch of `attachments` (`id`, `mime_type`, `size_bytes`, `content_hash`, `path`, …) is a different table of the same name. ATT-0 will not reshape it. Apply ATT-0 only to a database that does not already have that table. This packet does not apply it at all.

After DDL, the script compares `sqlite_master` for every object it does not own, and it compares the `message_embeddings` SQL (and row count when the table can be counted). A change there is a hard refuse.

Tokenizer choice: Heavy 05 did not pin the FTS tokenizer. `unicode61` without porter matches `messages_ids` (do not stem tokens that must stay whole).

---

## Extract (file only)

Module: `scripts/attachments/extract.py`  
Entry: `extract_file(path, ...)`.

The input is a filesystem path. The module reads bytes up to the file-size cap. It never calls `subprocess`, a shell, `textutil`, `pdftotext`, or an office application. Optional `pypdf` is imported inside a `try/except ImportError`. If it is missing, PDF text still uses the stdlib text-layer reader. If both fail to parse a PDF, the status is `unsupported` (no traceback, no partial binary dump).

| Kind | v1 | How |
| --- | --- | --- |
| plain text, markdown | yes | stdlib decode (`utf-8-sig` / `utf-8`). Form feed splits pages. |
| csv | yes | same decode, one page unless the file contains a form feed |
| PDF with a text layer | yes | stdlib reader for `Tj` / `TJ` / `'` and FlateDecode. `pypdf` only if the stdlib reader does not recognize the file |
| PDF with no text | `skip_ocr` | OCR is out of scope |
| encrypted PDF | `skip_encrypted` | no decrypt, no execute |
| docx, xlsx, pptx | yes | stdlib `zipfile` + `xml.etree`. Page breaks, sheets, and slides become page numbers. A member larger than the byte cap is `too_big` and is not inflated. |
| images | `skip_image` | magic or mime or suffix |
| video | `skip_video` | magic or mime or suffix |
| audio | `skip_audio` | magic or mime or suffix |
| zip, rar, 7z, gz, tar | `skip_archive` | office packages are recognized before this skip. Other archives are not unpacked. |
| exe, dll, dmg, pkg, MZ, ELF, Mach-O | `skip_executable` | magic wins over a `.txt` suffix |
| anything else (for example RTF) | `unsupported` | |

Status values: `ok`, `capped`, `too_big`, `timeout`, `unsupported`, `skip_ocr`, `skip_image`, `skip_video`, `skip_audio`, `skip_archive`, `skip_executable`, `skip_encrypted`, `error`.

Caps, all overridable per call:

| Cap | Default | Status |
| --- | --- | --- |
| file bytes | 50 MiB | `too_big` / error `max_bytes` (body is not read) |
| pages read | 500 | `capped` / error `max_pages` |
| extracted chars | 2,000,000 | `capped` / error `max_chars` |
| per-file timeout | 30 seconds | `timeout` / error `timeout` |

`page_count` is the document page count when the parser knows it. `pages` is the text actually returned after caps. `truncated` is true when status is `capped`. A timeout of zero or less returns `timeout` without reading the body. The alarm runs on the main thread (`SIGALRM`); the finally path always disarms it.

Charset failures return `error` / `charset`. They do not guess a single-byte encoding.

---

## Chunks and citations

Chunking: `scripts/attachments/chunk.py`, `chunk_pages(pages, *, target_chars=3000, overlap_chars=None)`.

Heavy 05 asked for roughly 2–4k characters and 10–15% overlap. The default target is 3000 characters. The default overlap is 12% of the target (360 characters).

Rules:

1. `chunk_index` starts at 0 in reading order.
2. Empty pages are omitted.
3. A page longer than the target is split on that page only. `page_start == page_end`. The next window starts `overlap_chars` before the previous cut. Overlap does not pull in a neighbor page.
4. Shorter pages pack until the next page would pass the target. Packed chunks record the first and last page included. Packed chunks do not overlap.
5. Page text inside a chunk is joined with newlines.

Search: `scripts/attachments/search.py`, `search_attachments(db, query, *, limit=10)`.

The helper opens the database `mode=ro` and sets `query_only`. It does not migrate, insert, or update. It runs an FTS5 `MATCH` against `attachment_chunks_fts`, joins chunk → extract → attachment, and returns `AttachmentCitation` values:

- `message_id`
- `filename`
- `page_start`, `page_end` (the page range)
- `snippet` (a window of the chunk text around the first query token)

A database without the FTS table raises `SearchError`. The helper does not create the table.

---

## Out of scope for this packet

- Applying the migration to the system of record or to a daily copy.
- Embeddings, Ollama, vec0, RRF, rerank, and any edit to `scripts/ask_mail.py`.
- OCR, archive member listing, and IMAP part fetch.
- Auth-lane enforcement and history/`--live` filtering (still specified in the 2026-09-14 contract, not coded here).

---

*End ATT-0 refresh. No personal info. Migration not applied. `message_embeddings` untouched.*
