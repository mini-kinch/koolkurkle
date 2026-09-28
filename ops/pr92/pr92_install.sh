#!/bin/bash
# PR92 install for the Mac mini at one pin.
# Authority: at 23:27 PT the user lifted the Mini hold for Developer to run
# this #92 install only.
#
# Invoke with /bin/bash (macOS /bin/bash 3.2). Do not rely on PATH or the
# shebang alone.
#   /bin/bash /tmp/pr92_install.sh install
#   /bin/bash /tmp/pr92_install.sh rollback STAMP
#
# Exit codes:
#   0  install verified, or rollback restored and re-verified, or rollback skipped
#   1  stopped before the mv; the live gate was not replaced
#   2  usage (missing mode, or rollback without STAMP)
#   3  failure after the mv; the output says whether rollback was done or is needed
#
# Install prints "PR92 STAMP=<STAMP>" as its first output line. A timeout or
# SIGKILL skips traps, so rollback does not look at an install-fail flag. It
# rolls back only when the live sor_writer_gate.py blob is not
# 6657b881b6b0bc0e6fe3c54e2241f337e91516d8, /tmp/pr92-install-<STAMP>.OK is
# absent, and the backup for that STAMP exists and hashes to that old blob.
# Otherwise it prints ROLLBACK-SKIPPED and exits 0.
#
# P3 positive ancestor chain when Developer runs this: the smoke python, then
# with_writer_lock.py, then /bin/bash this script, then the operator shell,
# then launchd pid 1. ALLOW is the detail "writer lock held by wrapper" from
# the ppid walk. An exception is a STOP, not an allow.
#
# Block 2 unittest can run longer than 5 minutes. The holder uses /bin/sleep
# 300 only as a ceiling and is killed on EXIT, INT, and TERM.
# No Keychain reads, no file removal, no SoR writes, no daily enable, and
# ask_mail.py is only hashed.

set -u
set -o pipefail
set -C

PIN=473b59a0bc859af2fbcf1ec4649c3af49a3ceea4
OLD_BLOB=6657b881b6b0bc0e6fe3c54e2241f337e91516d8
NEW_BLOB=5bd3abcd8b0d07ee40aebb90732e407dc2224022
WRAPPER_BLOB=ff778ad56170095cc6da684a9956c195a1ec9617
PLIST_SHA12=660616d94c56
CLONE=/tmp/pr48b
BACKUP_DIR=
SCRIPTS=
PLIST=
PYTHON=/usr/bin/python3

holder_pid=
STAMP=
WT=
MOVED=0
N=0
ROLL_WHY=
GIT=
LAUNCHCTL=
SHASUM=
FIND=
CMP=
AWK=
DATE=
ID=
CP=
CHMOD=
MV=
MKDIR=
TOUCH=
ENVBIN=

die_tool() {
    printf '%s\n' "PR92 FAIL tools: $1"
    printf '%s\n' "PR92 SUMMARY stamp=unknown exit=1"
    exit 1
}

