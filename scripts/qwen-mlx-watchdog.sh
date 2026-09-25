#!/bin/bash
# One pass per invocation for the local mlx_lm.server LaunchAgent.
# StartInterval drives the schedule. A restart is only:
#   launchctl kickstart -k gui/<uid>/<WD_LABEL>
# and only after the pinned snapshot is present and the loaded agent's
# --model argument is exactly that path. This pass never loads an agent,
# never writes a plist, and never talks to any host other than WD_HOST:WD_PORT.
set -u

WD_HOST="${WD_HOST:-127.0.0.1}"
WD_PORT="${WD_PORT:-1234}"
WD_LABEL="${WD_LABEL:-com.mailroom.mlx-lm-server}"
WD_HOLD="${WD_HOLD:-$HOME/qwen-mlx/HOLD}"
WD_STATE_DIR="${WD_STATE_DIR:-$HOME/qwen-mlx/watchdog}"
WD_LOG="${WD_LOG:-$HOME/MailArchive/logs/qwen-watchdog.log}"
WD_SERVER_LOG="${WD_SERVER_LOG:-$HOME/MailArchive/logs/mlx_lm_server_1234.log}"
WD_QUIET_SECS="${WD_QUIET_SECS:-900}"
WD_MODELS_TIMEOUT="${WD_MODELS_TIMEOUT:-10}"
WD_PROBE_TIMEOUT="${WD_PROBE_TIMEOUT:-240}"
WD_FAILS_BEFORE_RESTART="${WD_FAILS_BEFORE_RESTART:-2}"
WD_MAX_RESTARTS="${WD_MAX_RESTARTS:-3}"
WD_RESTART_WINDOW="${WD_RESTART_WINDOW:-3600}"
WD_POST_RESTART_GRACE="${WD_POST_RESTART_GRACE:-600}"
WD_LAUNCHCTL="${WD_LAUNCHCTL:-launchctl}"
WD_CURL="${WD_CURL:-curl}"
WD_MODEL_PATH="${WD_MODEL_PATH:-$HOME/.cache/huggingface/hub/models--mlx-community--Qwen3.8-27B-4bit/snapshots/10c35caafbb80f7dc6a7a432cdd11af10a6d4818}"

fails_file="$WD_STATE_DIR/consecutive_fails"
restarts_file="$WD_STATE_DIR/restarts"
last_restart_file="$WD_STATE_DIR/last_restart"
lockdir="$WD_STATE_DIR/lock"
owned=0
print_out=""
lat=0
active_age=0

log() {
  mkdir -p "$(dirname "$WD_LOG")"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$WD_LOG"
}

cleanup() {
  if [ -n "$print_out" ] && [ -f "$print_out" ]; then
    rm -f "$print_out"
  fi
  if [ "$owned" = "1" ]; then
    rm -rf "$lockdir"
  fi
}
trap cleanup EXIT

write_fails() {
  printf '%s\n' "$1" > "$fails_file"
}

read_fails() {
  local n
  n=0
  if [ -f "$fails_file" ]; then
    IFS= read -r n < "$fails_file" || n=0
  fi
  case "$n" in
    ''|*[!0-9]*) n=0 ;;
  esac
  printf '%s' "$n"
}

write_hold() {
  mkdir -p "$(dirname "$WD_HOLD")"
  printf '%s\n' "$1" > "$WD_HOLD"
  log "$1"
}

mtime_of() {
  # GNU stat (-c) and BSD stat (-f) disagree. Use whichever this host has,
  # and do not mix the other command's stdout into the timestamp.
  if stat -c %Y "$1" >/dev/null 2>&1; then
    stat -c %Y "$1"
    return 0
  fi
  if stat -f %m "$1" >/dev/null 2>&1; then
    stat -f %m "$1"
    return 0
  fi
  return 1
}

acquire_lock() {
  local mtime now age
  if mkdir "$lockdir" 2>/dev/null; then
    return 0
  fi
  if [ -d "$lockdir" ]; then
    mtime=$(mtime_of "$lockdir")
    now=$(date +%s)
    case "$mtime" in
      ''|*[!0-9]*) mtime="" ;;
    esac
    if [ -n "$mtime" ]; then
      age=$((now - mtime))
      if [ "$age" -gt 900 ]; then
        log "stale lock removed"
        rm -rf "$lockdir"
        if mkdir "$lockdir" 2>/dev/null; then
          return 0
        fi
      fi
    fi
  fi
  log "lock busy, skip"
  return 1
}

in_grace() {
  local prev now age
  [ -f "$last_restart_file" ] || return 1
  IFS= read -r prev < "$last_restart_file" || return 1
  case "$prev" in
    ''|*[!0-9]*) return 1 ;;
  esac
  now=$(date +%s)
  age=$((now - prev))
  [ "$age" -lt "$WD_POST_RESTART_GRACE" ]
}

restarts_in_window() {
  local now cutoff n epoch
  now=$(date +%s)
  cutoff=$((now - WD_RESTART_WINDOW))
  n=0
  if [ -f "$restarts_file" ]; then
    while IFS= read -r epoch || [ -n "${epoch:-}" ]; do
      case "$epoch" in
        ''|*[!0-9]*) continue ;;
      esac
      if [ "$epoch" -ge "$cutoff" ]; then
        n=$((n + 1))
      fi
    done < "$restarts_file"
  fi
  printf '%s' "$n"
}

