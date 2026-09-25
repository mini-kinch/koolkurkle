#!/bin/bash
# Phase C foreground drills for the Path A mlx_lm.server watchdog.
# bash 3.2 compatible. One subcommand per run, in the foreground.
# The EXIT trap sends kill -CONT for every pid this run stopped, removes HOLD
# when this run owns it, and kickstarts the server label if /v1/models is not 200.
# It does not call the chat session up or down scripts, and it does not edit the watchdog.
# It does not edit the server plist. KeepAlive is only read.
set -u
# kill is a shell builtin. Disable it so this script runs the kill on PATH
# (the real /bin/kill on the Mac, a test double in unit tests).
enable -n kill

DRILL_HOST="${DRILL_HOST:-127.0.0.1}"
DRILL_PORT="${DRILL_PORT:-1234}"
DRILL_LABEL="${DRILL_LABEL:-com.mailroom.mlx-lm-server}"
DRILL_WD_LABEL="${DRILL_WD_LABEL:-com.mailroom.qwen-watchdog}"
DRILL_HOLD="${DRILL_HOLD:-$HOME/qwen-mlx/HOLD}"
DRILL_LOG="${DRILL_LOG:-$HOME/MailArchive/logs/qwen-watchdog.log}"
DRILL_SERVER_PLIST="${DRILL_SERVER_PLIST:-$HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist}"
DRILL_CURL_MAX="${DRILL_CURL_MAX:-10}"

DRY=0
ASK_MAIL="${DRILL_ASK_MAIL:-}"
WAIT="${DRILL_WAIT:-}"
POLL="${DRILL_POLL:-5}"
CMD=""
uid=""
PID=""
ASK_SHA=""
BASE_FAILS=0
SEEN_RESTARTS=0
STARTED=0
CLEAR_HOLD=0
STOPPED_PIDS=""
START_TS=0
RECOVERED_BY=""
RECOVERED_PID=""

usage() {
  cat <<'EOF'
usage: path_a_drill.sh [--dry-run] [--ask-mail PATH] [--wait SECONDS] [--poll SECONDS] COMMAND

Commands (foreground only):
  port-kill       kill the listener; PASS on HTTP 200 and a new pid
                  (recovered_by=launchd-keepalive or recovered_by=watchdog)
  stop-hold       HOLD plus kill -STOP; no restart kickstart while HOLD exists,
                  then remove HOLD and require fail consecutive= and restart kickstart
  port-kill-hold  alias of stop-hold
  stop-cont       kill -STOP the listener; expect fail then kickstart; trap sends CONT
  loop3           three kill -STOP watchdog restarts, then HOLD containing "loop guard"

--wait defaults to 900 seconds (1800 for loop3). --poll defaults to 5.
KeepAlive is read with: plutil -extract KeepAlive raw "$DRILL_SERVER_PLIST"
(default $HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist).
Exit 0 is PASS, 1 is FAIL, 2 is usage.
EOF
}

elapsed() {
  echo $(( $(date +%s) - START_TS ))
}

sleep_poll() {
  if [ "$POLL" = "0" ]; then
    sleep 0.05
    return 0
  fi
  sleep "$POLL"
}

hash_file() {
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" 2>/dev/null | awk '{print $NF}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
    return 0
  fi
  sha256sum "$1" | awk '{print $1}'
}

listener_pid() {
  lsof -nP -iTCP:"$DRILL_PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 | tr -d '[:space:]'
}

models_code() {
  local code
  code=$(curl -sS -m "$DRILL_CURL_MAX" -o /dev/null -w '%{http_code}' \
    "http://${DRILL_HOST}:${DRILL_PORT}/v1/models" 2>/dev/null || true)
  code=$(printf '%s' "$code" | tr -cd '0-9')
  if [ -z "$code" ]; then
    code="000"
  fi
  printf '%s' "$code"
}

