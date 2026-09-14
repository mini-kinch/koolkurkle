# embed_backfill — one writer per sqlite (HARD DECK)

Preferred practice **before** any `embed_backfill.py` start. Read this
first. Human Terminal cards: [ops-terminal.md](ops-terminal.md). Daily
incremental: [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
Per-batch lock: [pr0/with_writer_lock_DESIGN.md](pr0/with_writer_lock_DESIGN.md).
Integrity: [sor-health.md](sor-health.md). Long embeds: host-kept
foreground (section below), not `nohup &`.

## One writer per `.sqlite` (HARD DECK)

Start **one** `embed_backfill` process against a given `.sqlite`.
Shipping guard: lockfile or busy refuse **before** a second
`embed_backfill`. Shipping the guard ≠ starting a writer. Rem-legacy
≠ Mini daily (HARD DECK): do not restart rem for daily; daily uses
`--quote-strip`; rem keeps old text until EXIT. Do not touch a
running rem-legacy job.
`--lock` takes the PR-0 writer lock **per batch / heartbeat** (not the
whole rem). It refuses `ACTION_REQUIRED` and a lock held >4h. It does
**not** make two writers on the same file safe. Same-file 2-wide
stays HARD DECK even with `--lock`.

`--lock` still belongs on the Mini daily incremental path. Use it for
that heartbeat / refuse behavior. Do not treat it as a 2-wide permit.

## `--reembed-legacy` ops contract

Shipped CLI only. No new flags. Defaults unchanged. Flag text:
`scripts/embed_backfill.py --help` (`--quote-strip` /
`--reembed-legacy`). Refuse text: `--reembed-legacy requires --quote-strip`.

- **`--reembed-legacy` requires `--quote-strip`.** Combined with
  `--quote-strip` only. Without `--quote-strip` the CLI refuses.
- **Default skip.** `--reembed-legacy` is **opt-in, default off**.
  Without the flag, rem-legacy rows stay `skipped_legacy_embedded`.
- **One writer per `.sqlite` (HARD DECK).** Never two writers on one
  sqlite. `--lock` is per-batch, not a 2-wide permit.
- **Not the daily path.** Do **not** put `--reembed-legacy` on the
  Mini daily argv.

`--quote-strip` incremental §6.1 **skips** rows where `embedding_meta`
is present and `content_hash` is NULL (live rem /
`skipped_legacy_embedded`). On SoR that skip is on the order of ~63k
rows. A normal daily/resume therefore stays safe: `--quote-strip
--min-chars 3000` can exit with 0 candidates when only rem-legacy rows
remain above the band.

Combined with `--quote-strip` only, `--reembed-legacy` treats those
rem-legacy rows as candidates so they can be rewritten onto the §6.1
header-prefixed document. Without the flag, behavior is unchanged
(same `skipped_legacy_embedded` counter). Do **not** put
`--reembed-legacy` on the Mini daily argv — a surprise ~63k rewrite
is the failure this default avoids.

`--reembed-legacy` without `--quote-strip` is refused. Still one writer
per `.sqlite`. `--lock` remains per-batch, not a 2-wide permit.

## `--embed-live-only` (flag + docs; no run)

Shipped CLI for a **future** daily incremental. Default off. Does
**not** start an embed job. Shipping the flag ≠ starting a job.
Guard ≠ run against rem-legacy.

- **`--embed-live-only`** restricts candidates to
  `present_on_server=1`.
- **must not delete existing tombstone embeds.** `present=0` rows
  stay `skipped_tombstone`; existing `message_embeddings` blobs stay.
- **Not on rem-legacy argv.** Do not restart rem. Do not put this
  flag on a running rem-legacy job.
- **Not the current Mini daily argv.** Daily stays
  `--skip-auth --quote-strip --lock` without `--embed-live-only`.

```zsh
# SoR host — FLAG ONLY / dry-run. Does not start rem-legacy.
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_backfill.py \
  --db $HOME/MailArchive/mailroom.sqlite --quote-strip --embed-live-only --lock --dry-run
```

```zsh
# SoR host — default skip (daily / resume). Rem-legacy stays skipped.
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_backfill.py \
  --db $HOME/MailArchive/mailroom.sqlite --quote-strip --min-chars 3000 --lock --dry-run
```

```zsh
# SoR host — OPT-IN rem-legacy re-embed under --quote-strip. One writer.
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_backfill.py \
  --db $HOME/MailArchive/mailroom.sqlite --quote-strip --reembed-legacy --min-chars 3000 --lock
```

## Host-kept foreground embed ops contract

Long MailArchive embeds stay **host-kept foreground** in a host
Terminal. Do **not** background with `nohup` or `&`.

Sole writer is HARD DECK: never two writers on one `.sqlite`. A live
rem-legacy job on the SoR-named file is that sole writer until EXIT 0.
Do not start a second `embed_backfill` against the same file.

After the foreground PID is known, keep the host awake for that
process (placeholder `<pid>`, not a live PID):

```zsh
# SoR host — prevent sleep while the foreground embed PID is live
caffeinate -w <pid>
```

`--reembed-legacy` is **not** the daily path. Daily incremental is
LaunchAgent `embed_backfill.py --skip-auth --quote-strip --lock`
without `--reembed-legacy`
([README.mailroom-daily.md](../scripts/README.mailroom-daily.md)).
Rem-legacy CLI rules (opt-in, default off, requires `--quote-strip`):
section above. No new flags. Defaults unchanged.

## Shard on separate files, then merge

Preferred split: **separate DBs and/or machines**, then merge.

Supported CLI: `scripts/embed_merge_shards.py` wraps
`embed_lib.merge_shards`. Missing-only: copy embed rows from
`--secondary` that are absent on `--primary` for the same
`(message_id, model, model_version)`. Existing primary rows stay
untouched. Never deletes primary rows. Never writes `messages` / FTS.
Never calls Ollama or IMAP. `--dry-run` counts without commit.
`--primary` and `--secondary` must be **different files**. This merge
does **not** make same-file 2-wide writers safe.

Example names only:

| Role | File |
|---|---|
| Copy host (Mini copy-only until PR-5) | `mailroom-copy.sqlite` (or `mailroom-daily-copy.sqlite`) |
| SoR host | SoR-named `mailroom.sqlite` |

1. One writer per file (char-band or `id-mod` / `id-rem` shard).
2. Wait until **both** writers **EXIT 0**.
3. Pause the other writer for the **merge window only**.
4. Run `embed_merge_shards.py` (`embed_lib.merge_shards`: missing-only
   embed rows into primary). Resume after merge EXIT 0.

```zsh
# copy host — short band on the copy file
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_backfill.py --db $HOME/MailArchive/mailroom-copy.sqlite --max-chars 3000
```

```zsh
# SoR host — long band on the SoR-named file
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_backfill.py --db $HOME/MailArchive/mailroom.sqlite --min-chars 3000
```

```zsh
# merge host — dry-run after both embed writers EXIT 0; other writer paused
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_merge_shards.py --primary $HOME/MailArchive/mailroom.sqlite --secondary $HOME/MailArchive/mailroom-copy.sqlite --dry-run
```

```zsh
# merge host — commit missing-only rows (same paths; other writer paused)
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_merge_shards.py --primary $HOME/MailArchive/mailroom.sqlite --secondary $HOME/MailArchive/mailroom-copy.sqlite
```

Same rule for `--id-mod` / `--id-rem`: separate files (and/or hosts),
then `embed_merge_shards.py` after both EXIT 0.

## Same-file char-bands (HARD DECK)

Parallel `--max-chars` / `--min-chars` (or two `id-mod` remainders) on
**one** file are HARD DECK. Preferred same-file pattern is sequential bands:
short band EXIT 0, then the long band on that same file.

A **single** argv closed band (`--min-chars 1500 --max-chars 2000`) is
one writer — that is fine. Parallel bands belong on **separate files**
only (copy vs SoR-named, above).

## Known-good recopy (do not merge-back a bad copy)

If a working copy is malformed (`malformed btree`,
`sqlite3.DatabaseError` on incremental write, or
`PRAGMA integrity_check` not `ok`):

1. Set that working copy **aside**. Do not merge it into SoR or the
   other shard.
2. Recopy from a **known-good** source (SoR-named file or last
   known-good backup).
3. Confirm `PRAGMA integrity_check` prints `ok`.
4. Then start **one** writer.

```zsh
# copy host — integrity before a new embed_backfill
sqlite3 "$HOME/MailArchive/mailroom-copy.sqlite" 'PRAGMA integrity_check;'
```

```zsh
# SoR host — integrity before a new embed_backfill
sqlite3 "$HOME/MailArchive/mailroom.sqlite" 'PRAGMA integrity_check;'
```

Expected: one line `ok`. The health pack hard-fails otherwise:
[sor-health.md](sor-health.md).

## Embed generation key (4096 native / 1024 store)

Locked key: `model_tag` + `embed_runtime` + `native_dim` +
`store_dim` + `instruction_prefix`. v1 is `qwen3-embedding:8b` /
Ollama / **4096 native / 1024 store** / Qwen3 query instruct prefix.
Refuse writes that mismatch `embedding_meta`. Aligns
[MAILROOM.md](MAILROOM.md) with `embed_lib.py`. Design:
[embed-generation-key.md](embed-generation-key.md).

## Post-rem same-writer batch bump (AFTER EXIT 0 only)

Rem-legacy stays **batch 8** until EXIT 0. Next-run default after
EXIT 0 + human go is **32**, then **64** if stable. **256 is not the
first bump.** Same writer. Same model / 1024-d / instruction prefix.
Commit per batch. **Forbid mid-job bump.** Docs PR Ready ≠ permission
to enable this against live rem. Config:
[post-rem-embed-batch.md](post-rem-embed-batch.md).

## Compute sidecar (one-writer apply; never live rem SoR)

Producer → shard files → one applier takes `with_writer_lock` and
writes `embedding_meta`+vec only. Missing-only INSERT. Never pointed
at live rem SoR. Design: [compute-sidecar.md](compute-sidecar.md).

## ATT-0 attachment lane (DESIGN ONLY)

Attachment chunks use a separate lane (`chunk_embeddings` 1024-d),
not `message_embeddings`. Same generation key unless a new generation
is declared. While rem holds the live SoR lock, only **file-stage**
work (B/C/E sidecars) or a **copy DB** is legal. Live SoR catalog /
APPLY_TEXT / APPLY_VEC wait rem EXIT 0 + `with_writer_lock`.
[att0-constraints.md](att0-constraints.md). ATT-1..8 implement is
FUTURE / out of scope. Ready ≠ ATT implement permission. This change
does not start an ATT catalog or apply. Unified-search / ask_all
DESIGN ONLY (separate SoRs; Mini embeds only from copied replicas;
no MSG/NOTE apply during rem):
[unified-search-design.md](unified-search-design.md).
Ready ≠ MSG/NOTE enable.
