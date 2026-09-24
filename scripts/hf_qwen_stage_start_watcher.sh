#!/bin/bash
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH
HF_ROOT="$HOME/.cache/huggingface/hub"
LOG="$HOME/MailArchive/logs/hf_qwen_stage_watcher.log"
READY="$HOME/MailArchive/logs/path_a_hf_stage_ready.md"
SERVER_EV="$HOME/MailArchive/logs/path_a_mlx_server_started.md"
SERVER_PLIST="$HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist"
QWEN_PY="$HOME/qwen-mlx/bin/python"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] watcher tick" >>"$LOG"

# Already started?
if lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] :1234 already listening — exit" >>"$LOG"
  exit 0
fi

MOD=$(ls -d "$HF_ROOT"/models--*Qwen* 2>/dev/null | head -1)
[ -z "$MOD" ] && exit 0
INC=$(find "$MOD" -name '*.incomplete' 2>/dev/null | wc -l | tr -d ' ')
SAFE=$(find "$MOD" -name '*.safetensors' 2>/dev/null | wc -l | tr -d ' ')
echo "[$(date '+%Y-%m-%d %H:%M:%S')] mod=$MOD incomplete=$INC safetensors=$SAFE" >>"$LOG"

if [ "$INC" != "0" ] || [ "$SAFE" -lt 1 ]; then
  exit 0
fi

SNAP=$(ls -d "$MOD"/snapshots/* 2>/dev/null | head -1)
if [ -z "$SNAP" ] || [ ! -d "$SNAP" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] no snapshot dir yet" >>"$LOG"
  exit 0
fi

# STAGE evidence (once)
if [ ! -f "$READY" ]; then
  MLX_VER=$("$QWEN_PY" -c 'import mlx_lm; print(mlx_lm.__version__)' 2>/dev/null || echo missing)
  {
    echo "# Path A — HF STAGE ready"
    echo
    echo "**When:** $(date '+%Y-%m-%d %H:%M:%S')"
    echo "**Model dir:** $MOD"
    echo "**Snapshot:** $SNAP"
    echo "**safetensors:** $SAFE  incomplete: $INC"
    echo "**du:** $(du -sh "$MOD" | awk '{print $1}')"
    echo "**mlx_lm:** $MLX_VER (env ~/qwen-mlx)"
    echo
    echo "Standing GO includes server start. Proceeding: stop Ollama → start mlx_lm.server :1234."
  } > "$READY"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] STAGE READY wrote $READY" >>"$LOG"
fi

# RAM: stop Ollama before Qwen server
echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping Ollama for RAM" >>"$LOG"
osascript -e 'quit application "Ollama"' >/dev/null 2>&1 || true
killall ollama 2>/dev/null || true
sleep 3

# Write/refresh server LaunchAgent
cat > "$SERVER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mailroom.mlx-lm-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>$QWEN_PY</string>
    <string>-m</string>
    <string>mlx_lm.server</string>
    <string>--model</string>
    <string>$SNAP</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>1234</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/MailArchive/logs/mlx_lm_server_1234.log</string>
  <key>StandardErrorPath</key><string>$HOME/MailArchive/logs/mlx_lm_server_1234.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF

launchctl bootout gui/$(id -u) "$SERVER_PLIST" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$SERVER_PLIST"
launchctl kickstart -k gui/$(id -u)/com.mailroom.mlx-lm-server

# Wait for listen (up to ~180s)
for i in $(seq 1 36); do
  if lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

if ! lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL :1234 not listening" >>"$LOG"
  tail -40 "$HOME/MailArchive/logs/mlx_lm_server_1234.log" >>"$LOG" 2>/dev/null || true
  exit 1
fi

MODELS=$(curl -sS -m 30 http://127.0.0.1:1234/v1/models 2>/dev/null | head -c 500)
{
  echo "# Path A — mlx_lm.server STARTED"
  echo
  echo "**When:** $(date '+%Y-%m-%d %H:%M:%S')"
  echo "**Snapshot:** $SNAP"
  echo "**Bind:** 127.0.0.1:1234"
  echo "**Ollama:** stopped before start (RAM rule)"
  echo "**Retrieve while Qwen up:** ask_mail --fts-only"
  echo "**ask_mail --serve:** 127.0.0.1:8743 (hits_only until wire)"
  echo
  echo "## Smoke /v1/models (truncated)"
  echo
  echo '```'
  echo "$MODELS"
  echo '```'
  echo
  echo "Watcher unloading self after success."
} > "$SERVER_EV"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] SERVER STARTED wrote $SERVER_EV" >>"$LOG"

# Unload HF watcher (one-shot success)
launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/com.mailroom.hf-qwen-stage.plist" 2>/dev/null || true
exit 0
