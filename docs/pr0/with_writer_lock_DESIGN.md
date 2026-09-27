# PR-0 writer lock — MAILROOM.md §9.5

Lock file: `~/MailArchive/mailroom.write.lock`

Mechanism: exclusive `flock` (advisory). Holder writes `PID` / `hostname` / `purpose` / ISO timestamp / a random token into the lock file. The token is never logged or printed.

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

## Identity handoff

The wrapper draws `secrets.token_urlsafe(16)` or longer, stores it in the lock file, and passes it to the child with the wrapper pid and purpose:

- `MAILROOM_WRITER_LOCK_TOKEN`
- `MAILROOM_WRITER_LOCK_PID`
- `MAILROOM_WRITER_LOCK_PURPOSE`

The child gate probes `mailroom.write.lock` with a new open and `LOCK_EX|LOCK_NB`, then closes that probe fd. It does not keep the probe lock. With no token in the environment, a free lock is allowed and a held lock is a conflict, same as before. With a token, a free lock is a conflict (the wrapper died; do not write unlocked). A held lock is allowed only when the lock-file token matches, the recorded pid is live, that pid is the child or an ancestor, and the purpose is on the writer allowlist (`att0-migrate`, `att0 meta fill`, and the existing writer purposes). Any miss or ancestor-walk error refuses. The walk is at most 32 steps and does not use `/proc` on Darwin.

`is_live_sor` compares realpaths, so a symlink is not judged by its raw path string.

`SOR_FORCE_LIVE_CHECKS=1` turns the live checks on for a path that is not the live SoR. No value turns those checks off on the live path. Do not set the variable in a LaunchAgent.

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
