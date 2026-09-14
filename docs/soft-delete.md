# Soft-delete one-pager (canonical)

Canonical soft-delete design for Mini / MBP mailroom. Machines:
**MBP** and **Mini** only. ZERO personal info. Docs only. This file
is not a live SoR writer and does not start rem-legacy.

Sync pointer: [MAILROOM.md](MAILROOM.md) §5. Tombstone:
[tombstone.md](tombstone.md). Retrieve: [ask_mail.md](ask_mail.md).
Human Terminal cards: [ops-terminal.md](ops-terminal.md).

## §1 Never physical delete

Never physically delete iCloud or server mail. Local tombstone only.
Soft-delete is **DECIDED**. This is not a standby contract.

## §2 Tombstone column

When IMAP no longer lists a message, SoR keeps the local row and
marks `messages.present_on_server=0`. Tombstone is local only.

## §3 Deleted-folder ≠ present=0

A row whose IMAP folder is Deleted is still present on the server.
Tombstone (`present_on_server=0`) means IMAP no longer lists the
message. Do not treat Deleted-folder as `present=0`.

## §4 Destructive CLI refuse

Hard-refuse CLI verbs: `purge` | `expunge` | `empty-trash` |
`delete-gone` | `drop-messages` (`scripts/refuse_destructive.py`).
No IMAP `STORE \Deleted`, `EXPUNGE`, Trash-purge, or server drop
from these CLIs.

## §5 SQL maintenance denylist

Fail-closed refuse in SQL helpers (`scripts/refuse_sql_maintenance.py`):

- `DELETE FROM messages`
- `DROP TABLE messages`
- `TRUNCATE`

Sibling tables (`messages_ids`, `messages_fts`, `message_embeddings`)
are not this denylist. No SoR open is required to refuse. Soft-delete
only — never physically drop the messages table.

## §6 Frozen dump

Frozen `icloud_mail_all.jsonl` is immutable. Rewrite / reconcile
paths that touch it are refused (`scripts/refuse_frozen_jsonl.py`).
No dump rewrite.

## §7 Retrieve (history default)

ask_mail retrieve default is **history** (Q1 **DECIDED**). `--live`
is an additive SELECT filter only (`present_on_server=1`). It does
not open IMAP. It does not mean "exclude Deleted-folder".

`--live-mailboxes` and `--trash-live` are further **opt-in**
read-side SELECT filters. They do not decide Q2 (trash in live remains
deferred). HARD DECK: never overwrite `ask_mail.py` with
an MCP stub.

## §8 Embed tombstones

`--embed-live-only` (future daily incremental) must not delete
existing tombstone embeds. Shipping the flag ≠ starting a job.
Guard ≠ run against rem-legacy. See [embed-backfill.md](embed-backfill.md).

## §9 ATT-0 never-purge attachment_* (pointer)

Never-purge extends to `attachment_*` tables/rows (docs/tests only;
same never-purge spirit as `messages`). Do not `DELETE` from
`attachment_*` as cleanup. Disk caps + skip/`too_big` beat silent
ballooning. History vs live for tombstoned attach hits follows Q1
(**history** default). [att0-constraints.md](att0-constraints.md).
ATT-1..8 implement is FUTURE / out of scope.
Unified-search (DESIGN ONLY) keeps mail history/`--live` semantics
and does not invent a unified-live lie for imsg/note (`replica_age`
only). Never dump Messages/Notes into `mailroom.sqlite`.
[unified-search-design.md](unified-search-design.md).

## §10 Out of scope

No live IMAP. No Keychain read/write. No RunAtLoad change. No PR-5
enable. No live MBP SoR writers. Rem-legacy untouched. No ATT
catalog/extract/chunk/embed/apply run.
