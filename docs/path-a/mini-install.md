# Path A Mini install (wire / paste)

Copies the rem-safe Path A wire/paste set from a local clone into `$MAILARCHIVE` (default `$HOME/MailArchive`). Does not replace `ask_mail.py`.

## Install
From a local clone:

```zsh
./scripts/install_path_a_wire_to_mailarchive.sh
```

Optional dest:

```zsh
MAILARCHIVE=/path ./scripts/install_path_a_wire_to_mailarchive.sh
```

`--dry-run` prints the planned copies and writes nothing. Re-run is safe.

## What lands
Five scripts under `$MAILARCHIVE/scripts/` (`ask_mail_wire.sh`, `ask_mail_paste.sh`, `qwen_paste_chat.sh`, `qwen_paste_chat_post.py`, `ask_mail_paste_fmt.py`) and these notes under `$MAILARCHIVE/docs/path-a/` ([wire-readme.md](wire-readme.md), [fts_caveat.md](fts_caveat.md), this file). Shell scripts and the two Python helpers are marked executable.

## What it does not
Does not copy or overwrite `ask_mail.py`. If that file is already in `$MAILARCHIVE/scripts/`, the installer leaves it untouched.

## Prerequisites
`ask_mail.py` and `$HOME/MailArchive/.venv` are already on the machine. Retrieve listens on `:8743`. mlx listens on `:1234`. Prefer Ollama down while Qwen is up. Paste is `--fts-only` (read-only `.backup`, no generate).

## Prove
```zsh
~/MailArchive/scripts/ask_mail_wire.sh American Express low balance 2015
```

Expect stderr `k=20`. The pack is fuller than the old k=8 default. See [fts_caveat.md](fts_caveat.md) and [wire-readme.md](wire-readme.md).

## Wedged generate
If `/v1/models` is OK but chat completions hang or time out (~300s), the chat server is wedged. Run `qwen-chat-down.sh` then `qwen-chat-up.sh` (Path A ops pack), confirm a tiny chat probe, then retry the wire. Do not blame paste/k first.

## HOLD
No ATT. No `ask_mail.py` overwrite. No `/ui`.