count_in_log() {
  local needle n
  needle="$1"
  if [ ! -f "$DRILL_LOG" ]; then
    printf '0'
    return 0
  fi
  n=$(grep -c -F "$needle" "$DRILL_LOG" 2>/dev/null || true)
  n=$(printf '%s' "$n" | tr -cd '0-9')
  if [ -z "$n" ]; then
    n=0
  fi
  printf '%s' "$n"
}

keepalive_value() {
  local raw
  if [ ! -f "$DRILL_SERVER_PLIST" ]; then
    printf 'unknown'
    return 0
  fi
  raw=$(plutil -extract KeepAlive raw "$DRILL_SERVER_PLIST" 2>/dev/null || true)
  raw=$(printf '%s' "$raw" | tr -d '[:space:]')
  if [ -z "$raw" ]; then
    printf 'unknown'
    return 0
  fi
  printf '%s' "$raw"
}

note_stopped() {
  STOPPED_PIDS="${STOPPED_PIDS} $1"
}

poll_line() {
  local code count now
  code="$1"
  count="$2"
  now=$(date +%s)
  echo "poll cmd=$CMD elapsed_s=$((now - START_TS)) models=$code restarts=$count"
}

restore_now() {
  local code pid
  for pid in $STOPPED_PIDS; do
    kill -CONT "$pid" >/dev/null 2>&1 || true
    echo "restore cont pid=$pid"
  done
  if [ "$CLEAR_HOLD" = "1" ]; then
    if [ -e "$DRILL_HOLD" ]; then
      rm -f "$DRILL_HOLD"
      echo "restore cleared HOLD"
    else
      echo "restore HOLD already absent"
    fi
  fi
  code=$(models_code)
  if [ "$code" != "200" ]; then
    if launchctl kickstart -k "gui/${uid}/${DRILL_LABEL}"; then
      echo "restore kickstart label=$DRILL_LABEL"
    else
      echo "restore kickstart failed label=$DRILL_LABEL"
    fi
  else
    echo "restore models=200"
  fi
}

on_exit() {
  if [ "$DRY" = "1" ] || [ "$STARTED" != "1" ]; then
    return 0
  fi
  restore_now
}
trap on_exit EXIT

fail() {
  echo "FAIL $CMD elapsed_s=$(elapsed) reason=$1"
  exit 1
}

succeed() {
  local end_sha
  if [ -n "$ASK_MAIL" ]; then
    end_sha=$(hash_file "$ASK_MAIL")
    if [ "$end_sha" != "$ASK_SHA" ]; then
      echo "FAIL $CMD elapsed_s=$(elapsed) reason=ask_mail sha changed"
      exit 1
    fi
  fi
  echo "PASS $CMD elapsed_s=$(elapsed) $*"
  exit 0
}

preflight() {
  local code ps_line keepalive
  if [ -n "$ASK_MAIL" ]; then
    if [ ! -f "$ASK_MAIL" ]; then
      fail "ask_mail missing"
    fi
    ASK_SHA=$(hash_file "$ASK_MAIL")
    echo "preflight ask_mail sha256=$ASK_SHA"
  fi
  keepalive=$(keepalive_value)
  echo "preflight keepalive=$keepalive"
  if ! launchctl print "gui/${uid}/${DRILL_LABEL}" >/dev/null 2>&1; then
    fail "label not loaded label=$DRILL_LABEL"
  fi
  if ! launchctl print "gui/${uid}/${DRILL_WD_LABEL}" >/dev/null 2>&1; then
    fail "label not loaded label=$DRILL_WD_LABEL"
  fi
  echo "preflight label loaded $DRILL_LABEL"
  echo "preflight label loaded $DRILL_WD_LABEL"
  PID=$(listener_pid || true)
  if [ -z "$PID" ]; then
    fail "no listener"
  fi
  ps_line=$(ps -p "$PID" -o pid=,command= 2>/dev/null | head -n 1 || true)
  echo "preflight ps=${ps_line:-unavailable}"
  echo "preflight pid=$PID"
  code=$(models_code)
  if [ "$code" != "200" ]; then
    fail "models not up http=$code"
  fi
  if [ -e "$DRILL_HOLD" ]; then
    fail "HOLD already present"
  fi
  SEEN_RESTARTS=$(count_in_log "restart kickstart")
  BASE_FAILS=$(count_in_log "fail consecutive=")
  echo "preflight models=200 restarts=$SEEN_RESTARTS"
}

