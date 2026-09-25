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
If `/v1/models` is OK but generate is wedged, the v4 preflight probe fails in about 60s (`QWEN_PROBE_TIMEOUT`) with exit 3 and the restart hint (stdout empty). The main post timeout is 180s (`QWEN_TIMEOUT`). The first request after idle can take 20-40s to page the model back in, so a slow first probe is not a wedge. Run `qwen-chat-down.sh` then `qwen-chat-up.sh` (Path A ops pack), confirm a tiny chat probe, then retry the wire. Do not blame paste/k first. See [Timeouts](wire-readme.md#timeouts).

## HOLD
No ATT. No `ask_mail.py` overwrite. No `/ui`.

## LaunchAgent templates

`launchd/com.mailroom.mlx-lm-server.plist.template` and `launchd/com.mailroom.ask-mail-serve.plist.template` are checked-in copies of the live login agents. Paths use the `__HOME__` placeholder (launchd does not expand `$HOME`). The mlx `--model` snapshot is the PIN in `scripts/qwen-chat-up.sh` ([model-pin.md](model-pin.md)).

Installing them is a separate operator step: `sed` `__HOME__` to `$HOME`, copy the result to `~/Library/LaunchAgents`, and `launchctl bootstrap`. This change does not do that.
