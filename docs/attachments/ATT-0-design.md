# ATT-0 design refresh — attachment search (FTS)

**Status:** design + code and tests. The migration is not applied to any database by this change. The metadata fill is not run against a live mailbox or the system of record.
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

The script loads that SQL. It is idempotent (`IF NOT EXISTS`, triggers created only when missing, FTS `rebuild` to match the content table). It does not bump `user_version`. It does not enable a writer pragma profile. It does not call `ask_mail`. The CLI returns exit code 2, and does not create a file, when `--db` is omitted or the path is not an existing database.

| Table | Columns |
| --- | --- |
| `attachments` | `attachment_id` INTEGER PK, `message_id` FK → `messages(id)`, `part_id`, `filename`, `mime`, `size`, `sha256`, `status`, `content_disposition` |
| `attachment_meta_scans` | `message_id` PK, `source`, `part_count`, `has_attachments`, `scanned_at` (resume marker for the metadata fill) |
| `attachment_folder_uidvalidity` | `folder` PK, `uidvalidity` (baseline recorded by the first fill of that folder, not at ingest) |
| `attachment_extracts` | `extract_id` INTEGER PK, `attachment_id` FK, `extractor`, `extractor_version`, `text`, `page_count`, `status`, `error`, `timings` |
| `attachment_chunks` | `chunk_id` INTEGER PK, `extract_id` FK, `chunk_index`, `page_start`, `page_end`, `text` |
| `attachment_chunks_fts` | FTS5 `text`, external content `attachment_chunks`, `content_rowid=chunk_id`, `tokenize=unicode61` (no porter, so identifiers are not stemmed) |

`timings` is a JSON text blob (`elapsed_ms`, and `bytes` when known).

If `attachment_extracts` or `attachment_chunks` already exists with a different column list, the script raises and does not change that table.

`attachments` may already exist as the PR-1 sketch: columns `id`, `message_id`, `filename`, `mime_type`, `size_bytes`, `content_hash`, `path`, `created_at`, in that order. Behavior:

- **Empty mismatch.** If `attachments` exists, its columns are not the ATT-0 list, and it has **zero rows**, the script renames it to `attachments_pr1_empty` and then creates the ATT-0 `attachments` table. The old definition is kept under the new name. There is nothing to drop. Any other empty column mismatch is renamed the same way, not only the PR-1 sketch. The rest of the schema, including `attachment_meta_scans`, is created in that same run.
- **Non-empty mismatch.** If the mismatched table has one or more rows, the script refuses loudly and leaves every object untouched. It does not rename, alter, or drop. The rows stay. `attachment_meta_scans` is not created on that failure.
- **Rename target already present.** If `attachments_pr1_empty` already exists, an empty mismatch is also a refuse. `attachments` is left as it is.

Data is never dropped. A later run, after a successful rename, sees the ATT-0 column list and does not rename again. This packet still does not apply the migration to a live database.

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

## Metadata-only catalog fill

A read-only audit of the live system of record found **0 rows** in any attachments data. `messages.has_attachments` is **0 on all ~65.5k rows**. About **2.1k** rows have `source='imap-live'`. About **63.4k** rows were imported from a JSONL dump and already store `messages.jsonl_offset` and `messages.jsonl_len`.

Those counts are why ATT-0 needs a metadata fill before extract or FTS can see attachments. The fill writes **metadata only**:

| Stored | Not stored |
| --- | --- |
| mime type, size in bytes, part index/path (`part_id`), content-disposition | part bytes, body text, sha256 (stays NULL), extract rows, chunk rows |
| `messages.has_attachments` (0 or 1) | any body-section fetch |

`attachments.status` for these rows is `meta`. `bytes_stored` in the report is always 0. The body text is parsed only to learn the tree and the decoded size, then dropped.

Script: `scripts/attachments/meta_fill.py`. It does not create schema and it does not reshape `messages`. Run `migrate_att0_schema.py` on the copy first. That migration creates `attachments`, `attachment_extracts`, `attachment_chunks`, `attachment_chunks_fts`, `attachment_meta_scans`, and `attachment_folder_uidvalidity`. It does not create or alter `messages.has_attachments`; that column is already on `messages`, and the fill updates it when the column is present. A missing `has_attachments` column or a missing ATT-0 table is a refuse. A database migrated by an older copy of this script, before `attachment_folder_uidvalidity` existed, must be migrated again. The rerun is idempotent: `CREATE TABLE IF NOT EXISTS` adds only the missing table and does not rewrite existing attachment rows. The migration is atomic: `BEGIN IMMEDIATE`, every DDL statement, then `COMMIT`. A refuse, including a mismatched `attachments` table that already has rows, runs `ROLLBACK` and leaves the database file bytes unchanged (same sha256 and size).

