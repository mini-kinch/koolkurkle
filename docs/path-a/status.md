# Path A read-only status

`scripts/path_a_status.sh` checks Path A and prints one `PASS`, `WARN`, or `FAIL` line per check, then a summary. Read-only: stdout and stderr only. It does not write files, mail, sqlite, or LaunchAgents, and it does not start or stop services. It does not print file contents, mail, or absolute paths. Details stay relative to `$MAILARCHIVE` (`scripts/...`).

## Run

From a clone (the Mini install helper does not copy this script):

```zsh
MAILARCHIVE="$HOME/MailArchive" ./scripts/path_a_status.sh
MAILARCHIVE="$HOME/MailArchive" ./scripts/path_a_status.sh --no-probe
./scripts/path_a_status.sh --json
```

Exit `0` when no check is `FAIL`. Exit `1` when any check is `FAIL`. `WARN` does not change the exit code. Unknown flags exit `2`.

## Checks

1. **retrieve_health** — `curl` `GET` `$RETRIEVE_URL/health` (default `http://127.0.0.1:8743/health`), 3s.
2. **mlx_models** — `curl` `GET` `$QWEN_BASE_URL/models` (default `http://127.0.0.1:1234/v1/models`), 3s.
3. **mlx_probe** — one chat completion with `max_tokens` 1 and `chat_template_kwargs.enable_thinking` false. Timeout is `QWEN_PROBE_TIMEOUT` (default 20s). If `/v1/models` is OK but the probe times out, the line is `FAIL` and includes `generate wedged: qwen-chat-down.sh then qwen-chat-up.sh`. `--no-probe` skips this check (no line).
4. **ollama** — `WARN` when Ollama is up and mlx `:1234` is up (`RAM rule: stop Ollama while Qwen up`). Ollama down is `PASS`. Default probe is `GET` `http://127.0.0.1:11434/api/tags`.
5. **path_a_files** — these files exist and are executable under `$MAILARCHIVE/scripts`: `ask_mail_wire.sh`, `ask_mail_paste.sh`, `qwen_paste_chat.sh`, `qwen_paste_chat_post.py`, `ask_mail_paste_fmt.py`.
6. **paste_k** — `ask_mail_paste.sh`, `ask_mail_wire.sh`, and `qwen_paste_chat.sh` default `ASK_MAIL_PASTE_K` to 20. `FAIL` if a file has the `ASK_MAIL_PASTE_K:-8` token (reported before any other k problem). Same token rule as the Mini install helper. A comment that only says old k=8 does not match.
7. **ask_mail_py** — `scripts/ask_mail.py` is present. The line prints byte size and a 12-character sha256 prefix only. `WARN` if smaller than 10240 bytes (`<10KB possible MCP stub`). Missing file is `FAIL`.

## Flags

- `--no-probe` — skip the mlx chat probe
- `--json` — one JSON object on stdout (`pass`, `warn`, `fail`, `checks` with `name`, `status`, `detail`)
- `--help` — usage

## Env

- `MAILARCHIVE` — default `$HOME/MailArchive`
- `RETRIEVE_URL` — default `http://127.0.0.1:8743` (`/health` is appended)
- `QWEN_BASE_URL` — default `http://127.0.0.1:1234/v1` (`/models` and `/chat/completions` are appended)
- `QWEN_PROBE_TIMEOUT` — probe timeout in seconds, default `20`
- `OLLAMA_BASE_URL` — optional, default `http://127.0.0.1:11434` (`/api/tags` is appended)

## Output

```
PASS retrieve_health /health reachable
PASS mlx_models /v1/models reachable
PASS mlx_probe chat probe max_tokens=1 thinking=off
PASS ollama down
PASS path_a_files present+executable
PASS paste_k default 20
WARN ask_mail_py size=12 sha256=0123456789ab <10KB possible MCP stub
SUMMARY pass=6 warn=1 fail=0
```

A wedged generate server (models up, probe timed out):

```
FAIL mlx_probe chat probe timed out; generate wedged: qwen-chat-down.sh then qwen-chat-up.sh
```

## Related

- [wire-readme.md](wire-readme.md)
- [mini-install.md](mini-install.md)
- [fts_caveat.md](fts_caveat.md)

No ATT. No `/ui`. Does not modify `ask_mail.py`, the installer, `post.py`, or LaunchAgents.