pin_files_ok() {
  local safe inc repo parent
  if [ ! -d "$WD_MODEL_PATH" ]; then
    return 1
  fi
  safe=$(find "$WD_MODEL_PATH" -type f -name '*.safetensors' 2>/dev/null | wc -l | tr -d '[:space:]')
  if [ "${safe:-0}" -lt 1 ]; then
    return 1
  fi
  repo="$WD_MODEL_PATH"
  parent=$(basename "$(dirname "$WD_MODEL_PATH")")
  if [ "$parent" = "snapshots" ]; then
    repo=$(dirname "$(dirname "$WD_MODEL_PATH")")
  fi
  if [ ! -d "$repo" ]; then
    return 1
  fi
  inc=$(find "$repo" -type f -name '*.incomplete' 2>/dev/null | wc -l | tr -d '[:space:]')
  if [ "${inc:-0}" -ne 0 ]; then
    return 1
  fi
  return 0
}

agent_model_matches() {
  local inf="$1"
  local in_args prev line trimmed
  in_args=0
  prev=""
  while IFS= read -r line || [ -n "${line:-}" ]; do
    trimmed=$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [ "$in_args" -eq 0 ]; then
      case "$trimmed" in
        "arguments = {"*) in_args=1 ;;
      esac
      continue
    fi
    if [ "$trimmed" = "}" ]; then
      break
    fi
    if [ "$prev" = "--model" ] && [ "$trimmed" = "$WD_MODEL_PATH" ]; then
      return 0
    fi
    prev=$trimmed
  done < "$inf"
  return 1
}

models_ok() {
  local body meta http
  body=$(mktemp "${TMPDIR:-/tmp}/qwen-wd.XXXXXX")
  meta=$("$WD_CURL" -sS -m "$WD_MODELS_TIMEOUT" -o "$body" -w '%{http_code}' \
    "http://${WD_HOST}:${WD_PORT}/v1/models" 2>/dev/null) || meta="000"
  rm -f "$body"
  http=${meta%% *}
  [ "$http" = "200" ]
}

# True when the server log exists and was modified inside WD_QUIET_SECS.
# mtime only; the log contents are never read.
server_recent() {
  local mtime now
  active_age=0
  [ -e "$WD_SERVER_LOG" ] || return 1
  mtime=$(mtime_of "$WD_SERVER_LOG") || return 1
  case "$mtime" in
    ''|*[!0-9]*) return 1 ;;
  esac
  now=$(date +%s)
  active_age=$((now - mtime))
  if [ "$active_age" -lt 0 ]; then
    active_age=0
  fi
  [ "$active_age" -lt "$WD_QUIET_SECS" ]
}

chat_ok() {
  local body meta http lat_out
  body=$(mktemp "${TMPDIR:-/tmp}/qwen-wd.XXXXXX")
  meta=$("$WD_CURL" -sS -m "$WD_PROBE_TIMEOUT" -o "$body" \
    -w '%{http_code} %{time_total}' \
    -H 'Content-Type: application/json' \
    -d '{"messages":[{"role":"user","content":"ping"}],"max_tokens":1,"temperature":0,"stream":false}' \
    "http://${WD_HOST}:${WD_PORT}/v1/chat/completions" 2>/dev/null) || true
  if [ -z "$meta" ]; then
    meta="000 0"
  fi
  http=${meta%% *}
  lat_out=${meta#* }
  lat=${lat_out%% *}
  if [ "$http" != "200" ]; then
    rm -f "$body"
    return 1
  fi
  if ! grep -q '"choices"[[:space:]]*:[[:space:]]*\[' "$body"; then
    rm -f "$body"
    return 1
  fi
  rm -f "$body"
  return 0
}

note_failure() {
  local n prior rc epoch
  n=$(read_fails)
  n=$((n + 1))
  write_fails "$n"
  log "fail consecutive=${n}"
  if [ "$n" -lt "$WD_FAILS_BEFORE_RESTART" ]; then
    return 0
  fi
  prior=$(restarts_in_window)
  if [ "$prior" -ge "$WD_MAX_RESTARTS" ]; then
    write_hold "loop guard: HOLD written"
    return 0
  fi
  if ! pin_files_ok; then
    write_hold "pin: model path missing"
    return 0
  fi
  if ! agent_model_matches "$print_out"; then
    write_hold "pin: loaded agent model != pinned path"
    return 0
  fi
  "$WD_LAUNCHCTL" kickstart -k "gui/${uid}/${WD_LABEL}"
  rc=$?
  epoch=$(date +%s)
  printf '%s\n' "$epoch" >> "$restarts_file"
  printf '%s\n' "$epoch" > "$last_restart_file"
  write_fails 0
  log "restart kickstart -k gui/${uid}/${WD_LABEL} rc=${rc}"
}

mkdir -p "$WD_STATE_DIR"

if ! acquire_lock; then
  exit 0
fi
owned=1

if [ -e "$WD_HOLD" ]; then
  log "hold"
  exit 0
fi

uid=$(id -u)
print_out=$(mktemp "${TMPDIR:-/tmp}/qwen-wd-print.XXXXXX")
if ! "$WD_LAUNCHCTL" print "gui/${uid}/${WD_LABEL}" >"$print_out" 2>/dev/null; then
  log "agent not loaded, skip"
  write_fails 0
  exit 0
fi

if in_grace; then
  log "grace"
  exit 0
fi

if ! models_ok; then
  note_failure
  exit 0
fi

if server_recent; then
  write_fails 0
  log "ok models-only (active ${active_age}s ago)"
  exit 0
fi

if chat_ok; then
  write_fails 0
  log "ok latency=${lat}s"
  exit 0
fi

note_failure
exit 0
