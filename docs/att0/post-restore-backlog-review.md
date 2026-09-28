Repo copies only; the installed copies on the Mac may differ and were not reviewed.

# Post-restore backlog review

The repo daily chain is `scripts/run_mailroom_daily.sh:105`, which execs `scripts/mailroom_daily.py --skip-if-fresh`. The plan is headers, body/FTS, classify, bills, then embed (`scripts/mailroom_daily.py:74-77`, `scripts/mailroom_daily.py:358-392`). Headers are `imap_newmail.py` and `imap_tombstone.py`. The bills step is `notify_bills.py`.

No backlog guard was added. Sections (a) and (b) do not show a burst that a cap or age limit would bound, and the repo tombstone pass in (c) already touches zero rows. `scripts/post_restore_check.py` only reads and reports; it does not change sends, bill rows, or `present_on_server`.

## (a) Urgent-text burst

The once-per-id rule in this tree is `notify_log`'s unique `(message_id, channel)` (`docs/pr0/mailroom_schema.sql:51`, `scripts/notify_bills.py:127-133`, `scripts/notify_bills.py:138-144`). The only Messages sender on the daily plan is `notify_bills.py`. It writes one log row per Pacific calendar day under the key `bills-<date>` and sends one digest (`scripts/notify_bills.py:183-191`). That key is not a mail `message_id`, and the sender never reads `messages.urgent`.

`classify.py` can set `urgent=1` (`scripts/classify.py:419-425`). Its select is every `source='imap-live'` row with `lane IS NULL`, ordered by `date_utc`, with no `LIMIT` and no received-at predicate (`scripts/classify.py:380-392`). `classify()` takes no message age (`scripts/classify.py:277-301`). The pass updates the row and writes `audit`. It has no Messages call.

`imap_newmail.py` is the bind-only child: `main` returns `child_main` (`scripts/imap_newmail.py:23-24`). `child_main` resolves the path, prints `opened_db`, and returns (`scripts/mailroom_copy_db.py:167-207`). It inserts nothing and sends nothing.

There is no per-run urgent-text cap, because there is no urgent-text loop. Mail received long before the run is not eligible to be texted by this chain. A 40 hour ingest through these repo copies cannot burst urgent texts. The risk in the repo copy is absent, so no cap or max age was added. The post-restore script reports how many non-digest `notify_log` rows fall since `--since`. That report does not send.

`send_urgent_texts` is the delivery helper on this module. It is not part of the report. One `message_id` produces one text: the helper consults `notify_log` under `BEGIN IMMEDIATE`, sends, and inserts the id only after the send returns. The file is `logs/notify_log.sqlite` (the same unique `(message_id, channel)` as `docs/pr0/mailroom_schema.sql`). Replacing `mailroom.sqlite` does not rewind it, and the helper does not delete its rows.

## (b) Bills already past due at run time

`open_bills` loads every `bills` row with `status='open'` and does not compare `due_date` to the clock (`scripts/notify_bills.py:94-100`). When `due_date` is set, the digest text includes it (`scripts/notify_bills.py:112-113`). If any open row exists, the step sends one iMessage for `bills-<today>` unless that key is already logged (`scripts/notify_bills.py:179-191`). `--force` sends that same digest again. No repo script inserts into `bills`. `classify.py` does not. The chain creates no reminder row.

A bill that is already past due and still `open` is included in that single daily digest. After about 40 hours with no run, the next success sends at most one digest for the new Pacific date. It does not send one reminder per past-due bill. That is the steady-state digest, not a gap-multiplied burst, so past-due rows were left in the digest. Skipping them would drop unpaid bills from the only reminder this tree sends.

The post-restore script counts bill rows dated since `--since` (a timestamp column on `bills` when present, otherwise the linked message's `ingested_at`). `bills` in `docs/pr0/mailroom_schema.sql:48` has no created-at column, and this change does not add one. The script does not update `bills`.

## (c) Tombstone / gone-pass volume

`imap_tombstone.py` returns `child_main` (`scripts/imap_tombstone.py:28-29`). That helper refuses destructive verbs, binds the copy path, and exits (`scripts/mailroom_copy_db.py:167-207`). It does not open sqlite and it does not update `present_on_server`. The repo contract for this child is the same copy-only bind (`docs/tombstone.md:21-23`).

One run, including a run after a long gap, touches 0 message rows. There is no gone-pass loop, so there is no unbounded update and no `LIMIT` that a gap could exceed. `imap_fetch_error.may_tombstone` is not on the daily argv.

No tombstone change was made. The post-restore script reports `G` as the current number of messages with `present_on_server` 0 and does not write that column.
