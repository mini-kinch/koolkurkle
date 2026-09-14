# MAILROOM (GitHub contract)

GitHub-facing sync of MAILROOM decisions used by Mini / MBP mailroom
scripts. No personal info. Machines: **MBP** and **Mini** only. This
file is not a live SoR writer and does not start rem-legacy.

Human Terminal cards: [ops-terminal.md](ops-terminal.md). Daily:
[README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
Retrieve: [ask_mail.md](ask_mail.md). Tombstone:
[tombstone.md](tombstone.md). Canonical soft-delete one-pager:
[soft-delete.md](soft-delete.md). Embed:
[embed-backfill.md](embed-backfill.md).

## §5 Soft-delete (DECIDED)

Soft-delete is **DECIDED**. This is not a standby contract.

Never physically delete iCloud or server mail. Local tombstone only
(`messages.present_on_server`). The tree hard-refuses destructive CLI
verbs: `purge` | `expunge` | `empty-trash` | `delete-gone` |
`drop-messages`. No IMAP `STORE \Deleted`, `EXPUNGE`, Trash-purge, or
server drop from these CLIs.

**Deleted-folder ≠ present=0.** A row whose IMAP folder is Deleted is
still present on the server. Tombstone (`present_on_server=0`) means
IMAP no longer lists the message. Do not treat Deleted-folder as
`present=0`.

SQL helpers fail-closed refuse `DELETE FROM messages` /
`DROP TABLE messages` / `TRUNCATE` (`scripts/refuse_sql_maintenance.py`).
Frozen `icloud_mail_all.jsonl` is immutable (no rewrite / reconcile).
Canonical design: [soft-delete.md](soft-delete.md).

## History default (Q1 DECIDED)

ask_mail retrieve default is **history** (local SoR sqlite). History
is the standing default. There is no `--history` flag. Live is
**opt-in**. `--live` is an additive SELECT filter only
(`present_on_server=1`). It does not open IMAP. It does not mean
"exclude Deleted-folder". `--live-mailboxes` and `--trash-live` are
further opt-in read-side SELECT filters. They do not decide Q2
(Q2 trash in live remains deferred).

## §6.1 Incremental embed (pointer)

Daily incremental embed uses `--quote-strip` (header-prefixed cleaned
body). Rem-legacy keeps the old text path until EXIT. Do not restart
rem for daily. `--embed-live-only` is shipped for a future daily
incremental (present_on_server=1 only; must not delete existing
tombstone embeds). Shipping the flag ≠ starting a job. Guard ≠ run
against rem-legacy. See [embed-backfill.md](embed-backfill.md).

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
Mini recipes must set `MAILROOM_DB` to that copy:

```zsh
# Mini — ask_mail (copy DB until PR-5)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/ask_mail.py --json 'SDGE bill'
```

Do not default Mini to the empty SoR stub. No RunAtLoad change. No
PR-5 cutover here.

## imap_tombstone IMAP verbs (local present_on_server only)

The tombstone path is local `present_on_server` only. Never IMAP
`STORE \Deleted`, `EXPUNGE`, or Trash-purge. Refuse those verbs.
See [tombstone.md](tombstone.md).

## with_writer_lock sole-writer wrapper

`with_writer_lock` is the sole-writer wrapper. Busy/lock refuse
before a second writer. Shipping this guard is not starting
rem-legacy. See [pr0/with_writer_lock_DESIGN.md](pr0/with_writer_lock_DESIGN.md).

## mailroom_copy_db rem-gated copy

Mini copy only when rem-legacy is not writing, or after rem-legacy
**EXIT 0**. No SMB/NFS dual-write. No live MBP→Mini copy in this
change. See [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).

## bind_copy_db / daily children honor MAILROOM_DB

`bind_copy_db(argv=None)` reads `sys.argv[1:]`. Daily children open
the copy DB. Refuse the SoR stub (`mailroom.sqlite`). No live SoR
open from these tests.

## PR-5 cutover checklist (docs only — do not enable)

Gated on rem-legacy **EXIT 0** plus Mini SoR switch steps. This
change does **not** enable PR-5 cutover and does **not** enable
RunAtLoad. Checklist: [pr5-cutover.md](pr5-cutover.md).

## sor_health_pack read-only / Mini-copy OK

sor_health_pack is read-only. Mini on a copy DB is OK and is not
a second writer. See [sor-health.md](sor-health.md).

## Mini bodies-fts curl + Keychain name

BODY.PEEK prefers Homebrew curl ≥ 8.17 at
`/opt/homebrew/opt/curl/bin/curl`. Apple `/usr/bin/curl` is
fail-closed for BODY.PEEK. Headers may still use Apple curl.
Apple /usr/bin/curl Little Snitch allow does not cover Homebrew curl.
BODY.PEEK Homebrew curl needs its own Little Snitch allow. No live IMAP.
Keychain item **name** only: `mailroom.imap.app-password`. Never
secret values in this repo. No live IMAP. No Keychain read/write
from GitHub tests.

## Embed single-writer + rem-legacy ≠ Mini daily (HARD DECK)

One `embed_backfill` writer per `.sqlite`. Shipping guard: lockfile
or busy refuse **before** a second `embed_backfill`. Shipping the
guard ≠ starting a writer. Do not restart rem-legacy for daily.
Daily uses `--quote-strip`. Rem keeps old text until EXIT. Do not
touch a running rem-legacy job.
