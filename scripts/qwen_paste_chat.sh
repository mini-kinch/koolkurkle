#!/bin/zsh
# qwen_paste_chat.sh — FTS paste → mlx_lm.server /v1/chat/completions
# No browser UI. No /ask audit on SoR (paste uses .backup). No wire tool.
# v3 2026-09-24 — thinking OFF; max_tokens 512; stderr version stamp
# Pack size: ASK_MAIL_PASTE_K default 20 (old k=8 under-packed multi-hit FTS).
set -euo pipefail
MAILARCHIVE="${MAILARCHIVE:-$HOME/MailArchive}"
PASTE="${MAILARCHIVE}/scripts/ask_mail_paste.sh"
PY="${MAILARCHIVE}/.venv/bin/python"
FMT="${MAILARCHIVE}/scripts/qwen_paste_chat_post.py"
BASE="${QWEN_BASE_URL:-http://127.0.0.1:1234/v1}"
MODEL="${QWEN_MODEL:-mlx-community/Qwen3.8-27B-4bit}"
MAX_TOKENS="${QWEN_MAX_TOKENS:-512}"
export ASK_MAIL_PASTE_K="${ASK_MAIL_PASTE_K:-20}"

if [[ $# -lt 1 ]]; then
  echo "usage: qwen_paste_chat.sh <query words...>" >&2
  echo "  ASK_MAIL_PASTE_K default 20" >&2
  exit 2
fi
[[ -x "$PASTE" ]] || { echo "error: missing $PASTE" >&2; exit 2; }
[[ -f "$FMT" ]] || { echo "error: missing $FMT" >&2; exit 2; }

echo "qwen_paste_chat.sh v3 | thinking=OFF | max_tokens=$MAX_TOKENS | k=$ASK_MAIL_PASTE_K | expect ~45-90s | look for post.py v3 on stderr" >&2
"$PASTE" "$@" | "$PY" "$FMT" --base "$BASE" --model "$MODEL" --max-tokens "$MAX_TOKENS"
