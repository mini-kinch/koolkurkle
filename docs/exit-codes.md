# Exit codes

Pinned statuses for the writer-lock wrapper, the SoR writer gate, the lock probes, and the other CLIs the ops, ATT-0, and phase-P run scripts invoke. Phase P here is the ATT-0 MIME sequence (P0 catalog through later extract phases) together with the L1 steps that call these programs (S1, A2, A3, AR-R).

Nothing in this note changes a status or a stderr string. Deployed run scripts already branch on them.

Libraries with no process entry point (`extract.py`, `chunk.py`, `search.py`, `bodystructure.py`, `mime_meta.py`, `att0_constraints.py`) have no status of their own.

## How a caller tells the cases apart

Status 2 is shared. Read stderr before treating 2 as a lock-busy STOP.

| What you see | What it is |
|---|---|
| A stderr line whose first word is `usage:` | Argparse usage (or `sqlite_pragmas.py` printing help). Status 2. The following line from argparse is `<script>: error: ...`. |
| A stderr line `error: writer lock held:` and no `usage:` line | `WriterLockError` because another process holds the flock. Status 2 from `scripts/with_writer_lock.py:380`. |
| A stderr line `error: writer lock held >` containing `no steal` | Same handler. The flock is older than the max age. Status 2. The lock is not taken. |
| A stderr line `error: CONFLICT:` containing `writer lock held` | Gate CLI (or a writer that calls the gate) refused a live SoR basename. Status 2 (`CONFLICT_EXIT`). |
| Status 2, empty stderr, child was started | The wrapper returned the child's status (`scripts/with_writer_lock.py:314`). A child that exits 2 is not a lock-busy STOP. |
| Status 1, stderr is the holder summary and does not contain `CONFLICT` or `usage:` | The flock probe boolean was used as a process status. See below. |
| Status 1, stderr `error: deadline file missing` or `error: run-id mismatch` | `search_resume_watchdog.py arm`. The wrapper reports the same failure as status 2. |
| A traceback and status 1 | An exception escaped `main`. Python's default status. The wrapper's drop path used to do this; the caught path is status 2 (`scripts/with_writer_lock.py:264`). |

`error:` alone is not a distinguisher. Argparse, `WriterLockError`, and `CONFLICT` all print it.

## Probe status 1, gate status 2

`_probe_writer_lock` (`scripts/sor_writer_gate.py:532`) and `writer_lock_held` (`scripts/sor_writer_gate.py:625`) return `(held, detail)`. `held` is a `bool`. On a flock held by someone else, `_probe_writer_lock` returns `True` and a detail `writer lock held: ...` (`scripts/sor_writer_gate.py:557`). `bool` is an `int`, and `True == 1`, so `sys.exit(held)` or `raise SystemExit(held)` ends the process with status **1**.

The gate CLI, for that same flock and a database whose basename is `mailroom.sqlite`, writes `error: CONFLICT: ... (writer lock held: ...)` and returns **2** (`scripts/sor_writer_gate.py:47`, `scripts/sor_writer_gate.py:791`).

The probe detail does not contain `CONFLICT`. The gate line does. A copy basename is allowed by the gate (status 0) even while the probe still reports held. `SOR_FORCE_LIVE_CHECKS=1` turns the live checks on for a non-live path; the gate then returns 2 as well.

With `MAILROOM_WRITER_LOCK_TOKEN` set and the flock free, `writer_lock_held` returns `True` and `writer lock identity refused: lock not held` (`scripts/sor_writer_gate.py:646`). Used as a process status that is again **1**. The gate CLI returns **2** with `error: CONFLICT:` and that same reason.

`pr5_preflight.py` is a different probe. It calls `writer_lock_held` and, when the flock is held, returns **2** with `error: writer flock held — flock free gate failed` (`scripts/pr5_preflight.py:294`). That string has no `CONFLICT`. Stdout status lines, including `enable_verdict=NON-GO`, are printed on the success path too. `NON-GO` on stdout is not a failure; the status is.

`search_resume_watchdog.py arm` returns **1** (`scripts/search_resume_watchdog.py:623`). The wrapper's matching drop failure returns **2** (`scripts/with_writer_lock.py:246`, `scripts/with_writer_lock.py:380`). `lsof -t` exiting 1 with empty output means no holder (`scripts/search_resume_watchdog.py:412`). The watchdog's own `watch` command still returns 0.

## Proposal: opt-in busy status for WriterLockError

