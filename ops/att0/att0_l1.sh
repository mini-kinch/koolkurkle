#!/bin/bash
# ATT-0 L1 live window, rollback, and daily restore.
# Phase P is a separate script (phaseP_473b59a0.sh) and is not this file.
#
# Invoke with /bin/bash (macOS /bin/bash 3.2). Do not rely on PATH or the
# shebang alone.
#   /bin/bash /tmp/att0_l1.sh window P_STAMP
#   /bin/bash /tmp/att0_l1.sh rollback WINDOW_STAMP
#   /bin/bash /tmp/att0_l1.sh restore-daily WINDOW_STAMP
#   /bin/bash /tmp/att0_l1.sh report WINDOW_STAMP
#
# Phase P is phaseP_473b59a0.sh (offline, then fill). This file has no
# phasep mode and does not invoke the Keychain binary. window requires
# /tmp/phaseP-offline-<P_STAMP>.OK, /tmp/phaseP-p8-<P_STAMP>.OK and
# /tmp/phaseP-state-<P_STAMP> before it creates a directory or a
# transcript. The Keychain and IMAP gate before S is the in-window
# rehearsal fill. There is no access probe. report is read-only: it
# recomputes the window verdict from that window's logs and markers,
# and it does not write.
# Mailroom's posted verdict stays official. The +26 search-resume
# deadline is written once, before search bootout, and dropped when A2
# takes the writer lock. Nothing in window, rollback, or restore-daily
# arms that +26 restore, or any other restore timer, in the free-lock
# gap between A2 and A3. The gap calls watchdog status only. The only
# deadline there is the S+50 file plus the S+51 budget.
#
# D_P is re-read here by the P8 D query, read-only, on Phase P's scratch
# copy $HOME/MailArchive/dryrun/att0-livepath-<P_STAMP>/mailroom.sqlite.
# The operator does not write /tmp/att0-dp-<P_STAMP>. D_3.5b is D_W from
# the in-window rehearsal copy, auto-confirmed by rule A2. After A3,
# D_A is the same query on the live SoR and D_late is D_A - D_W
# (0..D_LATE_MAX), auto-confirmed by rule A3.
# restore-daily's gate is the window-done or rollback-done marker.
# Mailroom's posted verdict is outside this script.
#
# Exit codes:
#   0  done, and the SAFE-STATE check passed
#   1  stopped before any live SoR write
#   2  usage
#   3  failure after a live SoR write; the RESULT line says whether
#      rollback ran and whether search was restored
#   4  the work finished but SAFE-STATE failed
# Exit 2 is never a success sentinel. WriterLockError from the wrapper
# is also exit 2 and is always a failure.
#
# The first output line is "<PFX> STAMP=<STAMP>". The last line is
# "<PFX> SUMMARY stamp=<STAMP> exit=<rc>".
# No human prompt, no sudo, and no Keychain binary. IMAP and Keychain
# are reached only by meta_fill.py.

set -u
set -o pipefail
set -C

PIN=473b59a0bc859af2fbcf1ec4649c3af49a3ceea4
SHA_GATE=505382b4e23dfed9f746d70fc6f3a79626524be37168aed955ac7bd8872d8345
SHA_WRAPPER=1e7f5aeee8dd05bca363e8f517e046cc68e22d25c18c7c4dc487d4121dba4f77
SHA_WATCHDOG=bae404ccae07857d0dcb47cbc47a107f68e367c3fe7edff07eb202a01aeb8d43
SHA_MIGRATE=b13b3c97d968c994955c1db313707dd1db372d47668f1d301b6b7c3febcbf95d
SHA_FILL=20f3d997dfa353f738b365542b468ded85cd27e871c166beef62ff7307968f8b
FIVE_SHAS="$SHA_GATE $SHA_WRAPPER $SHA_WATCHDOG $SHA_MIGRATE $SHA_FILL"
PLIST_SHA12=660616d94c56
DAILY_STAMP='2026-09-26 16:51:58'
IMAP_STAMP='2026-09-26 08:50:16'
G_EXPECT=992
BASELINE_44=44
D_MAX=300
D_LATE_MAX=10
D_BAND=50
# EXPECT_PRE is the un-migrated R1 v2 line count. AFTER is EXPECT_PRE
# plus the nine tables A2 adds. The plan's 29 was replaced by 25.
EXPECT_PRE=25
EXPECT_AFTER=34
R1V2_TIMEOUT_S=60
R1V2_SHA=45ef12295c21f028b2e99dc4cba81fe4025cf45af9debe08e04c6231d577f3aa
# S+51 budget. There is no P_STAMP start deadline.
HARD_LIMIT_S=3060
A2_TIMEOUT_S=120
A3_CAP_S=300
A3_LATEST_S=2520
STEP6_TIMEOUT_S=120
D_QUERY_TIMEOUT_S=60
HEALTH_TIMEOUT_S=60
REHEARSAL_TIMEOUT_S=300
ARR_RESERVE_S=480
S2_RESERVE_S=120
LOCK_WAIT_S=30
TERM_WAIT_S=5
HEALTH_URL=http://127.0.0.1:8743/health
# Bracket-guarded so pgrep -f does not match its own argv. Each name
# appears once. A second alternative that contains one of these names
# unguarded (for example [r]un_mailroom_daily) self-matches.
WRITER_PAT='[m]ailroom_daily|[i]map_newmail|[i]map_tombstone|[i]map_fetch_bodies|[n]otify_bills|[r]em-legacy|[m]eta_fill|[m]igrate_att0|[e]mbed_backfill|[e]mbed_merge_shards|[e]mbed_sidecar_apply|[p]ost_rem_embed_batch|[w]ith_writer_lock|[s]ecurity find-generic|[p]haseP_'
CURL_PAT='^/usr/bin/curl( |$)'
PERL_PAT='^/usr/bin/perl( |$)'
SECURITY_PAT='^/usr/bin/security( |$)'
TIME_PAT='^/usr/bin/time( |$)'
SCRIPT_PAT='[a]tt0_l1\.sh'
P5_LEFTOVER_NAME=mailroom-pre-att0-live-20260927-2019.sqlite

PYTHON=/usr/bin/python3
PERL=/usr/bin/perl
CURL=/usr/bin/curl

PFX=
STAMP=
P_STAMP=
W_STAMP=
MODE=
N=0
MA=
S_DIR=
SOR=
LOGS=
BK=
BK_SHA=
S_EPOCH=
RUN_ID=
D_P=
D_W=
D_A=
D_LATE=
D_UNSCANNED=
R_MESSAGES=
R_ERRORS=
R_CAPPED=
R_ELIGIBLE=
A_MESSAGES=
A_ERRORS=
A_CAPPED=
A_ELIGIBLE=
FILL_MESSAGES=
FILL_ERRORS=
FILL_CAPPED=
FILL_ELIGIBLE=
WROTE_SOR=0
SEARCH_DOWN=0
SEARCH_RESTORED=0
ARR_STATUS=not-run
SEARCH_STATUS=left-up
WANTED_RC=1
SUMMARY_DONE=0
CLEANED=0
ASK_SHA=
REH_DB=
HELPER_PL=
TRANSCRIPT=

LAUNCHCTL=
SHASUM=
AWK=
DATEBIN=
IDBIN=
CP=
MKDIR=
STAT=
SQLITE=
PGREP=
LSOF=
PS=
TEE=
SLEEP=
GREP=
MV=
PLUTIL=
ENVBIN=
TOUCH=

die_tool() {
    printf '%s\n' "${PFX} STAMP=unknown"
    printf '%s\n' "${PFX} FAIL tools: $1"
    printf '%s\n' "${PFX} SUMMARY stamp=unknown exit=1"
    exit 1
}

need_abs() {
    _name=$1
    _path=$2
    case "$_path" in
        /*) ;;
        *) die_tool "$_name" ;;
    esac
    if [ ! -x "$_path" ]; then
        die_tool "$_name"
    fi
}

load_tools() {
    if [ -z "${HOME:-}" ]; then
        die_tool HOME
    fi
    MA="$HOME/MailArchive"
    S_DIR="$MA/scripts"
    SOR="$MA/mailroom.sqlite"
    LOGS="$MA/logs"
    need_abs python3 "$PYTHON"
    need_abs perl "$PERL"
    need_abs curl "$CURL"
    LAUNCHCTL=$(command -v launchctl 2>/dev/null || true)
    SHASUM=$(command -v shasum 2>/dev/null || true)
    AWK=$(command -v awk 2>/dev/null || true)
    DATEBIN=$(command -v date 2>/dev/null || true)
    IDBIN=$(command -v id 2>/dev/null || true)
    CP=$(command -v cp 2>/dev/null || true)
    MKDIR=$(command -v mkdir 2>/dev/null || true)
    STAT=$(command -v stat 2>/dev/null || true)
    SQLITE=$(command -v sqlite3 2>/dev/null || true)
    PGREP=$(command -v pgrep 2>/dev/null || true)
    LSOF=$(command -v lsof 2>/dev/null || true)
    PS=$(command -v ps 2>/dev/null || true)
    TEE=$(command -v tee 2>/dev/null || true)
    SLEEP=$(command -v sleep 2>/dev/null || true)
    GREP=$(command -v grep 2>/dev/null || true)
    MV=$(command -v mv 2>/dev/null || true)
    PLUTIL=$(command -v plutil 2>/dev/null || true)
    ENVBIN=$(command -v env 2>/dev/null || true)
    TOUCH=$(command -v touch 2>/dev/null || true)
    need_abs launchctl "$LAUNCHCTL"
    need_abs shasum "$SHASUM"
    need_abs awk "$AWK"
    need_abs date "$DATEBIN"
    need_abs id "$IDBIN"
    need_abs cp "$CP"
    need_abs mkdir "$MKDIR"
    need_abs stat "$STAT"
    need_abs sqlite3 "$SQLITE"
    need_abs pgrep "$PGREP"
    need_abs lsof "$LSOF"
    need_abs ps "$PS"
    need_abs tee "$TEE"
    need_abs sleep "$SLEEP"
    need_abs grep "$GREP"
    need_abs mv "$MV"
    need_abs plutil "$PLUTIL"
    need_abs env "$ENVBIN"
    need_abs touch "$TOUCH"
}

say() {
    N=$((N + 1))
    _line="${PFX} ${N} $1"
    printf '%s\n' "$_line"
    if [ -n "${TRANSCRIPT:-}" ]; then
        printf '%s\n' "$_line" >> "$TRANSCRIPT"
    fi
}

uid_now() {
    "$IDBIN" -u
}

gui_target() {
    _label=$1
    printf '%s\n' "gui/$(uid_now)/${_label}"
}

stamp_ok() {
    case "$1" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;;
        *) return 1 ;;
    esac
}

new_stamp() {
    _s=$("$DATEBIN" +%Y%m%d-%H%M%S) || return 1
    stamp_ok "$_s" || return 1
    printf '%s\n' "$_s"
}

has_line() {
    _file=$1
    _needle=$2
    "$AWK" -v n="$_needle" 'BEGIN { f = 0 } index($0, n) { f = 1 } END { exit f ? 0 : 1 }' "$_file"
}

# Exactly one line that starts with key=, and the value is want.
# A missing line, a second line, or a different value is a miss.
kv_one() {
    _file=$1
    _key=$2
    _want=$3
    "$AWK" -v k="$_key" -v w="$_want" '
        BEGIN { n = 0; bad = 0 }
        index($0, k "=") == 1 {
            n++
            val = substr($0, length(k) + 2)
            if (val != w) bad = 1
        }
        END { if (n != 1 || bad) exit 1 }
    ' "$_file"
}

# Whole transcript line, or the line after the "PFX N " prefix, equals msg.
exact_msg() {
    _file=$1
    _msg=$2
    "$AWK" -v m="$_msg" '
        BEGIN { f = 0 }
        {
            line = $0
            sub(/^[^ ]+ [0-9]+ /, "", line)
            if ($0 == m || line == m) f = 1
        }
        END { exit f ? 0 : 1 }
    ' "$_file"
}

text_has() {
    _hay=$1
    _needle=$2
    printf '%s\n' "$_hay" | "$AWK" -v n="$_needle" 'BEGIN { f = 0 } index($0, n) { f = 1 } END { exit f ? 0 : 1 }'
}

env_check() {
    for _v in SOR_FORCE_LIVE_CHECKS MAILROOM_SEARCH_RESUME_RUN_ID MAILROOM_WRITER_LOCK_TOKEN \
        MAILROOM_WRITER_LOCK_PID MAILROOM_WRITER_LOCK_PURPOSE MAILROOM_WRITE_LOCK MAILROOM_DB \
        MAILROOM_IMAP_MAILBOX PYTHONPATH PYTHONHOME IMAP_APP_PASSWORD MAILROOM_IMAP_PASSWORD; do
        eval "_set=\${$_v+x}"
        if [ -n "$_set" ]; then
            if [ "$_v" = "SOR_FORCE_LIVE_CHECKS" ]; then
                say FLAG_EXPORTED_STOP
            else
                say "STOP-env-set ${_v}"
            fi
            return 1
        fi
    done
    say ENV-CLEAN-OK
    return 0
}

sha_five_ok() {
    _h=$("$SHASUM" -a 256 \
        "$S_DIR/sor_writer_gate.py" \
        "$S_DIR/with_writer_lock.py" \
        "$S_DIR/search_resume_watchdog.py" \
        "$S_DIR/attachments/migrate_att0_schema.py" \
        "$S_DIR/attachments/meta_fill.py" | "$AWK" '{ printf "%s ", $1 }') || return 1
    if [ "$_h" = "$FIVE_SHAS " ]; then
        say FIX-SHAS-OK
        return 0
    fi
    say STOP-0f1-shas
    return 1
}

# rc 0 is loaded. rc 113 or the not-found text is not-loaded.
# Any other rc is unclear. Both loaded and unclear are bad for a held daily.
daily_print_state() {
    _out=$("$LAUNCHCTL" print "$(gui_target "$1")" 2>&1)
    _rc=$?
    DAILY_PRINT_OUT=$_out
    DAILY_PRINT_RC=$_rc
    if [ "$_rc" -eq 0 ]; then
        DAILY_PRINT_STATE=loaded
        return 0
    fi
    if [ "$_rc" -eq 113 ] || text_has "$_out" "Could not find service"; then
        DAILY_PRINT_STATE=not-loaded
        return 0
    fi
    DAILY_PRINT_STATE=unclear
    return 0
}

daily_not_loaded() {
    daily_print_state com.mailroom.daily || return 1
    if [ "$DAILY_PRINT_STATE" = "not-loaded" ]; then
        say DAILY-NOT-LOADED
        return 0
    fi
    if [ "$DAILY_PRINT_STATE" = "loaded" ]; then
        say "STOP-daily-loaded rc=${DAILY_PRINT_RC}"
        return 1
    fi
    say "DAILY-UNCLEAR rc=${DAILY_PRINT_RC}"
    return 1
}

daily_disabled() {
    _out=$("$LAUNCHCTL" print-disabled "gui/$(uid_now)" 2>&1)
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        say STOP-print-disabled
        return 1
    fi
    if printf '%s\n' "$_out" | "$AWK" '{
        gsub(/^[ \t]+|[ \t]+$/, "")
        if ($0 == "\"com.mailroom.daily\" => disabled" || $0 == "\"com.mailroom.daily\" => true") found = 1
    } END { exit found ? 0 : 1 }'; then
        say DAILY-DISABLED
        return 0
    fi
    say STOP-daily-not-disabled
    return 1
}

# Prints absent, present, or error. rc 1 is absent (OK). rc 0 is
# present (BAD). Any other rc is PGREP-ERROR (BAD). Never fold with && / ||.
pgrep_state() {
    _pat=$1
    _out=$("$PGREP" -fl "$_pat" 2>&1)
    _rc=$?
    if [ "$_rc" -eq 1 ]; then
        printf '%s\n' absent
        return 0
    fi
    if [ "$_rc" -eq 0 ]; then
        printf '%s\n' present
        return 0
    fi
    printf '%s\n' error
    return 0
}

no_writer() {
    _st=$(pgrep_state "$WRITER_PAT")
    if [ "$_st" = "error" ]; then
        say PGREP-ERROR
        say STOP-pgrep-error
        return 1
    fi
    if [ "$_st" = "present" ]; then
        say STOP-writer-running
        return 1
    fi
    _st=$(pgrep_state "$CURL_PAT")
    if [ "$_st" = "error" ]; then
        say PGREP-ERROR
        say STOP-pgrep-error
        return 1
    fi
    if [ "$_st" = "present" ]; then
        say STOP-curl-running
        return 1
    fi
    say NO-WRITER-OK
    return 0
}

# One lsof -t -- FILE per lock, stderr included. An all-digit line is
# held. rc 1 with empty output is free. Anything else is LSOF-ERROR.
# A missing write lock is not free. A missing daily lock is free.
lock_probe_file() {
    _path=$1
    _missing_ok=$2
    if [ ! -e "$_path" ] && [ ! -L "$_path" ]; then
        if [ "$_missing_ok" = "1" ]; then
            printf '%s\n' free
        else
            printf '%s\n' missing
        fi
        return 0
    fi
    _out=$("$LSOF" -t -- "$_path" 2>&1)
    _rc=$?
    if [ -n "$_out" ] && printf '%s\n' "$_out" | "$AWK" 'BEGIN { f = 0 } $0 ~ /^[0-9]+$/ { f = 1 } END { exit f ? 0 : 1 }'; then
        printf '%s\n' held
        return 0
    fi
    if [ "$_rc" -eq 1 ] && [ -z "$_out" ]; then
        printf '%s\n' free
        return 0
    fi
    printf '%s\n' error
    return 0
}

# Same acquisition path as with_writer_lock.py: LOCK_EX|LOCK_NB, then
# release immediately. rc 2 is held. lsof rc is not this check.
flock_probe() {
    _rc=0
    "$PYTHON" "$S_DIR/with_writer_lock.py" \
        --purpose att0-lock-probe \
        --lock-file "$MA/mailroom.write.lock" \
        -- /usr/bin/true || _rc=$?
    if [ "$_rc" -eq 0 ]; then
        say FLOCK-WRITE-FREE
        return 0
    fi
    if [ "$_rc" -eq 2 ]; then
        say STOP-flock-held
        return 1
    fi
    say STOP-flock-error
    return 1
}

# Step 0. Writer, daily, lock, action-required, hits, health, and
# watchdog re-check. Caller must invoke this before it creates a
# directory, a transcript, or a SoR copy. Probe logs stay under /tmp.
step0_locks() {
    if ! no_writer; then
        return 1
    fi
    if ! daily_not_loaded; then
        return 1
    fi
    if ! daily_disabled; then
        return 1
    fi
    if ! locks_ok; then
        return 1
    fi
    if ! no_action_required; then
        return 1
    fi
    if ! hits_clear "/tmp/att0-step0-hits-${STAMP}-$$.log"; then
        return 1
    fi
    if ! health_ok "/tmp/att0-step0-health-${STAMP}-$$.log"; then
        return 1
    fi
    if ! watchdog_missing "/tmp/att0-step0-wd-${STAMP}-$$.log"; then
        return 1
    fi
    if ! r1v2_pin_ok; then
        return 1
    fi
    say STEP0-LOCKS-OK
    return 0
}

locks_ok() {
    _st=$(lock_probe_file "$MA/mailroom.write.lock" 0)
    if [ "$_st" = "missing" ]; then
        say STOP-write-lock-missing
        return 1
    fi
    if [ "$_st" = "held" ]; then
        say STOP-write-lock-held
        return 1
    fi
    if [ "$_st" != "free" ]; then
        say LSOF-ERROR
        say STOP-lsof-write
        return 1
    fi
    say LOCK-WRITE-FREE
    _st=$(lock_probe_file "$MA/mailroom.daily.lock" 1)
    if [ "$_st" = "held" ]; then
        say STOP-daily-lock-held
        return 1
    fi
    if [ "$_st" != "free" ]; then
        say LSOF-ERROR
        say STOP-lsof-daily
        return 1
    fi
    say LOCK-DAILY-FREE
    if ! flock_probe; then
        return 1
    fi
    return 0
}

no_action_required() {
    if [ -e "$MA/ACTION_REQUIRED" ] || [ -L "$MA/ACTION_REQUIRED" ]; then
        say ACTION_REQUIRED_PRESENT
        say STOP-action-required
        return 1
    fi
    say NO-ACTION-REQUIRED-OK
    return 0
}

stamps_ok() {
    _d=$("$STAT" -f '%Sm' -t '%F %T' "$LOGS/last_daily_rag_ok" 2>/dev/null || true)
    _i=$("$STAT" -f '%Sm' -t '%F %T' "$LOGS/last_imap_ok" 2>/dev/null || true)
    if [ "$_d" != "$DAILY_STAMP" ]; then
        say STOP-daily-stamp
        return 1
    fi
    if [ "$_i" != "$IMAP_STAMP" ]; then
        say STOP-imap-stamp
        return 1
    fi
    say STAMPS-OK
    return 0
}

sor_1c() {
    if [ -L "$SOR" ]; then
        say STOP-sor-symlink
        return 1
    fi
    _st=$("$STAT" -f '%HT %l' "$SOR" 2>/dev/null || true)
    case "$_st" in
        "Regular File 1") say "Regular File 1" ;;
        *) say STOP-sor-stat; return 1 ;;
    esac
    _rp=$("$PYTHON" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SOR" 2>/dev/null || true)
    case "$_rp" in
        */MailArchive/mailroom.sqlite) say "…/MailArchive/mailroom.sqlite" ;;
        *) say STOP-realpath; return 1 ;;
    esac
    say SOR-1C-OK
    return 0
}

