# PR-0 writer lock — MAILROOM.md §9.5

Lock file: `~/MailArchive/mailroom.write.lock`

Mechanism: exclusive `flock` (advisory). Holder writes `PID` / `hostname` / `purpose` / ISO timestamp / `writer_token` into the lock file. The token value is never logged or printed.

If the lock is held longer than **4 hours**, refuse. **Do not steal.**

Writers take the lock. `ask_mail` does **not**.

Action-required open ⇒ no lock / no writes.

## Who takes the lock

| Actor | Lock |
|---|---|
| IMAP header/body ingest, classify, bills, embed backfill, schema DDL | Yes — wrap with `scripts/with_writer_lock.py` |
| `ask_mail` retrieve (FTS / sqlite-vec) + short `ask_audit` / draft INSERT | No |
| Humans inspecting with `sqlite3` read-only | No |

`with_writer_lock` is the **sole-writer** wrapper. Busy/held lock
refuses before a second writer starts. Shipping this guard
(docs/tests/wrapper) is **not** starting rem-legacy. Do not start
rem-legacy from this wrapper.

This PR ships the wrapper only. It is **not** wired into `embed_backfill` or the Mini daily driver (live embeds stay up).

## Stale lock (>4h)

`flock` is released when the holder process exits (kernel drops the fd). A lock held >4h means a **live** writer has kept exclusive flock that long.

Policy:

1. Try `LOCK_EX | LOCK_NB`.
2. If acquired, overwrite metadata and run the command.
3. If not acquired, read metadata. If `acquired_at` age **> 4 hours**, exit non-zero: held too long, **no steal**.
4. If not acquired and age ≤ 4 hours, exit non-zero: held by PID/host/purpose since timestamp.

Never unlink, truncate, or overwrite a lock file that another process still flocks.

## Action-required

If `~/MailArchive/ACTION_REQUIRED` exists, refuse: take no lock, run no command, write nothing. Clear the file (human) before writers resume.

This is the writer-lock file, not a human Terminal / Action-required card.
Card practice: [docs/ops-terminal.md](../ops-terminal.md).

Override path: `--action-required-file` or `MAILROOM_ACTION_REQUIRED`.

## CLI

```text
with_writer_lock.py --purpose X -- cmd...
```

`--purpose` is required (goes into the lock metadata). Everything after `--` is the writer command. The parent holds flock until the child exits.

```text
with_writer_lock.py --purpose embed_backfill -- \
  ~/MailArchive/.venv/bin/python embed_backfill.py --db mailroom.sqlite
```

Testing overrides: `--lock-file`, `--max-age-hours` (default 4).

## Search-resume +26 drop

After the lock is acquired, and before the child runs, the wrapper looks only at `MAILROOM_SEARCH_RESUME_RUN_ID`. It never invents a run-id from the deadline file.

| `MAILROOM_SEARCH_RESUME_RUN_ID` | What happens |
|---|---|
| Absent | No drop. The deadline file is not read for a drop. The child runs. |
| Present and equal to the file `run_id`, and a re-read shows `d26_live=no` | Drop `+26`, then run the child. |
| Present and equal to the file `run_id`, and a re-read still shows `d26_live=yes` | Stop. Clean error, exit 2, child not run, lock released. |
| Present, and the run-id mismatches, or the deadline file is missing or unreadable | Stop. Clean error, exit 2, child not run, lock released. |
| Present, and `search_resume_watchdog` cannot be loaded | Stop. Clean error, exit 2, child not run, lock released. |

The clean error is `error: search resume +26 drop failed; child not started`. A bare acquire never drops.

## Identity handoff

The wrapper draws `secrets.token_urlsafe(16)` or longer, stores it in the lock file as `writer_token`, and passes it to the child with the wrapper pid and purpose:

- `MAILROOM_WRITER_LOCK_TOKEN`
- `MAILROOM_WRITER_LOCK_PID`
- `MAILROOM_WRITER_LOCK_PURPOSE`

The child gate probes `mailroom.write.lock` with a new open and `LOCK_EX|LOCK_NB`, then closes that probe fd. It does not keep the probe lock. With no token in the environment, a free lock is allowed and a held lock is a conflict, same as before. With a token, a free lock is a conflict (the wrapper died; do not write unlocked). A held lock is allowed only when the lock-file `writer_token` matches, the recorded pid is live, that pid is the child or an ancestor, and the purpose is an exact allowlist match (`att0-migrate`, `att0-restore`, `att0 meta fill`, and the existing writer purposes). `att0-restore` is not an alias of `att0-migrate`. `att0_restore`, `att0-restor`, a trailing space on `att0-restore`, and `ATT0-restore` are refused with `purpose not allowed`. The purpose field is not trimmed when the lock file is read. Any miss refuses. A walk that fails before any ancestor is read refuses (`ancestor walk error`). A later `syscall` failure, including Darwin `EPERM` from `proc_pidinfo(PROC_PIDTBSDINFO)` on a different-uid process, keeps the prefix already read. The lock pid must be in that prefix or the call refuses (`pid not ancestor`). The walk is at most 32 steps and does not use `/proc` on Darwin.

`is_live_sor` compares realpaths, so a symlink is not judged by its raw path string.

`SOR_FORCE_LIVE_CHECKS=1` turns the live checks on for a path that is not the live SoR. No value turns those checks off on the live path. Do not set the variable in a LaunchAgent.

## Caller-side live SoR guards

These four refuses run in the callers, before `refuse_if_sor_writer_conflict`. Each calls `sor_writer_gate.is_live_sor`. `--allow-mailroom-sqlite` bypasses that caller refuse and means the caller knows the resolved path is the live SoR. The writer gate still runs after the flag. Option A from `docs/heavy/20260927-2207-pr90-caller-live-only-guards.md` is implemented in the follow-up stacked on PR #90.

| Guard | Predicate |
|---|---|
| `migrate_database` in `scripts/attachments/migrate_att0_schema.py` | `sor_writer_gate.is_live_sor(path)` |
| `main` in `scripts/attachments/migrate_att0_schema.py` | `sor_writer_gate.is_live_sor(db_path)` |
| `fill_metadata` in `scripts/attachments/meta_fill.py` | `sor_writer_gate.is_live_sor(path)` |
| `main` in `scripts/attachments/meta_fill.py` | `sor_writer_gate.is_live_sor(db_path)` |

Residual, inventory only: `is_live_sor()` compares the resolved basename, not the inode. An APFS hard link with a different name to the SoR inode still looks not-live. This follow-up does not change the gate.

## Same-file embed_backfill (HARD DECK)

`--lock` on `embed_backfill.py` is **per batch / heartbeat**, not the
whole rem. It still refuses `ACTION_REQUIRED` and a lock held >4h. It
does **not** make two `embed_backfill` processes on one `.sqlite` safe.
Same-file 2-wide is HARD DECK. Preferred shard / char-band practice:
[docs/embed-backfill.md](../embed-backfill.md).

For rem itself, `with_writer_lock` is **process-lifetime**, not a
per-batch drop. Rem-window default is **freeze**
(`sor_increment=frozen`) until EXIT. Do not switch to interleave.
Do not drop the rem lock between batches. Daily `--lock` stays
per-batch. Rem EXIT 0 handling is out of scope here.
