# MAILROOM (GitHub contract)

GitHub-facing sync of MAILROOM decisions used by Mini / MBP mailroom
scripts. No personal info. Machines: **MBP** and **Mini** only. This
file is not a live SoR writer and does not start rem-legacy.

Human Terminal cards: [ops-terminal.md](ops-terminal.md). Daily:
[README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
Retrieve: [ask_mail.md](ask_mail.md). Tombstone:
[tombstone.md](tombstone.md). Embed:
[embed-backfill.md](embed-backfill.md).

## §5 Soft-delete (DECIDED)

Soft-delete is **DECIDED**. This is not soft-delete standby.

Never physically delete iCloud or server mail. Local tombstone only
(`messages.present_on_server`). The tree hard-refuses destructive CLI
verbs: `purge` | `expunge` | `empty-trash` | `delete-gone` |
`drop-messages`. No IMAP `STORE \Deleted`, `EXPUNGE`, Trash-purge, or
server drop from these CLIs.

**Deleted-folder ≠ present=0.** A row whose IMAP folder is Deleted is
still present on the server. Tombstone (`present_on_server=0`) means
IMAP no longer lists the message. Do not treat Deleted-folder as
`present=0`.

## History default (Q1 DECIDED)

ask_mail retrieve default is **history** (local SoR sqlite). History
is the standing default. There is no `--history` flag. Live is
**opt-in**. `--live` is an additive SELECT filter only
(`present_on_server=1`). It does not open IMAP. It does not mean
"exclude Deleted-folder".

## §6.1 Incremental embed (pointer)

Daily incremental embed uses `--quote-strip` (header-prefixed cleaned
body). Rem-legacy keeps the old text path until EXIT. Do not restart
rem for daily. See [embed-backfill.md](embed-backfill.md).

## §6.2 Hybrid retrieve (pointer)

`semantic_search.retrieve()` fuses FTS5 + sqlite-vec. See
[ask_mail.md](ask_mail.md).

## §9 SQLite PRAGMAs / §9.5 writer lock (pointer)

PRAGMAs: `scripts/sqlite_pragmas.py`. Exclusive writer lock:
[pr0/with_writer_lock_DESIGN.md](pr0/with_writer_lock_DESIGN.md).
ask_mail does not take the writer lock.

## Mini copy-only until PR-5

Mini unset / `mailroom.sqlite` is a hard-fail (`db_mode=refused`).
Daily LaunchAgent `MAILROOM_DB` is copy-only
(`mailroom-copy.sqlite` or `mailroom-daily-copy.sqlite`). ask_mail
Mini recipes must set `MAILROOM_DB` to that copy. Do not default Mini
to the empty SoR stub. No RunAtLoad change. No PR-5 cutover here.

## Mini bodies-fts curl + Keychain name

BODY.PEEK prefers Homebrew curl ≥ 8.17 at
`/opt/homebrew/opt/curl/bin/curl`. Apple `/usr/bin/curl` is
fail-closed for BODY.PEEK. Headers may still use Apple curl.
Keychain item **name** only: `mailroom.imap.app-password`. Never
secret values in this repo. No live IMAP. No Keychain read/write
from GitHub tests.

## Embed single-writer + rem-legacy ≠ Mini daily (HARD DECK)

One `embed_backfill` writer per `.sqlite`. Shipping guard: lockfile
or busy refuse **before** a second `embed_backfill`. Shipping the
guard ≠ starting a writer. Do not restart rem-legacy for daily.
Daily uses `--quote-strip`. Rem keeps old text until EXIT. Do not
touch a running rem-legacy job.
