# Compute sidecar design — one-writer apply (KOO-57)

Design + contract tests. Not a live run. HARD DECK one-writer apply.
**Never pointed at live rem SoR** (`mailroom.sqlite` basename is
refused). Docs PR Ready ≠ permission to apply against live rem.

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Lock: [pr0/with_writer_lock_DESIGN.md](pr0/with_writer_lock_DESIGN.md).
Generation key: [embed-generation-key.md](embed-generation-key.md).

## Apply contract

1. **Shard** = `id` + `content_hash` + `model_tag` + `store_dim` +
   `vector` + `checksum`.
2. **One applier** takes `with_writer_lock`.
3. Writes **`embedding_meta` + vec only** — never `messages` / FTS /
   IMAP.
4. **Missing-only INSERT.** Hash mismatch = skip unless explicit
   `--reembed` **human go**.
5. **Refuse second apply** on the same shard.
6. **Negative smoke:** corrupt / dim mismatch / second applier /
   lock held → non-zero.
7. **Never pointed at live rem SoR.**

Helper: `scripts/embed_sidecar_apply.py`. Temp fixtures only.

```zsh
# fixture host — sidecar apply dry contract (never mailroom.sqlite)
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/embed_sidecar_apply.py \
  --db /tmp/sidecar-apply.sqlite --shards /tmp/sidecar-shards.json --lock-file /tmp/sidecar.lock
```

Producer may write parquet / vector files off-box. Only one writer
applies into sqlite. Not two SoR writers.
