#!/bin/bash
# PR92 install for the Mac mini at one pin. One non-interactive bash run.
#   bash /tmp/pr92_install.sh install
#   bash /tmp/pr92_install.sh rollback [STAMP]
# Bash 3.2 (macOS /bin/bash). No Keychain reads, no deletes, no SoR writes,
# no daily enable, and no edits of ask_mail.py.
# Block 2 unittest can run longer than 5 minutes. The lock holder uses
# /bin/sleep 300 only as a ceiling and is killed before this script exits.

set -eu
set -o pipefail
set -o noclobber

PIN=473b59a0bc859af2fbcf1ec4649c3af49a3ceea4
OLD_BLOB=6657b881b6b0bc0e6fe3c54e2241f337e91516d8
NEW_BLOB=5bd3abcd8b0d07ee40aebb90732e407dc2224022
WRAPPER_BLOB=ff778ad56170095cc6da684a9956c195a1ec9617
PLIST_SHA12=660616d94c56
CLONE=/tmp/pr48b
BACKUP_DIR="$HOME/MailArchive/backups/pr92-473b59a0bc85"
SCRIPTS="$HOME/MailArchive/scripts"
PLIST="$HOME/Library/LaunchAgents/com.mailroom.daily.plist"
holder_pid=
verified_backup=0
STAMP=
F=
IF=
WT=

fail() {
    _step=$1
    _reason=$2
    _flag=$3
    if [ -n "${STAMP:-}" ]; then
        printf '%s\n' "STOP-${_reason}" > "/tmp/pr92-${_step}-${STAMP}.STOP" 2>/dev/null || true
        if [ "$_flag" = "F" ] && [ -n "${F:-}" ]; then
            touch "$F" 2>/dev/null || true
        fi
        if [ "$_flag" = "IF" ] && [ -n "${IF:-}" ]; then
            touch "$IF" 2>/dev/null || true
        fi
    fi
    printf '%s\n' "PR92 FAIL ${_step}: ${_reason}"
    if [ "$_flag" = "IF" ]; then
        printf '%s\n' "PR92 RUN: bash /tmp/pr92_install.sh rollback"
        printf '%s\n' "PR92 INSTALLFAIL stamp=${STAMP:-unknown}"
    fi
    printf '%s\n' "PR92 SUMMARY: FAIL ${_step} stamp=${STAMP:-unknown}"
    exit 1
}

on_fail() {
    if [ "$verified_backup" = "1" ]; then
        fail "$1" "$2" IF
    else
        fail "$1" "$2" F
    fi
}

mark() {
    _name=$1
    _token=$2
    if ! printf '%s\n' "$_token" > "/tmp/pr92-${_name}-${STAMP}.OK"; then
        on_fail "$_name" ok-write
    fi
    printf '%s\n' "$_token"
}

stop_files_present() {
    [ -n "$(find /tmp/ -maxdepth 1 -name "pr92-*-${STAMP}.STOP" -print -quit)" ]
}

kill_holder() {
    _pid=${holder_pid:-}
    holder_pid=
    if [ -z "$_pid" ]; then
        return 0
    fi
    kill -TERM -"$_pid" 2>/dev/null || kill -TERM "$_pid" 2>/dev/null || true
    wait "$_pid" 2>/dev/null || true
    return 0
}

