#!/bin/bash
# Phase D cold AR. Boot out only the qwen watchdog, run path_a_bench.py
# cold --idle, and bootstrap that same plist again on the way out.
# Does not touch the mlx server agent or retrieval.
# bash 3.2 compatible.
set -u

LABEL="com.mailroom.qwen-watchdog"
HERE=$(cd "$(dirname "$0")" && pwd)
BENCH="${COLD_BENCH:-$HERE/path_a_bench.py}"
PY="${COLD_PYTHON:-/usr/bin/python3}"
PLIST="${COLD_PLIST:-$HOME/Library/LaunchAgents/${LABEL}.plist}"
IDLE="1800"
ASK=""
OUT=""
BASE=""
DRY=0
uid=""
STARTED=0
START_TS=0

usage() {
  cat <<'EOF'
usage: path_a_cold_ar.sh [--dry-run] [--idle SECONDS] [--ask-mail PATH] [--out PATH] [--base-url URL] [--plist PATH]

Boots out com.mailroom.qwen-watchdog only, runs path_a_bench.py cold --idle
(default 1800), and bootstraps the watchdog plist on exit.
EOF
}

refuse_offline_base() {
  if [ "${PATH_A_OFFLINE:-}" != "1" ]; then
    return 0
  fi
  if [ -z "$BASE" ]; then
    echo "error: offline run requires --base-url on a non-live port" >&2
    exit 2
  fi
  case "$BASE" in
    *://127.0.0.1:1234|*://127.0.0.1:1234/*|*://localhost:1234*|*://127.0.0.1:8743*|*://localhost:8743*|*://127.0.0.1:11434*|*://localhost:11434*)
      echo "error: refusing live port in offline test" >&2
      exit 2
      ;;
  esac
}

restore_now() {
  echo "restore bootstrap label=$LABEL"
  if launchctl bootstrap "gui/${uid}" "$PLIST"; then
    return 0
  fi
  echo "restore bootstrap failed label=$LABEL"
  return 0
}

on_exit() {
  if [ "$DRY" = "1" ] || [ "$STARTED" != "1" ]; then
    return 0
  fi
  restore_now
}
trap on_exit EXIT

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --idle)
      if [ $# -lt 2 ]; then echo "error: --idle needs a value" >&2; exit 2; fi
      IDLE="$2"; shift 2 ;;
    --ask-mail)
      if [ $# -lt 2 ]; then echo "error: --ask-mail needs a value" >&2; exit 2; fi
      ASK="$2"; shift 2 ;;
    --out)
      if [ $# -lt 2 ]; then echo "error: --out needs a value" >&2; exit 2; fi
      OUT="$2"; shift 2 ;;
    --base-url)
      if [ $# -lt 2 ]; then echo "error: --base-url needs a value" >&2; exit 2; fi
      BASE="$2"; shift 2 ;;
    --plist)
      if [ $# -lt 2 ]; then echo "error: --plist needs a value" >&2; exit 2; fi
      PLIST="$2"; shift 2 ;;
    *)
      echo "error: unknown argument $1" >&2
      exit 2
      ;;
  esac
done

case "$IDLE" in
  ''|*[!0-9.]*) echo "error: bad idle" >&2; exit 2 ;;
esac
case "$IDLE" in
  .*|*.*.*) echo "error: bad idle" >&2; exit 2 ;;
esac

base_name=$(basename "$PLIST")
if [ "$base_name" != "${LABEL}.plist" ]; then
  echo "FAIL cold reason=refusing plist basename"
  exit 1
fi

uid=$(id -u)

if [ "$DRY" = "1" ]; then
  echo "dry-run: launchctl print gui/${uid}/${LABEL}"
  echo "dry-run: launchctl bootout gui/${uid} ${PLIST}"
  printf 'dry-run: %s %s cold --idle %s' "$PY" "$BENCH" "$IDLE"
  if [ -n "$ASK" ]; then printf ' --ask-mail %s' "$ASK"; fi
  if [ -n "$OUT" ]; then printf ' --out %s' "$OUT"; fi
  if [ -n "$BASE" ]; then printf ' --base-url %s' "$BASE"; fi
  printf '\n'
  echo "dry-run: launchctl bootstrap gui/${uid} ${PLIST}"
  echo "PASS dry-run cold"
  exit 0
fi

refuse_offline_base

if [ ! -f "$PLIST" ]; then
  echo "FAIL cold reason=watchdog plist missing"
  exit 1
fi
if [ ! -f "$BENCH" ]; then
  echo "FAIL cold reason=bench missing"
  exit 1
fi

if ! launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
  echo "FAIL cold reason=watchdog not loaded"
  exit 1
fi

STARTED=1
launchctl bootout "gui/${uid}" "$PLIST" || true
if launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
  echo "FAIL cold reason=watchdog still loaded"
  exit 1
fi

args=("$PY" "$BENCH" cold --idle "$IDLE")
if [ -n "$ASK" ]; then
  args+=(--ask-mail "$ASK")
fi
if [ -n "$OUT" ]; then
  args+=(--out "$OUT")
fi
if [ -n "$BASE" ]; then
  args+=(--base-url "$BASE")
fi

START_TS=$(date +%s)
rc=0
"${args[@]}" || rc=$?
echo "cold bench_rc=$rc elapsed_s=$(( $(date +%s) - START_TS )) idle=$IDLE"
if [ "$rc" -eq 0 ]; then
  echo "PASS cold elapsed_s=$(( $(date +%s) - START_TS )) idle=$IDLE"
else
  echo "FAIL cold elapsed_s=$(( $(date +%s) - START_TS )) reason=bench_rc=$rc"
fi
exit "$rc"