### Option 1 — IMAP rows (`source='imap-live'`)

`--source imap`. UIDs are unique only inside one `messages.folder`. `_select_rows` does not load every imap-live row and then select one mailbox. A full pass queries `DISTINCT` folder keys, then for each folder runs `AND folder=?` (a null or blank folder uses `folder IS NULL OR TRIM(folder)=''`) and issues `EXAMINE` (read-only) on the base URL for that folder before UID FETCH of `(BODYSTRUCTURE)` for its rows. The same UID in two folders is two rows. `--mailbox` (or `MAILROOM_IMAP_MAILBOX`) is a single `AND folder=?` query for that folder. Rows in other folders, or with a null or blank folder, are not fetched. They are counted in `skipped` and are not marked scanned. Omit `--mailbox` to select every folder that still has unscanned rows. A row with no folder is an error and is not marked scanned.

On the live copy the unscanned imap-live spread is Deleted 980, Junk 509, INBOX 422, Newsletters 177, Sent 34, and 9 in others (2131 rows). A full pass has to walk that set folder by folder.

The production transport is pinned `/usr/bin/curl` `imaps://` on port 993. `fill_metadata` and the CLI construct `_CurlProductionClient` only. They do not open a Python socket to the IMAP host. `CURL_BIN` is not read and there is no Homebrew curl fallback. The binary is the literal `/usr/bin/curl`. Per folder, one curl process logs in once. The URL is the base `imaps://<host>:993/` with no mailbox in the path, so curl does not send SELECT. The first transfer is `-X` `EXAMINE "<mbox>"` on that base URL. Transfers are joined by `next` in one curl config on stdin (`-K -`). The process is started with `--fail-early`, so a failed transfer does not open the next connection. `next` resets per-transfer options, so every transfer repeats `user`, `connect-timeout`, `max-time`, `write-out`, and `cacert` when a test certificate is injected. The mailbox in `EXAMINE` is IMAP-quoted (`"Deleted Messages"`, with quotes and backslashes escaped). Later transfers are `UID FETCH <uidset> (BODYSTRUCTURE)` with UIDs batched (ranges such as `1:50,77`, bounded batch size). No other IMAP command is allowed. A mailbox name containing CR, LF, or NUL is rejected. `UID FETCH` of `(BODYSTRUCTURE)` does not set the seen flag. Curl stdout is read as bytes so a CRLF before a `{n}` literal is preserved. There is no `--dump-header`. UIDVALIDITY is the first untagged `* OK [UIDVALIDITY n]` in the EXAMINE reply, before any `* N FETCH` line. It is not the last match in the buffer and it is not the tagged `[READ-ONLY]` text, which curl does not print. `write-out` reports `num_connects`; a second connection fails closed and is not retried. A curl status other than 0 counts that folder's UIDs as errors, keeps them unscanned, writes the report, and continues with later folders. It is not retried, including one UID at a time, and the command does not exit with an empty report: 21 is NO or BAD, 7 is connect, 28 is timeout, 60 is the certificate, 67 is login. The message is the classified status only, with no curl stderr and no secret. A UID that was requested and is missing from the batch reply is an error and is not marked scanned. After each folder, stderr is flushed with `HH:MM PT | folder n/N | rc=N` (Pacific time, folder index only, no folder name) so a stall watcher does not false-stall. Stderr from curl is redacted before it is retained. There is no `-v`, `--verbose`, or `--trace`.

`ImapBodystructureClient` remains a test-only double over `imaplib.IMAP4_SSL` on port 993 with `ssl.create_default_context()`. It lives in `tests/imap_bodystructure_double.py`. `scripts/attachments/meta_fill.py` does not import it and does not import imaplib. That context has `verify_mode` `CERT_REQUIRED` and `check_hostname` true, and that same object is passed as `ssl_context`. Plain IMAP is not constructed. The double is not reachable from the CLI or from `fill_metadata`. Its EXAMINE response UIDVALIDITY is still read with `IMAP4.response('UIDVALIDITY')`, which pops the untagged value and returns `('UIDVALIDITY', [b'42'])`, or `('UIDVALIDITY', [None])` when it is absent. Tests of that double mock `imaplib.IMAP4_SSL`. A production-path test patches `imaplib.IMAP4_SSL` and `socket.create_connection` to raise and still completes through curl.