wait_for_restart() {
  local start now count code
  start=$(date +%s)
  while true; do
    count=$(count_in_log "restart kickstart")
    code=$(models_code)
    now=$(date +%s)
    poll_line "$code" "$count"
    if [ "$count" -gt "$SEEN_RESTARTS" ] && [ "$code" = "200" ]; then
      SEEN_RESTARTS=$count
      return 0
    fi
    if [ $((now - start)) -ge "$WAIT" ]; then
      return 1
    fi
    sleep_poll
  done
}

wait_for_port_recovery() {
  local before start now count code after
  before="$1"
  start=$(date +%s)
  while true; do
    code=$(models_code)
    after=$(listener_pid || true)
    count=$(count_in_log "restart kickstart")
    now=$(date +%s)
    poll_line "$code" "$count"
    if [ -n "$after" ] && [ "$after" != "$before" ] && [ "$code" = "200" ]; then
      RECOVERED_PID=$after
      if [ "$count" -gt "$SEEN_RESTARTS" ]; then
        SEEN_RESTARTS=$count
        RECOVERED_BY=watchdog
      else
        RECOVERED_BY=launchd-keepalive
      fi
      return 0
    fi
    if [ $((now - start)) -ge "$WAIT" ]; then
      return 1
    fi
    sleep_poll
  done
}

wait_no_restart() {
  local start now count
  start=$(date +%s)
  while true; do
    count=$(count_in_log "restart kickstart")
    now=$(date +%s)
    poll_line "hold" "$count"
    if [ "$count" -gt "$SEEN_RESTARTS" ]; then
      return 1
    fi
    if [ $((now - start)) -ge "$WAIT" ]; then
      return 0
    fi
    sleep_poll
  done
}

wait_for_stop() {
  local start now count fails code
  start=$(date +%s)
  while true; do
    count=$(count_in_log "restart kickstart")
    fails=$(count_in_log "fail consecutive=")
    code=$(models_code)
    now=$(date +%s)
    poll_line "$code" "$count"
    if [ "$fails" -gt "$BASE_FAILS" ] && [ "$count" -gt "$SEEN_RESTARTS" ] && [ "$code" = "200" ]; then
      SEEN_RESTARTS=$count
      return 0
    fi
    if [ $((now - start)) -ge "$WAIT" ]; then
      return 1
    fi
    sleep_poll
  done
}

wait_for_loop_guard() {
  local start now count line
  start=$(date +%s)
  while true; do
    count=$(count_in_log "restart kickstart")
    now=$(date +%s)
    poll_line "guard" "$count"
    if [ "$count" -gt "$SEEN_RESTARTS" ]; then
      return 2
    fi
    if [ -f "$DRILL_HOLD" ] && grep -q -F "loop guard" "$DRILL_HOLD"; then
      IFS= read -r line < "$DRILL_HOLD" || line=""
      echo "loop3 hold_text=$line"
      return 0
    fi
    if [ $((now - start)) -ge "$WAIT" ]; then
      return 1
    fi
    sleep_poll
  done
}

do_port_kill() {
  local before after
  preflight
  before=$PID
  STARTED=1
  echo "action kill pid=$before"
  kill "$before" || true
  if ! wait_for_port_recovery "$before"; then
    fail "port did not recover"
  fi
  after=$RECOVERED_PID
  if [ -z "$after" ] || [ "$after" = "$before" ]; then
    fail "listener pid unchanged"
  fi
  succeed "pid_before=$before pid_after=$after recovered_by=$RECOVERED_BY"
}