legacy_rows_ok() {
    # Master-list step 0: legacy attachment rows are 0.
    # An absent table counts as 0. The count is a second statement:
    # SQLite prepares every table named in a statement, so a CASE that
    # mentions attachments fails when the table does not exist yet.
    _exists=$("$SQLITE" -readonly "$SOR" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='attachments';" 2>/dev/null || true)
    if [ "$_exists" = "0" ]; then
        say LEGACY-ROWS-OK
        return 0
    fi
    if [ "$_exists" != "1" ]; then
        say STOP-legacy-rows
        return 1
    fi
    _n=$("$SQLITE" -readonly "$SOR" "SELECT COUNT(*) FROM attachments;" 2>/dev/null || true)
    if [ "$_n" = "0" ]; then
        say LEGACY-ROWS-OK
        return 0
    fi
    say STOP-legacy-rows
    return 1
}

ask_hash_record() {
    _p="$S_DIR/ask_mail.py"
    if [ ! -f "$_p" ]; then
        ASK_SHA=absent
        say ASK-MAIL-ABSENT
        return 0
    fi
    ASK_SHA=$("$SHASUM" -a 256 "$_p" | "$AWK" '{print $1; exit}') || return 1
    if [ -z "$ASK_SHA" ]; then
        return 1
    fi
    say ASK-MAIL-HASH-RECORDED
    return 0
}

ask_hash_same() {
    _p="$S_DIR/ask_mail.py"
    if [ "$ASK_SHA" = "absent" ]; then
        if [ -f "$_p" ]; then
            say STOP-ask-mail-appeared
            return 1
        fi
        say ASK-MAIL-UNCHANGED-OK
        return 0
    fi
    _now=$("$SHASUM" -a 256 "$_p" | "$AWK" '{print $1; exit}') || return 1
    if [ "$_now" != "$ASK_SHA" ]; then
        say STOP-ask-mail-changed
        return 1
    fi
    say ASK-MAIL-UNCHANGED-OK
    return 0
}

now_epoch() {
    "$DATEBIN" +%s
}

phasep_markers_ok() {
    if [ ! -f "/tmp/phaseP-offline-${P_STAMP}.OK" ]; then
        say STOP-no-phasep-offline
        return 1
    fi
    if [ ! -f "/tmp/phaseP-p8-${P_STAMP}.OK" ]; then
        say STOP-no-phasep-p8
        return 1
    fi
    if [ ! -s "/tmp/phaseP-state-${P_STAMP}" ]; then
        say STOP-no-phasep-state
        return 1
    fi
    if [ ! -f "$MA/dryrun/att0-livepath-${P_STAMP}/mailroom.sqlite" ]; then
        say STOP-no-phasep-scratch
        return 1
    fi
    say PHASEP-MARKERS-OK
    return 0
}

# Entry re-check after the markers. Both Phase P
# stamps are the daily and imap stamps. G and D_P come from the read-only
# P8 query on the Phase P scratch copy. The A2 band is [D_P, D_P+D_BAND].
entry_dp_ok() {
    if ! stamps_ok; then
        return 1
    fi
    _dlog="$LOGS/att0w-dp-${STAMP}.log"
    if ! run_d_query "$MA/dryrun/att0-livepath-${P_STAMP}/mailroom.sqlite" "$_dlog"; then
        say STOP-d-p-query
        return 1
    fi
    D_P=$D_TOTAL
    if [ "$D_GONE" != "$G_EXPECT" ]; then
        say STOP-gone
        return 1
    fi
    _hi=$((D_P + D_BAND))
    say "G=${G_EXPECT}"
    say "D_P=${D_P} source=phasep-scratch"
    say "D-BAND D_P=${D_P} low=${D_P} high=${_hi}"
    return 0
}

claim_window() {
    _f="/tmp/att0w-claimed-${P_STAMP}"
    if [ -e "$_f" ]; then
        say STOP-window-already-claimed
        return 1
    fi
    if ! printf '%s\n' "$STAMP" > "$_f"; then
        say STOP-claim-write
        return 1
    fi
    say WINDOW-CLAIMED
    return 0
}

d_query_sql() {
    # Per-index lines carry an index number only. Folder names stay in SQL.
    # Each statement has its own WITH. A second statement does not see the first CTE.
    # A missing attachment_meta_scans table is the pre-A2 state: nothing is scanned.
    _scan=$1
    if [ "$_scan" = "scan" ]; then
        _s='(x.id IN (SELECT message_id FROM attachment_meta_scans))'
    else
        _s='0'
    fi
    _m="WITH m AS (SELECT x.folder AS folder, x.present_on_server AS p, ${_s} AS s FROM messages x WHERE x.source='imap-live' AND x.folder IS NOT NULL AND trim(x.folder)<>'')"
    printf '%s\n' "${_m}, g AS (SELECT folder, sum(p=1) AS present, sum(p=1 AND NOT s) AS missing_present, sum(p=0 AND s) AS scanned_gone FROM m GROUP BY folder) SELECT 'idx='||row_number() OVER (ORDER BY folder)||' present='||present||' missing_present='||missing_present||' scanned_gone='||scanned_gone FROM g ORDER BY folder; ${_m} SELECT 'TOTAL present='||sum(p=1)||' missing_present='||sum(p=1 AND NOT s)||' D_total='||(sum(p=1 AND NOT s)-${BASELINE_44})||' gone='||sum(p=0)||' scanned_gone='||sum(p=0 AND s)||' unscanned_all='||sum(NOT s) FROM m;"
}

field_of() {
    _line=$1
    _key=$2
    printf '%s\n' "$_line" | "$AWK" -v k="$_key" '{
        n = split($0, a, " ")
        for (i = 1; i <= n; i++) {
            split(a[i], b, "=")
            if (b[1] == k) { print b[2]; exit }
        }
    }'
}

parse_d_line() {
    _line=$1
    D_PRESENT=$(field_of "$_line" present)
    D_MISSING=$(field_of "$_line" missing_present)
    D_TOTAL=$(field_of "$_line" D_total)
    D_GONE=$(field_of "$_line" gone)
    D_SCANNED_GONE=$(field_of "$_line" scanned_gone)
    D_UNSCANNED=$(field_of "$_line" unscanned_all)
    if [ -z "${D_TOTAL}" ] || [ -z "${D_GONE}" ] || [ -z "${D_SCANNED_GONE}" ] || [ -z "${D_UNSCANNED}" ]; then
        return 1
    fi
    return 0
}

run_d_query() {
    _db=$1
    _log=$2
    _has=$("$SQLITE" -readonly "$_db" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='attachment_meta_scans';" 2>/dev/null || true)
    if [ "$_has" = "1" ]; then
        _sql=$(d_query_sql scan)
    else
        _sql=$(d_query_sql noscan)
    fi
    _out=$(run_group "$D_QUERY_TIMEOUT_S" "$_log" "$SQLITE" -readonly "$_db" "$_sql") || return 1
    _line=$("$AWK" '/^TOTAL / { print; exit }' "$_log" 2>/dev/null || true)
    if [ -z "$_line" ]; then
        return 1
    fi
    parse_d_line "$_line" || return 1
    printf '%s\n' "$_line"
    return 0
}

# A2 item 1 and item 7. Every index line has scanned_gone=0, and
# missing_present <= max(10, present/2). Folder names are not read.
# indexes_hold is quiet so the read-only report can reuse it.
indexes_hold() {
    _log=$1
    _n=$("$AWK" 'BEGIN { n = 0 } /^idx=/ { n++ } END { print n + 0 }' "$_log")
    if [ "${_n:-0}" -lt 1 ]; then
        return 1
    fi
    "$AWK" '
        function val(line, key,    n, i, parts, kv) {
            n = split(line, parts, " ")
            for (i = 1; i <= n; i++) {
                split(parts[i], kv, "=")
                if (kv[1] == key) return kv[2]
            }
            return ""
        }
        BEGIN { bad = 0 }
        /^idx=/ {
            p = val($0, "present")
            m = val($0, "missing_present")
            g = val($0, "scanned_gone")
            if (p !~ /^[0-9]+$/ || m !~ /^[0-9]+$/ || g !~ /^[0-9]+$/) bad = 1
            if (g != 0) bad = 1
            half = int(p / 2)
            cap = (half > 10 ? half : 10)
            if (m + 0 > cap) bad = 1
        }
        END { exit bad ? 1 : 0 }
    ' "$_log"
}

d_indexes_ok() {
    _log=$1
    _n=$("$AWK" 'BEGIN { n = 0 } /^idx=/ { n++ } END { print n + 0 }' "$_log")
    if [ "${_n:-0}" -lt 1 ]; then
        say STOP-d-no-index
        return 1
    fi
    if ! indexes_hold "$_log"; then
        say STOP-d-index
        return 1
    fi
    return 0
}

# A2, before any live SoR write. D_late is not this check.
# 1 scanned_gone=0 on every index. 2 TOTAL gone=G. 3 unscanned_all=R_errors.
# 4 rehearsal curl/literal/capped/uidvalidity/partial=0, in log_ok_fill.
# 5 0<=D_W<=D_MAX. 6 D_P<=D_W<=D_P+D_BAND. 7 index bound. 8 fill identity.
d_check() {
    _log=$1
    if [ -z "${D_P}" ] || [ -z "${D_W}" ] || [ -z "${R_ERRORS}" ]; then
        say STOP-d-check-missing
        return 1
    fi
    case "$D_P" in
        ''|*[!0-9-]*) say STOP-d-p-nan; return 1 ;;
    esac
    case "$D_W" in
        ''|*[!0-9-]*) say STOP-d-w-nan; return 1 ;;
    esac
    case "$D_UNSCANNED" in
        ''|*[!0-9]*) say STOP-unscanned; return 1 ;;
    esac
    case "$R_ERRORS" in
        ''|*[!0-9]*) say STOP-unscanned; return 1 ;;
    esac
    if ! d_indexes_ok "$_log"; then
        return 1
    fi
    if [ "$D_GONE" != "$G_EXPECT" ]; then
        say STOP-gone
        return 1
    fi
    if [ "$D_UNSCANNED" != "$R_ERRORS" ]; then
        say STOP-unscanned
        return 1
    fi
    if [ "$D_P" -lt 0 ] || [ "$D_P" -gt "$D_MAX" ]; then
        say STOP-d-p-range
        return 1
    fi
    if [ "$D_W" -lt 0 ] || [ "$D_W" -gt "$D_MAX" ]; then
        say STOP-d-max
        return 1
    fi
    _hi=$((D_P + D_BAND))
    if [ "$D_W" -lt "$D_P" ] || [ "$D_W" -gt "$_hi" ]; then
        say STOP-d-band
        return 1
    fi
    _sum=$((R_MESSAGES + R_ERRORS + R_CAPPED))
    if [ "$_sum" != "$R_ELIGIBLE" ]; then
        say STOP-fill-identity
        return 1
    fi
    say "D-CHECK-OK D_P=${D_P} D_W=${D_W}"
    say "D_3.5b=${D_W} auto (rule A2)"
    return 0
}

