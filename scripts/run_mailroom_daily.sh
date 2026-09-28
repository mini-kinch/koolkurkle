#!/bin/zsh
# Mini daily RAG LaunchAgent entry (mac-mini.local; paths from $HOME / MAILARCHIVE).
#
# Headers IMAP uses Apple /usr/bin/curl via mailroom_daily.py.
# Body/FTS scripts pick Homebrew curl themselves (CURL_BIN unset for that step).
# Embed uses ~/MailArchive/.venv/bin/python — not Apple /usr/bin/python3.
#
# Keychain item name is not compiled into this file.
# Read MAILROOM_KEYCHAIN_ITEM, or the first non-comment line of the file
# named by MAILROOM_KEYCHAIN_CONFIG. Docs and tests say <keychain-item>.
# Never echo, log, or commit the password. There is no legacy fallback.
#
# zsh on Mac (also runs under bash 3.2+ with the same builtins).
set -eu

MAILARCHIVE="${MAILARCHIVE:-$HOME/MailArchive}"
SCRIPTS="${MAILARCHIVE_SCRIPTS:-$MAILARCHIVE/scripts}"
LOGS="${MAILARCHIVE_LOGS:-$MAILARCHIVE/logs}"
mkdir -p "$LOGS"

export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin${PATH:+:$PATH}"
export PYTHONUNBUFFERED=1
export MAILARCHIVE
export MAILARCHIVE_SCRIPTS="$SCRIPTS"
export MAILARCHIVE_LOGS="$LOGS"
export OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

# Copy-only until SoR cutover (PR-5). Refuse unset / SoR / unknown
# basenames before Keychain or IMAP. Preferred practice: explicit copy
# path so the job cannot write mailroom.sqlite (empty Mini SoR, or race
# rem embed). Use mailroom-daily-copy.sqlite when rem still holds the copy.
_db="${MAILROOM_DB:-}"
if [ -z "$_db" ]; then
  echo "error: MAILROOM_DB is unset. Set an explicit copy path (basename mailroom-copy.sqlite or mailroom-daily-copy.sqlite). Preferred practice: the Mini daily job writes only a copy until SoR cutover (PR-5). A silent default to mailroom.sqlite would write the SoR name." >&2
  echo "db_mode=refused" >&2
  exit 2
fi
_base="${_db##*/}"
_base="${_base%/}"
case "$_base" in
  mailroom-copy.sqlite|mailroom-daily-copy.sqlite)
    echo "db_mode=copy" >&2
    ;;
  *)
    echo "error: MAILROOM_DB basename '${_base}' is not on the copy allowlist (mailroom-copy.sqlite, mailroom-daily-copy.sqlite). Refusing start until SoR cutover (PR-5). No IMAP/embed." >&2
    echo "db_mode=refused" >&2
    exit 2
    ;;
esac
unset _db _base

# The Keychain read uses this interpreter so the child can be killed.
APPLE_PY="${MAILROOM_APPLE_PY:-/usr/bin/python3}"
if [ ! -x "$APPLE_PY" ]; then
  APPLE_PY="$(command -v python3 || true)"
fi
if [ -z "$APPLE_PY" ]; then
  echo "error: no python3 for mailroom_daily.py" >&2
  exit 2
fi

# Load IMAP app password from Keychain by service name only.
# The item name is not compiled in. Inherited IMAP_APP_PASSWORD is unset
# before the lookup. Do not print the value. Binary is pinned to
# /usr/bin/security. A missing pin, a missing item, or an empty password
# is a hard stop. The read dies at MAILROOM_KEYCHAIN_TIMEOUT_S (default 15).
KEYCHAIN_ITEM="${MAILROOM_KEYCHAIN_ITEM:-}"
if [ -z "$KEYCHAIN_ITEM" ] && [ -n "${MAILROOM_KEYCHAIN_CONFIG:-}" ] && [ -f "$MAILROOM_KEYCHAIN_CONFIG" ]; then
  KEYCHAIN_ITEM="$(awk 'NF && $1 !~ /^#/ { print; exit }' "$MAILROOM_KEYCHAIN_CONFIG" | tr -d '\r')"
fi
KEYCHAIN_ITEM="${KEYCHAIN_ITEM#"${KEYCHAIN_ITEM%%[![:space:]]*}"}"
KEYCHAIN_ITEM="${KEYCHAIN_ITEM%"${KEYCHAIN_ITEM##*[![:space:]]}"}"
if [ -z "$KEYCHAIN_ITEM" ]; then
  echo "error: keychain item is not pinned" >&2
  exit 4
fi
SECURITY_BIN="/usr/bin/security"
unset IMAP_APP_PASSWORD
export SECURITY_BIN KEYCHAIN_ITEM
set +e
_pw="$(
  SECURITY_BIN="$SECURITY_BIN" KEYCHAIN_ITEM="$KEYCHAIN_ITEM" "$APPLE_PY" -c '
import os, subprocess, sys
binary = os.environ["SECURITY_BIN"]
item = os.environ["KEYCHAIN_ITEM"]
raw = os.environ.get("MAILROOM_KEYCHAIN_TIMEOUT_S", "15")
try:
    seconds = float(raw)
except ValueError:
    seconds = 15.0
if seconds <= 0:
    seconds = 15.0
try:
    proc = subprocess.run(
        [binary, "find-generic-password", "-s", item, "-w"],
        capture_output=True,
        text=True,
        timeout=seconds,
    )
except subprocess.TimeoutExpired:
    sys.exit(124)
except OSError:
    sys.exit(1)
out = proc.stdout or ""
if out.endswith("\r\n"):
    out = out[:-2]
elif out.endswith("\n"):
    out = out[:-1]
if proc.returncode != 0 or out == "":
    sys.exit(1)
sys.stdout.write(out)
'
)"
_rc=$?
set -e
if [ "$_rc" -eq 124 ]; then
  unset _pw
  echo "error: imap keychain read timed out" >&2
  exit 5
fi
if [ "$_rc" -ne 0 ] || [ -z "${_pw:-}" ]; then
  unset _pw
  echo "error: keychain item is missing" >&2
  exit 4
fi
IMAP_APP_PASSWORD="$_pw"
export IMAP_APP_PASSWORD
unset _pw _rc

DAILY_PY="$SCRIPTS/mailroom_daily.py"
if [ ! -f "$DAILY_PY" ]; then
  echo "error: missing $DAILY_PY" >&2
  exit 2
fi

# Fresh stamp → mailroom_daily.py exits 0 with no output (RunAtLoad catch-up).
# Step lines go to stderr (LaunchAgent StandardErrorPath) and the dated log.
# Exclusive flock on mailroom.daily.lock is taken inside mailroom_daily.py so
# StartCalendarInterval + RunAtLoad cannot double-run.
exec "$APPLE_PY" "$DAILY_PY" --skip-if-fresh "$@"
