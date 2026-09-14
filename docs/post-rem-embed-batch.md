# Post-rem same-writer embed batch bump (KOO-55)

Docs + config + tests for **AFTER rem-legacy EXIT 0 only**. This change
does not start, stop, restart, or hot-swap rem-legacy. Shipping this
config ≠ enabling the bump against a live rem job. Docs PR Ready ≠
permission to enable the bump.

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Generation key: [embed-generation-key.md](embed-generation-key.md).
Practice: [embed-backfill.md](embed-backfill.md).

## Next-run default (Heavy §5 / Heavy 04)

- Rem-legacy stays **batch 8** until EXIT 0.
- First next-run bump is **32**. Then **64** if that bump is stable.
- **256 is not the first bump.** 256 only after a measured holdout.
- Same writer. Same `qwen3-embedding:8b` / 1024-d store / instruction
  prefix. Native dim stays 4096.
- **Commit per batch** (already the backfill path).
- **Forbid mid-job bump.** Do not change `--batch-size` on a live rem
  process. No mid-job hot-swap language.

Config constants: `scripts/post_rem_embed_batch.py`
(`POST_REM_NEXT_RUN_BATCH_SIZE=32`). `embed_lib.DEFAULT_BATCH_SIZE`
stays **8** so this tree cannot imply a live rem bump.

```zsh
# SoR host — AFTER EXIT 0 + human go only. Next-run default 32. Not a live start.
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_backfill.py \
  --db $HOME/MailArchive/mailroom.sqlite --quote-strip --lock --batch-size 32 --dry-run
```

Rem EXIT 0 handling is out of scope for this PR.
