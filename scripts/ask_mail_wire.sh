#!/bin/zsh
# ask_mail_wire.sh — thin Path A wire alias over qwen_paste_chat (v3).
# FTS retrieve RO (.backup) → DATA+QUESTION → mlx :1234 chat (thinking OFF).
# No SoR writes, no /ui, no Ollama chat, no ask_audit writes.
# 2026-09-24 — health checks + stderr stamp; delegates to qwen_paste_chat.sh
# Pack size: ASK_MAIL_PASTE_K default 20 (old k=8 under-packed multi-hit FTS).
set -euo pipefail

MAILARCHIVE="${MAILARCHIVE:-$HOME/MailArchive}"
CHAT="${MAILARCHIVE}/scripts/qwen_paste_chat.sh"
QWEN_BASE="${QWEN_BASE_URL:-http://127.0.0.1:1234/v1}"
ASK_HEALTH="${ASK_MAIL_HEALTH_URL:-http://127.0.0.1:8743/}"
WIRE_VER="ask_mail_wire.sh v1 2026-09-24"
export ASK_MAIL_PASTE_K="${ASK_MAIL_PASTE_K:-20}"

usage() {
  echo "usage: ask_mail_wire.sh <query words...>" >&2
  echo "  Path A one-liner: FTS RO paste → mlx :1234 /v1/chat/completions (thinking OFF)" >&2
  echo "  Requires: ask_mail serve :8743 (or paste path via .backup) + mlx_lm.server :1234" >&2
  echo "  Env: QWEN_BASE_URL QWEN_MODEL QWEN_MAX_TOKENS ASK_MAIL_PASTE_K (default 20) MAILARCHIVE" >&2
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

[[ -x "$CHAT" ]] || { echo "error: missing $CHAT" >&2; exit 2; }

# Health: :1234 must be up (fail-closed). :8743 is nice-to-have for paste serve;
# ask_mail_paste.sh uses sqlite .backup directly so 8743 down is WARN not FAIL.
qwen_ok=0
ask_ok=0
if curl -sf -m 2 "${QWEN_BASE%/v1}/v1/models" >/dev/null 2>&1 || \
   curl -sf -m 2 "http://127.0.0.1:1234/v1/models" >/dev/null 2>&1; then
  qwen_ok=1
fi
if curl -sf -m 2 "$ASK_HEALTH" >/dev/null 2>&1; then
  ask_ok=1
fi

echo "$WIRE_VER | qwen:1234=$qwen_ok ask:8743=$ask_ok | k=$ASK_MAIL_PASTE_K | delegating qwen_paste_chat.sh v3" >&2

if [[ "$qwen_ok" -ne 1 ]]; then
  echo "error: mlx_lm.server :1234 down — fail-closed (do not invent bake-off)" >&2
  exit 4
fi
if [[ "$ask_ok" -ne 1 ]]; then
  echo "warn: ask_mail :8743 not responding; paste still uses .backup RO path" >&2
fi

# Ollama chat/embed must stay down while :1234 is up (advisory; not a SoR writer).
if curl -sf -m 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "error: Ollama :11434 is UP while mlx :1234 is up — refuse dual-LLM" >&2
  exit 5
fi

exec "$CHAT" "$@"
