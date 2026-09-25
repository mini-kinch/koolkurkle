#!/bin/bash
# Path A Qwen chat session UP — RAM rules (CoS 2026-09-24)
# 1) Stop Ollama  2) ask_mail --serve --fts-only --no-generate  3) mlx_lm.server :1234 if weights ready
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH
LOG="$HOME/MailArchive/logs/qwen-chat-up.log"
exec >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] qwen-chat-up start"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping Ollama"
osascript -e 'quit application "Ollama"' >/dev/null 2>&1 || true
killall ollama 2>/dev/null || true
sleep 2

# Pin / reload ask-mail-serve (FTS-only, no generate)
PLIST="$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist"
if [ -f "$PLIST" ]; then
  launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$PLIST"
  launchctl kickstart -k gui/$(id -u)/com.mailroom.ask-mail-serve
fi
for i in 1 2 3 4 5 6 7 8; do
  if curl -sf -m 2 http://127.0.0.1:8743/health >/dev/null; then break; fi
  sleep 1
done
curl -sS -m 5 http://127.0.0.1:8743/health || echo "ask_mail health FAIL"
echo

HF_ROOT="$HOME/.cache/huggingface/hub"
MOD=$(ls -d "$HF_ROOT"/models--*Qwen* 2>/dev/null | head -1 || true)
INC=1; SAFE=0; SNAP=""
if [ -n "${MOD:-}" ]; then
  INC=$(find "$MOD" -name '*.incomplete' 2>/dev/null | wc -l | tr -d ' ')
  SAFE=$(find "$MOD" -name '*.safetensors' 2>/dev/null | wc -l | tr -d ' ')
  SNAP=$(ls -d "$MOD"/snapshots/* 2>/dev/null | head -1 || true)
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] HF incomplete=$INC safetensors=$SAFE snap=${SNAP:-none}"

if [ "${INC}" = "0" ] && [ "${SAFE}" -ge 1 ] && [ -n "${SNAP:-}" ]; then
  SPLIST="$HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist"
  cat > "$SPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mailroom.mlx-lm-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/qwen-mlx/bin/python</string>
    <string>-m</string>
    <string>mlx_lm.server</string>
    <string>--model</string>
    <string>$SNAP</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>1234</string>
    <string>--chat-template-args</string>
    <string>{"enable_thinking":false}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/MailArchive/logs/mlx_lm_server_1234.log</string>
  <key>StandardErrorPath</key><string>$HOME/MailArchive/logs/mlx_lm_server_1234.log</string>
</dict>
</plist>
EOF
  launchctl bootout gui/$(id -u) "$SPLIST" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$SPLIST"
  launchctl kickstart -k gui/$(id -u)/com.mailroom.mlx-lm-server
  for i in $(seq 1 36); do
    if lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] mlx_lm.server listening :1234"
      curl -sS -m 30 http://127.0.0.1:1234/v1/models | head -c 400; echo
      break
    fi
    sleep 5
  done
  if ! lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN :1234 not up yet — check mlx_lm_server_1234.log"
  fi
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] model not ready — ask_mail FTS-only serve up; mlx_lm.server deferred (HF watcher will start)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] qwen-chat-up done"
echo "Product path: ask_mail /ask (FTS-only) + mlx_lm.server :1234. No /ui."
