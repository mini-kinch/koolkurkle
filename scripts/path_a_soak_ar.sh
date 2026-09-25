#!/bin/bash
# Phase E soak AR. Starts a transient launchd job (no plist file).
# The watchdog stays loaded. Remove the job with the remove subcommand.
# bash 3.2 compatible.
set -u

LABEL="com.mailroom.path-a-soak"
HERE=$(cd "$(dirname "$0")" && pwd)
BENCH="${SOAK_BENCH:-$HERE/path_a_bench.py}"
PY="${SOAK_PYTHON:-/usr/bin/python3}"
LOG="${SOAK_LOG:-$HOME/MailArchive/logs/path-a-soak.log}"
ERRLOG="${SOAK_ERR:-}"
JSONL="${SOAK_OUT:-$HOME/MailArchive/logs/path-a-soak.jsonl}"
WD_LOG="${SOAK_WATCHDOG_LOG:-$HOME/MailArchive/logs/qwen-watchdog.log}"
N="48"
INTERVAL="300"
MEM_AT="24"
ASK=""
BASE=""
DRY=0
CMD=""

usage() {
  cat <<'EOF'
usage: path_a_soak_ar.sh [--dry-run] [--n N] [--interval SECONDS] [--mem-at K]
                         [--ask-mail PATH] [--out PATH] [--log PATH] [--watchdog-log PATH]
                         [--base-url URL] COMMAND

Commands:
  start    launchctl submit -l com.mailroom.path-a-soak (default 48 x 300s)
  status   launchctl print gui/<uid>/com.mailroom.path-a-soak
  remove   launchctl remove com.mailroom.path-a-soak

The watchdog is left loaded. This script does not write a plist.
EOF
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
    --n)
      if [ $# -lt 2 ]; then echo "error: --n needs a value" >&2; exit 2; fi
      N="$2"; shift 2 ;;
    --interval)
      if [ $# -lt 2 ]; then echo "error: --interval needs a value" >&2; exit 2; fi
      INTERVAL="$2"; shift 2 ;;
    --mem-at)
      if [ $# -lt 2 ]; then echo "error: --mem-at needs a value" >&2; exit 2; fi
      MEM_AT="$2"; shift 2 ;;
    --ask-mail)
      if [ $# -lt 2 ]; then echo "error: --ask-mail needs a value" >&2; exit 2; fi
      ASK="$2"; shift 2 ;;
    --out)
      if [ $# -lt 2 ]; then echo "error: --out needs a value" >&2; exit 2; fi
      JSONL="$2"; shift 2 ;;
    --log)
      if [ $# -lt 2 ]; then echo "error: --log needs a value" >&2; exit 2; fi
      LOG="$2"; shift 2 ;;
    --watchdog-log)
      if [ $# -lt 2 ]; then echo "error: --watchdog-log needs a value" >&2; exit 2; fi
      WD_LOG="$2"; shift 2 ;;
    --base-url)
      if [ $# -lt 2 ]; then echo "error: --base-url needs a value" >&2; exit 2; fi
      BASE="$2"; shift 2 ;;
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
  start|status|remove) ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if ! is_uint "$N" || [ "$N" -lt 1 ]; then
  echo "error: bad n" >&2
  exit 2
fi
if ! is_uint "$INTERVAL"; then
  echo "error: bad interval" >&2
  exit 2
fi
if ! is_uint "$MEM_AT" || [ "$MEM_AT" -lt 1 ]; then
  echo "error: bad mem-at" >&2
  exit 2
fi
if [ "$MEM_AT" -gt "$N" ]; then
  echo "error: mem-at greater than n" >&2
  exit 2
fi

if [ -z "$ERRLOG" ]; then
  ERRLOG="$LOG"
fi

uid=$(id -u)

build_submit() {
  SUBMIT_ARGS=(
    submit
    -l "$LABEL"
    -o "$LOG"
    -e "$ERRLOG"
    --
    "$PY"
    "$BENCH"
    soak
    --continue-on-hang
    --mem-at "$MEM_AT"
    --watchdog-log "$WD_LOG"
    -n "$N"
    --interval "$INTERVAL"
    --out "$JSONL"
  )
  if [ -n "$ASK" ]; then
    SUBMIT_ARGS+=(--ask-mail "$ASK")
  fi
  if [ -n "$BASE" ]; then
    SUBMIT_ARGS+=(--base-url "$BASE")
  fi
}

print_submit() {
  local s
  printf 'dry-run: launchctl'
  for s in "${SUBMIT_ARGS[@]}"; do
    printf ' %s' "$s"
  done
  printf '\n'
}

case "$CMD" in
  start)
    build_submit
    if [ "$DRY" = "1" ]; then
      print_submit
      echo "PASS dry-run soak-start label=$LABEL n=$N interval_s=$INTERVAL mem_at=$MEM_AT"
      exit 0
    fi
    if [ ! -f "$BENCH" ]; then
      echo "FAIL soak-start reason=bench missing"
      exit 1
    fi
    mkdir -p "$(dirname "$LOG")" "$(dirname "$JSONL")"
    if launchctl "${SUBMIT_ARGS[@]}"; then
      echo "PASS soak-start label=$LABEL n=$N interval_s=$INTERVAL mem_at=$MEM_AT"
      echo "watchdog=left-loaded"
      exit 0
    fi
    echo "FAIL soak-start reason=submit failed label=$LABEL"
    exit 1
    ;;
  status)
    if [ "$DRY" = "1" ]; then
      echo "dry-run: launchctl print gui/${uid}/${LABEL}"
      echo "PASS dry-run soak-status label=$LABEL"
      exit 0
    fi
    if launchctl print "gui/${uid}/${LABEL}"; then
      echo "PASS status label=$LABEL"
      exit 0
    fi
    echo "FAIL status reason=not loaded label=$LABEL"
    exit 1
    ;;
  remove)
    if [ "$DRY" = "1" ]; then
      echo "dry-run: launchctl remove ${LABEL}"
      echo "PASS dry-run soak-remove label=$LABEL"
      exit 0
    fi
    if launchctl remove "$LABEL"; then
      echo "PASS remove label=$LABEL"
      exit 0
    fi
    echo "FAIL remove reason=launchctl remove failed label=$LABEL"
    exit 1
    ;;
esac
