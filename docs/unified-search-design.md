# Unified-search / ask_all (Heavy-06 + Mailroom 06b)

**Status:** DESIGN ONLY — docs/tests contract for a future combined PR  
**As-of:** 2026-09-14 ~2:37 PM PT  
**Sources:** Heavy `20260914-06-unified-search-design` + Mailroom
`20260914-06b-mailroom-gaps-accepted` (CoS-accepted)  
**Repo:** https://github.com/mini-kinch/koolkurkle  
**PII:** ZERO  
**Rem-legacy:** untouched — no live SoR writer from this packet

This is **Heavy-06 / 06b** only: unified-search spine + locked Mailroom
gaps for docs + tests. **Not** MSG/NOTE implement. **Not** ATT-1..8
implement. **Not** PR-5. **Not** a rem stop/restart/hot-swap.

**Ready ≠ MSG/NOTE enable permission.** Ready = this docs/tests PR URL.

Factory note: **KOO-65..70 Done (#36 merged).** Do not fold this packet
back into the ATT-0 PR. ATT-1..8 implement remains FUTURE. This change
does not start msgroom/noteroom, does not open `chat.db` / NoteStore,
does not grant FDA, and does not dump Messages/Notes into
`mailroom.sqlite`.

---

## 0. ACK CoS PT 2:28 on Heavy-05 — locked

Keep (agreed with ATT-0 / [att0-constraints.md](att0-constraints.md)):

- Separate chunk tables. Do not touch `message_embeddings`.
- File-only extract workers.
- SoR apply waits rem EXIT 0.
- Same 1024-d generation key.
- Citations = file + page.
- v1 skip OCR / video / executables.
- ATT-0..5 is the product cut.
- Caps 50MB blob / 2MB extract text / zip-bomb refuse. Out-of-process
  extract + timeout.

Tighten (now locked — was open in 05):

1. **RRF is one unified list** tagged `source=body|attach`. Not two RRF
   then merge. Same rule later: `source=body|attach|imsg|note` in one
   list. Do not reopen “or two RRF.” Do not invent a second RRF product.
2. **Blob-tree path contract** (one tree, both machines):

```
$MAILARCHIVE/att-blobs/<sha256[:2]>/<sha256>     mail attachment bytes
$MAILARCHIVE/imsg-blobs/<sha256[:2]>/<sha256>    copy-out from Messages/Attachments
$MAILARCHIVE/note-blobs/<sha256[:2]>/<sha256>    copy-out from Notes media
$MAILARCHIVE/att-extract/<lane>/<sha256>.txt     extract sidecars (all corpora)
$MAILARCHIVE/att-shards/                         embed parquet
```

Do not write into Apple’s `Messages/Attachments` or Notes Group
Container. Copy-out by sha only. Missing blob =
`extract_status=blob_missing`, not a second catalog.

`$MAILARCHIVE` is the same logical tree on MBP and Mini (`MailArchive`
under home). Mini until PR-5: **read/stage only**. New blobs land on
the SoR host (MBP today). Mini may hold a **copy** of `att-blobs/` next
to the copy DB; it does not grow a second canonical tree. PR-5 flips
which machine owns writes; it does not invent `MailArchive-mini/`.
`copy_age` covers sqlite **and** blob-tree mtime. No SMB write of
sqlite; blob stage is copy-then-local.

3. **Daily ATT catalog is time-boxed** under `with_writer_lock`. Default
   cap: **20 seconds or 50 new parts**, whichever first, then release.
   If rem holds the SoR lock, daily ATT catalog does **not** run on the
   SoR. Allowed early work: catalog + extract **files** against the Mini
   **copy DB + copy blob tree** only. No SoR apply during rem. A copy-DB
   catalog must not take a lock that rem needs; rem lock is the SoR file.

Sequence (CoS + Heavy; docs/tests during rem are OK):

1. Finish **KOO-55..64 docs/tests** first (landed).
2. **ATT-0** docs/tests (landed, #36) after rem EXIT 0, **or** file-only
   stage against the copy tree while rem runs. ATT-1..8 implement is
   still FUTURE.
3. No attachment vec/text apply to live SoR during rem.
4. No MSG/NOTE implement, snapshot, or apply during rem.

---

## 1. Goal

One ask surface that retrieves across:

| Corpus | Source of truth (Apple) | Our SoR (we write) |
|---|---|---|
| Mail + attachments | iCloud / Apple Mail | `mailroom.sqlite` |
| iMessage + SMS + RCS | Messages.app `chat.db` | `msgroom.sqlite` |
| Notes | Notes.app `NoteStore.sqlite` | `noteroom.sqlite` |

Apple apps stay the identity and the live store. We **replicate then
index**. We never write Apple’s files. We never auto-send iMessage.
Drafts-only stays the mail rule; Messages/Notes have **no send path at
all** in v1.

Product CLI/MCP: `ask_all` (or `ask_mail` with
`--corpus mail,att,imsg,note`). Citations name `source_kind` + native
id + (filename/page | chat | note title).

**HARD DECK:** never overwrite `scripts/ask_mail.py` with an MCP stub.
`ask_all` is a future reader, not a stub swap.

---

## 2. Why not one sqlite (separate SoRs)

- Different rights: FDA vs IMAP Keychain vs none
- Different writers and refresh clocks
- A mail rem must not lock Notes
- One-writer-per-file HARD DECK explodes if everything shares
  `mailroom.sqlite`
- PII blast radius: handles and phone numbers must not ride along in
  mail backups
- Bot box / GitHub already forbid mail SoR; Messages is worse

Federate at **retrieve**, not at storage. **Three SoRs / three locks /
three backup sets.** Mail rem lock ≠ msgroom/noteroom locks. Never dump
Messages/Notes into `mailroom.sqlite`. Shared `att-extract` trees must
not tempt a merge or “simplify” into one DB.

```
Apple Mail          Messages.app         Notes.app
     │                    │                   │
  IMAP/replica         replica.db          replica.db
     │                    │                   │
mailroom.sqlite      msgroom.sqlite      noteroom.sqlite
  body+att vec         imsg vec             note vec
     │                    │                   │
     └──────────── ask_all RRF ───────────────┘
                      citations
```

Shared: embed model + store dim 1024 + instruction-aware prefixes.
Different `embed_document_version`: `mail:body`, `mail:att`, `imsg`,
`note`.

---

## 3. Hard decks (copy into a future spec)

1. Never WRITE `~/Library/Messages/chat.db` or Notes `NoteStore.sqlite`.
   Read-only snapshot, then our file.
2. One writer per **our** `.sqlite`. Three files ⇒ three locks.
3. Bot Linux box never holds chat.db, NoteStore, replicas, or our SoRs.
4. No cloud vector DB. No upload of message/note text.
5. No auto-send, no Messages AppleScript send, no Notes create-as-agent
   in v1.
6. Full Disk Access is a **human** grant to the local indexer
   binary/Terminal. Bot does not grant TCC.
7. Fetch/replica fail ≠ “conversation deleted.” Status column, not
   tombstone-as-gone.
8. Locked Notes / encrypted PDFs / tapbacks-only rows: skip, labeled.
9. `ask_audit` stores query + hit ids + source_kind. Never bodies, note
   text, phone numbers, or chat handles.
10. ZERO PII on GitHub (no handles, no sample snippets from real chats).
11. Same 1024-d family or a declared new generation. Do not mix dims
    across corpora in one RRF.
12. Mail HARD DECKs unchanged (drafts, Keychain name-only, Mini
    copy-only until PR-5).

---

## 4. Messages / SMS lane (`msgroom`) — MSG-0..2 FUTURE docs only

**NO MSG implement in this PR.** No `msgroom.sqlite` creation. No
`chat.db` open from the bot box. No FDA grant. MSG-0..2 are FUTURE
outlines.

### 4.1 Apple reality (docs outline)

- Live store: `~/Library/Messages/chat.db` (+ WAL). SMS, iMessage, RCS
  live in the same file (`service` column).
- Attachments: `~/Library/Messages/Attachments/`
- Open **read-only**, not `immutable=1` (WAL would look stale).
- Full Disk Access required on the process that reads it (human grant
  to indexer/Terminal — never Grok Bot.app / Linux box).
- **Messages in iCloud**: the Mac copy can be incomplete. Index what is
  local. Label `replica_complete=unknown|partial|local`. Do not pretend
  the SoR is the whole Apple history.
- `text` is often NULL; body is in `attributedBody` (typedstream).
  Decode or skip. Fail-open per row.
- Dates are Apple epoch (ns since 2001-01-01).

Do not use chat.db as our SoR. Snapshot (sqlite backup API or file copy
of db+wal+shm while Messages is quiet enough), then ingest into
`msgroom.sqlite`. That snapshot is FUTURE (MSG-0). This PR does not
open the live store.

### 4.2 Our tables (sketch — not applied)

```
conversations   -- chat_id, service, display_title, handle_hash (not raw phone in logs)
messages        -- guid PK, chat_id, is_from_me, date, text, service, has_attach
msg_attachments -- like mail ATT catalog
msg_chunks      -- group-chat threads can be one chunk per message; long texts split
msg_fts
msg_embeddings vec0 1024-d
```

Handles stored locally if needed for retrieve filters; **never**
printed in packets, GitHub, or `ask_audit`. Prefer contact-book display
name resolved on the Mac at query time, not baked into the vector
prefix as a raw number.

### 4.3 What to index (v1 outline)

| Include v1 | Skip v1 |
|---|---|
| User-visible text (decoded) | Tapbacks / reactions as standalone docs |
| Group chat messages | Stickers-only, empty, service messages |
| SMS and iMessage same lane | Audio messages (whisper later) |
| Attachment catalog + text-extract (reuse ATT pipeline) | Raw plugin payloads |

Prefix for embed:

```
kind: imsg
chat: {title or "direct"}
date: {iso}
from: me|them
service: iMessage|SMS|RCS

{text}
```

### 4.4 Shard / batch (FUTURE)

| Stage | Shard | Batch |
|---|---|---|
| Replica snapshot | one job, MBP (FDA lives here) | n/a |
| Ingest catalog | one writer on `msgroom.sqlite` | 500 msgs/txn |
| Extract attach | same ATT size bands; files only | 1 file/process |
| Embed | `chat_id % N` or year-band sidecar files | 32–64 |
| Apply vec | one writer missing-only | 200/txn |

Year-band is the natural Messages split (2020/, 2021/, …). Mini can
embed year-bands **after** the replica is copied to a stage dir Mini
may read. Mini does not need FDA on live `chat.db` if it only sees our
replica. Mini embeds only from copied replicas.

Daily: incremental by `message.ROWID` / `guid` watermark on the
replica. Do not poll chat.db from two machines.

Suggested ID split (docs only):

| ID | Scope | Status |
|---|---|---|
| **MSG-0** | replica + catalog only | FUTURE / out of scope |
| **MSG-1** | text ingest + FTS | FUTURE / out of scope |
| **MSG-2** | embed sidecar + apply | FUTURE / out of scope |

---

## 5. Notes lane (`noteroom`) — NOTE-0..2 FUTURE docs only

**NO NOTE implement in this PR.** No `noteroom.sqlite` creation. No
NoteStore open or write. No FDA grant. NOTE-0..2 are FUTURE outlines.

### 5.1 Apple reality (docs outline)

- Store: `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`
- Bodies are **gzip + protobuf** in `ZICNOTEDATA.ZDATA`. There is no
  Apple FTS table to reuse.
- Attachments sit beside the store. Reuse ATT extract for PDFs/images
  inside notes.
- Open `mode=ro`, not `immutable=1`. Snapshot via sqlite `backup()` for
  a point-in-time copy.
- **Never write NoteStore.** CloudKit will punish you.
- Locked notes: skip `locked`. AppleScript body is lossy (checklists).
  Prefer protobuf parse.
- FDA required for the Group Container (human grant; never Bot.app /
  Linux box).

### 5.2 Our tables (sketch — not applied)

```
notes        -- note_id (ZIDENTIFIER), folder, title, updated_at, locked, extract_status
note_chunks  -- heading-aware chunks
note_fts
note_embeddings vec0 1024-d
note_attachments catalog → same ATT extract/embed contract
```

Prefix:

```
kind: note
folder: {folder}
title: {title}
date: {iso}

{chunk}
```

### 5.3 Shard / batch (FUTURE)

| Stage | Shard | Batch |
|---|---|---|
| Snapshot | one job, MBP | n/a |
| Parse protobuf → markdown sidecar | by folder | 50 notes/process |
| Chunk | files only | 50 |
| Embed | folder-mod or id-mod sidecar | 32–64 |
| Apply | one writer | 200/txn |

Folder bands (Work / Farm / Medical) are better than random id-mod for
Mini vs MBP: small text notes on Mini, attachment-heavy notes on MBP.

Daily: watermark on Notes `ZMODIFICATIONDATE` / identifier.
Incremental parse + embed. Do not re-protobuf the whole store every
night.

Suggested ID split (docs only):

| ID | Scope | Status |
|---|---|---|
| **NOTE-0** | snapshot + protobuf parse sidecars | FUTURE / out of scope |
| **NOTE-1** | FTS | FUTURE / out of scope |
| **NOTE-2** | embed + apply | FUTURE / out of scope |

---

## 6. Unified retrieve (`ask_all`) — FED-0

Not a fourth sqlite of all text. A **reader** that queries 3–4 files
and fuses hits. **No new writer.** FED-0 is FUTURE implement;
this packet locks the retrieve shape.

RRF is **one** list. Every hit tagged `source=body|attach|imsg|note`
before fusion. Not per-corpus RRF then a second merge.

```
query
  ├─ mailroom: FTS body ∪ FTS att ∪ KNN body ∪ KNN att
  ├─ msgroom:  FTS ∪ KNN
  └─ noteroom: FTS ∪ KNN
        ↓
   tag source= on every hit
        ↓
   ONE RRF (k=60) over the unified list
        ↓
   optional CrossEncoder on top-N snippets (same 0.6B)
        ↓
   cap per corpus (e.g. 8 mail, 8 imsg, 6 notes)
        ↓
   generate on MBP mlx_lm.server
   DATA fences per snippet, labeled by kind
        ↓
   citations: {source, id, title/filename/chat, page?}
```

Flags (future CLI; not shipped here):

- `--corpus mail,att,imsg,note` (default all that exist)
- `--no-imsg` / `--notes-only` / existing `--fts-only` / `--live`
  (mail only)
- Generate-down = labeled hits-only, same as ask_mail

Do not thread-expand iMessage into generate without a cap (group chats
explode). Cap 20 messages of context per cited chat.

Mail `--live` stays a mail SELECT filter (`present_on_server=1`).
There is no “live IMAP” for iMessage. Freshness is replica lag,
labeled `replica_age`. CLI/docs must not imply unified “live” means
fresh iMessage. Per-corpus caps required so group-chat volume does not
starve mail hits.

`ask_all` on the box is retrieve-only against **our** replicas
(`msgroom` / `noteroom`), never a path that opens live `chat.db` /
NoteStore.

---

## 7. Hardware / daily placement

| Job | Where |
|---|---|
| Snapshot chat.db / NoteStore | MBP (FDA) |
| Mail IMAP / rem / att apply | MBP SoR until PR-5 |
| Embed year-band / small notes | Mini on **copies of our SoRs**, not Apple files |
| Generate | MBP `127.0.0.1:1234` |
| `ask_all` | Reads local files. After PR-5, Mini can host retrieve if generate still gets snippets over localhost |

RAM law unchanged: Mini does not co-reside 8B embed + 35B generate.

128GB Max is optional here for “all corpora embedded + generate
loaded.” Not required to *start* msg/note lanes.

---

## 8. Rem-safe sequence (locked with CoS ATT feedback)

Do not start Messages/Notes during rem-legacy. Do not start ATT SoR
apply during rem. Docs/tests during rem are OK; SoR apply waits rem
**EXIT 0**.

| Step | Depends on | Notes |
|---|---|---|
| KOO-55..64 docs/tests | now | landed; no live rem path |
| rem EXIT 0 + catch-up | rem process | Heavy-04 protocol |
| ATT-0 docs/tests | rem EXIT **or** file-only on copy tree | landed #36; no SoR apply while rem live |
| ATT-1..5 | ATT-0 + rem EXIT 0 | catalog → extract files → chunk FTS apply → embed sidecar → vec apply |
| MSG-0 | FDA grant human + ATT extract contract | replica + catalog only — FUTURE |
| MSG-1 | MSG-0 | text ingest + FTS — FUTURE |
| MSG-2 | MSG-1 | embed sidecar + apply — FUTURE |
| NOTE-0 | FDA grant | snapshot + protobuf parse sidecars — FUTURE |
| NOTE-1 | NOTE-0 | FTS — FUTURE |
| NOTE-2 | NOTE-1 | embed + apply — FUTURE |
| FED-0 | mail+ATT retrieve + at least one of MSG-2 / NOTE-2 | `ask_all`, one RRF, no new writer — FUTURE |
| MSG-ATT / NOTE-ATT | ATT-5 | same blob tree + extract/embed contract — FUTURE |

Each MSG/NOTE apply is its own one-writer AR. Never share
`with_writer_lock` across mailroom and msgroom (different files,
different locks).

Ready ≠ MSG/NOTE enable. Ready ≠ ATT implement. Ready ≠ rem stop.

---

## 9. Time (order of magnitude, not a lab)

Messages history on a long-lived Apple ID is often **100k–1M rows**,
mostly short. Embed is cheap per row, catalog/decode is the annoyance.

- Replica + decode + FTS: hours
- Embed 200k short texts @ batch 64 on M1 Max: several hours, not days
- Group-chat attachment extract: follows ATT math

Notes are usually **thousands, not millions**. Protobuf parse + embed
is a same-day job unless every note is a scanned PDF.

Unified retrieve adds almost no index time. It adds query-time fanout
(3 files × FTS+KNN). Fine on SSD.

---

## 10. What not to do

- Do not `INSERT INTO mailroom.sqlite` from chat.db
- Do not grant FDA to Grok Bot.app so it can “just read Messages”
- Do not build an iMessage sender to “complete the loop”
- Do not index locked notes by asking the human for the password in a
  packet
- Do not put raw phone numbers in embed prefixes or GitHub tests
- Do not use AppleScript as the SoR (lossy, slow, dialog-prone)
- Do not run this before rem EXIT 0 + mail ATT lane exists — you want
  one extract/embed contract reused
- Do not start/stop/restart/hot-swap rem-legacy from this packet
- Do not overwrite `scripts/ask_mail.py` with an MCP stub
- Do not put OTP/auth codes in citations or `ask_audit`

---

## 11. Mailroom 06b — locked constraints (CoS PT 2:37 PM)

Parent packet: `20260914-06-unified-search-design.md`. Addendum:
`20260914-06b-mailroom-gaps-accepted.md`. **Not** an implement order.

### 11.1 FDA / bot boundary

FDA must stay a **human** grant to the local indexer/Terminal, never
Grok Bot.app / Linux box.

Lock: `ask_all` on the box is retrieve-only against **our** replicas
(`msgroom` / `noteroom`), never a path that opens live `chat.db` /
NoteStore. Mini embeds only from copied replicas (no FDA on live Apple
stores).

### 11.2 Separate sqlite is load-bearing

Never dump Messages/Notes into `mailroom.sqlite`.

Ops contract: **three SoRs / three locks / three backup sets**. Mail
rem lock ≠ msgroom/noteroom locks. Shared `att-extract` trees must not
tempt a merge or “simplify” into one DB.

### 11.3 One RRF vs freshness semantics

One tagged RRF (`source=body|attach|imsg|note`) matches ATT-0 — do not
invent a second RRF product.

Gap locked: mail `--live` is IMAP `present_on_server`; imsg/note
freshness is **`replica_age` only**. CLI/docs must not imply unified
“live” means fresh iMessage. Per-corpus caps required so group-chat
volume does not starve mail hits.

### 11.4 No-send vs existing bill texts

Heavy-06 bans auto-send / Messages AppleScript send for ask.

Isolated exception: Mailroom’s narrow `notify_bills` → Messages path
(Keychain item **name** only in docs; once per bill) is an **ops
exception**, not an `ask_all` capability and not a precedent for agent
send. `ask_all` has no send path.

### 11.5 MSG OTP / auth hard-gate

Mail auth hard-gate (ATT-0) does not cover 2FA that arrives as
SMS/iMessage.

MSG lane needs an equivalent gate (skip or hard-filter likely OTP
patterns / known auth senders) so `ask_all` cannot become a code dump.
Never put codes in citations / `ask_audit`. No sample codes in this
repo.

---

## 12. Factory note

| Track | Status |
|---|---|
| KOO-55..64 docs/tests | Done (#35) |
| KOO-65..70 ATT docs/tests | **Done (#36 merged)** |
| Heavy-06 + 06b (this file) | DESIGN ONLY — rem-safe docs/tests |
| MSG-0..2 / NOTE-0..2 / FED-0 implement | FUTURE. Ready ≠ MSG/NOTE enable |

Rem untouched. ZERO PII. DESIGN ONLY.

---

## 13. Repo touch targets (this Developer docs PR)

- New: `docs/unified-search-design.md` (this contract)
- Cross-links from `docs/MAILROOM.md`, `docs/ask_mail.md`,
  `docs/att0-constraints.md` (and soft-delete/tombstone pointers)
- Tests: contract/needle/fail-closed/fixtures only (no live IMAP, no
  live SoR, no Apple stores, no msgroom/noteroom creation)

---

*End Heavy-06 + 06b. ZERO PII. DESIGN ONLY. Rem untouched. Ready ≠ MSG/NOTE enable.*