# A3, on the live SoR after A3. A miss is a FAIL (caller sets exit 3).
# D_late is D_A - D_W and must sit in 0..D_LATE_MAX.
d_check_a3() {
    _log=$1
    case "$D_A" in
        ''|*[!0-9-]*) say STOP-d-a-nan; return 1 ;;
    esac
    case "$A_ERRORS" in
        ''|*[!0-9]*) say STOP-a3-unscanned; return 1 ;;
    esac
    case "$D_UNSCANNED" in
        ''|*[!0-9]*) say STOP-a3-unscanned; return 1 ;;
    esac
    if ! d_indexes_ok "$_log"; then
        return 1
    fi
    if [ "$D_GONE" != "$G_EXPECT" ]; then
        say STOP-gone
        return 1
    fi
    if [ "$D_UNSCANNED" != "$A_ERRORS" ]; then
        say STOP-a3-unscanned
        return 1
    fi
    _sum=$((A_MESSAGES + A_ERRORS + A_CAPPED))
    if [ "$_sum" != "$A_ELIGIBLE" ]; then
        say STOP-fill-identity
        return 1
    fi
    if [ "$A_ELIGIBLE" != "$R_ELIGIBLE" ]; then
        say STOP-a3-eligible
        return 1
    fi
    D_LATE=$((D_A - D_W))
    if [ "$D_LATE" -lt 0 ] || [ "$D_LATE" -gt "$D_LATE_MAX" ]; then
        say STOP-d-late
        return 1
    fi
    _cap=$((G_EXPECT + BASELINE_44 + D_W + D_LATE))
    _expect=$((G_EXPECT + BASELINE_44 + D_A))
    if [ "$A_ERRORS" -gt "$_cap" ] || [ "$A_ERRORS" != "$_expect" ]; then
        say STOP-a3-44
        return 1
    fi
    say "D_late=${D_LATE} auto (rule A3)"
    say A3-D-OK
    return 0
}

