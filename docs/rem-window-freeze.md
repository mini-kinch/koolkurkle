# Rem-window freeze + lock lifetime + generate topology (KOO-64)

Docs/tests only. No live act. Rem-legacy stays running. Rem EXIT 0 handling is out of scope for this PR.

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Lock: [pr0/with_writer_lock_DESIGN.md](pr0/with_writer_lock_DESIGN.md).
Catch-up: [post-exit-catchup.md](post-exit-catchup.md).
PR-5: [pr5-cutover.md](pr5-cutover.md).

## 1. Lock lifetime

`with_writer_lock` is **process-lifetime for rem**, not a per-batch drop. Daily `--lock` on `embed_backfill` stays per-batch / heartbeat.
Do not drop the rem lock between batches.

## 2. Rem-window default = FREEZE

Label **freeze-increment** (`sor_increment=frozen`) **OR** interleave.
Default is **freeze until EXIT**. Do **not** switch to interleave.
While rem is live, SoR increment stays frozen. Clear
`sor_increment=frozen` only after post-EXIT catch-up.

## 3. PR-5 generate topology

After a later PR-5 (not this change):

- **Retrieve on the SoR host** (Mini once Mini is SoR).
- **Generate localhost** hits/snippets (`mlx_lm.server` on
  `127.0.0.1:1234`; ask_mail UI `127.0.0.1:8743`).
- MBP generate stays localhost. Do not send Mini retrieve hits to a
  remote generate over the LAN as the default.

No PR-5 enable here. No RunAtLoad change.
