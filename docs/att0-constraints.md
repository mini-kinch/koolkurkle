# ATT-0 — Attachment lane constraints (design docs)

**Status:** DESIGN ONLY — docs/tests contract for a future combined PR  
**As-of:** 2026-09-14 ~2:32 PM PT  
**Sources:** Heavy `20260914-05-attachment-search-design` + Mailroom gaps (CoS-accepted)  
**Repo:** https://github.com/mini-kinch/koolkurkle  
**PII:** ZERO  
**Rem-legacy:** untouched — no live SoR writer from this packet

This is **ATT-0** only: schema/generation/skip/hard-deck language for docs + tests.  
**Not** ATT-1..8 implement. **Not** PR-5. **Not** a rem stop/restart.

---

## 1. Goal (from Heavy 05)

ask_mail can eventually answer from **attachment text** (PDF text-layer, Office, plain) and cite parent message + file + page/chunk.

**Non-goals (v1):** embed raw bytes/images; cloud vector DB; auto-open/send; blobs inside sqlite; OCR-everything day one; touch rem-legacy or body `message_embeddings`.

---

## 2. Why a new lane

Body rem uses `message_embeddings` (PK `message_id`). Attachment chunks must **not** overwrite or average into body vectors.

**Separate tables** + retrieve **union** (body FTS/KNN ∪ chunk FTS/KNN) → RRF → optional rerank → collapse to parent `message_id` for generate. Citations keep `message_id` + `attachment_id` + `chunk_ix` (+ page if known).

---

## 3. Hard decks (ship in docs/tests)

1. **One writer per `.sqlite`.** Extract/chunk/embed **workers write files**, not the SoR.
2. Attachment **bytes stay on disk** (or IMAP parts). sqlite = metadata + extracted text refs + 1024-d chunk vectors.
3. Same embed generation key as bodies unless a new generation is declared: `qwen3-embedding:8b`, store dim **1024**, instruction prefix. Mixed dim = refuse.
4. Chunk hits are first-class. Never invent ids.
5. Extracted text is untrusted **DATA** (same fence as bodies).
6. Extract fail ≠ tombstone the parent message. Attachment has its own status/`skip_reason`.
7. Bot box never holds SoR, live extracts, or attachment blobs.
8. Mini **copy-only until PR-5**. Attachment jobs on Mini use copy DB + local extract dir.
9. No SMB/NFS sqlite. Stage extract trees locally.
10. Do **not** rebuild/drop `message_embeddings` vec0 to “make room” for chunks.

### Mailroom-accepted constraints (2026-09-14)

11. **Auth / 2FA hard-gate** — `lane=auth` (and equivalent) must not enter extract→FTS/chunk retrieve by default. Auth body-block and never-text-codes extend to attachment text unless an explicit human ask lifts the gate for that message.
12. **History vs live for tombstoned parents** — Soft-delete Q1 DECIDED: ask_mail default = **history** (include `present_on_server=0`). Attachment hits for tombstoned parents follow the same mode: visible under history; hidden only under `--live` / live modes. Do **not** permanently hide attach hits solely because the parent is tombstoned.
13. **No live SoR catalog/apply while rem holds the lock** — Stage A (catalog), D (apply text), F (apply vec) are SoR writers. While rem-legacy (or any embed writer) holds live MBP SoR, only **file-stage** work (B/C/E sidecars) or work against a **copy DB** is legal. Live SoR catalog/apply waits rem **EXIT 0** + `with_writer_lock`.
14. **IMAP part-fetch rails** — Same as bodies-fts: Homebrew curl **≥ 8.17** for `BODY.PEEK`, Apple `/usr/bin/curl` fail-closed for streaming literals, Apple curl LS allow ≠ brew curl, `\Seen` restore is not delete, **never** `EXPUNGE` / `\Deleted` for hygiene.
15. **Never-purge + disk caps** — Do not `DELETE` from `attachment_*` as cleanup (same never-purge spirit as `messages`). Define disk_path / extract-tree caps and MBP+Mini backup expectations; skip/`too_big` beats silent ballooning.

---

## 4. Data model sketch (docs contract — not applied here)

Do not store blobs in sqlite. Paths + hashes only.

Logical tables (names locked for ATT-0 docs/tests):

- `attachments` — catalog; `attachment_id` stable; `message_id` logical FK **without** `ON DELETE CASCADE`; `present`, `skip_reason`
- `attachment_extracts` — extractor, `text_path` sidecar, status
- `attachment_chunks` — chunk text capped (~4k chars) for FTS
- `attachment_chunks_fts`
- `chunk_embedding_meta` + `chunk_embeddings` vec0(`float[1024]`, cosine)

`messages` / `message_embeddings` remain the body lane.

---

## 5. Extract policy (v1)