do_stop_hold() {
  local before after hold_start recover_start hold_s recover_s
  preflight
  before=$PID
  STARTED=1
  CLEAR_HOLD=1
  note_stopped "$before"
  mkdir -p "$(dirname "$DRILL_HOLD")"
  touch "$DRILL_HOLD"
  echo "action touch HOLD"
  echo "action kill -STOP pid=$before"
  kill -STOP "$before" || true
  hold_start=$(date +%s)
  if ! wait_no_restart; then
    fail "restart while HOLD set"
  fi
  hold_s=$(( $(date +%s) - hold_start ))
  rm -f "$DRILL_HOLD"
  CLEAR_HOLD=0
  echo "action rm HOLD"
  recover_start=$(date +%s)
  if ! wait_for_stop; then
    fail "no probe failure then kickstart"
  fi
  recover_s=$(( $(date +%s) - recover_start ))
  after=$(listener_pid || true)
  if [ -z "$after" ] || [ "$after" = "$before" ]; then
    fail "listener pid unchanged"
  fi
  succeed "pid_before=$before pid_after=$after hold_elapsed_s=$hold_s recovery_elapsed_s=$recover_s"
}

do_stop_cont() {
  local before after
  preflight
  before=$PID
  STARTED=1
  note_stopped "$before"
  echo "action kill -STOP pid=$before"
  kill -STOP "$before" || true
  if ! wait_for_stop; then
    fail "no probe failure then kickstart"
  fi
  after=$(listener_pid || true)
  if [ -z "$after" ] || [ "$after" = "$before" ]; then
    fail "listener pid unchanged"
  fi
  succeed "pid_before=$before pid_after=$after"
}

do_loop3() {
  local cycle before got
  preflight
  STARTED=1
  CLEAR_HOLD=1
  cycle=1
  while [ "$cycle" -le 3 ]; do
    before=$PID
    note_stopped "$before"
    echo "action kill -STOP pid=$before cycle=$cycle"
    kill -STOP "$before" || true
    if ! wait_for_restart; then
      fail "restart $cycle missing"
    fi
    PID=$(listener_pid || true)
    if [ -z "$PID" ] || [ "$PID" = "$before" ]; then
      fail "listener pid unchanged cycle=$cycle"
    fi
    echo "loop3 cycle=$cycle pid_before=$before pid_after=$PID"
    cycle=$((cycle + 1))
  done
  before=$PID
  note_stopped "$before"
  echo "action kill -STOP pid=$before cycle=guard"
  kill -STOP "$before" || true
  wait_for_loop_guard
  got=$?
  if [ "$got" -eq 2 ]; then
    fail "restart during loop guard"
  fi
  if [ "$got" -ne 0 ]; then
    fail "loop guard HOLD missing"
  fi
  succeed "restarts_seen=$SEEN_RESTARTS hold=loop-guard"
}

