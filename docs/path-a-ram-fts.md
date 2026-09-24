# Path A — RAM and FTS-only rules

When the Path A Qwen generate server (`mlx_lm.server` via `com.mailroom.mlx-lm-server`) is running:

1. **Ollama must be stopped** (free RAM for the large MLX model).
2. **ask_mail retrieve must use `--fts-only`** so `/ask` does not call the embed runtime. Pin the serve LaunchAgent with `--serve --fts-only --no-generate`.
3. Hybrid / sqlite-vec retrieve returns only after Qwen is down and Ollama (or another embed runtime) is back.

Helpers: `scripts/qwen-chat-up.sh` and `scripts/qwen-chat-down.sh` (`--with-ollama` / `--start-ollama`).

This is the Mini Path A track. It does not replace `com.mailroom.mlx-generate` ([generate-mlx.md](generate-mlx.md)). Both tracks bind `127.0.0.1:1234`; run one listener.