| Kind | v1 | Notes |
|------|----|-------|
| text/plain, csv | Yes | charset decode |
| PDF with text layer | Yes | pdftotext / pymupdf text |
| scanned / image-only PDF | Skip `needs_ocr` | OCR = later lane |
| docx/xlsx/pptx | Yes | textutil or libs |
| html parts | Yes | existing `mail_clean` |
| images | Skip | OCR later |
| eml / rfc822 | Yes | catalog child parts |
| zip/rar/7z | names only | no bomb recurse in v1 |
| exe/dmg/pkg/encrypted PDF | Skip | never execute |
| audio/video | Skip | separate generation later |

**Caps:** blob > 50 MB → `too_big`; extract text > 2 MB → truncate + flag; zip uncompressed estimate > 200 MB → skip; encrypted → skip. Extractors **out of process**; per-file timeout; crash isolation from SoR writer.

---

## 6. Chunking defaults

- Target 512–1024 tokens (~2–4k chars), overlap 10–15%
- Split page → heading → paragraph
- Frozen prefix (filename, parent subject/from/date, page range) is part of `embed_document_version`
- `content_hash` over prefix+text; incremental skip on match

---

## 7. Retrieve shape

1. Body FTS + body KNN (existing)  
2. Chunk FTS + chunk KNN (new)  
3. One RRF over unified hits tagged `source=body|attach` (lock this; do not invent a second product). Same one-RRF product later tags `source=body|attach|imsg|note` — [unified-search-design.md](unified-search-design.md).  
4. Optional CrossEncoder fail-open labeled  
5. Collapse to parent `message_id` for generate; citations still list file + page/chunk  
6. Cap attach snippets in prompt; `--no-attach` flag; `--fts-only` includes chunk FTS  

Mode interaction: **history** (default) may include attach hits whose parents are tombstoned; **`--live`** filters parents to `present_on_server=1` (and future live_mailboxes/trash_live as already designed for bodies).

---

## 8. Stage machine (parallelism)

```
A  CATALOG      one writer     attachments rows
B  EXTRACT      N workers      sidecar .txt only
C  CHUNK        N workers      sidecar chunk jsonl
D  APPLY_TEXT   one writer     chunks + FTS
E  EMBED        N workers      sidecar parquet/vec
F  APPLY_VEC    one writer     chunk_embedding_meta + vec0
```

A/D/F never overlap each other or rem on the **same** live sqlite.  
B/C/E may be many-wide on files.

**Shard keys:** MIME phase (P0 catalog → P1 PDF/plain → P2 office → P3 OCR later) → size band (S/M Mini, L MBP, X skip) → `hash(attachment_id) % N` embed shards → missing-only apply under `with_writer_lock`.

Embed batch start **32** (not 256). Dim ≠ 1024 refuse. Rem-lock refuse if rem still running.

---

## 9. Daily increment (after backfill)

Body path unchanged. New parts: short catalog under lock → S-band extract on daily host → L-band queue → incremental embed by `content_hash` → one-writer apply.  
Daily must not start `--reembed-legacy` or P3 OCR.

---

## 10. Suggested ID split (unchanged)

| ID | Scope |
|----|--------|
| **ATT-0** | This packet — schema + generation key + skip + hard decks (docs/tests) |
| ATT-1 | Catalog only |
| ATT-2 | Extract P1 → sidecars |
| ATT-3 | Chunk + FTS apply |
| ATT-4 | Embed sidecar + vec apply |
| ATT-5 | Retrieve union + citations + `--no-attach` |
| ATT-6 | Daily increment queue |
| ATT-7 | P2 office |
| ATT-8 | P3 OCR (separate GO) |

ATT-0..5 = product spine. None of A/D/F start on live SoR while rem holds the writer except file-only stage against copy/stage tree.

---

## 11. What not to do

- Wait on ATT before rem EXIT 0 body protocol  
- Put attachment vectors in `message_embeddings`  
- 2-wide apply on live SoR  
- OCR the whole archive in the first PR  
- Load frozen JSONL wholesale to find parts  
- Different embed dim “just for PDFs”  
- Let Mini write SoR because “attachments are extra”  
- Use attachment retrieve to bypass auth body-block  

---

## 12. Repo touch targets (for Developer docs PR)

Expected docs landing (Developer chooses exact paths on main):

- New: `docs/attachment-search.md` (or `docs/att0-constraints.md`) containing this contract  
- Cross-links from `docs/MAILROOM.md`, `docs/ask_mail.md`, `docs/embed-backfill.md`, `docs/tombstone.md`  
- Tests: contract/needle tests only (no live IMAP, no SoR open required)
- Later rem-safe sibling (not ATT implement): [unified-search-design.md](unified-search-design.md) — Heavy-06 / 06b; Ready ≠ MSG/NOTE enable

---

## 13. Refresh (2026-09-26)

The operating picture changed after this 2026-09-14 contract. The refresh is
[attachments/ATT-0-design.md](attachments/ATT-0-design.md) (sole SoR writer
is one Mac mini; search is FTS-only because there is no embedding service
and Ollama is down; daily writes use Scope B). The text above stays the
Heavy 05 record. The refresh does not delete this contract.

---

*End ATT-0. ZERO PII. DESIGN ONLY. Rem untouched.*
