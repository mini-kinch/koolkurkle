# Path A wire / paste scripts (rem-safe)

Companion to the Path A Qwen chat ops pack (up/down and LaunchAgent templates live in the sibling ops PR). This slice is the thin wire only: FTS read-only retrieve → DATA+QUESTION paste → mlx `127.0.0.1:1234` chat. Mini-local. No SoR writes, no `/ui`, no ATT.

## Files (repo layout)
- `scripts/ask_mail_wire.sh` — health checks + thin alias → `qwen_paste_chat.sh`
- `scripts/qwen_paste_chat.sh` — paste → `qwen_paste_chat_post.py` → `/v1/chat/completions`
- `scripts/qwen_paste_chat_post.py` — thinking OFF; final answer on stdout only (no JSON dump)
- `scripts/ask_mail_paste.sh` — `.backup` snap + `ask_mail.py --fts-only --no-generate --no-rerank --json --k` (`ASK_MAIL_PASTE_K` default **20**)
- `scripts/ask_mail_paste_fmt.py` — JSON hits → DATA + QUESTION block
- `docs/path-a/fts_caveat.md` — prefer "American Express" not bare "Amex"; default k=20 (old k=8 under-packed)
- `docs/path-a/wire-readme.md` — this note

## Install
In this repo the scripts stay under `scripts/`. These notes stay under `docs/path-a/`.

On Mini, copy the five script files into `$HOME/MailArchive/scripts/`. The wrappers resolve `$MAILARCHIVE/scripts/` (default `$HOME/MailArchive`). Then:

```zsh
chmod +x "$HOME/MailArchive/scripts/ask_mail_wire.sh" \
  "$HOME/MailArchive/scripts/qwen_paste_chat.sh" \
  "$HOME/MailArchive/scripts/ask_mail_paste.sh"
```

Requires the existing `ask_mail.py` and `$HOME/MailArchive/.venv` already on the machine. This pack does not modify `ask_mail.py`.

Desk one-liner:

```zsh
~/MailArchive/scripts/ask_mail_wire.sh American Express low balance 2015
```

Pack size default is `ASK_MAIL_PASTE_K=20`. See [fts_caveat.md](fts_caveat.md).
