# Path A — Qwen chat RAG (Mini)

Parallel Mini track beside the existing generate LaunchAgent. Product path is `/ask` retrieve plus `mlx_lm.server`.

## Product path

- Retrieve: `ask_mail.py --serve` on loopback (`GET /health`, `POST /ask`) with **`--fts-only --no-generate`** while Qwen is up.
- Generate: `python -m mlx_lm.server` on `127.0.0.1:1234` (MLX native; separate venv `$HOME/qwen-mlx`).
- Browser `/ui` is not part of this path.

## Parallel to the existing generate track

[generate-mlx.md](generate-mlx.md) and `com.mailroom.mlx-generate` stay as they are (`scripts/mlx-generate-server.sh`, canonical python `~/MailArchive/venv-mlx/bin/python`). Path A adds `com.mailroom.mlx-lm-server` on the same loopback port `127.0.0.1:1234`, using `__QWEN_MLX_HOME__` (`$HOME/qwen-mlx`). Run one listener. Do not overwrite `launchd/com.mailroom.mlx-generate.plist`, `scripts/install-mlx-generate.sh`, or `docs/generate-mlx.md`.

## RAM / FTS rules (Qwen sessions)

1. Before starting Qwen: **stop Ollama**.
2. While Qwen is up: retrieve with **`--fts-only`** (do not call embed).
3. After Qwen is down: optional Ollama restart (`qwen-chat-down.sh --with-ollama`).

See [path-a-ram-fts.md](path-a-ram-fts.md).

## Session scripts

- `scripts/qwen-chat-up.sh` — stop Ollama, ensure ask-mail-serve is FTS-only, start `mlx_lm.server` if HF weights are ready.
- `scripts/qwen-chat-down.sh` — stop the mlx server; optional `--with-ollama` / `--start-ollama`.
- `scripts/hf_qwen_stage_start_watcher.sh` — when the HF cache is complete (no `*.incomplete`, at least one `*.safetensors`): STAGE note, stop Ollama, start `com.mailroom.mlx-lm-server`, smoke `GET /v1/models`, unload the watcher.

## LaunchAgent templates

See `launchd/*.plist.template`. Replace `__MAILARCHIVE_HOME__`, `__QWEN_MLX_HOME__`, and `__MODEL_SNAPSHOT__` at install. launchd does not expand `$HOME`.

Default ask-mail-serve pin:

`--serve --db <mailroom.sqlite> --fts-only --no-generate --host 127.0.0.1 --port 8743`

Those flags match `scripts/ask_mail.py`.

## Model

- Example HF id: `mlx-community/Qwen3.8-27B-4bit` (weights stay on the host; never commit).
- MLX env: separate venv (`$HOME/qwen-mlx`), not the MailArchive `.venv` and not `venv-mlx`.

## Out of scope

- Attachment search (ATT)
- SoR writers / dual-writer
- Overwriting `ask_mail.py` with an MCP-only stub
- Treating an HTTP `/ask` response as proof that ops wrote `ask_audit`
- Claiming browser `/ui` is Done
