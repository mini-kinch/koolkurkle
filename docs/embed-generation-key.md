# Embed generation key (KOO-58)

Lock the embed generation key. Refuse writes that mismatch
`embedding_meta`. Align MAILROOM + embed-backfill with `embed_lib.py`
(**4096 native / 1024 store**).

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Practice: [embed-backfill.md](embed-backfill.md).
MAILROOM: [MAILROOM.md](MAILROOM.md).

## Key fields

`model_tag` + `embed_runtime` + `native_dim` + `store_dim` +
`instruction_prefix`.

Locked v1:

| Field | Value |
|---|---|
| model_tag | `qwen3-embedding:8b` |
| embed_runtime | `ollama` (Mini MLX is a new generation unless holdout PASSes) |
| native_dim | 4096 |
| store_dim | 1024 |
| instruction_prefix | Qwen3 query instruct (`QUERY_INSTRUCT`) |

Helper: `scripts/embed_generation_key.py`. `embed_lib.upsert_embedding`
refuses a mismatch.

Rem untouched. No mid-index model swap. No live SoR writes from this
gate.