A distinct status for `WriterLockError` is warranted. Status 2 is also argparse, a propagated child status, `CONFLICT_EXIT`, and several other refuses (`database not found`, a missing flag). A caller that only inspects `$?` cannot tell a lock-busy STOP from a usage error.

Do not change the default. Deployed scripts depend on 2.

Later, add both of these, defaulting to today's behavior:

- Environment variable `MAILROOM_WRITER_LOCK_BUSY_EXIT`. Unset or empty: the handler at `scripts/with_writer_lock.py:380` still returns 2. Set to a decimal integer from 3 through 125: that handler returns the integer instead.
- Flag `--busy-exit N`, same range. When the flag is present it wins. When it is absent, the environment variable applies. When both are absent, the status stays 2.

Argparse usage stays 2. The child status is still returned unchanged (`scripts/with_writer_lock.py:314`), so a child that exits with the chosen busy code still collides with it. These CLIs do not use 75. Do not choose 1: an uncaught traceback is 1, `arm` and `status` use 1, `notify_bills.py` uses 1, and the flock-probe boolean is 1. Do not choose 2.

Migration:

1. Leave the variable unset and do not pass the flag.
2. Split lock-busy, usage, and child failure with the stderr markers in the tables below.
3. After every caller uses those markers, set the variable in the run script. A caller that still treats every 2 as usage will mis-read a busy lock until it moves.
4. This proposal does not change `CONFLICT_EXIT`, the probe boolean, `arm`, or `notify_bills.py`. Aligning those would be a separate behavior change.

## `scripts/with_writer_lock.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | The child ran and exited 0. | `scripts/with_writer_lock.py:314` | No wrapper stderr. Stdout and stderr are the child's. |
| child | The child's status, including 2. | `scripts/with_writer_lock.py:314` | The child was started. The wrapper adds no `error:` line of its own. |
| 2 | `WriterLockError`. The child was not started. | `scripts/with_writer_lock.py:378` `scripts/with_writer_lock.py:380` | Stderr is `error: ` plus the message. No line begins with `usage:`. |
| 2 | Argparse rejected the argv. | `scripts/with_writer_lock.py:362` | First stderr line begins with `usage: with_writer_lock.py`. Next diagnostic is `with_writer_lock.py: error:`. |
| 1 | An exception escaped `main` (traceback). | `scripts/with_writer_lock.py:264` `scripts/with_writer_lock.py:384` | A traceback. The caught drop failure is the status-2 row above. |

`WriterLockError` messages, all status 2 from the same handler:

| Message prefix | Source |
|---|---|
| `error: writer lock held:` | `scripts/with_writer_lock.py:225` |
| `error: writer lock held >` … `no steal` | `scripts/with_writer_lock.py:222` |
| `error: purpose is required` | `scripts/with_writer_lock.py:209` |
| `error: command is required after --` | `scripts/with_writer_lock.py:295` |
| `error: action-required open` | `scripts/with_writer_lock.py:291` |
| `error: search resume +26 drop failed; child not started:` | `scripts/with_writer_lock.py:246` |
| `error: writer lock token generation failed` | `scripts/with_writer_lock.py:184` |
| `error: writer lock token missing` | `scripts/with_writer_lock.py:308` |

`--purpose` with only spaces passes argparse (the option is present) and then hits `purpose is required`. A missing `--purpose`, or a non-numeric `--max-age-hours`, is the `usage:` row.

## `scripts/sor_writer_gate.py`

The gate does not take the writer lock. It probes and then allows or refuses.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Live checks passed, or the path is not under live checks. | `scripts/sor_writer_gate.py:793` | Stdout `sor_writer_gate=allow db=`. Stderr empty. |
| 2 | `SorWriterRefuse` (`CONFLICT_EXIT`). | `scripts/sor_writer_gate.py:47` `scripts/sor_writer_gate.py:791` | Stderr begins `error: CONFLICT:`. No `usage:` line. |
| 2 | Argparse (`--db` is required). | `scripts/sor_writer_gate.py:785` | First stderr line begins with `usage: sor_writer_gate.py`. |

A copy basename stays status 0 while another process holds the flock. A live basename (`mailroom.sqlite`) with that flock is status 2 and the detail contains `writer lock held`. `SOR_FORCE_LIVE_CHECKS=1` makes a copy take the live checks; a held flock is then status 2.

## Flock probe (not a separate program)

