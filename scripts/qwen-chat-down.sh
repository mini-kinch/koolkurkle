#!/bin/bash
# Path A Qwen chat session DOWN — stop mlx_lm.server; optional start Ollama for hybrid
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH
LOG="$HOME/MailArchive/logs/qwen-chat-down.log"
START_OLLAMA=0
if [ "${1:-}" = "--with-ollama" ] || [ "${1:-}" = "--start-ollama" ]; then
  START_OLLAMA=1
fi
exec >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] qwen-chat-down start (start_ollama=$START_OLLAMA)"

SPLIST="$HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist"
if [ -f "$SPLIST" ]; then
  launchctl bootout gui/$(id -u) "$SPLIST" 2>/dev/null || true
fi
# belt-and-suspenders
lsof -tiTCP:1234 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
sleep 1
lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1 && echo "WARN :1234 still up" || echo ":1234 idle"

# ask_mail --serve can stay up (FTS-only is fine without Qwen); leave LaunchAgent alone

if [ "$START_OLLAMA" = "1" ]; then
  echo "starting Ollama for hybrid/embed sessions"
  open -a Ollama 2>/dev/null || true
  sleep 2
  pgrep -lf 'ollama serve' | head -2 || echo "Ollama start pending"
else
  echo "Ollama not started (pass --with-ollama to enable hybrid/embed sessions)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] qwen-chat-down done"