# A4 counts. Non-ATT-0 identity is the R1 v2 before/after digest, not this loop.
a4_counts_ok() {
    _log="$LOGS/att0w-a4-${STAMP}.log"
    if ! run_group "$STEP6_TIMEOUT_S" "$_log" "$PYTHON" -c '
import hashlib, sqlite3, sys
bk_path, sor_path, reh_path = sys.argv[1], sys.argv[2], sys.argv[3]
a_messages = int(sys.argv[4])
d_late = int(sys.argv[5])

def ident(name):
    ok = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
    if not name or any(c not in ok for c in name):
        raise SystemExit(1)
    return "\"" + name + "\""

def open_ro(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA query_only=ON")
    return conn

def tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='\''table'\'' AND name NOT LIKE '\''sqlite_%'\'' ORDER BY name").fetchall()
    return [r[0] for r in rows]

def count(conn, name):
    return conn.execute("SELECT count(*) FROM %s" % ident(name)).fetchone()[0]

def maxrow(conn, name):
    return conn.execute("SELECT coalesce(max(rowid),0) FROM %s" % ident(name)).fetchone()[0]

def has_sum(conn):
    return conn.execute("SELECT coalesce(sum(has_attachments),0) FROM messages").fetchone()[0]

def main():
    bk = open_ro(bk_path)
    sor = open_ro(sor_path)
    reh = open_ro(reh_path)
    if count(bk, "messages") != count(sor, "messages") or maxrow(bk, "messages") != maxrow(sor, "messages"):
        sys.stdout.write("a4_messages=mismatch\n")
        return 1
    sys.stdout.write("a4_messages=%d\n" % count(sor, "messages"))
    sys.stdout.write("a4_messages_max=%d\n" % maxrow(sor, "messages"))
    for name in ("attachments_pr1_empty", "attachment_extracts", "attachment_chunks"):
        if name not in tables(sor):
            sys.stdout.write("a4_missing=%s\n" % name)
            return 1
        n = count(sor, name)
        sys.stdout.write("a4_%s=%d\n" % (name, n))
        if n != 0:
            return 1
    if "attachment_meta_scans" not in tables(sor):
        sys.stdout.write("a4_scans=missing\n")
        return 1
    scans = count(sor, "attachment_meta_scans")
    sys.stdout.write("a4_scans=%d\n" % scans)
    if scans != a_messages:
        return 1
    def triple(conn):
        return (count(conn, "attachments"), count(conn, "attachment_folder_uidvalidity"), has_sum(conn))
    live = triple(sor)
    prev = triple(reh)
    sys.stdout.write("a4_att_live=%d\n" % live[0])
    sys.stdout.write("a4_att_reh=%d\n" % prev[0])
    sys.stdout.write("a4_uid_live=%d\n" % live[1])
    sys.stdout.write("a4_uid_reh=%d\n" % prev[1])
    sys.stdout.write("a4_has_live=%d\n" % live[2])
    sys.stdout.write("a4_has_reh=%d\n" % prev[2])
    if d_late == 0:
        if live != prev:
            return 1
    else:
        if live[0] > prev[0] or live[1] > prev[1] or live[2] > prev[2]:
            return 1
        if (prev[2] - live[2]) > d_late:
            return 1
    sys.stdout.write("a4_result=ok\n")
    return 0

try:
    rc = main()
except Exception:
    sys.stdout.write("a4_error=1\n")
    rc = 1
raise SystemExit(rc)
' "$BK" "$SOR" "$REH_DB" "$A_MESSAGES" "$D_LATE"; then
        say STOP-a4
        return 1
    fi
    if ! kv_one "$_log" a4_result ok; then
        say STOP-a4
        return 1
    fi
    say A4-OK
    return 0
}

# Nine tables A2 adds. The first five are empty at R-POSTA2.
NINE_NAMES='attachments_pr1_empty attachment_extracts attachment_chunks attachment_meta_scans attachment_folder_uidvalidity attachment_chunks_fts_config attachment_chunks_fts_data attachment_chunks_fts_docsize attachment_chunks_fts_idx'
NINE_ZERO='attachments_pr1_empty attachment_extracts attachment_chunks attachment_meta_scans attachment_folder_uidvalidity'
AFTER_EXCEPT='attachments attachment_meta_scans attachment_folder_uidvalidity'

r1v2_zsh() {
    _self=${BASH_SOURCE[0]}
    case "$_self" in
        */*) printf '%s\n' "${_self%/*}/r1v2_digest.zsh" ;;
        *) printf '%s\n' "./r1v2_digest.zsh" ;;
    esac
}

r1v2_pin_ok() {
    _z=$(r1v2_zsh)
    _got=$("$SHASUM" -a 256 "$_z" 2>/dev/null | "$AWK" '{ print $1; exit }') || true
    if [ "$_got" != "$R1V2_SHA" ]; then
        say STOP-r1v2-sha
        return 1
    fi
    say R1V2-SHA-OK
    return 0
}

# One child. PASS is rc 0, last stdout line exactly K_OK lines=EXPECT,
# and OUT.stderr at 0 bytes. The transcript tag is R1V2-<LABEL>.
r1v2_run() {
    _tag=$1
    _label=$2
    _db=$3
    _expect=$4
    _stamp=$5
    _out="$LOGS/att0w-r1v2-${_tag}-${_stamp}.tsv"
    _log="$LOGS/att0w-r1v2-${_tag}-${_stamp}.log"
    _err="${_out}.stderr"
    if [ -e "$_out" ] || [ -L "$_out" ] || [ -e "$_err" ] || [ -L "$_err" ]; then
        say "STOP-r1v2-exists ${_tag}"
        return 1
    fi
    _z=$(r1v2_zsh)
    if ! run_group "$R1V2_TIMEOUT_S" "$_log" /bin/zsh "$_z" "$_db" "$_out" "$_expect"; then
        say "STOP-r1v2 ${_tag}"
        return 1
    fi
    _last=$("$AWK" 'NF { line = $0 } END { print line }' "$_log")
    if [ "$_last" != "K_OK lines=${_expect}" ]; then
        say "STOP-r1v2 ${_tag}"
        return 1
    fi
    if [ ! -f "$_err" ]; then
        say "STOP-r1v2 ${_tag}"
        return 1
    fi
    _errb=$(/usr/bin/wc -c < "$_err" | tr -d ' ')
    if [ "$_errb" != "0" ]; then
        say "STOP-r1v2 ${_tag}"
        return 1
    fi
    say "R1V2-${_label} K_OK lines=${_expect}"
    return 0
}

r1_forbid_nine() {
    _tsv=$1
    "$AWK" -F '\t' -v nine="$NINE_NAMES" '
        BEGIN {
            n = split(nine, a, " ")
            for (i = 1; i <= n; i++) bad[a[i]] = 1
        }
        bad[$1] { found = 1 }
        END { exit found ? 1 : 0 }
    ' "$_tsv"
}

# Every before line is in post. Extra names are exactly NINE.
# The five content tables have count 0.
r1_posta2_ok() {
    _before=$1
    _post=$2
    "$AWK" -F '\t' -v nine="$NINE_NAMES" -v zero="$NINE_ZERO" '
        BEGIN {
            n = split(nine, a, " ")
            for (i = 1; i <= n; i++) allow[a[i]] = 1
            z = split(zero, b, " ")
            for (i = 1; i <= z; i++) wantz[b[i]] = 1
            if (n != 9 || z != 5) bad = 1
        }
        NR == FNR { bline[$0] = 1; bname[$1] = 1; next }
        { pline[$0] = 1; pname[$1] = $2 }
        END {
            extra = 0
            for (line in bline) if (!(line in pline)) bad = 1
            for (name in pname) {
                if (!(name in bname)) {
                    extra++
                    if (!allow[name]) bad = 1
                    seen[name] = 1
                }
            }
            for (name in allow) if (!seen[name]) bad = 1
            if (extra != 9) bad = 1
            for (name in wantz) if (pname[name] != "0") bad = 1
            exit bad ? 1 : 0
        }
    ' "$_before" "$_post"
}

# Same names as post. Every line matches except the three A3 tables.
r1_after_ok() {
    _post=$1
    _after=$2
    "$AWK" -F '\t' -v exc="$AFTER_EXCEPT" '
        BEGIN {
            n = split(exc, a, " ")
            for (i = 1; i <= n; i++) skip[a[i]] = 1
        }
        NR == FNR { pline[$1] = $0; pset[$1] = 1; next }
        { aline[$1] = $0; aset[$1] = 1 }
        END {
            for (name in pset) if (!aset[name]) bad = 1
            for (name in aset) if (!pset[name]) bad = 1
            for (name in pset) {
                if (skip[name]) continue
                if (pline[name] != aline[name]) bad = 1
            }
            exit bad ? 1 : 0
        }
    ' "$_post" "$_after"
}

r1_sor0_before() {
    if ! r1v2_run sor0 SOR0 "$SOR" "$EXPECT_PRE" "$STAMP"; then
        return 1
    fi
    if ! r1v2_run before BEFORE "$BK" "$EXPECT_PRE" "$STAMP"; then
        return 1
    fi
    _sor0="$LOGS/att0w-r1v2-sor0-${STAMP}.tsv"
    _before="$LOGS/att0w-r1v2-before-${STAMP}.tsv"
    if ! r1_forbid_nine "$_sor0" || ! r1_forbid_nine "$_before"; then
        say STOP-r1v2-nine
        return 1
    fi
    if /usr/bin/cmp -s "$_sor0" "$_before"; then
        say LOGICAL_MATCH_BK=YES
    else
        say LOGICAL_MATCH_BK=NO
        say STOP-r1v2-cmp
        return 1
    fi
    PRE_MESSAGES=$("$SQLITE" -readonly "$SOR" "SELECT COUNT(*) FROM messages;") || return 1
    PRE_MAX_ROWID=$("$SQLITE" -readonly "$SOR" "SELECT coalesce(MAX(rowid),0) FROM messages;") || return 1
    _glog="$LOGS/att0w-pre-gone-${STAMP}.log"
    if ! run_d_query "$SOR" "$_glog"; then
        say STOP-r1v2-gone
        return 1
    fi
    PRE_GONE=$D_GONE
    case "$PRE_MESSAGES" in ''|*[!0-9]*) say STOP-r1v2-pre; return 1 ;; esac
    case "$PRE_MAX_ROWID" in ''|*[!0-9]*) say STOP-r1v2-pre; return 1 ;; esac
    case "$PRE_GONE" in ''|*[!0-9]*) say STOP-r1v2-pre; return 1 ;; esac
    printf '%s\n' \
        "pre_messages=${PRE_MESSAGES}" \
        "pre_max_rowid=${PRE_MAX_ROWID}" \
        "pre_gone=${PRE_GONE}" >> "/tmp/att0w-state-${STAMP}" || return 1
    say R1V2-PRE-OK
    return 0
}

r1_posta2() {
    _before="$LOGS/att0w-r1v2-before-${STAMP}.tsv"
    if ! r1v2_run posta2 POSTA2 "$SOR" "$EXPECT_AFTER" "$STAMP"; then
        return 1
    fi
    _post="$LOGS/att0w-r1v2-posta2-${STAMP}.tsv"
    if ! r1_posta2_ok "$_before" "$_post"; then
        say STOP-r1v2-posta2
        return 1
    fi
    say R1V2-POSTA2-OK
    return 0
}

r1_after() {
    _post="$LOGS/att0w-r1v2-posta2-${STAMP}.tsv"
    if ! r1v2_run after AFTER "$SOR" "$EXPECT_AFTER" "$STAMP"; then
        return 1
    fi
    _after="$LOGS/att0w-r1v2-after-${STAMP}.tsv"
    if r1_after_ok "$_post" "$_after"; then
        say LOGICAL_MATCH=YES
        return 0
    fi
    say LOGICAL_MATCH=NO
    return 1
}

copy_db() {
    _src=$1
    _dest=$2
    _dir=${_dest%/*}
    if [ -e "$_dest" ] || [ -e "$_dir" ]; then
        say STOP-scratch-exists
        return 1
    fi
    "$MKDIR" "$_dir" || return 1
    "$SQLITE" -readonly "$_src" ".backup '${_dest}'" || return 1
    return 0
}

imap_user_len() {
    _py=$S_DIR/imap_tombstone.py
    if [ ! -f "$_py" ]; then
        say STOP-imap-user-missing
        return 1
    fi
    IMAP_USER=$("$PYTHON" -c 'import ast,sys
for n in ast.parse(open(sys.argv[1]).read()).body:
    if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "EMAIL" for t in n.targets):
        print(n.value.value)
        break' "$_py" 2>/dev/null || true)
    if [ -z "${IMAP_USER:-}" ]; then
        say STOP-imap-user-missing
        return 1
    fi
    say "imap_user_set len=${#IMAP_USER}"
    return 0
}

write_helper_pl() {
    HELPER_PL="/tmp/att0-run-group-${STAMP}-$$.pl"
    if [ -e "$HELPER_PL" ]; then
        return 0
    fi
    cat > "$HELPER_PL" <<'ENDPERL'
use strict;
use warnings;
use Errno qw(EINTR);
use POSIX qw(WNOHANG);
my $alarm_s = shift @ARGV;
my $log = shift @ARGV;
my $term_wait = shift @ARGV;
die "bad args\n" unless defined $alarm_s && $alarm_s =~ /^\d+$/ && defined $log && defined $term_wait && $term_wait =~ /^\d+$/ && @ARGV;
my $psbin = "/bin/ps";
if (defined $ENV{ATT0_PS_BIN} && $ENV{ATT0_PS_BIN} =~ m{^/}) {
    $psbin = $ENV{ATT0_PS_BIN};
}
delete $ENV{ATT0_PS_BIN};
pipe(my $rh, my $wh) or die "pipe failed\n";
my $pid = fork();
die "fork failed\n" unless defined $pid;
if ($pid == 0) {
    close $rh;
    setpgrp(0, 0) or die "setpgrp failed\n";
    open STDOUT, ">&", $wh or die "stdout failed\n";
    open STDERR, ">&", $wh or die "stderr failed\n";
    close $wh;
    exec @ARGV or die "exec failed\n";
}
close $wh;
my $reader = fork();
die "reader fork failed\n" unless defined $reader;
if ($reader == 0) {
    open my $out, ">>", $log or die "log failed\n";
    my $fb = 0;
    while (my $line = <$rh>) {
        my $keep = 0;
        if (index($line, "falling back") >= 0) {
            $fb = 1;
            $keep = 1;
        }
        if ($line =~ /^(error:|a4_|att0 meta fill|att0 schema migrate|dry_run=|source=|db_basename=|messages=|parts=|has_attachments=|filenames=|bytes_stored=|scanned=|eligible=|stopped=|capped=|capped: |skipped=|errors=|partial=|parts_truncated=|uidvalidity_mismatch=|literal_|curl_failures=|summary_json=|user_version=|legacy_attachments=|attachments=|attachment_|message_embeddings=|fts=|hits_count=|TOTAL |idx=|ok$|[0-9]+$|K_OK |K_FAIL |REFUSE_EXISTS|RESTORE-SWAPPED|REFUSE-|WAL-RETURNED|WAL-RESTORE-FAILED|QUICK-CHECK-FAILED|SWAP-FAILED|run_id=|deadline_26=|deadline_50=|d26_live=|status=)/) {
            $keep = 1;
        }
        print $out $line if $keep;
    }
    close $out;
    exit($fb ? 7 : 0);
}
close $rh;
my $why = "";
my $child_status = -1;
# Live timer parent. The child already called setpgrp and exec.
# This is not alarm-then-exec: the parent stays up and reaps.
# INT, TERM, timeout, and a normal exit all fall through to the same
# TERM, wait, KILL of the whole group.
local $SIG{ALRM} = sub { $why = "timeout" if $why eq ""; };
local $SIG{INT} = sub { $why = "signal" if $why eq ""; };
local $SIG{TERM} = sub { $why = "signal" if $why eq ""; };
alarm($alarm_s);
# Poll. A restarted wait would sit until the leader exits and would
# miss the deadline when the leader ignores the alarm.
while ($why eq "" && $child_status < 0) {
    my $w = waitpid($pid, WNOHANG);
    if ($w == $pid) {
        $child_status = $?;
        last;
    }
    if ($w < 0 && $! != EINTR) {
        last;
    }
    select(undef, undef, undef, 0.2);
}
alarm(0);
# Every child exit, including a normal one. An inner alarm can reap
# only the leader and leave meta_fill, curl, or security in this group.
# TERM, wait, then KILL to -pgid. An empty group is harmless.
# with_writer_lock.py, meta_fill.py, and imap_curl.py do not call
# setsid or start_new_session, so every descendant stays in the group.
die "refusing to signal the current group\n" if $pid <= 1;
kill "TERM", -$pid;
my $left = $term_wait;
while ($left > 0) {
    my $slept = sleep($left);
    last if $slept <= 0;
    $left -= $slept;
}
kill "KILL", -$pid;
if ($child_status < 0) {
    my $w = waitpid($pid, 0);
    if ($w == $pid) {
        $child_status = $?;
    }
} else {
    waitpid($pid, 0);
}
my $reader_status = -1;
if (waitpid($reader, 0) == $reader) {
    $reader_status = $?;
}
my $exit = 1;
if ($child_status >= 0) {
    if ($child_status & 127) {
        $exit = 128 + ($child_status & 127);
    } else {
        $exit = $child_status >> 8;
    }
}
my $how = "normal";
if ($why eq "timeout") {
    $how = "timeout then TERM/KILL";
} elsif ($why eq "signal") {
    $how = "signal then TERM/KILL";
}
print "child_rc=$exit\n";
print "harness=$how\n";
if ($reader_status >= 0 && (($reader_status >> 8) == 7)) {
    print "fallback=1\n";
}
sub group_has_member {
    my $found = 0;
    my $opened = open my $ps, "-|", $psbin, "-ax", "-o", "pid=,pgid=";
    return 1 unless $opened;
    while (my $line = <$ps>) {
        my ($proc_pid, $proc_pgid) = split ' ', $line;
        if (defined $proc_pgid && $proc_pgid =~ /^\d+$/ && $proc_pgid == $pid) {
            $found = 1;
        }
    }
    close $ps;
    return $found;
}
sub fill_curl_in_group {
    my $found = 0;
    my $opened = open my $ps, "-|", $psbin, "-ax", "-o", "pid=,pgid=,command=";
    return 1 unless $opened;
    while (my $line = <$ps>) {
        if ($line =~ /^\s*(\d+)\s+(\d+)\s+(.*)$/) {
            my ($proc_pgid, $cmd) = ($2, $3);
            if ($proc_pgid =~ /^\d+$/ && $proc_pgid == $pid && $cmd =~ m{^/usr/bin/curl( |$)}) {
                $found = 1;
            }
        }
    }
    close $ps;
    return $found;
}
my $group_empty = group_has_member() ? 0 : 1;
if (!$group_empty) {
    select(undef, undef, undef, 0.2);
    $group_empty = group_has_member() ? 0 : 1;
}
my $fill_curl = fill_curl_in_group() ? 1 : 0;
print "group_empty=$group_empty\n";
print "fill_curl=$fill_curl\n";
exit($why eq "timeout" ? 124 : ($exit == 0 ? 0 : 1));
ENDPERL
}

RUN_CHILD_RC=
RUN_HOW=
RUN_FALLBACK=0
RUN_GROUP_EMPTY=0
RUN_FILL_CURL=1

run_group() {
    _alarm=$1
    _log=$2
    shift 2
    write_helper_pl || return 1
    if [ -e "$_log" ]; then
        return 1
    fi
    : > "$_log" || return 1
    _out=$(ATT0_PS_BIN="$PS" "$PERL" "$HELPER_PL" "$_alarm" "$_log" "$TERM_WAIT_S" "$@") || true
    printf '%s\n' "$_out" >&2
    _parsed=$(printf '%s\n' "$_out" | "$AWK" -F= '
        /^child_rc=/ { c = $2 }
        /^harness=/ { h = $2 }
        /^fallback=/ { f = $2 }
        /^group_empty=/ { g = $2 }
        /^fill_curl=/ { k = $2 }
        END { printf "%s|%s|%s|%s|%s\n", c, h, f, g, k }
    ')
    RUN_CHILD_RC=${_parsed%%|*}
    _rest=${_parsed#*|}
    RUN_HOW=${_rest%%|*}
    _rest=${_rest#*|}
    RUN_FALLBACK=${_rest%%|*}
    _rest=${_rest#*|}
    RUN_GROUP_EMPTY=${_rest%%|*}
    RUN_FILL_CURL=${_rest#*|}
    if [ -z "$RUN_CHILD_RC" ]; then
        RUN_CHILD_RC=1
    fi
    _fail=0
    if [ "${RUN_FALLBACK:-0}" = "1" ]; then
        _fail=1
    fi
    if [ "$RUN_HOW" = "timeout then TERM/KILL" ] || [ "$RUN_HOW" = "signal then TERM/KILL" ]; then
        _fail=1
    fi
    if [ "$RUN_CHILD_RC" != "0" ]; then
        _fail=1
    fi
    # Assert the fill group is empty, and that no pinned curl argv is
    # still a member, before any verdict line from the caller.
    if [ "${RUN_GROUP_EMPTY:-0}" != "1" ] || [ "${RUN_FILL_CURL:-1}" != "0" ]; then
        say STOP-group-occupied
        _fail=1
    else
        say GROUP-EMPTY
    fi
    if ! leftover_clear; then
        _fail=1
    fi
    if [ "$_fail" = "1" ]; then
        return 1
    fi
    return 0
}

# format_report puts curl_failures only inside one summary_json= line.
summary_curl_ok() {
    _log=$1
    _n=$("$AWK" 'index($0, "summary_json=") == 1 { n++ } END { print n + 0 }' "$_log")
    if [ "${_n:-0}" != "1" ]; then
        return 1
    fi
    "$AWK" 'index($0, "summary_json=") == 1 { print substr($0, 14); exit }' "$_log" | "$PYTHON" -c 'import json, sys
raw = sys.stdin.read()
if raw.endswith("\n"):
    raw = raw[:-1]
try:
    obj = json.loads(raw)
except Exception:
    raise SystemExit(2)
if not isinstance(obj, dict) or obj.get("curl_failures") != []:
    raise SystemExit(1)
'
}

log_ok_fill() {
    _log=$1
    _apply=$2
    if has_line "$_log" "falling back"; then
        say STOP-falling-back
        return 1
    fi
    # format_report always emits partial=0 or partial=1. The PARTIAL: banner
    # is not a kept line. Apply runs require the key to be exactly 0.
    if [ "$_apply" = "1" ]; then
        if ! kv_one "$_log" partial 0; then
            say STOP-partial
            return 1
        fi
    fi
    # parts_truncated is meta_fill's --max-parts default. It is not a gate.
    _pt=$("$AWK" -F= '/^parts_truncated=/ { print $2; exit }' "$_log")
    if [ -n "${_pt:-}" ]; then
        say "PARTS-TRUNCATED-INFO parts_truncated=${_pt}"
    fi
    if ! summary_curl_ok "$_log"; then
        say STOP-curl-failures
        return 1
    fi
    if ! kv_one "$_log" bytes_stored 0; then
        say STOP-bytes-stored
        return 1
    fi
    if ! kv_one "$_log" capped 0; then
        say STOP-capped
        return 1
    fi
    if ! kv_one "$_log" uidvalidity_mismatch 0; then
        say STOP-uidvalidity
        return 1
    fi
    if ! kv_one "$_log" literal_dropped 0; then
        say STOP-literal-dropped
        return 1
    fi
    if ! kv_one "$_log" literal_truncated 0; then
        say STOP-literal-truncated
        return 1
    fi
    if ! kv_one "$_log" filenames 0; then
        say STOP-filenames
        return 1
    fi
    _m=$("$AWK" -F= '/^messages=/ { print $2; exit }' "$_log")
    _e=$("$AWK" -F= '/^errors=/ { print $2; exit }' "$_log")
    _c=$("$AWK" -F= '/^capped=/ { print $2; exit }' "$_log")
    _g=$("$AWK" -F= '/^eligible=/ { print $2; exit }' "$_log")
    if [ -z "$_m" ] || [ -z "$_e" ] || [ -z "$_c" ] || [ -z "$_g" ]; then
        say STOP-fill-fields
        return 1
    fi
    case "$_m" in ''|*[!0-9]*) say STOP-fill-fields; return 1 ;; esac
    case "$_e" in ''|*[!0-9]*) say STOP-fill-fields; return 1 ;; esac
    case "$_c" in ''|*[!0-9]*) say STOP-fill-fields; return 1 ;; esac
    case "$_g" in ''|*[!0-9]*) say STOP-fill-fields; return 1 ;; esac
    _sum=$((_m + _e + _c))
    if [ "$_sum" != "$_g" ]; then
        say STOP-fill-identity
        return 1
    fi
    FILL_MESSAGES=$_m
    FILL_ERRORS=$_e
    FILL_CAPPED=$_c
    FILL_ELIGIBLE=$_g
    say FILL-REPORT-OK
    return 0
}

log_ok_migrate() {
    _log=$1
    _kind=$2
    if ! kv_one "$_log" user_version 1; then
        say STOP-user-version
        return 1
    fi
    if [ "$_kind" = "first" ]; then
        if ! kv_one "$_log" legacy_attachments renamed_empty; then
            say STOP-legacy-rename
            return 1
        fi
        _n=$("$AWK" 'BEGIN { n = 0 } /=created$/ { n++ } END { print n }' "$_log")
        if [ "$_n" != "5" ]; then
            say STOP-created-count
            return 1
        fi
    else
        if ! kv_one "$_log" legacy_attachments unchanged; then
            say STOP-legacy-unchanged
            return 1
        fi
        _n=$("$AWK" 'BEGIN { n = 0 } /=exists$/ { n++ } END { print n }' "$_log")
        if [ "$_n" != "5" ]; then
            say STOP-exists-count
            return 1
        fi
    fi
    say MIGRATE-OK
    return 0
}

budget_ok() {
    _name=$1
    _tout=$2
    _now=$(now_epoch) || return 1
    _need=$((_now + _tout + ARR_RESERVE_S + S2_RESERVE_S))
    _limit=$((S_EPOCH + HARD_LIMIT_S))
    if [ "$_need" -gt "$_limit" ]; then
        say "STOP-s51 ${_name}"
        return 1
    fi
    say "BUDGET-OK ${_name}"
    return 0
}

a3_timeout() {
    _now=$(now_epoch) || { printf '%s\n' 0; return; }
    _left=$((S_EPOCH + A3_LATEST_S - _now))
    if [ "$_left" -lt 1 ]; then
        printf '%s\n' 0
        return
    fi
    if [ "$_left" -gt "$A3_CAP_S" ]; then
        printf '%s\n' "$A3_CAP_S"
        return
    fi
    printf '%s\n' "$_left"
}

wait_lock_free() {
    _i=0
    while [ "$_i" -lt "$LOCK_WAIT_S" ]; do
        _st=$(lock_probe_file "$MA/mailroom.write.lock" 0)
        if [ "$_st" = "free" ]; then
            return 0
        fi
        "$SLEEP" 1
        _i=$((_i + 1))
    done
    return 1
}

script_others() {
    _out=$("$PGREP" -fl "$SCRIPT_PAT" 2>&1)
    _rc=$?
    if [ "$_rc" -eq 1 ]; then
        return 0
    fi
    if [ "$_rc" -ne 0 ]; then
        say PGREP-ERROR
        say STOP-pgrep-error
        return 1
    fi
    _n=$(printf '%s\n' "$_out" | "$AWK" -v me="$$" -v pp="${PPID:-}" 'NF > 0 && $1 != me && $1 != pp { c++ } END { print c + 0 }')
    if [ "$_n" != "0" ]; then
        say STOP-script-still-running
        return 1
    fi
    return 0
}

leftover_clear() {
    # Follow-up after every run_group, including a normal child exit.
    # The curl pattern is any /usr/bin/curl, so an imap curl is included.
    # Names are bracket-guarded so pgrep does not match its own argv.
    for _pat in \
        "$WRITER_PAT" \
        "$CURL_PAT" \
        "$SECURITY_PAT" \
        "$PERL_PAT" \
        "$TIME_PAT"
    do
        _st=$(pgrep_state "$_pat")
        if [ "$_st" = "error" ]; then
            say PGREP-ERROR
            say STOP-leftover-process
            return 1
        fi
        if [ "$_st" != "absent" ]; then
            say STOP-leftover-process
            return 1
        fi
    done
    if ! script_others; then
        return 1
    fi
    _st=$(lock_probe_file "$MA/mailroom.write.lock" 0)
    if [ "$_st" = "error" ]; then
        say LSOF-ERROR
        say STOP-write-lock-held
        return 1
    fi
    if [ "$_st" != "free" ]; then
        say STOP-write-lock-held
        return 1
    fi
    _st=$(lock_probe_file "$MA/mailroom.daily.lock" 1)
    if [ "$_st" = "error" ]; then
        say LSOF-ERROR
        say STOP-daily-lock-held
        return 1
    fi
    if [ "$_st" != "free" ]; then
        say STOP-daily-lock-held
        return 1
    fi
    # Second check, same exclusive flock with_writer_lock.py takes.
    # Released immediately. A descendant that still holds it is a STOP,
    # which is the case where the leader already dropped its own lock.
    if ! flock_probe; then
        return 1
    fi
    say "FOLLOWUP-CLEAR with_writer_lock meta_fill curl security perl time script locks"
    say LEFTOVER-CLEAR
    return 0
}

search_loaded() {
    _out=$("$LAUNCHCTL" print "$(gui_target com.mailroom.ask-mail-serve)" 2>&1)
    _rc=$?
    if [ "$_rc" -eq 0 ]; then
        printf '%s\n' loaded
        return 0
    fi
    if [ "$_rc" -eq 113 ] || text_has "$_out" "Could not find service"; then
        printf '%s\n' unloaded
        return 0
    fi
    printf '%s\n' unclear
    return 0
}

search_not_disabled() {
    _pd=$("$LAUNCHCTL" print-disabled "gui/$(uid_now)" 2>&1)
    _prc=$?
    _lines=$(printf '%s\n' "$_pd" | "$GREP" -F '"com.mailroom.ask-mail-serve"' || true)
    _n=$(printf '%s' "$_lines" | "$GREP" -c . || true)
    if [ "$_prc" -ne 0 ] || ! text_has "$_pd" "disabled services = {" || ! text_has "$_pd" "=>"; then
        say "DISABLED_STATE_UNCLEAR rc=${_prc}"
        return 1
    fi
    if [ "${_n:-0}" -eq 0 ]; then
        say NOT_DISABLED
        return 0
    fi
    if [ "$_n" -ne 1 ]; then
        say "DISABLED_STATE_UNCLEAR lines=${_n}"
        return 1
    fi
    if text_has "$_lines" "=> disabled" || text_has "$_lines" "=> true"; then
        say DISABLED_STOP
        return 1
    fi
    if text_has "$_lines" "=> enabled" || text_has "$_lines" "=> false"; then
        say NOT_DISABLED
        return 0
    fi
    say "DISABLED_STATE_UNCLEAR line"
    return 1
}

say_search_state() {
    case "$1" in
        loaded) say SEARCH_LOADED ;;
        unloaded) say SEARCH_NOT_LOADED ;;
        *) say "SEARCH_STATE_UNCLEAR" ;;
    esac
}

deadline_26_margin_ok() {
    _slog="$LOGS/att0w-d26-${STAMP}.log"
    if [ -e "$_slog" ]; then
        _slog="$LOGS/att0w-d26-${STAMP}-$$.log"
    fi
    if ! run_group 30 "$_slog" "$PYTHON" "$S_DIR/search_resume_watchdog.py" status; then
        say STOP-deadline-status
        return 1
    fi
    if ! has_line "$_slog" "run_id=${RUN_ID}"; then
        say STOP-deadline-run-id
        return 1
    fi
    if ! has_line "$_slog" "d26_live=yes"; then
        say STOP-deadline-26-not-live
        return 1
    fi
    _d26=$("$AWK" -F= '/^deadline_26=/ { print $2; exit }' "$_slog")
    case "$_d26" in
        ''|*[!0-9]*) say STOP-deadline-26-margin; return 1 ;;
    esac
    _now=$(now_epoch) || return 1
    _left=$((_d26 - _now))
    if [ "$_left" -lt 120 ]; then
        say STOP-deadline-26-margin
        return 1
    fi
    say "DEADLINE-26-MARGIN left=${_left}"
    return 0
}

schema_sha() {
    "$SQLITE" -readonly "$1" "SELECT type||'|'||name||'|'||tbl_name||'|'||coalesce(sql,'') FROM sqlite_master ORDER BY type,name;" | "$SHASUM" -a 256 | "$AWK" '{print $1; exit}'
}

health_ok() {
    _log=${1:-}
    if [ -z "$_log" ]; then
        _log="$LOGS/att0w-health-${STAMP}.log"
        if [ -e "$_log" ]; then
            _log="$LOGS/att0w-health-${STAMP}-$$.log"
        fi
    fi
    run_group "$HEALTH_TIMEOUT_S" "$_log" "$CURL" -s -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH_URL" || return 1
    if ! "$AWK" 'BEGIN { f = 0 } $0 == "200" { f = 1 } END { exit f ? 0 : 1 }' "$_log"; then
        return 1
    fi
    return 0
}

s2_body() {
    if ! no_writer; then
        SEARCH_STATUS=not-restored
        return 1
    fi
    _st=$(lock_probe_file "$MA/mailroom.write.lock" 0)
    if [ "$_st" = "error" ]; then
        say LSOF-ERROR
        say STOP-s2-lock-held
        SEARCH_STATUS=not-restored
        return 1
    fi
    if [ "$_st" != "free" ]; then
        say STOP-s2-lock-held
        SEARCH_STATUS=not-restored
        return 1
    fi
    _plist="$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist"
    _brc=0
    "$LAUNCHCTL" bootstrap "gui/$(uid_now)" "$_plist" || _brc=$?
    say "bootstrap_rc=${_brc}"
    if [ "$_brc" != "0" ]; then
        _st=$(search_loaded)
        say_search_state "$_st"
        if [ "$_st" != "loaded" ]; then
            say STOP-s2-bootstrap
            SEARCH_STATUS=not-restored
            return 1
        fi
    fi
    _st=$(search_loaded)
    if [ "$_st" != "loaded" ]; then
        say STOP-s2-not-loaded
        SEARCH_STATUS=not-restored
        return 1
    fi
    if ! health_ok; then
        say STOP-s2-health
        SEARCH_STATUS=not-restored
        return 1
    fi
    if ! "$PYTHON" "$S_DIR/search_resume_watchdog.py" clear; then
        say STOP-s2-clear
        SEARCH_STATUS=not-restored
        return 1
    fi
    if [ -e "$MA/state/search_resume_after.epoch" ]; then
        say DEADLINE_STILL_PRESENT_STOP
        SEARCH_STATUS=not-restored
        return 1
    fi
    say deadline_cleared
    SEARCH_RESTORED=1
    SEARCH_STATUS=restored
    SEARCH_DOWN=0
    say S2-OK
    return 0
}

write_restore_sh() {
    _path=$1
    if [ -e "$_path" ]; then
        return 1
    fi
    cat > "$_path" <<'ENDSH'
set -u
set -o pipefail
set -C
BK=$1
STAGE=$2
SOR=$3
MA=$4
STAMP=$5
SQLITE=$6
STATBIN=$7
AWKBIN=$8
if [ -e "$STAGE" ]; then
    printf '%s\n' REFUSE-STAGE-EXISTS
    exit 1
fi
"$SQLITE" -readonly "$BK" ".backup '${STAGE}'" || exit 1
"$SQLITE" "$STAGE" "PRAGMA journal_mode=DELETE;" || exit 1
for side in wal shm journal; do
    src="$SOR-$side"
    dest="$MA/backups/mailroom-arr-aside-${STAMP}-${side}"
    if [ -e "$src" ]; then
        if [ -e "$dest" ]; then
            printf '%s\n' REFUSE-ASIDE-EXISTS
            exit 1
        fi
        mv "$src" "$dest" || exit 1
    fi
done
if [ -e "$SOR-wal" ]; then
    sz=$("$STATBIN" -f '%z %m %N' "$SOR-wal" | "$AWKBIN" '{print $1; exit}')
    if [ "${sz:-0}" -gt 0 ]; then
        for side in wal shm journal; do
            dest="$MA/backups/mailroom-arr-aside-${STAMP}-${side}"
            if [ -e "$dest" ]; then
                mv "$dest" "$SOR-$side" || exit 1
            fi
        done
        printf '%s\n' WAL-RETURNED
        exit 1
    fi
fi
OLD="$MA/backups/mailroom-arr-oldlive-${STAMP}.sqlite"
if [ -e "$OLD" ]; then
    printf '%s\n' REFUSE-OLDLIVE-EXISTS
    exit 1
fi
mv "$SOR" "$OLD" || exit 1
if ! mv "$STAGE" "$SOR"; then
    mv "$OLD" "$SOR" || true
    printf '%s\n' SWAP-FAILED
    exit 1
fi
if ! "$SQLITE" "$SOR" "PRAGMA quick_check;" | "$AWKBIN" 'BEGIN{ok=0} $0=="ok"{ok=1} END{exit ok?0:1}'; then
    bad="$MA/backups/mailroom-arr-bad-${STAMP}.sqlite"
    if [ ! -e "$bad" ]; then
        mv "$SOR" "$bad" || true
    fi
    if [ ! -e "$SOR" ]; then
        mv "$OLD" "$SOR" || true
    fi
    printf '%s\n' QUICK-CHECK-FAILED
    exit 1
fi
_jm=$("$SQLITE" "$SOR" "PRAGMA journal_mode=WAL;") || exit 1
if [ "$_jm" != "wal" ]; then
    printf '%s\n' WAL-RESTORE-FAILED
    exit 1
fi
printf '%s\n' RESTORE-SWAPPED
exit 0
ENDSH
}

arr_body() {
    _wstamp=$1
    _state="/tmp/att0w-state-${_wstamp}"
    _s1="/tmp/att0w-s1-${_wstamp}"
    if [ ! -f "$_state" ]; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    BK=$( "$AWK" -F= '/^bk_path=/ { print substr($0, index($0, "=") + 1); exit }' "$_state" )
    BK_SHA=$( "$AWK" -F= '/^bk_sha=/ { print $2; exit }' "$_state" )
    if [ -f "$_s1" ]; then
        RUN_ID=$( "$AWK" -F= '/^run_id=/ { print $2; exit }' "$_s1" )
        S_EPOCH=$( "$AWK" -F= '/^s_epoch=/ { print $2; exit }' "$_s1" )
    fi
    if [ -z "${BK:-}" ] || [ -z "${BK_SHA:-}" ] || [ ! -f "$BK" ]; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    _now=$("$SHASUM" -a 256 "$BK" | "$AWK" '{print $1; exit}') || return 1
    if [ "$_now" != "$BK_SHA" ]; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    say BK-SHA-OK
    if ! no_action_required; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    _runr="att0-L1-${_wstamp}-R"
    if ! "$PYTHON" "$S_DIR/search_resume_watchdog.py" write --run-id "$_runr"; then
        say "ROLLBACK-FAILED backup=${BK}"
        ARR_STATUS=failed
        return 1
    fi
    say DEADLINE-R-OK
    _st=$(search_loaded)
    if [ "$_st" = "unclear" ]; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    if [ "$_st" = "loaded" ]; then
        if ! search_not_disabled; then
            say ROLLBACK-NEEDED
            ARR_STATUS=needed
            return 1
        fi
        if ! "$LAUNCHCTL" bootout "$(gui_target com.mailroom.ask-mail-serve)"; then
            say "ROLLBACK-FAILED backup=${BK}"
            ARR_STATUS=failed
            return 1
        fi
        SEARCH_DOWN=1
    fi
    _st=$(search_loaded)
    if [ "$_st" != "unloaded" ]; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    if ! no_writer; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    if ! wait_lock_free; then
        say "ROLLBACK-NEEDED lock-held"
        ARR_STATUS=needed
        return 1
    fi
    _st=$(lock_probe_file "$MA/mailroom.write.lock" 0)
    if [ "$_st" = "error" ]; then
        say LSOF-ERROR
        say "ROLLBACK-NEEDED lock-held"
        ARR_STATUS=needed
        return 1
    fi
    if [ "$_st" != "free" ]; then
        say "ROLLBACK-NEEDED lock-held"
        ARR_STATUS=needed
        return 1
    fi
    if ! flock_probe; then
        say "ROLLBACK-NEEDED lock-held"
        ARR_STATUS=needed
        return 1
    fi
    if ! hits_clear "/tmp/att0-arr-hits-${_wstamp}-$$.log"; then
        say ROLLBACK-NEEDED
        ARR_STATUS=needed
        return 1
    fi
    _stage="$MA/backups/mailroom-arr-stage-${_wstamp}.sqlite"
    _sh="/tmp/att0-restore-${_wstamp}.sh"
    write_restore_sh "$_sh" || {
        say "ROLLBACK-FAILED backup=${BK}"
        ARR_STATUS=failed
        return 1
    }
    _rlog="$LOGS/att0-arr-${_wstamp}.log"
    WROTE_SOR=1
    if ! MAILROOM_SEARCH_RESUME_RUN_ID="$_runr" run_group "$A2_TIMEOUT_S" "$_rlog" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose att0-restore -- \
        /bin/bash "$_sh" "$BK" "$_stage" "$SOR" "$MA" "$_wstamp" "$SQLITE" "$STAT" "$AWK"
    then
        wait_lock_free || true
        say "ROLLBACK-FAILED backup=${BK}"
        ARR_STATUS=failed
        return 1
    fi
    if ! has_line "$_rlog" "RESTORE-SWAPPED"; then
        say "ROLLBACK-FAILED backup=${BK}"
        ARR_STATUS=failed
        return 1
    fi
    if ! arr_verify "$_wstamp"; then
        say "ROLLBACK-FAILED verify backup=${BK}"
        ARR_STATUS=failed
        return 1
    fi
    _stout=$("$PYTHON" "$S_DIR/search_resume_watchdog.py" status 2>/dev/null || true)
    say "status-logged"
    if text_has "$_stout" "d26_live=no"; then
        say D26-DROPPED
    else
        say D26-STILL-LIVE
    fi
    ARR_STATUS=ran
    if ! printf '%s\n' "$_wstamp" > "/tmp/att0r-verified-${_wstamp}.OK"; then
        say "ROLLBACK-FAILED verify backup=${BK}"
        ARR_STATUS=failed
        return 1
    fi
    say ROLLED-BACK-VERIFIED
    say ROLLBACK-DONE
    return 0
}

# W5. R-ARR matches R-BEFORE, journal_mode is wal, and messages, max
# rowid, and G match the pre-window values. No second swap on a miss.
arr_verify() {
    _wstamp=$1
    _before="$LOGS/att0w-r1v2-before-${_wstamp}.tsv"
    _state="/tmp/att0w-state-${_wstamp}"
    if [ ! -f "$_before" ] || [ ! -f "$_state" ]; then
        return 1
    fi
    if ! r1v2_run arr ARR "$SOR" "$EXPECT_PRE" "$_wstamp"; then
        return 1
    fi
    _arr="$LOGS/att0w-r1v2-arr-${_wstamp}.tsv"
    if ! /usr/bin/cmp -s "$_before" "$_arr"; then
        return 1
    fi
    _jm=$("$SQLITE" -readonly "$SOR" "PRAGMA journal_mode;") || return 1
    if [ "$_jm" != "wal" ]; then
        return 1
    fi
    _pm=$("$AWK" -F= '/^pre_messages=/ { print $2; exit }' "$_state")
    _pr=$("$AWK" -F= '/^pre_max_rowid=/ { print $2; exit }' "$_state")
    _pg=$("$AWK" -F= '/^pre_gone=/ { print $2; exit }' "$_state")
    case "$_pm" in ''|*[!0-9]*) return 1 ;; esac
    case "$_pr" in ''|*[!0-9]*) return 1 ;; esac
    case "$_pg" in ''|*[!0-9]*) return 1 ;; esac
    _mc=$("$SQLITE" -readonly "$SOR" "SELECT COUNT(*) FROM messages;") || return 1
    _mr=$("$SQLITE" -readonly "$SOR" "SELECT coalesce(MAX(rowid),0) FROM messages;") || return 1
    if [ "$_mc" != "$_pm" ] || [ "$_mr" != "$_pr" ]; then
        return 1
    fi
    _glog="$LOGS/att0-arr-gone-${_wstamp}.log"
    if ! run_d_query "$SOR" "$_glog"; then
        return 1
    fi
    if [ "$D_GONE" != "$_pg" ]; then
        return 1
    fi
    return 0
}

safe_state() {
    _kind=$1
    _ok=1
    _st=$(search_loaded)
    if [ "$_st" = "unclear" ]; then
        _ok=0
    elif [ "$_st" != "loaded" ]; then
        _ok=0
    fi
    if ! health_ok "/tmp/att0-safe-health-${STAMP}-$$.log"; then
        _ok=0
    fi
    _st=$(lock_probe_file "$MA/mailroom.write.lock" 0)
    if [ "$_st" = "error" ]; then
        say LSOF-ERROR
        _ok=0
    elif [ "$_st" != "free" ]; then
        _ok=0
    elif ! flock_probe; then
        _ok=0
    fi
    _st=$(lock_probe_file "$MA/mailroom.daily.lock" 1)
    if [ "$_st" = "error" ]; then
        say LSOF-ERROR
        _ok=0
    elif [ "$_st" != "free" ]; then
        _ok=0
    fi
    _st=$(pgrep_state "$WRITER_PAT")
    if [ "$_st" = "error" ]; then
        say PGREP-ERROR
        _ok=0
    elif [ "$_st" != "absent" ]; then
        _ok=0
    fi
    _st=$(pgrep_state "$CURL_PAT")
    if [ "$_st" = "error" ]; then
        say PGREP-ERROR
        _ok=0
    elif [ "$_st" != "absent" ]; then
        _ok=0
    fi
    if ! no_action_required; then
        _ok=0
    fi
    if ! hits_clear "/tmp/att0-safe-hits-${STAMP}-$$.log"; then
        _ok=0
    fi
    if [ "$_kind" = "held" ]; then
        daily_print_state com.mailroom.daily || _ok=0
        if [ "$DAILY_PRINT_STATE" = "unclear" ]; then
            say "DAILY-UNCLEAR rc=${DAILY_PRINT_RC}"
            _ok=0
        elif [ "$DAILY_PRINT_STATE" != "not-loaded" ]; then
            _ok=0
        fi
        if ! daily_disabled; then
            _ok=0
        fi
    else
        daily_print_state com.mailroom.daily || _ok=0
        if [ "$DAILY_PRINT_STATE" = "unclear" ]; then
            say "DAILY-UNCLEAR rc=${DAILY_PRINT_RC}"
            _ok=0
        elif [ "$DAILY_PRINT_STATE" != "loaded" ] || ! text_has "$DAILY_PRINT_OUT" "last exit code = 0"; then
            _ok=0
        fi
        _dis=$("$LAUNCHCTL" print-disabled "gui/$(uid_now)" 2>&1)
        _prc=$?
        if [ "$_prc" -ne 0 ]; then
            say STOP-print-disabled
            _ok=0
        elif printf '%s\n' "$_dis" | "$AWK" '{
            gsub(/^[ \t]+|[ \t]+$/, "")
            if ($0 == "\"com.mailroom.daily\" => disabled" || $0 == "\"com.mailroom.daily\" => true") found = 1
        } END { exit found ? 0 : 1 }'; then
            _ok=0
        fi
    fi
    if [ "$_ok" = "1" ]; then
        say SAFE-STATE
        return 0
    fi
    say SAFE-STATE-FAIL
    return 1
}

emit_result() {
    printf '%s\n' "${PFX} RESULT ar-r=${ARR_STATUS} search=${SEARCH_STATUS}"
}

cleanup() {
    trap - EXIT INT TERM
    if [ "$CLEANED" = "1" ]; then
        exit "$WANTED_RC"
    fi
    CLEANED=1
    if [ "$MODE" = "window" ] && [ "$WANTED_RC" != "0" ] && [ "$WROTE_SOR" = "1" ] && [ "$ARR_STATUS" = "not-run" ]; then
        arr_body "$STAMP" || true
        wait_lock_free || true
    fi
    if [ "$SEARCH_DOWN" = "1" ] && [ "$SEARCH_RESTORED" != "1" ]; then
        s2_body || true
    fi
    if [ "$MODE" = "restore-daily" ] && [ "$WANTED_RC" = "0" ]; then
        safe_state open || WANTED_RC=4
    else
        safe_state held || {
            if [ "$WANTED_RC" = "0" ] || [ "$WANTED_RC" = "1" ]; then
                WANTED_RC=4
            fi
        }
    fi
    if [ "$SUMMARY_DONE" != "1" ]; then
        emit_result
        printf '%s\n' "${PFX} SUMMARY stamp=${STAMP:-unknown} exit=${WANTED_RC}"
        if [ -n "${TRANSCRIPT:-}" ] && [ -f "$TRANSCRIPT" ]; then
            printf '%s\n' "${PFX} RESULT ar-r=${ARR_STATUS} search=${SEARCH_STATUS}" >> "$TRANSCRIPT"
            printf '%s\n' "${PFX} SUMMARY stamp=${STAMP:-unknown} exit=${WANTED_RC}" >> "$TRANSCRIPT"
        fi
        SUMMARY_DONE=1
    fi
    exit "$WANTED_RC"
}

on_signal() {
    WANTED_RC=3
    exit 3
}

meta_fill_cmd() {
    _db=$1
    shift
    "$ENVBIN" MAILROOM_IMAP_HOST=imap.mail.me.com MAILROOM_IMAP_USER="$IMAP_USER" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose 'att0 meta fill' -- \
        "$ENVBIN" -u MAILROOM_SEARCH_RESUME_RUN_ID \
        MAILROOM_IMAP_HOST=imap.mail.me.com MAILROOM_IMAP_USER="$IMAP_USER" \
        PYTHONPATH="${S_DIR}:${S_DIR}/attachments" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$S_DIR/attachments/meta_fill.py" --db "$_db" --source imap \
        --allow-mailroom-sqlite "$@"
}

hits_clear() {
    _log=${1:-"$LOGS/att0-hits-entry-${STAMP}.log"}
    if ! run_group 30 "$_log" "$PYTHON" -c 'import sys
sys.path.insert(0, sys.argv[1])
import sor_writer_gate as g
h = g.rem_process_hits()
sys.stdout.write("hits_count=%d pids=%s\n" % (len(h), [p for p,_ in h]))' "$S_DIR"; then
        say STOP-hits
        return 1
    fi
    if ! has_line "$_log" "hits_count=0 pids=[]"; then
        say STOP-hits
        return 1
    fi
    say "hits_count=0 pids=[]"
    say HITS-OK
    return 0
}

watchdog_missing() {
    _log=${1:-"$LOGS/att0-watchdog-entry-${STAMP}.log"}
    run_group 30 "$_log" "$PYTHON" "$S_DIR/search_resume_watchdog.py" status || true
    if [ "$RUN_HOW" != "normal" ] || [ "${RUN_GROUP_EMPTY:-0}" != "1" ] || [ "${RUN_FILL_CURL:-1}" != "0" ]; then
        say STOP-watchdog-status
        return 1
    fi
    if [ "$RUN_CHILD_RC" = "1" ] && has_line "$_log" "status=missing"; then
        say WATCHDOG-MISSING-OK
        return 0
    fi
    say STOP-watchdog-not-missing
    return 1
}

do_window() {
    P_STAMP=$1
    MODE=window
    STAMP=$(new_stamp) || { WANTED_RC=1; return; }
    printf '%s\n' "${PFX} STAMP=${STAMP}"
    if ! stamp_ok "$P_STAMP"; then
        say STOP-bad-p-stamp
        WANTED_RC=2
        return
    fi
    if ! env_check; then WANTED_RC=1; return; fi
    if ! phasep_markers_ok; then WANTED_RC=1; return; fi
    if ! step0_locks; then WANTED_RC=1; return; fi
    "$MKDIR" -p "$LOGS" "$MA/backups" "$MA/dryrun" "$MA/state" || { WANTED_RC=1; return; }
    TRANSCRIPT="$LOGS/att0-window-${STAMP}.transcript"
    if [ -e "$TRANSCRIPT" ]; then
        say STOP-transcript-exists
        WANTED_RC=1
        return
    fi
    printf '%s\n' "${PFX} STAMP=${STAMP}" >> "$TRANSCRIPT" || { WANTED_RC=1; return; }
    say "P_STAMP=${P_STAMP}"
    if ! entry_dp_ok; then WANTED_RC=1; return; fi
    if ! sha_five_ok; then WANTED_RC=1; return; fi
    if ! daily_not_loaded; then WANTED_RC=1; return; fi
    if ! daily_disabled; then WANTED_RC=1; return; fi
    if ! no_writer; then WANTED_RC=1; return; fi
    if ! locks_ok; then WANTED_RC=1; return; fi
    if ! hits_clear; then WANTED_RC=1; return; fi
    if ! watchdog_missing; then WANTED_RC=1; return; fi
    if ! claim_window; then WANTED_RC=1; return; fi
    if ! no_action_required; then WANTED_RC=1; return; fi
    if ! ask_hash_record; then WANTED_RC=1; return; fi
    if ! sor_1c; then WANTED_RC=1; return; fi
    if ! legacy_rows_ok; then WANTED_RC=1; return; fi
    if ! imap_user_len; then WANTED_RC=1; return; fi
    REH_DB="$MA/dryrun/att0-window-${STAMP}/mailroom.sqlite"
    if ! copy_db "$SOR" "$REH_DB"; then
        say STOP-rehearsal-copy
        WANTED_RC=1
        return
    fi
    _mlog="$LOGS/att0w-reh-migrate-${STAMP}.log"
    if ! run_group "$A2_TIMEOUT_S" "$_mlog" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose att0-migrate -- \
        "$ENVBIN" -u MAILROOM_SEARCH_RESUME_RUN_ID \
        SOR_FORCE_LIVE_CHECKS=1 PYTHONPATH="${S_DIR}:${S_DIR}/attachments" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$S_DIR/attachments/migrate_att0_schema.py" --db "$REH_DB" --allow-mailroom-sqlite
    then
        say STOP-rehearsal-migrate
        WANTED_RC=1
        return
    fi
    if ! log_ok_migrate "$_mlog" first; then WANTED_RC=1; return; fi
    # In-window 3.5L-b rehearsal fill. Own process group, live timer parent.
    # This is the Keychain and IMAP gate before S. There is no access probe.
    _flog="$LOGS/att0w-reh-fill-${STAMP}.log"
    if ! run_group "$REHEARSAL_TIMEOUT_S" "$_flog" \
        "$ENVBIN" MAILROOM_IMAP_HOST=imap.mail.me.com MAILROOM_IMAP_USER="$IMAP_USER" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose 'att0 meta fill' -- \
        "$ENVBIN" -u MAILROOM_SEARCH_RESUME_RUN_ID \
        SOR_FORCE_LIVE_CHECKS=1 MAILROOM_IMAP_HOST=imap.mail.me.com MAILROOM_IMAP_USER="$IMAP_USER" \
        PYTHONPATH="${S_DIR}:${S_DIR}/attachments" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$S_DIR/attachments/meta_fill.py" --db "$REH_DB" --source imap \
        --max-messages 0 --timeout 0 --apply --allow-mailroom-sqlite
    then
        if has_line "$_flog" "falling back" || [ "${RUN_FALLBACK:-0}" = "1" ]; then
            say STOP-falling-back
        else
            say STOP-rehearsal-fill
        fi
        WANTED_RC=1
        return
    fi
    if ! log_ok_fill "$_flog" 1; then WANTED_RC=1; return; fi
    R_MESSAGES=$FILL_MESSAGES
    R_ERRORS=$FILL_ERRORS
    R_CAPPED=$FILL_CAPPED
    R_ELIGIBLE=$FILL_ELIGIBLE
    _wlog="$LOGS/att0w-dw-${STAMP}.log"
    if ! run_d_query "$REH_DB" "$_wlog"; then
        say STOP-d-w-query
        WANTED_RC=1
        return
    fi
    D_W=$D_TOTAL
    if ! d_check "$_wlog"; then WANTED_RC=1; return; fi
    BK="$MA/backups/mailroom-pre-att0-window-${STAMP}.sqlite"
    if [ -e "$BK" ]; then
        say STOP-backup-exists
        WANTED_RC=1
        return
    fi
    if ! "$SQLITE" -readonly "$SOR" ".backup '${BK}'"; then
        say STOP-backup
        WANTED_RC=1
        return
    fi
    # The backup keeps WAL mode but has no -wal or -shm, so sqlite3 -readonly
    # cannot open it (SQLITE_CANTOPEN). Switch it to rollback mode before
    # its sha is taken; restore sets WAL back on the live SoR.
    _bjm=$("$SQLITE" "$BK" "PRAGMA journal_mode=DELETE;") || _bjm=
    if [ "$_bjm" != "delete" ] || [ -e "${BK}-wal" ] || [ -e "${BK}-shm" ]; then
        say STOP-backup-journal
        WANTED_RC=1
        return
    fi
    BK_SHA=$("$SHASUM" -a 256 "$BK" | "$AWK" '{print $1; exit}') || { WANTED_RC=1; return; }
    if ! "$SQLITE" -readonly "$BK" "PRAGMA quick_check;" | "$AWK" 'BEGIN{ok=0} $0=="ok"{ok=1} END{exit ok?0:1}'; then
        say STOP-backup-quick
        WANTED_RC=1
        return
    fi
    if ! printf '%s\n' "bk_path=${BK}" "bk_sha=${BK_SHA}" "p_stamp=${P_STAMP}" "window_stamp=${STAMP}" > "/tmp/att0w-state-${STAMP}"; then
        say STOP-state-write
        WANTED_RC=1
        return
    fi
    say BACKUP-OK
    if ! r1_sor0_before; then
        WANTED_RC=1
        return
    fi
    RUN_ID="att0-L1-${STAMP}"
    if ! search_not_disabled; then
        WANTED_RC=1
        return
    fi
    S_EPOCH=$(now_epoch) || { WANTED_RC=1; return; }
    _wrc=0
    "$PYTHON" "$S_DIR/search_resume_watchdog.py" write --run-id "$RUN_ID" || _wrc=$?
    say "write_rc=${_wrc}"
    if [ "$_wrc" != "0" ]; then
        say STOP-deadline-write
        WANTED_RC=1
        return
    fi
    say DEADLINE-OK
    _brc=0
    "$LAUNCHCTL" bootout "$(gui_target com.mailroom.ask-mail-serve)" || _brc=$?
    say "bootout_rc=${_brc}"
    if [ "$_brc" != "0" ]; then
        say STOP-bootout
        WANTED_RC=1
        return
    fi
    SEARCH_DOWN=1
    SEARCH_STATUS=down
    _st=$(search_loaded)
    say_search_state "$_st"
    if [ "$_st" != "unloaded" ]; then
        say STOP-search-still-loaded
        WANTED_RC=1
        return
    fi
    say SEARCH-BOOTED-OUT
    if ! deadline_26_margin_ok; then
        WANTED_RC=1
        return
    fi
    if ! printf '%s\n' "s_epoch=${S_EPOCH}" "run_id=${RUN_ID}" > "/tmp/att0w-s1-${STAMP}"; then
        say STOP-s1-write
        WANTED_RC=1
        return
    fi
    if ! budget_ok a2 "$A2_TIMEOUT_S"; then WANTED_RC=1; return; fi
    WROTE_SOR=1
    _a2log="$LOGS/att0w-a2-${STAMP}.log"
    if ! MAILROOM_SEARCH_RESUME_RUN_ID="$RUN_ID" run_group "$A2_TIMEOUT_S" "$_a2log" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose att0-migrate -- \
        "$ENVBIN" MAILROOM_SEARCH_RESUME_RUN_ID="$RUN_ID" \
        PYTHONPATH="${S_DIR}:${S_DIR}/attachments" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$S_DIR/attachments/migrate_att0_schema.py" --db "$SOR" --allow-mailroom-sqlite
    then
        wait_lock_free || true
        say STOP-a2
        WANTED_RC=3
        return
    fi
    if ! log_ok_migrate "$_a2log" first; then
        WANTED_RC=3
        return
    fi
    say A2-OK
    _a2rlog="$LOGS/att0w-a2-rerun-${STAMP}.log"
    if ! run_group "$A2_TIMEOUT_S" "$_a2rlog" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose att0-migrate -- \
        "$ENVBIN" -u MAILROOM_SEARCH_RESUME_RUN_ID \
        PYTHONPATH="${S_DIR}:${S_DIR}/attachments" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$S_DIR/attachments/migrate_att0_schema.py" --db "$SOR" --allow-mailroom-sqlite
    then
        wait_lock_free || true
        say STOP-a2-rerun
        WANTED_RC=3
        return
    fi
    if ! log_ok_migrate "$_a2rlog" rerun; then
        WANTED_RC=3
        return
    fi
    say A2-RERUN-OK
    _live_schema=$(schema_sha "$SOR") || { say STOP-schema-sha; WANTED_RC=3; return; }
    _reh_schema=$(schema_sha "$REH_DB") || { say STOP-schema-sha; WANTED_RC=3; return; }
    if [ -z "$_live_schema" ] || [ "$_live_schema" != "$_reh_schema" ]; then
        say STOP-schema-sha
        WANTED_RC=3
        return
    fi
    say SCHEMA-SHA-OK
    if ! r1_posta2; then
        WANTED_RC=3
        return
    fi
    # Free-lock gap before A3. A2's writer-lock drop already removed +26.
    # window, rollback, and restore-daily do not arm +26, and they do
    # not arm any other restore timer, in this gap. status is the only
    # watchdog call here. The only deadline is the S+50 file plus the
    # S+51 budget check below.
    _glog="$LOGS/att0w-gap-${STAMP}.log"
    if ! run_group 30 "$_glog" "$PYTHON" "$S_DIR/search_resume_watchdog.py" status; then
        say STOP-gap-status
        WANTED_RC=3
        return
    fi
    if ! has_line "$_glog" "d26_live=no"; then
        say STOP-gap-d26-live
        WANTED_RC=3
        return
    fi
    say GAP-D26-DROPPED
    _st=$(search_loaded)
    say_search_state "$_st"
    if [ "$_st" != "unloaded" ]; then
        say STOP-a3-search-loaded
        WANTED_RC=3
        return
    fi
    _a3t=$(a3_timeout)
    if [ "$_a3t" -lt 1 ]; then
        say STOP-a3-no-time
        WANTED_RC=3
        return
    fi
    if ! budget_ok a3 "$_a3t"; then WANTED_RC=3; return; fi
    # Live fill after A2. Same live timer parent as the rehearsal fill.
    _a3log="$LOGS/att0w-a3-${STAMP}.log"
    if ! run_group "$_a3t" "$_a3log" \
        "$ENVBIN" MAILROOM_IMAP_HOST=imap.mail.me.com MAILROOM_IMAP_USER="$IMAP_USER" \
        "$PYTHON" "$S_DIR/with_writer_lock.py" --purpose 'att0 meta fill' -- \
        "$ENVBIN" -u MAILROOM_SEARCH_RESUME_RUN_ID \
        MAILROOM_IMAP_HOST=imap.mail.me.com MAILROOM_IMAP_USER="$IMAP_USER" \
        PYTHONPATH="${S_DIR}:${S_DIR}/attachments" PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$S_DIR/attachments/meta_fill.py" --db "$SOR" --source imap \
        --max-messages 0 --timeout 0 --apply --allow-mailroom-sqlite
    then
        wait_lock_free || true
        if has_line "$_a3log" "falling back" || [ "${RUN_FALLBACK:-0}" = "1" ]; then
            say STOP-falling-back
        else
            say STOP-a3
        fi
        WANTED_RC=3
        return
    fi
    if ! log_ok_fill "$_a3log" 1; then
        WANTED_RC=3
        return
    fi
    A_MESSAGES=$FILL_MESSAGES
    A_ERRORS=$FILL_ERRORS
    A_CAPPED=$FILL_CAPPED
    A_ELIGIBLE=$FILL_ELIGIBLE
    say A3-OK
    _dalog="$LOGS/att0w-da-${STAMP}.log"
    if ! run_d_query "$SOR" "$_dalog"; then
        say STOP-d-a-query
        WANTED_RC=3
        return
    fi
    D_A=$D_TOTAL
    if ! d_check_a3 "$_dalog"; then
        WANTED_RC=3
        return
    fi
    if ! budget_ok step6 "$STEP6_TIMEOUT_S"; then WANTED_RC=3; return; fi
    _before=$("$AWK" -F= '/^bk_sha=/ { print $2; exit }' "/tmp/att0w-state-${STAMP}")
    _after=$("$SHASUM" -a 256 "$SOR" | "$AWK" '{print $1; exit}') || { WANTED_RC=3; return; }
    _qlog="$LOGS/att0w-r1-${STAMP}.log"
    if ! run_group "$STEP6_TIMEOUT_S" "$_qlog" "$SQLITE" -readonly "$SOR" "PRAGMA quick_check;"; then
        say STOP-r1
        WANTED_RC=3
        return
    fi
    if ! has_line "$_qlog" "ok"; then
        say STOP-r1-quick
        WANTED_RC=3
        return
    fi
    say "R1-DIFF before=${_before} after=${_after}"
    if ! a4_counts_ok; then
        WANTED_RC=3
        return
    fi
    if ! r1_after; then
        WANTED_RC=3
        return
    fi
    say R1-OK
    if ! ask_hash_same; then WANTED_RC=3; return; fi
    if ! s2_body; then
        WANTED_RC=3
        return
    fi
    if ! printf '%s\n' "$STAMP" > "/tmp/att0w-done-${STAMP}.OK"; then
        say STOP-done-marker
        WANTED_RC=3
        return
    fi
    say WINDOW-DONE
    WANTED_RC=0
}

do_rollback() {
    W_STAMP=$1
    MODE=rollback
    STAMP=$(new_stamp) || { WANTED_RC=1; return; }
    printf '%s\n' "${PFX} STAMP=${STAMP}"
    if ! stamp_ok "$W_STAMP"; then
        say STOP-bad-window-stamp
        WANTED_RC=2
        return
    fi
    say "WINDOW_STAMP=${W_STAMP}"
    "$MKDIR" -p "$LOGS" "$MA/backups" "$MA/state" || { WANTED_RC=1; return; }
    if ! env_check; then WANTED_RC=1; return; fi
    if ! no_writer; then WANTED_RC=1; return; fi
    if ! daily_not_loaded; then WANTED_RC=1; return; fi
    if ! daily_disabled; then WANTED_RC=1; return; fi
    if ! locks_ok; then WANTED_RC=1; return; fi
    if ! no_action_required; then WANTED_RC=1; return; fi
    if ! arr_body "$W_STAMP"; then
        if [ "$ARR_STATUS" = "not-run" ]; then
            ARR_STATUS=failed
        fi
        WANTED_RC=3
        return
    fi
    if ! s2_body; then
        WANTED_RC=3
        return
    fi
    WANTED_RC=0
}

plist_checks() {
    _pl="$HOME/Library/LaunchAgents/com.mailroom.daily.plist"
    _gate=$("$SHASUM" -a 256 "$S_DIR/sor_writer_gate.py" | "$AWK" '{print $1; exit}') || return 1
    if [ "$_gate" != "$SHA_GATE" ]; then
        say STOP-gate-not-installed
        return 1
    fi
    say GATE-NEW-OK
    _wrap=$("$SHASUM" -a 256 "$S_DIR/with_writer_lock.py" | "$AWK" '{print $1; exit}') || return 1
    if [ "$_wrap" != "$SHA_WRAPPER" ]; then
        say STOP-wrapper-not-installed
        return 1
    fi
    say WRAPPER-NEW-OK
    _wd=$("$SHASUM" -a 256 "$S_DIR/search_resume_watchdog.py" | "$AWK" '{print $1; exit}') || return 1
    if [ "$_wd" != "$SHA_WATCHDOG" ]; then
        say STOP-watchdog-not-installed
        return 1
    fi
    say WATCHDOG-OK
    if ! "$GREP" -q '^LOCK_TOKEN_ENV = ' "$S_DIR/with_writer_lock.py"; then
        say STOP-no-LOCK_TOKEN_ENV
        return 1
    fi
    say LOCK-TOKEN-ENV-OK
    _pp=$("$PLUTIL" -p "$_pl" 2>/dev/null || true)
    if text_has "$_pp" "MAILROOM_DAILY_LOCK"; then
        say STOP-daily-lock-override
        return 1
    fi
    say NO-LOCK-OVERRIDE-OK
    if text_has "$_pp" "MAILROOM_WRITER_LOCK_TOKEN"; then
        say STOP-token-in-plist
        return 1
    fi
    say NO-TOKEN-IN-PLIST-OK
    if ! text_has "$_pp" "\"MAILARCHIVE\" => \"${MA}\""; then
        say STOP-mailarchive-not-default
        return 1
    fi
    say MAILARCHIVE-OK
    if ! "$PLUTIL" -lint -s "$_pl" >/dev/null 2>&1; then
        say STOP-plist-lint
        return 1
    fi
    say PLIST-LINT-OK
    _sha12=$("$SHASUM" -a 256 "$_pl" | "$AWK" '{print substr($1,1,12); exit}') || return 1
    if [ "$_sha12" != "$PLIST_SHA12" ]; then
        say STOP-plist-changed
        return 1
    fi
    say PLIST-SAME-OK
    return 0
}

do_restore_daily() {
    W_STAMP=$1
    MODE=restore-daily
    STAMP=$(new_stamp) || { WANTED_RC=1; return; }
    printf '%s\n' "${PFX} STAMP=${STAMP}"
    if ! stamp_ok "$W_STAMP"; then
        say STOP-bad-window-stamp
        WANTED_RC=2
        return
    fi
    say "WINDOW_STAMP=${W_STAMP}"
    # A7. The mechanical gate is the window PASS marker or the verified
    # AR-R marker. Mailroom's posted run verdict is not visible here.
    if [ ! -f "/tmp/att0w-done-${W_STAMP}.OK" ] && [ ! -f "/tmp/att0r-verified-${W_STAMP}.OK" ]; then
        say STOP-no-window-or-arr-marker
        WANTED_RC=1
        return
    fi
    say WINDOW-OR-ARR-MARKER-OK
    "$MKDIR" -p "$LOGS" || { WANTED_RC=1; return; }
    if ! env_check; then WANTED_RC=1; return; fi
    if ! sha_five_ok; then WANTED_RC=1; return; fi
    if ! no_writer; then WANTED_RC=1; return; fi
    if ! locks_ok; then WANTED_RC=1; return; fi
    if ! no_action_required; then WANTED_RC=1; return; fi
    if ! hits_clear "/tmp/att0-daily-hits-${STAMP}-$$.log"; then WANTED_RC=1; return; fi
    if ! plist_checks; then WANTED_RC=1; return; fi
    if ! daily_not_loaded; then WANTED_RC=1; return; fi
    if ! stamps_ok; then WANTED_RC=1; return; fi
    if ! daily_disabled; then WANTED_RC=1; return; fi
    _bk="$MA/backups/${P5_LEFTOVER_NAME}"
    if [ -e "$_bk" ]; then
        say P5-BACKUP-PRESENT-LEAVE-IN-PLACE
    else
        say P5-NO-BACKUP-FILE
    fi
    say p5_sidecar_check_done
    _uid=$(uid_now)
    if ! "$LAUNCHCTL" enable "gui/${_uid}/com.mailroom.daily"; then
        say STOP-enable-failed
        WANTED_RC=3
        return
    fi
    say "enable_rc=0"
    _dis=$("$LAUNCHCTL" print-disabled "gui/${_uid}" 2>&1)
    _prc=$?
    if [ "$_prc" -ne 0 ]; then
        say STOP-print-disabled
        WANTED_RC=3
        return
    fi
    if printf '%s\n' "$_dis" | "$AWK" '{
        gsub(/^[ \t]+|[ \t]+$/, "")
        if ($0 == "\"com.mailroom.daily\" => disabled" || $0 == "\"com.mailroom.daily\" => true") found = 1
    } END { exit found ? 0 : 1 }'; then
        say STOP-still-disabled
        WANTED_RC=3
        return
    fi
    say ENABLED-OK
    _st=$(pgrep_state "$WRITER_PAT")
    _lk=$(lock_probe_file "$MA/mailroom.daily.lock" 1)
    if [ "$_st" != "absent" ] || [ "$_lk" != "free" ]; then
        say STOP-daily-running-no-bootstrap
        WANTED_RC=3
        return
    fi
    T0=$(now_epoch) || { WANTED_RC=3; return; }
    if ! "$LAUNCHCTL" bootstrap "gui/${_uid}" "$HOME/Library/LaunchAgents/com.mailroom.daily.plist"; then
        say STOP-bootstrap-failed-no-retry
        WANTED_RC=3
        return
    fi
    say "bootstrap_rc=0 T0=${T0}"
    "$SLEEP" 10
    _seen=0
    _max=20
    while [ "$_seen" -lt "$_max" ]; do
        _st=$(pgrep_state "$WRITER_PAT")
        if [ "$_st" = "present" ]; then
            say STILL-RUNNING
            "$SLEEP" 120
            _seen=$((_seen + 1))
            continue
        fi
        if [ "$_st" != "absent" ]; then
            say PGREP-ERROR
            say STOP-pgrep-error
            WANTED_RC=3
            return
        fi
        say CHAIN-EXITED
        daily_print_state com.mailroom.daily || {
            say DAILY-UNCLEAR
            say STOP-exit-nonzero
            WANTED_RC=3
            return
        }
        if [ "$DAILY_PRINT_STATE" = "unclear" ]; then
            say "DAILY-UNCLEAR rc=${DAILY_PRINT_RC}"
            say STOP-exit-nonzero
            WANTED_RC=3
            return
        fi
        if [ "$DAILY_PRINT_STATE" != "loaded" ] || ! text_has "$DAILY_PRINT_OUT" "last exit code = 0"; then
            say STOP-exit-nonzero
            WANTED_RC=3
            return
        fi
        say EXIT-0-OK
        _m=$("$STAT" -f '%m' "$LOGS/last_daily_rag_ok" 2>/dev/null || echo 0)
        if [ "$_m" -le "$T0" ]; then
            say STOP-stamp-not-moved
            WANTED_RC=3
            return
        fi
        say STAMP-MOVED-OK
        say RESTORE-OK
        WANTED_RC=0
        return
    done
    say STOP-restore-poll
    WANTED_RC=3
}

# Quiet helpers for report. They do not print and they do not write.
is_uint() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

read_total() {
    _file=$1
    D_PRESENT=
    D_MISSING=
    D_TOTAL=
    D_GONE=
    D_SCANNED_GONE=
    D_UNSCANNED=
    if [ ! -f "$_file" ]; then
        return 1
    fi
    _tline=$("$AWK" '/^TOTAL / { print; exit }' "$_file" 2>/dev/null || true)
    parse_d_line "$_tline" || return 1
    return 0
}

fill_gates_ok() {
    _log=$1
    if [ ! -f "$_log" ]; then
        return 1
    fi
    if has_line "$_log" "falling back"; then
        return 1
    fi
    if ! kv_one "$_log" partial 0; then
        return 1
    fi
    if ! summary_curl_ok "$_log"; then
        return 1
    fi
    if ! kv_one "$_log" bytes_stored 0; then
        return 1
    fi
    if ! kv_one "$_log" capped 0; then
        return 1
    fi
    if ! kv_one "$_log" uidvalidity_mismatch 0; then
        return 1
    fi
    if ! kv_one "$_log" literal_dropped 0; then
        return 1
    fi
    if ! kv_one "$_log" literal_truncated 0; then
        return 1
    fi
    if ! kv_one "$_log" filenames 0; then
        return 1
    fi
    return 0
}

fill_take() {
    _log=$1
    FILL_MESSAGES=
    FILL_ERRORS=
    FILL_CAPPED=
    FILL_ELIGIBLE=
    if [ ! -f "$_log" ]; then
        return 1
    fi
    FILL_MESSAGES=$("$AWK" -F= '/^messages=/ { print $2; exit }' "$_log" 2>/dev/null || true)
    FILL_ERRORS=$("$AWK" -F= '/^errors=/ { print $2; exit }' "$_log" 2>/dev/null || true)
    FILL_CAPPED=$("$AWK" -F= '/^capped=/ { print $2; exit }' "$_log" 2>/dev/null || true)
    FILL_ELIGIBLE=$("$AWK" -F= '/^eligible=/ { print $2; exit }' "$_log" 2>/dev/null || true)
    is_uint "$FILL_MESSAGES" || return 1
    is_uint "$FILL_ERRORS" || return 1
    is_uint "$FILL_CAPPED" || return 1
    is_uint "$FILL_ELIGIBLE" || return 1
    return 0
}

# Read-only verdict from the window's own logs and /tmp markers.
# A2-A4 numbers are recomputed from att0w-dp/dw/da, the fill logs, and
# the A4 log. D_late is D_A - D_W. parts_truncated is not a gate.
# a4-r1v2 requires exact transcript lines, not substrings.
# A rolled-back window needs R1V2-ARR and ROLLED-BACK-VERIFIED.
# LOGICAL_MATCH=NO fails either way.
# Mailroom's posted verdict stays the official run verdict.
do_report() {
    W_STAMP=$1
    MODE=report
    STAMP=$(new_stamp) || { WANTED_RC=1; return; }
    printf '%s\n' "${PFX} STAMP=${STAMP}"
    if ! stamp_ok "$W_STAMP"; then
        printf '%s\n' "ATT0-DONE RULE stamp FAIL"
        printf '%s\n' "ATT0-DONE FAIL rule=stamp"
        WANTED_RC=1
        return
    fi
    _tr="$LOGS/att0-window-${W_STAMP}.transcript"
    _bad=
    _rule() {
        printf '%s\n' "ATT0-DONE RULE $1 $2"
        if [ "$2" != "PASS" ]; then
            if [ -z "$_bad" ]; then
                _bad=$1
            fi
        fi
    }
    if [ -f "/tmp/att0w-done-${W_STAMP}.OK" ]; then
        _rule done-marker PASS
    else
        _rule done-marker FAIL
    fi
    if [ -f "$_tr" ] && has_line "$_tr" "WINDOW-DONE" && has_line "$_tr" "SUMMARY stamp=${W_STAMP} exit=0"; then
        _rule window-exit PASS
    else
        _rule window-exit FAIL
    fi
    _dp="$LOGS/att0w-dp-${W_STAMP}.log"
    _dw="$LOGS/att0w-dw-${W_STAMP}.log"
    _da="$LOGS/att0w-da-${W_STAMP}.log"
    _reh="$LOGS/att0w-reh-fill-${W_STAMP}.log"
    _a3="$LOGS/att0w-a3-${W_STAMP}.log"
    _a4="$LOGS/att0w-a4-${W_STAMP}.log"
    _r1="$LOGS/att0w-r1-${W_STAMP}.log"
    _gap="$LOGS/att0w-gap-${W_STAMP}.log"
    _rp=
    _rw=
    _rgone=
    _runsc=
    _ra=
    _ragone=
    _raunsc=
    if read_total "$_dp"; then
        _rp=$D_TOTAL
    fi
    if read_total "$_dw"; then
        _rw=$D_TOTAL
        _rgone=$D_GONE
        _runsc=$D_UNSCANNED
    fi
    if read_total "$_da"; then
        _ra=$D_TOTAL
        _ragone=$D_GONE
        _raunsc=$D_UNSCANNED
    fi
    _rms=
    _re=
    _rcap=
    _rel=
    _am=
    _ae=
    _acap=
    _ael=
    if fill_take "$_reh"; then
        _rms=$FILL_MESSAGES
        _re=$FILL_ERRORS
        _rcap=$FILL_CAPPED
        _rel=$FILL_ELIGIBLE
    fi
    if fill_take "$_a3"; then
        _am=$FILL_MESSAGES
        _ae=$FILL_ERRORS
        _acap=$FILL_CAPPED
        _ael=$FILL_ELIGIBLE
    fi
    _late=
    if is_uint "$_ra" && is_uint "$_rw"; then
        _late=$((_ra - _rw))
    fi
    if is_uint "$_rp" && is_uint "$_rw" && [ "$_rw" -ge "$_rp" ] && [ "$_rw" -le $((_rp + D_BAND)) ]; then
        _rule a2-band PASS
    else
        _rule a2-band FAIL
    fi
    if is_uint "$_rgone" && [ "$_rgone" = "$G_EXPECT" ]; then
        _rule a2-gone PASS
    else
        _rule a2-gone FAIL
    fi
    if [ -f "$_dw" ] && indexes_hold "$_dw"; then
        _rule a2-index PASS
    else
        _rule a2-index FAIL
    fi
    if is_uint "$_runsc" && is_uint "$_re" && [ "$_runsc" = "$_re" ]; then
        _rule a2-unscanned PASS
    else
        _rule a2-unscanned FAIL
    fi
    if fill_gates_ok "$_reh"; then
        _rule a2-fill PASS
    else
        _rule a2-fill FAIL
    fi
    if is_uint "$_rms" && is_uint "$_re" && is_uint "$_rcap" && is_uint "$_rel" && [ $((_rms + _re + _rcap)) = "$_rel" ]; then
        _rule a2-identity PASS
    else
        _rule a2-identity FAIL
    fi
    if is_uint "$_rp" && is_uint "$_rw" && [ "$_rp" -le "$D_MAX" ] && [ "$_rw" -le "$D_MAX" ]; then
        _rule d-max PASS
    else
        _rule d-max FAIL
    fi
    _b35=
    if [ -f "$_tr" ]; then
        _b35=$("$AWK" '/D_3.5b=/ { print; exit }' "$_tr")
    fi
    _b35v=$(field_of "${_b35:-}" D_3.5b)
    case "${_b35v:-x}" in
        ''|*[!0-9]*) _b35v= ;;
    esac
    if [ -n "$_b35v" ] && [ "$_b35v" = "${_rw:-}" ] && text_has "${_b35:-}" "auto (rule A2)"; then
        _rule d-3.5b PASS
    else
        _rule d-3.5b FAIL
    fi
    _lline=
    _tlate=
    if [ -f "$_tr" ]; then
        _lline=$("$AWK" '/D_late=/ { print; exit }' "$_tr")
        _tlate=$(field_of "${_lline:-}" D_late)
    fi
    case "${_tlate:-x}" in
        ''|*[!0-9]*) _tlate= ;;
    esac
    if is_uint "$_late" && [ -n "$_tlate" ] && [ "$_late" = "$_tlate" ] && [ "$_late" -le "$D_LATE_MAX" ] && text_has "${_lline:-}" "auto (rule A3)"; then
        _rule d-late PASS
    else
        _rule d-late FAIL
    fi
    if is_uint "$_ragone" && [ "$_ragone" = "$G_EXPECT" ]; then
        _rule a3-gone PASS
    else
        _rule a3-gone FAIL
    fi
    if [ -f "$_da" ] && indexes_hold "$_da"; then
        _rule a3-index PASS
    else
        _rule a3-index FAIL
    fi
    if is_uint "$_raunsc" && is_uint "$_ae" && [ "$_raunsc" = "$_ae" ]; then
        _rule a3-unscanned PASS
    else
        _rule a3-unscanned FAIL
    fi
    if fill_gates_ok "$_a3"; then
        _rule a3-fill PASS
    else
        _rule a3-fill FAIL
    fi
    if is_uint "$_am" && is_uint "$_ae" && is_uint "$_acap" && is_uint "$_ael" && [ $((_am + _ae + _acap)) = "$_ael" ]; then
        _rule a3-identity PASS
    else
        _rule a3-identity FAIL
    fi
    if is_uint "$_ael" && is_uint "$_rel" && [ "$_ael" = "$_rel" ]; then
        _rule a3-eligible PASS
    else
        _rule a3-eligible FAIL
    fi
    if is_uint "$_ae" && is_uint "$_ra" && is_uint "$_rw" && is_uint "$_late"; then
        _expect=$((G_EXPECT + BASELINE_44 + _ra))
        _cap=$((G_EXPECT + BASELINE_44 + _rw + _late))
        if [ "$_ae" = "$_expect" ] && [ "$_ae" -le "$_cap" ]; then
            _rule a3-44 PASS
        else
            _rule a3-44 FAIL
        fi
    else
        _rule a3-44 FAIL
    fi
    if [ -f "$_tr" ] && has_line "$_tr" "A2-OK" && has_line "$_tr" "A3-OK" && has_line "$_tr" "R1-OK" && has_line "$_tr" "ASK-MAIL-UNCHANGED-OK"; then
        _rule writers PASS
    else
        _rule writers FAIL
    fi
    _scans=
    if [ -f "$_a4" ]; then
        _scans=$("$AWK" -F= '/^a4_scans=/ { print $2; exit }' "$_a4" 2>/dev/null || true)
    fi
    if [ -f "$_a4" ] && kv_one "$_a4" a4_result ok && is_uint "$_scans" && is_uint "$_am" && [ "$_scans" = "$_am" ]; then
        _rule a4-counts PASS
    else
        _rule a4-counts FAIL
    fi
    if [ -f "$_r1" ] && "$AWK" '$0 == "ok" { f = 1 } END { exit f ? 0 : 1 }' "$_r1"; then
        _rule a4-quick PASS
    else
        _rule a4-quick FAIL
    fi
    _leak=0
    for _f in "$_tr" "$LOGS"/att0w-*-"${W_STAMP}".log; do
        if [ ! -f "$_f" ]; then
            continue
        fi
        if "$AWK" 'index($0, "falling back") || index($0, "IMAP_APP_PASSWORD=") || index($0, "MAILROOM_IMAP_PASSWORD=") || index($0, "LOGIN ") || index($0, "Subject:") { found = 1 } END { exit found ? 0 : 1 }' "$_f"; then
            _leak=1
        fi
    done
    if [ "$_leak" = "0" ] && [ -f "$_tr" ]; then
        _rule leak-w21 PASS
    else
        _rule leak-w21 FAIL
    fi
    _safe=1
    if [ ! -f "$_tr" ] || ! "$AWK" '{ n = split($0, a, " "); if (a[n] == "SAFE-STATE") f = 1 } END { exit f ? 0 : 1 }' "$_tr"; then
        _safe=0
    fi
    if [ "$_safe" = "1" ] && has_line "$_tr" "S2-OK" && has_line "$_tr" "search=restored" && has_line "$_tr" "GAP-D26-DROPPED" && [ -f "$_gap" ] && has_line "$_gap" "d26_live=no"; then
        _rule search-restored PASS
    else
        _rule search-restored FAIL
    fi
    _r1ok=0
    if [ -f "$_tr" ] && exact_msg "$_tr" "LOGICAL_MATCH=NO"; then
        _r1ok=0
    elif [ -f "$_tr" ] && exact_msg "$_tr" "ROLLED-BACK-VERIFIED"; then
        if exact_msg "$_tr" "R1V2-ARR K_OK lines=${EXPECT_PRE}"; then
            _r1ok=1
        fi
    elif [ -f "$_tr" ] \
        && exact_msg "$_tr" "R1V2-BEFORE K_OK lines=${EXPECT_PRE}" \
        && exact_msg "$_tr" "R1V2-POSTA2 K_OK lines=${EXPECT_AFTER}" \
        && exact_msg "$_tr" "R1V2-AFTER K_OK lines=${EXPECT_AFTER}" \
        && exact_msg "$_tr" "LOGICAL_MATCH_BK=YES" \
        && exact_msg "$_tr" "LOGICAL_MATCH=YES"
    then
        _r1ok=1
    fi
    if [ "$_r1ok" = "1" ]; then
        _rule a4-r1v2 PASS
    else
        _rule a4-r1v2 FAIL
    fi
    if [ -n "$_bad" ]; then
        printf '%s\n' "ATT0-DONE FAIL rule=${_bad}"
        WANTED_RC=1
        return
    fi
    printf '%s\n' "ATT0-DONE PASS"
    WANTED_RC=0
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0 2>/dev/null || exit 0
fi

case "${1:-}" in
    window)
        PFX=ATT0W
        if [ -z "${2:-}" ] || [ -n "${3:-}" ]; then
            printf '%s\n' "usage: /bin/bash /tmp/att0_l1.sh window P_STAMP"
            printf '%s\n' "ATT0W SUMMARY stamp=unknown exit=2"
            exit 2
        fi
        load_tools
        trap cleanup EXIT
        trap on_signal INT
        trap on_signal TERM
        do_window "$2"
        ;;
    rollback)
        PFX=ATT0R
        if [ -z "${2:-}" ] || [ -n "${3:-}" ]; then
            printf '%s\n' "usage: /bin/bash /tmp/att0_l1.sh rollback WINDOW_STAMP"
            printf '%s\n' "ATT0R SUMMARY stamp=unknown exit=2"
            exit 2
        fi
        load_tools
        trap cleanup EXIT
        trap on_signal INT
        trap on_signal TERM
        do_rollback "$2"
        ;;
    restore-daily)
        PFX=ATT0D
        if [ -z "${2:-}" ] || [ -n "${3:-}" ]; then
            printf '%s\n' "usage: /bin/bash /tmp/att0_l1.sh restore-daily WINDOW_STAMP"
            printf '%s\n' "ATT0D SUMMARY stamp=unknown exit=2"
            exit 2
        fi
        load_tools
        trap cleanup EXIT
        trap on_signal INT
        trap on_signal TERM
        do_restore_daily "$2"
        ;;
    report)
        PFX=ATT0Q
        if [ -z "${2:-}" ] || [ -n "${3:-}" ]; then
            printf '%s\n' "usage: /bin/bash /tmp/att0_l1.sh report WINDOW_STAMP"
            printf '%s\n' "ATT0Q SUMMARY stamp=unknown exit=2"
            exit 2
        fi
        load_tools
        do_report "$2"
        printf '%s\n' "${PFX} SUMMARY stamp=${STAMP:-unknown} exit=${WANTED_RC}"
        exit "$WANTED_RC"
        ;;
    *)
        printf '%s\n' "usage: /bin/bash /tmp/att0_l1.sh window P_STAMP | rollback WINDOW_STAMP | restore-daily WINDOW_STAMP | report WINDOW_STAMP"
        printf '%s\n' "ATT0 SUMMARY stamp=unknown exit=2"
        exit 2
        ;;
esac