assert_holder_dead() {
    _pidfile="/tmp/pr92-holder-${STAMP}.pid"
    if [ ! -e "$_pidfile" ]; then
        return 0
    fi
    _pid=$(awk 'NR==1{print; exit}' "$_pidfile")
    if [ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null; then
        on_fail holder-leftover still-alive
    fi
    _lock="${WT}/pr92-smoke/negative.write.lock"
    if [ -n "${WT:-}" ] && [ -e "$_lock" ]; then
        if ! /usr/bin/python3 - "$_lock" << 'PY'
import fcntl
import sys
fh = open(sys.argv[1], "a+")
try:
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(1)
fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
sys.exit(0)
PY
        then
            on_fail holder-leftover lock-held
        fi
    fi
}

require_gateok() {
    if [ -z "${STAMP:-}" ] || [ ! -e "/tmp/pr92-gate-${STAMP}.GATEOK" ]; then
        fail gateok no-gateok none
    fi
    if [ -e "$F" ]; then
        fail gateok flag-set none
    fi
    if [ -e "$IF" ]; then
        fail gateok install-flag none
    fi
}

begin_attempt() {
    STAMP=$(date +%Y%m%d-%H%M%S)
    case "$STAMP" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) fail stamp bad-format none ;;
    esac
    F="/tmp/pr92-install-473b59a0bc85-${STAMP}.STOP"
    IF="/tmp/pr92-install-473b59a0bc85-${STAMP}.INSTALLFAIL"
    WT="/tmp/pr92-gate-473b59a0bc85-${STAMP}"
    if ! printf '%s\n' "$STAMP" > "/tmp/pr92-stamp-${STAMP}.OK"; then
        fail stamp noclobber none
    fi
    if ! printf '%s\n' "$STAMP" >> /tmp/pr92-last-stamp; then
        fail stamp pointer none
    fi
    printf '%s\n' "PR92-STAMP-OK"
}

load_stamp() {
    _arg=${1:-}
    if [ -n "$_arg" ]; then
        STAMP=$_arg
    else
        if [ ! -e /tmp/pr92-last-stamp ]; then
            fail rollback missing-pointer none
        fi
        STAMP=$(awk 'END{print}' /tmp/pr92-last-stamp)
    fi
    case "$STAMP" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) fail rollback bad-stamp none ;;
    esac
    if [ ! -e "/tmp/pr92-stamp-${STAMP}.OK" ]; then
        fail rollback missing-stamp none
    fi
    _got=$(awk 'NR==1{print; exit}' "/tmp/pr92-stamp-${STAMP}.OK")
    if [ "$_got" != "$STAMP" ]; then
        fail rollback stamp-mismatch none
    fi
    F="/tmp/pr92-install-473b59a0bc85-${STAMP}.STOP"
    IF="/tmp/pr92-install-473b59a0bc85-${STAMP}.INSTALLFAIL"
    WT="/tmp/pr92-gate-473b59a0bc85-${STAMP}"
}

daily_disabled() {
    _when=$1
    _flag=$2
    set +e
    out=$(launchctl print-disabled "gui/$(id -u)" 2>&1)
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
        fail "daily-disabled-${_when}" daily-not-disabled "$_flag"
    fi
    if ! printf '%s\n' "$out" | awk '{ gsub(/^[ \t]+|[ \t]+$/, ""); if ($0 == "\"com.mailroom.daily\" => disabled" || $0 == "\"com.mailroom.daily\" => true") found=1 } END { exit found ? 0 : 1 }'; then
        fail "daily-disabled-${_when}" daily-not-disabled "$_flag"
    fi
    if [ "$_when" = "before" ]; then
        mark daily-disabled-before PR92-DAILY-DISABLED-BEFORE
    else
        mark daily-disabled-after PR92-DAILY-DISABLED-AFTER
    fi
}

daily_booted() {
    _when=$1
    _flag=$2
    set +e
    out=$(launchctl print "gui/$(id -u)/com.mailroom.daily" 2>&1)
    rc=$?
    set -e
    _hit=0
    if [ "$rc" -eq 113 ]; then
        _hit=1
    fi
    case "$out" in
        *"Could not find service"*) _hit=1 ;;
    esac
    if [ "$_hit" -eq 1 ]; then
        if [ "$_when" = "before" ]; then
            mark daily-booted-before PR92-DAILY-BOOTED-OUT-BEFORE
        else
            mark daily-booted-after PR92-DAILY-BOOTED-OUT-AFTER
        fi
        return 0
    fi
    if [ "$rc" -eq 0 ]; then
        fail "daily-booted-${_when}" daily-loaded "$_flag"
    fi
    fail "daily-booted-${_when}" daily-print "$_flag"
}

plist_before() {
    if ! shasum -a 256 "$PLIST" > "/tmp/pr92-daily-plist.${STAMP}.sha"; then
        on_fail daily-plist shasum
    fi
    _sha12=$(awk '{print substr($1,1,12)}' "/tmp/pr92-daily-plist.${STAMP}.sha")
    if [ "$_sha12" != "$PLIST_SHA12" ]; then
        on_fail daily-plist sha12
    fi
    mark daily-plist-before PR92-DAILY-PLIST-BEFORE-OK
}