load_tools() {
    if [ -z "${HOME:-}" ]; then
        die_tool HOME
    fi
    BACKUP_DIR="$HOME/MailArchive/backups/pr92-473b59a0bc85"
    SCRIPTS="$HOME/MailArchive/scripts"
    PLIST="$HOME/Library/LaunchAgents/com.mailroom.daily.plist"
    if [ ! -x "$PYTHON" ]; then
        die_tool python3
    fi
    GIT=$(command -v git 2>/dev/null || true)
    LAUNCHCTL=$(command -v launchctl 2>/dev/null || true)
    SHASUM=$(command -v shasum 2>/dev/null || true)
    FIND=$(command -v find 2>/dev/null || true)
    CMP=$(command -v cmp 2>/dev/null || true)
    AWK=$(command -v awk 2>/dev/null || true)
    DATE=$(command -v date 2>/dev/null || true)
    ID=$(command -v id 2>/dev/null || true)
    CP=$(command -v cp 2>/dev/null || true)
    CHMOD=$(command -v chmod 2>/dev/null || true)
    MV=$(command -v mv 2>/dev/null || true)
    MKDIR=$(command -v mkdir 2>/dev/null || true)
    TOUCH=$(command -v touch 2>/dev/null || true)
    ENVBIN=$(command -v env 2>/dev/null || true)
    for _pair in \
        "git:$GIT" \
        "launchctl:$LAUNCHCTL" \
        "shasum:$SHASUM" \
        "find:$FIND" \
        "cmp:$CMP" \
        "awk:$AWK" \
        "date:$DATE" \
        "id:$ID" \
        "cp:$CP" \
        "chmod:$CHMOD" \
        "mv:$MV" \
        "mkdir:$MKDIR" \
        "touch:$TOUCH" \
        "env:$ENVBIN"
    do
        _name=${_pair%%:*}
        _path=${_pair#*:}
        case "$_path" in
            /*) ;;
            *) die_tool "$_name" ;;
        esac
    done
    if ! cd /tmp; then
        die_tool cd
    fi
}

step() {
    N=$((N + 1))
    printf '%s\n' "PR92 ${N} $1"
}

fail() {
    _step=$1
    _reason=$2
    if [ -n "${STAMP:-}" ]; then
        printf '%s\n' "STOP-${_reason}" > "/tmp/pr92-${_step}-${STAMP}.STOP" 2>/dev/null || true
    fi
    printf '%s\n' "PR92 FAIL ${_step}: ${_reason}"
    if [ "${MOVED:-0}" = 1 ]; then
        post_mv_rollback
    fi
    printf '%s\n' "PR92 SUMMARY stamp=${STAMP:-unknown} exit=1"
    exit 1
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
    _pid=$("$AWK" 'NR==1{print; exit}' "$_pidfile" 2>/dev/null || true)
    if [ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null; then
        fail holder-leftover still-alive
    fi
    _lock="${WT}/pr92-smoke/negative.write.lock"
    if [ -n "${WT:-}" ] && [ -e "$_lock" ]; then
        if ! "$PYTHON" - "$_lock" << 'PY'
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
            fail holder-leftover lock-held
        fi
    fi
}

mark() {
    _name=$1
    _token=$2
    if ! printf '%s\n' "$_token" > "/tmp/pr92-${_name}-${STAMP}.OK"; then
        fail "$_name" ok-write
    fi
    step "$_token"
}

stop_files_present() {
    _hit=$("$FIND" /tmp/ -maxdepth 1 -name "pr92-*-${STAMP}.STOP" -print -quit 2>/dev/null) || fail find find
    if [ -n "$_hit" ]; then
        return 0
    fi
    return 1
}

require_gateok() {
    if [ -z "${STAMP:-}" ] || [ ! -e "/tmp/pr92-gate-${STAMP}.GATEOK" ]; then
        fail gateok no-gateok
    fi
}

rollback_ready() {
    _live="$SCRIPTS/sor_writer_gate.py"
    _bak="$BACKUP_DIR/sor_writer_gate.py.${STAMP}"
    _okf="/tmp/pr92-install-${STAMP}.OK"
    ROLL_WHY=
    _live_hash=$("$GIT" hash-object "$_live" 2>/dev/null || true)
    if [ "$_live_hash" = "$OLD_BLOB" ]; then
        ROLL_WHY=live-blob-is-6657b881
        return 1
    fi
    if [ -e "$_okf" ]; then
        ROLL_WHY=install-ok-present
        return 1
    fi
    if [ ! -e "$_bak" ]; then
        ROLL_WHY=backup-missing
        return 1
    fi
    _bak_hash=$("$GIT" hash-object "$_bak" 2>/dev/null || true)
    if [ "$_bak_hash" != "$OLD_BLOB" ]; then
        ROLL_WHY=backup-hash
        return 1
    fi
    return 0
}

restore_backup() {
    _bak="$BACKUP_DIR/sor_writer_gate.py.${STAMP}"
    _live="$SCRIPTS/sor_writer_gate.py"
    if ! "$CP" "$_bak" "$_live"; then
        return 1
    fi
    _now=$("$GIT" hash-object "$_live" 2>/dev/null || true)
    if [ "$_now" != "$OLD_BLOB" ]; then
        return 1
    fi
    printf '%s\n' PR92-ROLLBACK-BLOB-OK
    return 0
}

post_mv_rollback() {
    if rollback_ready && restore_backup; then
        printf '%s\n' "PR92 ROLLBACK-DONE stamp=${STAMP}"
        printf '%s\n' "PR92 post-mv failure: rollback was done"
        printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=3"
        exit 3
    fi
    if [ -z "${ROLL_WHY:-}" ]; then
        ROLL_WHY=restore-failed
    fi
    printf '%s\n' "PR92 ROLLBACK-SKIPPED ${ROLL_WHY}"
    printf '%s\n' "PR92 post-mv failure: rollback is needed"
    printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=3"
    exit 3
}

do_rollback() {
    STAMP=$1
    case "$STAMP" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *)
            printf '%s\n' "PR92 ROLLBACK-SKIPPED bad-stamp"
            printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=0"
            exit 0
            ;;
    esac
    if rollback_ready; then
        if restore_backup; then
            printf '%s\n' "PR92 ROLLBACK-DONE stamp=${STAMP}"
            printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=0"
            exit 0
        fi
        printf '%s\n' "PR92 post-mv failure: rollback is needed"
        printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=3"
        exit 3
    fi
    printf '%s\n' "PR92 ROLLBACK-SKIPPED ${ROLL_WHY}"
    printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=0"
    exit 0
}

begin_attempt() {
    STAMP=$("$DATE" +%Y%m%d-%H%M%S) || fail stamp date
    case "$STAMP" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) fail stamp bad-format ;;
    esac
    printf '%s\n' "PR92 STAMP=${STAMP}"
    WT="/tmp/pr92-gate-473b59a0bc85-${STAMP}"
    if ! printf '%s\n' "$STAMP" > "/tmp/pr92-stamp-${STAMP}.OK"; then
        fail stamp noclobber
    fi
    if ! printf '%s\n' "$STAMP" >> /tmp/pr92-last-stamp; then
        fail stamp pointer
    fi
    step PR92-STAMP-OK
}

daily_disabled() {
    _when=$1
    out=$("$LAUNCHCTL" print-disabled "gui/$("$ID" -u)" 2>&1) || fail "daily-disabled-${_when}" daily-not-disabled
    if ! printf '%s\n' "$out" | "$AWK" '{ gsub(/^[ \t]+|[ \t]+$/, ""); if ($0 == "\"com.mailroom.daily\" => disabled" || $0 == "\"com.mailroom.daily\" => true") found=1 } END { exit found ? 0 : 1 }'; then
        fail "daily-disabled-${_when}" daily-not-disabled
    fi
    if [ "$_when" = "before" ]; then
        mark daily-disabled-before PR92-DAILY-DISABLED-BEFORE
    else
        mark daily-disabled-after PR92-DAILY-DISABLED-AFTER
    fi
}

daily_booted() {
    _when=$1
    out=$("$LAUNCHCTL" print "gui/$("$ID" -u)/com.mailroom.daily" 2>&1)
    rc=$?
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
        fail "daily-booted-${_when}" daily-loaded
    fi
    fail "daily-booted-${_when}" daily-print
}

plist_before() {
    if ! "$SHASUM" -a 256 "$PLIST" > "/tmp/pr92-daily-plist.${STAMP}.sha"; then
        fail daily-plist shasum
    fi
    _sha12=$("$AWK" '{print substr($1,1,12)}' "/tmp/pr92-daily-plist.${STAMP}.sha") || fail daily-plist awk
    if [ "$_sha12" != "$PLIST_SHA12" ]; then
        fail daily-plist sha12
    fi
    mark daily-plist-before PR92-DAILY-PLIST-BEFORE-OK
}

plist_after() {
    _now=$("$SHASUM" -a 256 "$PLIST" | "$AWK" '{print substr($1,1,12)}') || fail daily-plist-after shasum
    if [ "$_now" != "$PLIST_SHA12" ]; then
        fail daily-plist-after sha12
    fi
    if ! "$SHASUM" -a 256 "$PLIST" | "$CMP" - "/tmp/pr92-daily-plist.${STAMP}.sha"; then
        fail daily-plist-after changed
    fi
    mark daily-plist-after PR92-DAILY-PLIST-AFTER-OK
}

hash_eq() {
    _path=$1
    _want=$2
    _step=$3
    _got=$("$GIT" hash-object "$_path" 2>/dev/null || true)
    if [ -z "$_got" ]; then
        fail "$_step" hash-object
    fi
    if [ "$_got" != "$_want" ]; then
        fail "$_step" blob
    fi
}

record_five() {
    _out=$1
    if ! {
        "$GIT" hash-object "$SCRIPTS/with_writer_lock.py" &&
            "$GIT" hash-object "$SCRIPTS/mailroom_daily.py" &&
            "$GIT" hash-object "$SCRIPTS/run_mailroom_daily.sh" &&
            "$GIT" hash-object "$SCRIPTS/embed_lib.py" &&
            "$GIT" hash-object "$SCRIPTS/ask_mail.py"
    } > "$_out"; then
        fail five hash-object
    fi
}

block1() {
    begin_attempt
    mark noclobber PR92-NOCLOBBER-OK
    if ! "$GIT" -C "$CLONE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail clone no-clone
    fi
    mark clone PR92-CLONE-OK
    if ! "$GIT" -C "$CLONE" fetch origin main; then
        fail fetch fetch
    fi
    mark fetch PR92-FETCH-OK
    if ! "$GIT" -C "$CLONE" merge-base --is-ancestor "$PIN" origin/main; then
        fail pin pin-not-on-main
    fi
    mark pin PR92-PIN-OK
}

block2() {
    if ! "$GIT" -C "$CLONE" worktree add --detach "$WT" "$PIN"; then
        fail worktree worktree
    fi
    mark worktree PR92-WORKTREE-OK
    if ! ( cd "$WT" && PYTHONPATH=tests "$PYTHON" -m unittest discover -s tests ); then
        fail unit unittest
    fi
    mark unit PR92-UNIT-OK
    if ! ( cd "$WT" && "$PYTHON" scripts/rerank_smoke.py ); then
        fail rerank rerank
    fi
    mark rerank PR92-RERANK-OK
    if ! unset MAILROOM_SEARCH_RESUME_RUN_ID; then
        fail runid unset
    fi
    mark runid PR92-RUNID-CLEAR-OK
    if ! "$MKDIR" -p "$WT/pr92-smoke"; then
        fail smoke-dir mkdir
    fi
    mark smoke-dir PR92-SMOKE-DIR-OK
    if ! "$TOUCH" "$WT/pr92-smoke/mailroom.sqlite"; then
        fail smoke-db touch
    fi
    mark smoke-db PR92-SMOKE-DB-OK

    "$PYTHON" - "$WT" << 'PY' &
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
        fail holder pidfile
    fi
    mark holder PR92-HOLDER-OK

    if ! "$PYTHON" - "$WT/pr92-smoke/negative.write.lock" << 'PY'
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
        fail lock lock-not-held
    fi
    mark lock PR92-LOCK-HELD-OK

    "$ENVBIN" -u MAILROOM_WRITER_LOCK_TOKEN -u MAILROOM_WRITER_LOCK_PID -u MAILROOM_WRITER_LOCK_PURPOSE \
        "$PYTHON" - "$WT" << 'PY'
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
    if [ "$_neg" -ne 2 ]; then
        fail negative p3-negative
    fi
    mark negative PR92-NEGATIVE-OK

    if ! unset MAILROOM_SEARCH_RESUME_RUN_ID; then
        fail runid unset
    fi
    step "p3-chain smoke-python with_writer_lock.py bash-pr92_install operator-shell launchd-1"
    if ! "$PYTHON" "$WT/scripts/with_writer_lock.py" \
        --purpose att0-migrate \
        --lock-file "$WT/pr92-smoke/positive.write.lock" \
        --action-required-file "$WT/pr92-smoke/absent-action-required" \
        -- "$ENVBIN" PYTHONPATH="${WT}/scripts" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" - "$WT" << 'PY'
import sys
wt = sys.argv[1]
sys.path.insert(0, wt + "/scripts")
import sor_writer_gate as g
db = wt + "/pr92-smoke/mailroom.sqlite"
lock = wt + "/pr92-smoke/positive.write.lock"
if not g.is_live_sor(db):
    sys.stdout.write("STOP-not-live\n")
    raise SystemExit(1)
held, detail = g.writer_lock_held(lock)
if held or detail != "writer lock held by wrapper":
    sys.stdout.write("STOP-p3-positive-reason\n")
    raise SystemExit(1)
try:
    g.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)
except Exception:
    sys.stdout.write("STOP-p3-positive\n")
    raise SystemExit(1)
sys.stdout.write("PR92-P3-ALLOW\n")
PY
    then
        fail p3 p3-positive
    fi
    mark p3 PR92-P3-OK

    kill_holder
    assert_holder_dead
    mark holder-stop PR92-HOLDER-STOPPED

    for _need in worktree unit rerank runid smoke-dir smoke-db holder lock negative p3 holder-stop; do
        if [ ! -e "/tmp/pr92-${_need}-${STAMP}.OK" ]; then
            fail gate "missing-${_need}"
        fi
    done
    if stop_files_present; then
        fail gate stopfile
    fi
    if ! printf '%s\n' PR92-GATE-OK > "/tmp/pr92-gate-${STAMP}.GATEOK"; then
        fail gate gate-write
    fi
    step PR92-GATE-OK
}

block3() {
    require_gateok
    mark b3-entry PR92-B3-ENTRY-OK
    require_gateok
    daily_disabled before
    require_gateok
    daily_booted before
    require_gateok
    plist_before
    require_gateok
    if ! "$MKDIR" -p "$BACKUP_DIR"; then
        fail backup-dir mkdir
    fi
    mark backup-dir PR92-BACKUP-DIR-OK
    require_gateok
    _bak="$BACKUP_DIR/sor_writer_gate.py.${STAMP}"
    if [ -e "$_bak" ]; then
        fail backup exists
    fi
    if ! "$CP" -n "$SCRIPTS/sor_writer_gate.py" "$_bak"; then
        fail backup cp
    fi
    mark backup PR92-BACKUP-OK
    require_gateok
    hash_eq "$_bak" "$OLD_BLOB" backup-blob
    mark backup-blob PR92-BACKUP-BLOB-OK
    require_gateok
    hash_eq "$SCRIPTS/with_writer_lock.py" "$WRAPPER_BLOB" wrapper
    mark wrapper PR92-WRAPPER-OK
    require_gateok
    record_five "/tmp/pr92-five.${STAMP}.sha"
    mark five PR92-FIVE-RECORDED
    require_gateok
    _stage="$SCRIPTS/sor_writer_gate.py.${STAMP}.stage"
    if ! "$GIT" -C "$CLONE" show "${PIN}:scripts/sor_writer_gate.py" > "$_stage"; then
        fail stage show
    fi
    mark stage PR92-STAGED
    require_gateok
    hash_eq "$_stage" "$NEW_BLOB" stage-blob
    mark stage-blob PR92-STAGE-BLOB-OK
    require_gateok
    if ! "$CHMOD" 644 "$_stage"; then
        fail stage-mode chmod
    fi
    mark stage-mode PR92-STAGE-MODE-OK
    require_gateok
    if [ ! -e "/tmp/pr92-gate-${STAMP}.GATEOK" ]; then
        fail mv no-gateok
    fi
    if [ ! -e "/tmp/pr92-stage-blob-${STAMP}.OK" ]; then
        fail mv stage-blob-missing
    fi
    if [ ! -e "/tmp/pr92-stage-mode-${STAMP}.OK" ]; then
        fail mv stage-mode-missing
    fi
    if ! "$MV" "$_stage" "$SCRIPTS/sor_writer_gate.py"; then
        fail mv mv
    fi
    MOVED=1
    mark copied PR92-COPIED
    require_gateok
    hash_eq "$SCRIPTS/sor_writer_gate.py" "$NEW_BLOB" gate-blob
    mark gate-blob PR92-GATE-BLOB-OK
    require_gateok
    record_five "/tmp/pr92-five-after.${STAMP}.sha"
    if ! "$CMP" "/tmp/pr92-five.${STAMP}.sha" "/tmp/pr92-five-after.${STAMP}.sha"; then
        fail unchanged cmp
    fi
    mark unchanged PR92-UNCHANGED-OK
    require_gateok
    daily_disabled after
    require_gateok
    daily_booted after
    require_gateok
    plist_after
    require_gateok
    for _need in b3-entry daily-disabled-before daily-booted-before daily-plist-before backup-dir backup backup-blob wrapper five stage stage-blob stage-mode copied gate-blob unchanged daily-disabled-after daily-booted-after daily-plist-after; do
        if [ ! -e "/tmp/pr92-${_need}-${STAMP}.OK" ]; then
            fail install "missing-${_need}"
        fi
    done
    if stop_files_present; then
        fail install stopfile
    fi
    mark install PR92-INSTALL-OK
    printf '%s\n' "PR92 SUMMARY stamp=${STAMP} exit=0"
}

do_install() {
    trap kill_holder EXIT INT TERM
    block1
    block2
    block3
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0 2>/dev/null || exit 0
fi

case "${1:-}" in
    install)
        load_tools
        do_install
        ;;
    rollback)
        if [ -z "${2:-}" ]; then
            printf '%s\n' "PR92 FAIL usage: rollback needs STAMP"
            printf '%s\n' "PR92 SUMMARY stamp=unknown exit=2"
            exit 2
        fi
        load_tools
        do_rollback "$2"
        ;;
    *)
        printf '%s\n' "usage: /bin/bash /tmp/pr92_install.sh install|rollback STAMP"
        printf '%s\n' "PR92 SUMMARY stamp=unknown exit=2"
        exit 2
        ;;
esac