| Result | Meaning | Source | Distinguisher |
|---|---|---|---|
| `(True, "writer lock held: ...")` | Someone else holds the flock. As a process status this is 1. | `scripts/sor_writer_gate.py:557` | Detail has `writer lock held` and no `CONFLICT`. |
| `(False, "writer lock free")` | This probe acquired and dropped the flock. | `scripts/sor_writer_gate.py:562` | |
| `(False, "writer lock absent")` | No lock file. | `scripts/sor_writer_gate.py:547` | |
| `(True, "writer lock identity refused: lock not held")` | A token is set and the probe found the flock free. As a process status this is 1. The gate CLI is 2. | `scripts/sor_writer_gate.py:646` | |
| `(False, "writer lock held by wrapper")` | Token, pid, and purpose match. The gate allows this. | `scripts/sor_writer_gate.py:650` | |

## `scripts/pr5_preflight.py`

Dry lock probe. It does not steal the flock.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Asked checks passed. `enable_verdict=NON-GO` is still printed. | `scripts/pr5_preflight.py:296` | Stdout contains `flock_free=true` when `--lock-file` was set and the flock was free. Stderr empty. |
| 2 | `--enable`. | `scripts/pr5_preflight.py:250` | `error: NON-GO:`. No stdout status block. |
| 2 | Repo template refused. | `scripts/pr5_preflight.py:266` | `error:`. |
| 2 | Installed plist has RunAtLoad. | `scripts/pr5_preflight.py:289` | `error: installed LaunchAgent RunAtLoad is true`. Stdout was already printed. |
| 2 | Copy path missing or not a copy basename. | `scripts/pr5_preflight.py:292` | `error: copy DB path missing or not a copy basename`. |
| 2 | Flock held. | `scripts/pr5_preflight.py:294` `scripts/pr5_preflight.py:295` | `error: writer flock held — flock free gate failed`. Stdout contains `flock_free=false`. No `CONFLICT`. |
| 2 | Argparse. | `scripts/pr5_preflight.py:245` | A `usage:` line. |

## `scripts/search_resume_watchdog.py`

Ops S1 / AR-R invoke `write`, `status`, `clear`, and `arm`. `watch` is the LaunchAgent pass.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | `watch`, including a held lock, a detection fault, and a missing deadline file. | `scripts/search_resume_watchdog.py:569` | The process status stays 0. A held lock is a log line, not a status. |
| 0 | `write` stored the deadline. | `scripts/search_resume_watchdog.py:611` | Empty stdout. |
| 0 | `clear`. | `scripts/search_resume_watchdog.py:632` | |
| 0 | `status` parsed the file. | `scripts/search_resume_watchdog.py:645` | Stdout is `run_id=`, `deadline_26=`, `deadline_50=`, `d26_live=`. |
| 0 | `arm` dropped `+26`. | `scripts/search_resume_watchdog.py:627` | |
| 1 | `arm` refused (missing, unparseable, or run-id mismatch). | `scripts/search_resume_watchdog.py:623` | `error: ` plus the refusal. No `usage:` line. The wrapper's same mismatch is status 2. |
| 1 | `arm` could not rewrite the file. | `scripts/search_resume_watchdog.py:626` | `error: deadline rewrite failed`. |
| 1 | `status` and the file is missing. | `scripts/search_resume_watchdog.py:640` | Stdout `status=missing`. Stderr empty. |
| 1 | `status` and the file is unparseable. | `scripts/search_resume_watchdog.py:643` | Stdout `status=unparseable`. |
| 2 | Usage (`write` / `arm` / `status` / `clear` / unknown command). | `scripts/search_resume_watchdog.py:598` `scripts/search_resume_watchdog.py:618` `scripts/search_resume_watchdog.py:653` `scripts/search_resume_watchdog.py:670` | Stderr begins `usage: search_resume_watchdog.py`. |

`lsof` status 1 with empty output is classified as free (`scripts/search_resume_watchdog.py:426`). Any other `lsof` failure is a detection fault. The watchdog process does not forward that 1.

## `scripts/attachments/meta_fill.py`

