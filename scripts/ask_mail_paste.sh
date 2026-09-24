#!/bin/zsh
# ask_mail_paste.sh — FTS-only → DATA + QUESTION paste block for Qwen.
# Rem-safe: sqlite .backup of SoR; ask_audit never writes mailroom.sqlite.
# No /ask HTTP, no wire to :1234, no generate.
set -euo pipefail

MAILARCHIVE="${MAILARCHIVE:-$HOME/MailArchive}"
PY="${MAILARCHIVE}/.venv/bin/python"
ASK="${MAILARCHIVE}/scripts/ask_mail.py"
FMT="${MAILARCHIVE}/scripts/ask_mail_paste_fmt.py"
SOR="${MAILROOM_DB:-$MAILARCHIVE/mailroom.sqlite}"
K="${ASK_MAIL_PASTE_K:-20}"

if [[ $# -lt 1 ]]; then
  echo "usage: ask_mail_paste.sh <query words...>" >&2
  exit 2
fi
QUERY="$*"

[[ -x "$PY" ]] || { echo "error: missing $PY" >&2; exit 2; }
[[ -f "$ASK" ]] || { echo "error: missing $ASK" >&2; exit 2; }
[[ -f "$FMT" ]] || { echo "error: missing $FMT" >&2; exit 2; }
[[ -f "$SOR" ]] || { echo "error: missing $SOR" >&2; exit 2; }

SNAP="${TMPDIR:-/tmp}/mailroom-paste-$$.sqlite"
cleanup() { rm -f "$SNAP" "$SNAP-journal" "$SNAP-wal" "$SNAP-shm" 2>/dev/null || true; }
trap cleanup EXIT

sqlite3 "$SOR" ".backup '$SNAP'"

"$PY" "$ASK" \
  --db "$SNAP" \
  --fts-only \
  --no-generate \
  --no-rerank \
  --json \
  --k "$K" \
  -- "$QUERY" \
| "$PY" "$FMT" "$QUERY"