dry_run_plan() {
  if [ -n "$ASK_MAIL" ]; then
    echo "dry-run: hash ask_mail"
  fi
  echo "dry-run: plutil -extract KeepAlive raw ${DRILL_SERVER_PLIST}"
  echo "dry-run: launchctl print gui/${uid}/${DRILL_LABEL}"
  echo "dry-run: launchctl print gui/${uid}/${DRILL_WD_LABEL}"
  echo "dry-run: lsof -nP -iTCP:${DRILL_PORT} -sTCP:LISTEN -t"
  echo "dry-run: ps -p <pid> -o pid=,command="
  echo "dry-run: curl -sS -m ${DRILL_CURL_MAX} -o /dev/null -w %{http_code} http://${DRILL_HOST}:${DRILL_PORT}/v1/models"
  case "$CMD" in
    port-kill)
      echo "dry-run: kill <pid>"
      echo "dry-run: poll /v1/models until 200 and a new listener pid (wait ${WAIT}s)"
      echo "dry-run: recovered_by=watchdog if a new restart kickstart line, else recovered_by=launchd-keepalive"
      ;;
    stop-hold)
      echo "dry-run: touch ${DRILL_HOLD}"
      echo "dry-run: kill -STOP <pid>"
      echo "dry-run: poll ${WAIT}s for no restart kickstart line"
      echo "dry-run: rm -f ${DRILL_HOLD}"
      echo "dry-run: poll watchdog log for fail consecutive= then restart kickstart and /v1/models 200 (wait ${WAIT}s)"
      echo "dry-run: kill -CONT <pid>"
      ;;
    stop-cont)
      echo "dry-run: kill -STOP <pid>"
      echo "dry-run: poll watchdog log for fail consecutive= then restart kickstart and /v1/models 200 (wait ${WAIT}s)"
      echo "dry-run: kill -CONT <pid>"
      ;;
    loop3)
      echo "dry-run: kill -STOP <pid>"
      echo "dry-run: poll restart kickstart and /v1/models 200 (wait ${WAIT}s)"
      echo "dry-run: kill -STOP <pid>"
      echo "dry-run: poll restart kickstart and /v1/models 200 (wait ${WAIT}s)"
      echo "dry-run: kill -STOP <pid>"
      echo "dry-run: poll restart kickstart and /v1/models 200 (wait ${WAIT}s)"
      echo "dry-run: kill -STOP <pid>"
      echo "dry-run: poll HOLD for loop guard and no further restart kickstart (wait ${WAIT}s)"
      echo "dry-run: kill -CONT every stopped pid"
      echo "dry-run: rm -f ${DRILL_HOLD}"
      ;;
  esac
  echo "dry-run: launchctl kickstart -k gui/${uid}/${DRILL_LABEL}  # only if /v1/models is not 200"
}

is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
  return 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --ask-mail)
      if [ $# -lt 2 ]; then
        echo "error: --ask-mail needs a value" >&2
        exit 2
      fi
      ASK_MAIL="$2"
      shift 2
      ;;
    --wait)
      if [ $# -lt 2 ]; then
        echo "error: --wait needs a value" >&2
        exit 2
      fi
      WAIT="$2"
      shift 2
      ;;
    --poll)
      if [ $# -lt 2 ]; then
        echo "error: --poll needs a value" >&2
        exit 2
      fi
      POLL="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "error: unknown flag $1" >&2
      exit 2
      ;;
    *)
      if [ -n "$CMD" ]; then
        echo "error: extra argument $1" >&2
        exit 2
      fi
      CMD="$1"
      shift
      ;;
  esac
done

case "$CMD" in
  port-kill|port-kill-hold|stop-hold|stop-cont|loop3) ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if [ -z "$WAIT" ]; then
  if [ "$CMD" = "loop3" ]; then
    WAIT=1800
  else
    WAIT=900
  fi
fi

if ! is_uint "$WAIT"; then
  echo "error: bad wait" >&2
  exit 2
fi
if ! is_uint "$POLL"; then
  echo "error: bad poll" >&2
  exit 2
fi
if ! is_uint "$DRILL_PORT"; then
  echo "error: bad port" >&2
  exit 2
fi

if [ "${PATH_A_OFFLINE:-}" = "1" ]; then
  case "$DRILL_PORT" in
    1234|8743|11434)
      echo "error: refusing live port ${DRILL_PORT}" >&2
      exit 2
      ;;
  esac
fi

uid=$(id -u)
START_TS=$(date +%s)

if [ "$CMD" = "port-kill-hold" ]; then
  echo "port-kill-hold alias of stop-hold: kill -STOP under HOLD (no restart kickstart while HOLD exists), then remove HOLD and require fail consecutive= and restart kickstart"
  CMD="stop-hold"
fi

echo "foreground cmd=$CMD wait_s=$WAIT poll_s=$POLL"

if [ "$DRY" = "1" ]; then
  dry_run_plan
  echo "PASS dry-run $CMD elapsed_s=$(elapsed)"
  exit 0
fi

case "$CMD" in
  port-kill) do_port_kill ;;
  stop-hold) do_stop_hold ;;
  stop-cont) do_stop_cont ;;
  loop3) do_loop3 ;;
esac