The baseline is recorded in `attachment_folder_uidvalidity` on the first `--apply` fill of that folder, not when the message row is ingested. A later run that sees a different UIDVALIDITY does not write those rows. `uidvalidity_mismatch` is the number of those rows, and the `PARTIAL:` banner includes `uidvalidity_mismatch=N`. Host and user come from `--host` / `--user` or `MAILROOM_IMAP_HOST` / `MAILROOM_IMAP_USER`. There is no password option and no password environment variable. The password is read from macOS Keychain the same way `scripts/run_mailroom_daily.sh` loads it for the IMAP scripts: `/usr/bin/security find-generic-password -s mailroom.imap.app-password -w`, with one fallback to `mailroom.icloud.app-password` when the default item misses. The binary and the item name are pinned. `MAILROOM_SECURITY_BIN` and `MAILROOM_KEYCHAIN_ITEM` are not read. The helper is `scripts/imap_keychain.py`. Curl receives the password only on stdin via `-K -` as `user = "..."`, with curl-config quoting. It is never placed in argv, the environment, or a file. TLS verification stays on (no `-k` / `--insecure`). A test may inject `--cacert` for a throwaway local certificate; production does not. The password is not written to the report, the database, or the repository. Nothing in this packet connects to a real mailbox. This is the `docs/ops-terminal.md` contract: IMAP live checks use `/usr/bin/curl imaps://` and never open a Python socket to the IMAP host. A test may pass `cacert` and `imap_port` into `fill_metadata` so that function's curl client can EXAMINE, read UIDVALIDITY, and UID FETCH `(BODYSTRUCTURE)` against a local fake IMAPS server. `main` does not pass those arguments.

### Option 2 — archive rows (JSONL dump)

`--source jsonl`. One streamed pass over rows whose `source` is not `imap-live` and whose `jsonl_offset` is not NULL, ordered by that offset. The dump is opened read-only (`rb`). Each row seeks to `jsonl_offset` and reads `jsonl_len` bytes in 1 MiB chunks. The frozen dump stores the RFC822 text on a `raw` key. A slice that starts with `{` is a JSON object: `raw` wins when that key is present, otherwise `rfc822`. Anything else is raw RFC822. The JSON wrapper is not retained. MIME headers are parsed the same way as option 1. The dump is not rewritten. Tests use a synthetic dump name.

### What a part row means

Part ids follow the tree. A multipart root is `0`. Its children are `1`, `2`, `3`, … A nested multipart `1` has children `1.1`, `1.2`. An attached `message/rfc822` keeps its own id, and the encapsulated body is `4.1` (or `4.1`, `4.2`, … when that body is multipart). Every node is a row, including `text/plain` and multipart containers, so the catalog is complete.

`has_attachments` is 1 when any part in the full tree is an attachment, including parts past `--max-parts`:

- disposition `attachment`, or
- `message/rfc822`, or
- a major type of image, audio, video, or application, or
- disposition `inline` on anything other than `text/plain` or `text/html`

Multipart containers are not attachments. `text/plain` and `text/html` without an attachment disposition are not attachments. An inline image is an attachment. The flag is computed from the full tree before any `--max-parts` prefix is stored. `--max-parts 0` means no cap and does not increment `capped`. A positive cap that drops a suffix stores the prefix, increments `parts_truncated` by one message, and still records the full-tree `has_attachments` so a 0 flag is not a stand-in for "the tree was cut". `max_parts` keeps a prefix of the walk, so set it before the first apply.

A body-only message still gets an `attachment_meta_scans` row (`has_attachments` 0) so a later run skips it. `part_count` is the number of MIME rows stored.

### Filename flag (default off)

`--store-filenames` defaults **off**. When it is off, `attachments.filename` stays **NULL**. The filler does not write a hash or an extension in its place. When the flag is on, the raw filename is stored (content-disposition filename, otherwise the MIME name parameter).

`attachment_meta_scans.message_id` is the resume key. A later run skips that message, so turning the flag on later does **not** backfill names. The same is true of `max_parts`: a truncated tree is marked scanned. Choose both before the first apply.

A record longer than `--max-record-bytes` is not parsed from a short slice and is **not** marked scanned, so a later higher cap can retry. The flag accepts plain bytes or a human size (`64MB` and `64MiB` are both 1024*1024, so `64MB` is 67108864). The default is 64 MiB (67108864 bytes), which already includes records over 2 MB and over 10 MB (about 1,503 records are over 2 MB, and 257 are over 10 MB). Pass a larger `--max-record-bytes`, such as `128MB`, to raise that cap. `64MB` is the default, not a value the full pass has to raise the flag to. Each excluded row increments `capped` and is not counted as `scanned` or `skipped`. The text summary always prints `capped: N` on its own line. The dump is not loaded. Each row seeks to `jsonl_offset` and streams at most one record.