A2/A3 wrap this with `with_writer_lock.py`. The process status from `main` is 0 or 2. Argparse is also 2.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Report printed. A `PARTIAL:` banner can still be status 0. | `scripts/attachments/meta_fill.py:1326` | Stdout begins `att0 meta fill` when the report is formatted. |
| 2 | Destructive argv. | `scripts/attachments/meta_fill.py:1275` | `error:`. No `usage:` line. |
| 2 | Argparse (`--db` and `--source` required). | `scripts/attachments/meta_fill.py:1276` | `usage: meta_fill.py`. |
| 2 | Both `--apply` and `--dry-run`. | `scripts/attachments/meta_fill.py:1279` | `error: pass only one of --apply and --dry-run`. |
| 2 | Live SoR basename without `--allow-mailroom-sqlite`. | `scripts/attachments/meta_fill.py:1286` | `error: refuse: resolved path is the live SoR`. |
| 2 | Database file missing. | `scripts/attachments/meta_fill.py:1289` | `error: database not found`. |
| 2 | `--source jsonl` without `--jsonl`. | `scripts/attachments/meta_fill.py:1292` | `error: jsonl path is required`. |
| 2 | `FillRefuse`, `SorWriterRefuse`, or `DestructiveRefuse` from the fill. | `scripts/attachments/meta_fill.py:1321` | `error:`. A gate refusal contains `CONFLICT`. |
| 2 | `sqlite3.Error`. | `scripts/attachments/meta_fill.py:1324` | `error: sqlite fill failed`. |

`_progress_rc` returns 1 when an exception has no integer `rc` (`scripts/attachments/meta_fill.py:198`). That value is written on a stderr progress line `folder <i>/<n> | rc=<n>` (`scripts/attachments/meta_fill.py:205`). It is not the process status. A run can print `rc=1` and still exit 0 or 2.

## `scripts/attachments/migrate_att0_schema.py`

A2. Same wrapper. Process status 0 or 2.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Report printed. | `scripts/attachments/migrate_att0_schema.py:453` | Stdout begins `att0 schema migrate`. |
| 2 | Destructive argv. | `scripts/attachments/migrate_att0_schema.py:428` | `error:`. |
| 2 | Argparse (`--db` required). | `scripts/attachments/migrate_att0_schema.py:429` | `usage: migrate_att0_schema.py`. |
| 2 | Live SoR basename without the allow flag. | `scripts/attachments/migrate_att0_schema.py:436` | `error: refuse: resolved path is the live SoR`. |
| 2 | Database file missing. | `scripts/attachments/migrate_att0_schema.py:439` | `error: database not found`. |
| 2 | `MigrateRefuse` / `SorWriterRefuse` / `DestructiveRefuse`. | `scripts/attachments/migrate_att0_schema.py:448` | `error:`. Gate text contains `CONFLICT`. |
| 2 | `sqlite3.Error`. | `scripts/attachments/migrate_att0_schema.py:451` | `error: sqlite migrate failed`. |

## `scripts/embed_backfill.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Backfill finished. | `scripts/embed_backfill.py:486` | |
| 2 | Destructive argv, before argparse. | `scripts/embed_backfill.py:434` | `error:`. |
| 2 | Argparse. | `scripts/embed_backfill.py:435` | `usage: embed_backfill.py`. |
| 2 | `EmbedError` or `SorWriterRefuse`. | `scripts/embed_backfill.py:480` | `error:`. A held flock on a live basename contains `CONFLICT` and `writer lock held`. |
| 2 | `FileNotFoundError` (including a missing vec extension). | `scripts/embed_backfill.py:483` | `error:`. |

## `scripts/embed_sidecar_apply.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | JSON result on stdout. | `scripts/embed_sidecar_apply.py:312` | |
| 2 | Argparse (`--db` and `--shards` required). | `scripts/embed_sidecar_apply.py:295` | `usage: embed_sidecar_apply.py`. |
| 2 | Sidecar, generation-key, or gate refusal. | `scripts/embed_sidecar_apply.py:310` | `error:`. |

## `scripts/embed_merge_shards.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Merge finished. | `scripts/embed_merge_shards.py:166` | |
| 2 | Argparse. | `scripts/embed_merge_shards.py:141` | `usage: embed_merge_shards.py`. |
| 2 | `EmbedError` or `SorWriterRefuse`. | `scripts/embed_merge_shards.py:162` | `error:`. Live primary plus a held flock contains `CONFLICT`. |
| 2 | `FileNotFoundError`. | `scripts/embed_merge_shards.py:165` | `error:`. |

## `scripts/migrate_pr1_schema.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Migration report. | `scripts/migrate_pr1_schema.py:404` | Returned through `scripts/migrate_pr1_schema.py:416`. |
| 2 | `MigrateError` or `SorWriterRefuse`. | `scripts/migrate_pr1_schema.py:419` | `error:`. |
| 2 | Argparse. | `scripts/migrate_pr1_schema.py:397` | `usage: migrate_pr1_schema.py`. |
| child | `--lock` delegates to the wrapper, so a lock-busy STOP is the wrapper's 2. | `scripts/migrate_pr1_schema.py:415` | Wrapper stderr, not this file's `error:` line. |

