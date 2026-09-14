# Mini MLX embedder path (design) — KOO-56

Design + holdout contract only. Rem-legacy stays running and
untouched. No mid-index switch. No live embedder cutover. Docs PR Ready ≠ permission to enable Mini MLX against live rem.

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Generation key: [embed-generation-key.md](embed-generation-key.md).
RAM law: [generate-mlx.md](generate-mlx.md).

## Same family or new generation

Prefer the same family + dim + prefix as the live rem index:

- model_tag `qwen3-embedding:8b`
- native_dim 4096 / store_dim 1024
- same instruction prefix

Else treat the Mini MLX path as a **new `model_version` / SoR
generation**. Do not mix vectors in one index.

## Holdout required before cutover (HARD DECK)

Before any MLX cutover:

1. Freeze N `message_ids` (fixture / holdout list).
2. Embed the same documents on Ollama and on Mini MLX.
3. Require cosine-agreement ≥ threshold (default 0.97).
4. **Fail-closed if miss** (missing id or cosine below threshold).

Helper: `scripts/mlx_embed_holdout.py`. Fixture only. Never starts an
embedder. Never writes SoR.

## Mini RAM law (HARD DECK)

No co-reside **8B embed + 35B generate** on Mini (no co-reside 8B embed + 35B generate). Unload embed before
`mlx_lm.server` generate. Bind generate `1234` and ask_mail `8743` to
`127.0.0.1` only.

## Out of scope

Mid-index model change. Rem kill / restart. Second writer. Live MLX
switch. Buying hardware.