Peak memory was measured with `tracemalloc` around one dry-run `fill_metadata` of a synthetic JSONL record. The record is a JSON object whose `raw` value is a `text/plain` message with a 60 MiB body (`60*1024*1024` = 62914560 bytes of ASCII `A`), written by hand so `json.dumps` never builds a second copy of the body. The trace starts immediately before `fill_metadata` and the peak is `tracemalloc.get_traced_memory()[1]` after it returns. On CPython 3.12.3 that peak was 699503008 bytes (667.1 MiB). The JSON reader streams the file in 1 MiB chunks and keeps the decoded message, not the wrapper. The peak above that decoded message is `email.message_from_bytes` (`policy.default`), which turns the RFC822 text into one `str` and copies the body line while it records the part size. Those buffers are released before the next row. The same ratio applied to a record at the 64 MiB cap is about 746136542 bytes (711.6 MiB).

A parse error, a missing UID, or a bad offset is an error and is not marked scanned. `--max-messages` (default 200) and `--timeout` (default 30 seconds) stop before the next message. Those defaults do not cover the whole mailbox, and the summary still starts with a `PARTIAL:` banner when they stop the run early. A full pass is `--max-messages 0 --timeout 0` (0 means no cap for those two flags). `--max-record-bytes` already defaults to 64 MiB; pass a larger value to raise it. When the run stops early and rows remain, the text summary starts with a loud banner `PARTIAL: scanned X of Y (limit max_messages=…)` or `PARTIAL: scanned X of Y (limit timeout=…)`, and the JSON summary sets `partial` to true. X is how many eligible rows this run handled. Y is the eligible rows at the start of the run (after a mailbox filter). Rows already committed stay. The process exit code is still 0. `scanned` in the report is how many matching rows were already in `attachment_meta_scans` and were left alone. `skipped` is unscanned rows excluded by `--mailbox` (a different folder, or a null or blank folder). `capped` is rows excluded by `--max-record-bytes` only. `--max-parts 0` is not a cap. `parts_truncated` counts messages whose stored part list is a prefix. `uidvalidity_mismatch` counts rows whose folder UIDVALIDITY disagreed with the baseline stored on the first fill; those rows are not written, and the `PARTIAL:` banner includes `uidvalidity_mismatch=N`. Those numbers are separate.

### Writer rules

Both options are writers, including dry-run:

- `refuse_destructive.refuse_destructive_cli` runs first.
- Basename `mailroom.sqlite` is refused unless `--allow-mailroom-sqlite`, **before** a connection, including dry-run. The writer gate still runs after that flag.
- The database file must already exist. A missing path exits 2 and does not create a file.
- Default is dry-run (counts only). `--apply` writes. Passing both exits 2.
- Dry-run opens the file `mode=ro` with `query_only`, so it cannot write. The report prints counts only: `dry_run`, `source`, `db_basename` (not a full path), `messages`, `parts`, `has_attachments`, `filenames`, `bytes_stored=0`, `scanned`, `eligible`, `stopped`, `capped`, `skipped`, `errors`, `partial`, `parts_truncated`, `uidvalidity_mismatch`, and its own line `capped: N`. When `partial` is true the first line is the `PARTIAL:` banner. The same object is printed as one JSON object on the `summary_json=` line.
- Each apply commits one message: part rows, `has_attachments`, and the scan row, together. A second apply does not insert those parts again.

### Live run needs a separate approval

This packet does not run the fill against a live mailbox or the system of record. Doing that is a separate approval. In that approval the user decides whether `--store-filenames` is on. Until then, the supported check is `--dry-run` against an explicit copy database.

The dry-run is meant to run from a `/tmp` clone with no install. From the clone root:

```
PYTHONPATH=scripts:scripts/attachments python3 scripts/attachments/meta_fill.py --dry-run --db /tmp/mailroom-copy.sqlite --source jsonl --jsonl /tmp/archive.jsonl --max-messages 0 --timeout 0 --max-record-bytes 64MB
```

`PYTHONPATH=scripts:scripts/attachments` is required for that layout. The script also inserts its own directory on `sys.path`, and it does not need a package install.

---

## Out of scope for this packet

- Applying the migration to the system of record or to a daily copy.
- Embeddings, Ollama, vec0, RRF, rerank, and any edit to `scripts/ask_mail.py`.
- OCR, archive member listing, and IMAP fetches of body bytes (`BODY[]`, `BODY.PEEK[]`, `RFC822`).
- A live metadata fill. The design and the dry-run CLI are in this packet. Running it against the system of record needs a separate approval.
- Auth-lane enforcement and history/`--live` filtering (still specified in the 2026-09-14 contract, not coded here).

---

*End ATT-0 refresh. No personal info. Migration not applied. `message_embeddings` untouched.*