plist_after() {
    _now=$(shasum -a 256 "$PLIST" | awk '{print substr($1,1,12)}') || on_fail daily-plist-after shasum
    if [ "$_now" != "$PLIST_SHA12" ]; then
        on_fail daily-plist-after sha12
    fi
    if ! shasum -a 256 "$PLIST" | cmp - "/tmp/pr92-daily-plist.${STAMP}.sha"; then
        on_fail daily-plist-after changed
    fi
    mark daily-plist-after PR92-DAILY-PLIST-AFTER-OK
}

hash_eq() {
    _path=$1
    _want=$2
    _step=$3
    _got=$(git hash-object "$_path") || on_fail "$_step" hash-object
    if [ "$_got" != "$_want" ]; then
        on_fail "$_step" blob
    fi
}

record_five() {
    _out=$1
    {
        git hash-object "$SCRIPTS/with_writer_lock.py" &&
            git hash-object "$SCRIPTS/mailroom_daily.py" &&
            git hash-object "$SCRIPTS/run_mailroom_daily.sh" &&
            git hash-object "$SCRIPTS/embed_lib.py" &&
            git hash-object "$SCRIPTS/ask_mail.py"
    } > "$_out" || on_fail five hash-object
}

block1() {
    begin_attempt
    mark noclobber PR92-NOCLOBBER-OK
    if ! git -C "$CLONE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        on_fail clone no-clone
    fi
    mark clone PR92-CLONE-OK
    if ! git -C "$CLONE" fetch origin main; then
        on_fail fetch fetch
    fi
    mark fetch PR92-FETCH-OK
    if ! git -C "$CLONE" merge-base --is-ancestor "$PIN" origin/main; then
        on_fail pin pin-not-on-main
    fi
    mark pin PR92-PIN-OK
}

block2() {
    if ! git -C "$CLONE" worktree add --detach "$WT" "$PIN"; then
        on_fail worktree worktree
    fi
    mark worktree PR92-WORKTREE-OK
    if ! ( cd "$WT" && PYTHONPATH=tests /usr/bin/python3 -m unittest discover -s tests -v ); then
        on_fail unit unittest
    fi
    mark unit PR92-UNIT-OK
    if ! ( cd "$WT" && /usr/bin/python3 scripts/rerank_smoke.py ); then
        on_fail rerank rerank
    fi
    mark rerank PR92-RERANK-OK
    unset MAILROOM_SEARCH_RESUME_RUN_ID || on_fail runid unset
    mark runid PR92-RUNID-CLEAR-OK
    if ! mkdir -p "$WT/pr92-smoke"; then
        on_fail smoke-dir mkdir
    fi
    mark smoke-dir PR92-SMOKE-DIR-OK
    if ! touch "$WT/pr92-smoke/mailroom.sqlite"; then
        on_fail smoke-db touch
    fi
    mark smoke-db PR92-SMOKE-DB-OK

    /usr/bin/python3 - "$WT" << 'PY' &
import os
import sys
os.setsid()
wt = sys.argv[1]
os.execv("/usr/bin/python3", [
    "/usr/bin/python3",
    wt + "/scripts/with_writer_lock.py",
    "--purpose", "att0-migrate",
    "--lock-file", wt + "/pr92-smoke/negative.write.lock",
    "--action-required-file", wt + "/pr92-smoke/absent-action-required",
    "--",
    "/bin/sleep",
    "300",
])
PY
    holder_pid=$!
    if ! printf '%s\n' "$holder_pid" > "/tmp/pr92-holder-${STAMP}.pid"; then
        on_fail holder pidfile
    fi
    mark holder PR92-HOLDER-OK

    if ! /usr/bin/python3 - "$WT/pr92-smoke/negative.write.lock" << 'PY'
import fcntl
import sys
import time
path = sys.argv[1]
ok = False
for _ in range(50):
    try:
        fh = open(path, "r+")
    except FileNotFoundError:
        time.sleep(0.1)
        continue
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        ok = True
        break
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    fh.close()
    time.sleep(0.1)
if ok:
    sys.stdout.write("PR92-LOCK-HELD-OK\n")
    sys.exit(0)
sys.stdout.write("STOP-lock-not-held\n")
sys.exit(1)
PY
    then
        on_fail lock lock-not-held
    fi
    mark lock PR92-LOCK-HELD-OK

    set +e
    env -u MAILROOM_WRITER_LOCK_TOKEN -u MAILROOM_WRITER_LOCK_PID -u MAILROOM_WRITER_LOCK_PURPOSE \
        /usr/bin/python3 - "$WT" << 'PY'
import sys
wt = sys.argv[1]
sys.path.insert(0, wt + "/scripts")
import sor_writer_gate as g
db = wt + "/pr92-smoke/mailroom.sqlite"
lock = wt + "/pr92-smoke/negative.write.lock"
try:
    g.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)