## `scripts/messages_ids.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Backfill report. | `scripts/messages_ids.py:366` | Stdout begins `messages_ids backfill`. |
| 2 | `--backfill` omitted. | `scripts/messages_ids.py:346` | `error: pass --backfill`. No `usage:` line. |
| 2 | `MessagesIdsError` or `SorWriterRefuse`. | `scripts/messages_ids.py:361` | `error:`. |
| 2 | Argparse. | `scripts/messages_ids.py:343` | `usage: messages_ids.py`. |

## `scripts/mailroom_copy_db.py`

Daily children call `child_main` (`scripts/mailroom_copy_db.py:167`). `imap_newmail.py` (`scripts/imap_newmail.py:24`), `imap_tombstone.py` (`scripts/imap_tombstone.py:29`), and `imap_fetch_bodies_fts.py` (`scripts/imap_fetch_bodies_fts.py:141`) return that status unchanged. They do not run argparse, so a bad argv is `error:` plus `db_mode=refused`, not a `usage:` line.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | `child_main` bound a path. | `scripts/mailroom_copy_db.py:207` | Stdout `opened_db=`. Stderr `db_mode=copy` or `db_mode=sor`. |
| 2 | Destructive argv, gate refusal, or copy-db refusal inside `child_main`. | `scripts/mailroom_copy_db.py:197` `scripts/mailroom_copy_db.py:201` `scripts/mailroom_copy_db.py:205` | Stderr contains `db_mode=refused` and `error:`. A held flock on a live basename also contains `CONFLICT`. |
| 0 | This program's own CLI resolved a path. | `scripts/mailroom_copy_db.py:308` | Stdout is the path. Stderr `db_mode=`. |
| 2 | This program's own CLI refused. | `scripts/mailroom_copy_db.py:286` `scripts/mailroom_copy_db.py:301` `scripts/mailroom_copy_db.py:305` | `db_mode=refused` and `error:`. |
| 2 | Argparse on this program's own CLI. | `scripts/mailroom_copy_db.py:287` | `usage: mailroom_copy_db.py`. |

Unset `MAILROOM_DB` and no `--db` is status 2 here. The same condition in `notify_bills.py` is status 1 (next table). The stderr text matches.

## `scripts/classify.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Classified, or the bound database file is absent. | `scripts/classify.py:471` `scripts/classify.py:451` | An absent file is status 0 with no `error:` line. |
| 1 | SQLite write failed. | `scripts/classify.py:467` | `error: classify database write failed`. |
| 2 | Rules refused. | `scripts/classify.py:448` | `error:`. |
| 2 | Destructive argv, bad `--source`, or `--all` without `--folder`. | `scripts/classify.py:483` `scripts/classify.py:489` `scripts/classify.py:492` | `error:` or `db_mode=refused`. |
| child | Bind failed; the child status is returned. | `scripts/classify.py:495` | Same stderr as `child_main` (status 2). |
| 2 | Argparse. | `scripts/classify.py:484` | `usage: classify.py`. |

## `scripts/notify_bills.py`

The daily plan invokes this. A `CopyDbRefuse` is `raise SystemExit("error: ...")` (`scripts/notify_bills.py:167`). Python prints that string on stderr and uses status **1**. `mailroom_copy_db.py` returns **2** for the same message. No Keychain read happens on that path: the bind runs first.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 1 | `SystemExit` with a string (bind refusal, and the Keychain / send failures). | `scripts/notify_bills.py:167` `scripts/notify_bills.py:45` `scripts/notify_bills.py:47` `scripts/notify_bills.py:63` `scripts/notify_bills.py:91` | Status 1. Bind refusal also prints `db_mode=refused` before the `error:` line. |
| 1 | Database file missing after a successful bind. | `scripts/notify_bills.py:177` | Log line `missing`. |
| 0 | Sent, already sent, quiet, or `--test` sent. | `scripts/notify_bills.py:174` `scripts/notify_bills.py:182` `scripts/notify_bills.py:186` `scripts/notify_bills.py:192` | |
| 2 | Argparse. | `scripts/notify_bills.py:161` | `usage: notify_bills.py`. |

## `scripts/mailroom_daily.py`