except g.SorWriterRefuse as exc:
    text = str(exc)
    if ("writer lock held" in text) and ("ancestor walk error" not in text):
        sys.stdout.write("PR92-NEGATIVE-OK\n")
        raise SystemExit(2)
    sys.stdout.write("STOP-negative-string\n")
    raise SystemExit(1)
sys.stdout.write("STOP-negative-allowed\n")
raise SystemExit(0)
PY
    _neg=$?
    set -e
    if [ "$_neg" -ne 2 ]; then
        on_fail negative p3-negative
    fi
    mark negative PR92-NEGATIVE-OK

    if ! /usr/bin/python3 "$WT/scripts/with_writer_lock.py" \
        --purpose att0-migrate \
        --lock-file "$WT/pr92-smoke/positive.write.lock" \
        --action-required-file "$WT/pr92-smoke/absent-action-required" \
        -- /usr/bin/env PYTHONPATH="${WT}/scripts" PYTHONDONTWRITEBYTECODE=1 \
        /usr/bin/python3 - "$WT" << 'PY'
import sys
wt = sys.argv[1]
sys.path.insert(0, wt + "/scripts")
import sor_writer_gate as g
db = wt + "/pr92-smoke/mailroom.sqlite"
lock = wt + "/pr92-smoke/positive.write.lock"
if not g.is_live_sor(db):
    sys.stdout.write("STOP-not-live\n")
    raise SystemExit(1)
try:
    g.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)
except Exception:
    sys.stdout.write("STOP-p3-positive\n")
    raise SystemExit(1)
sys.stdout.write("PR92-P3-ALLOW\n")
PY
    then
        on_fail p3 p3-positive
    fi
    mark p3 PR92-P3-OK

    kill_holder
    assert_holder_dead
    mark holder-stop PR92-HOLDER-STOPPED

    for _need in worktree unit rerank runid smoke-dir smoke-db holder lock negative p3 holder-stop; do
        if [ ! -e "/tmp/pr92-${_need}-${STAMP}.OK" ]; then
            on_fail gate "missing-${_need}"
        fi
    done
    if [ -e "$F" ] || [ -e "$IF" ]; then
        on_fail gate flag
    fi
    if stop_files_present; then
        on_fail gate stopfile
    fi
    if ! printf '%s\n' PR92-GATE-OK > "/tmp/pr92-gate-${STAMP}.GATEOK"; then
        on_fail gate gate-write
    fi
    printf '%s\n' PR92-GATE-OK
}