The daily lock is `mailroom.daily.lock` under the archive directory. A busy daily lock is status **0** (`skip:`), which is a different contract from `with_writer_lock.py`.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Daily lock already held; this start skips. | `scripts/mailroom_daily.py:627` | Stderr contains `skip:` and `mailroom.daily.lock held`. |
| 0 | Fresh stamp, or `--print-plan`, or a dry-run that finished. | `scripts/mailroom_daily.py:641` `scripts/mailroom_daily.py:654` `scripts/mailroom_daily.py:741` | |
| 0 | Required phases completed. | `scripts/mailroom_daily.py:751` | |
| 0 | `--print-plan` / `--dry-run` with `--allow-missing` and an incomplete plan. | `scripts/mailroom_daily.py:648` | Stdout `plan incomplete:`. |
| 2 | Destructive argv. | `scripts/mailroom_daily.py:585` | `error:`. |
| 2 | Gate or daily-db refusal. | `scripts/mailroom_daily.py:613` `scripts/mailroom_daily.py:617` | `db_mode=refused` and `error:`. |
| 2 | Plan could not be built. | `scripts/mailroom_daily.py:650` | `error:`. |
| 2 | Embed required and the health check failed. | `scripts/mailroom_daily.py:709` | `error:` and `chain aborted`. |
| 2 | Required phases incomplete. | `scripts/mailroom_daily.py:753` | `required phases incomplete`. |
| child | A non-warn step's status is returned. | `scripts/mailroom_daily.py:503` `scripts/mailroom_daily.py:728` | `step exit:` in the log, then that status. |
| 2 | Argparse. | `scripts/mailroom_daily.py:586` | `usage: mailroom_daily.py`. |

## `scripts/sqlite_pragmas.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | `--check-writer-version` and this interpreter meets the pin. | `scripts/sqlite_pragmas.py:135` | Stdout `sqlite <ver> pin <pin> ok`. Stderr empty. |
| 2 | `--check-writer-version` and sqlite is below the pin. | `scripts/sqlite_pragmas.py:135` | Stdout `sqlite <ver> pin <pin> below`. No `usage:` line. This 2 is not a usage error and not a lock. |
| 0 | `--apply` or `--show` finished. | `scripts/sqlite_pragmas.py:152` | |
| 2 | `--apply` or `--show` without `--db`. | `scripts/sqlite_pragmas.py:139` | `error: --db is required with --apply / --show`. |
| 2 | No mode selected. Help is written to stderr. | `scripts/sqlite_pragmas.py:154` | Stderr begins `usage: sqlite_pragmas.py` because help was printed. There is no `error: the following arguments` line. |
| 2 | Argparse (bad choice or bad flag). | `scripts/sqlite_pragmas.py:124` | `usage:` plus `<script>: error:`. |

## `scripts/refuse_destructive.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | No denied verb. | `scripts/refuse_destructive.py:123` | Stdout `ok: no destructive CLI verbs`. |
| 2 | A denied verb. | `scripts/refuse_destructive.py:121` | `error:`. No `usage:` line. |

## `scripts/refuse_sql_maintenance.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Statement is not denied. Empty argv is this row. | `scripts/refuse_sql_maintenance.py:97` | Stdout `ok: sql not on maintenance denylist`. |
| 2 | `DELETE FROM messages`, `DROP TABLE messages`, or `TRUNCATE`. | `scripts/refuse_sql_maintenance.py:95` | `error:`. |

## `scripts/refuse_frozen_jsonl.py`

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | No frozen-dump mutation. | `scripts/refuse_frozen_jsonl.py:110` | Stdout `ok: frozen jsonl not rewritten`. |
| 2 | Frozen basename plus a mutation verb. | `scripts/refuse_frozen_jsonl.py:108` | `error:`. |

## `scripts/sor_health_pack.py`

Read-only ops check. Integrity failure is 1. A missing database is 2. That 1 is not the flock probe.

| Code | Meaning | Source | Distinguisher |
|---|---|---|---|
| 0 | Opened and `integrity_check` is ok. Warnings stay 0. | `scripts/sor_health_pack.py:126` | Returned from `scripts/sor_health_pack.py:716`. |
| 1 | Opened and integrity is not ok. | `scripts/sor_health_pack.py:125` | Report text shows the integrity failure. |
| 2 | Database was not opened. | `scripts/sor_health_pack.py:123` | |
| 2 | Argparse. | `scripts/sor_health_pack.py:698` | `usage: sor_health_pack.py`. |