block3() {
    require_gateok
    mark b3-entry PR92-B3-ENTRY-OK
    require_gateok
    daily_disabled before F
    require_gateok
    daily_booted before F
    require_gateok
    plist_before
    require_gateok
    if ! mkdir -p "$BACKUP_DIR"; then
        on_fail backup-dir mkdir
    fi
    mark backup-dir PR92-BACKUP-DIR-OK
    require_gateok
    _bak="$BACKUP_DIR/sor_writer_gate.py.${STAMP}"
    if [ -e "$_bak" ]; then
        on_fail backup exists
    fi
    if ! cp -n "$SCRIPTS/sor_writer_gate.py" "$_bak"; then
        on_fail backup cp
    fi
    mark backup PR92-BACKUP-OK
    require_gateok
    hash_eq "$_bak" "$OLD_BLOB" backup-blob
    mark backup-blob PR92-BACKUP-BLOB-OK
    verified_backup=1
    require_gateok
    hash_eq "$SCRIPTS/with_writer_lock.py" "$WRAPPER_BLOB" wrapper
    mark wrapper PR92-WRAPPER-OK
    require_gateok
    record_five "/tmp/pr92-five.${STAMP}.sha"
    mark five PR92-FIVE-RECORDED
    require_gateok
    _stage="$SCRIPTS/sor_writer_gate.py.${STAMP}.stage"
    if ! git -C "$CLONE" show "${PIN}:scripts/sor_writer_gate.py" > "$_stage"; then
        on_fail stage show
    fi
    mark stage PR92-STAGED
    require_gateok
    hash_eq "$_stage" "$NEW_BLOB" stage-blob
    mark stage-blob PR92-STAGE-BLOB-OK
    require_gateok
    if ! chmod 644 "$_stage"; then
        on_fail stage-mode chmod
    fi
    mark stage-mode PR92-STAGE-MODE-OK
    require_gateok
    if [ ! -e "/tmp/pr92-stage-blob-${STAMP}.OK" ]; then
        on_fail mv stage-blob-missing
    fi
    if [ ! -e "/tmp/pr92-stage-mode-${STAMP}.OK" ]; then
        on_fail mv stage-mode-missing
    fi
    if ! mv "$_stage" "$SCRIPTS/sor_writer_gate.py"; then
        on_fail mv mv
    fi
    mark copied PR92-COPIED
    require_gateok
    hash_eq "$SCRIPTS/sor_writer_gate.py" "$NEW_BLOB" gate-blob
    mark gate-blob PR92-GATE-BLOB-OK
    require_gateok
    record_five "/tmp/pr92-five-after.${STAMP}.sha"
    if ! cmp "/tmp/pr92-five.${STAMP}.sha" "/tmp/pr92-five-after.${STAMP}.sha"; then
        on_fail unchanged cmp
    fi
    mark unchanged PR92-UNCHANGED-OK
    require_gateok
    daily_disabled after IF
    require_gateok
    daily_booted after IF
    require_gateok
    plist_after
    require_gateok
    for _need in b3-entry daily-disabled-before daily-booted-before daily-plist-before backup-dir backup backup-blob wrapper five stage stage-blob stage-mode copied gate-blob unchanged daily-disabled-after daily-booted-after daily-plist-after; do
        if [ ! -e "/tmp/pr92-${_need}-${STAMP}.OK" ]; then
            on_fail install "missing-${_need}"
        fi
    done
    if [ -e "$F" ] || [ -e "$IF" ]; then
        on_fail install flag
    fi
    if stop_files_present; then
        on_fail install stopfile
    fi
    mark install PR92-INSTALL-OK
    assert_holder_dead
    printf '%s\n' "PR92 SUMMARY: PASS stamp=${STAMP} sor_writer_gate=5bd3abcd"
}

do_install() {
    trap kill_holder EXIT INT TERM
    block1
    block2
    block3
}

do_rollback() {
    trap kill_holder EXIT INT TERM
    load_stamp "${1:-}"
    if [ -e "$F" ] || [ ! -e "$IF" ]; then
        fail rollback not-needed none
    fi
    _bak="$BACKUP_DIR/sor_writer_gate.py.${STAMP}"
    if ! cp "$_bak" "$SCRIPTS/sor_writer_gate.py"; then
        fail rollback restore none
    fi
    printf '%s\n' PR92-RESTORED
    _got=$(git hash-object "$SCRIPTS/sor_writer_gate.py") || fail rollback hash-object none
    if [ "$_got" != "$OLD_BLOB" ]; then
        fail rollback blob none
    fi
    printf '%s\n' PR92-ROLLBACK-OK
    printf '%s\n' "PR92 SUMMARY: PASS stamp=${STAMP} sor_writer_gate=6657b881"
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

case "${1:-}" in
    install) do_install ;;
    rollback) do_rollback "${2:-}" ;;
    *)
        printf '%s\n' "usage: bash /tmp/pr92_install.sh install|rollback [STAMP]"
        exit 2
        ;;
esac
