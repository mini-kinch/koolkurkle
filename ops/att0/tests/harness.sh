#!/bin/bash
# Linux stand-in for the Mac mini tools. Runs the operator script.
set -u
set -o pipefail

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
SCRIPT="$ROOT/ops/att0/att0_l1.sh"
FAKES="$ROOT/ops/att0/tests/fakes"
FIX="$ROOT/ops/att0/tests/fixtures"
CANARY=CANARY-SECRET-VALUE

fail() {
    printf '%s\n' "FAIL $*" >&2
    exit 1
}

/bin/bash -n "$SCRIPT" || fail "bash -n"
if /usr/bin/grep -n -E 'declare -A|mapfile|readarray|\[\[ -v|wait -n|\|&|\$\{[A-Za-z_][A-Za-z0-9_]*,,|\$\{[A-Za-z_][A-Za-z0-9_]*\^\^' "$SCRIPT"; then
    fail "forbidden construct"
fi
if /usr/bin/grep -n '/Users/\|unlock-keychain\|set-generic-password\|partition-list\|/usr/bin/security' "$SCRIPT"; then
    fail "forbidden path or keychain workaround"
fi
if /usr/bin/grep -n -E '(^|[^a-zA-Z])rm( |$)' "$SCRIPT"; then
    fail "rm in script"
fi
if /usr/bin/grep -n -E 'search_resume_watchdog\.py arm|search_resume_watchdog\.py schedule' "$SCRIPT"; then
    fail "watchdog arm or schedule entry point"
fi
# Free-lock gap: the only watchdog call between A2-OK and A3-OK is status.
# Matching the words arm or schedule here would hit the comment that names them.
if ! /usr/bin/awk '
    /say A2-OK$/ { on = 1; next }
    /say A3-OK$/ { on = 0; next }
    on && /search_resume_watchdog\.py/ {
        n++
        if ($0 !~ / status/) bad = 1
    }
    END { if (n != 1 || bad) exit 1 }
' "$SCRIPT"; then
    fail "gap watchdog call is not status-only"
fi
if /usr/bin/grep -n -E 'curl\.\*imap|run_mailroom_daily' "$SCRIPT"; then
    fail "fail-open process pattern"
fi
if /usr/bin/grep -n 'mailroom.write.lock.*mailroom.daily.lock\|mailroom.daily.lock.*mailroom.write.lock' "$SCRIPT"; then
    fail "combined lock check"
fi
_lsof_n=$(/usr/bin/grep -c '"\$LSOF" -t -- "\$_path"' "$SCRIPT" || true)
if [ "$_lsof_n" != "1" ]; then
    fail "lsof invocation count ${_lsof_n}"
fi
if ! /usr/bin/grep -q '"\$LSOF" -t -- "\$_path" 2>&1' "$SCRIPT"; then
    fail "lsof does not merge stderr"
fi
if /usr/bin/grep -n -E '\$PGREP[^\\n]*&&|pgrep [^\\n]*&&' "$SCRIPT"; then
    fail "pgrep folded with &&"
fi
_wlock_n=$(/usr/bin/grep -c 'lock_probe_file "\$MA/mailroom.write.lock"' "$SCRIPT" || true)
_dlock_n=$(/usr/bin/grep -c 'lock_probe_file "\$MA/mailroom.daily.lock"' "$SCRIPT" || true)
if [ "$_wlock_n" -lt 3 ] || [ "$_dlock_n" -lt 3 ]; then
    fail "per-file lock probes write=${_wlock_n} daily=${_dlock_n}"
fi
_cpat=$(/usr/bin/awk -F"'" '/^CURL_PAT=/{ print $2; exit }' "$SCRIPT")
if [ "$_cpat" != '^/usr/bin/curl( |$)' ]; then
    fail "curl pat ${_cpat}"
fi
_pinned='/usr/bin/curl --silent --show-error --fail-early -K -'
if printf '%s\n' "$_pinned" | /usr/bin/grep -E 'curl.*imap' >/dev/null; then
    fail "imap pattern matches pinned curl argv"
fi
if ! printf '%s\n' "$_pinned" | /usr/bin/grep -E "$_cpat" >/dev/null; then
    fail "curl pat misses pinned argv"
fi
if ! /usr/bin/awk '
    /^run_group\(\)/ { on = 1 }
    on && /RUN_GROUP_EMPTY/ && /!= "1"/ { hit = 1 }
    on && /^}/ { exit hit ? 0 : 1 }
' "$SCRIPT"; then
    fail "run_group ignores a live process group"
fi
if /usr/bin/grep -n 'alarm shift; exec' "$SCRIPT"; then
    fail "alarm plus exec"
fi
if ! /usr/bin/grep -q 'setpgrp(0, 0)' "$SCRIPT"; then
    fail "timer parent missing setpgrp"
fi
if ! /usr/bin/grep -F -q '$SIG{INT}' "$SCRIPT"; then
    fail "timer parent missing INT"
fi
if ! /usr/bin/grep -F -q '$SIG{TERM}' "$SCRIPT"; then
    fail "timer parent missing TERM"
fi
if ! /usr/bin/awk '
    /^do_window\(\)/ { on = 1 }
    on && /step0_locks/ { step = 1 }
    on && /\$MKDIR/ {
        if (!step) bad = 1
        saw = 1
        exit
    }
    END { if (!step || !saw || bad) exit 1 }
' "$SCRIPT"; then
    fail "step0 is not before mkdir"
fi
if ! /usr/bin/awk '
    /^flock_probe\(\)/ { on = 1 }
    on && /with_writer_lock\.py/ { hit = 1 }
    on && /^}/ { exit hit ? 0 : 1 }
' "$SCRIPT"; then
    fail "flock probe is not the wrapper acquisition path"
fi
_wpat=$(/usr/bin/awk -F"'" '/^WRITER_PAT=/{ print $2; exit }' "$SCRIPT")
/usr/bin/pgrep -fl "$_wpat" >/tmp/att0-pgrep-self.out 2>&1
_prc=$?
if [ "$_prc" -eq 0 ]; then
    cat /tmp/att0-pgrep-self.out >&2
    fail "writer pattern self-match"
fi
if [ "$_prc" -ne 1 ]; then
    fail "writer pattern pgrep rc ${_prc}"
fi
_self=$(
    set +e
    /bin/bash -c '
        set +C
        # shellcheck disable=SC1090
        . "$1"
        PFX=ATT0T
        N=0
        TRANSCRIPT=
        PGREP=/usr/bin/pgrep
        no_writer
        printf "RC=%s\n" "$?"
    ' bash "$SCRIPT" "$_wpat" 2>&1
)
printf '%s\n' "$_self" | /usr/bin/grep -q 'NO-WRITER-OK' || {
    printf '%s\n' "$_self" >&2
    fail "writer check self-match"
}
printf '%s\n' "$_self" | /usr/bin/grep -q '^RC=0$' || fail "writer check rc"
# pgrep does not report itself, so the bad pattern has to sit in
# another process's argv. [r]un_mailroom_daily contains mailroom_daily.
_bad=$(
    /bin/bash -c '/usr/bin/pgrep -fl "$1" >/tmp/att0-pgrep-bad.out; printf "%s\n" "$?"' \
        bash '[m]ailroom_daily|[r]un_mailroom_daily'
)
if [ "$_bad" != "0" ]; then
    cat /tmp/att0-pgrep-bad.out >&2
    fail "bad writer pattern did not self-match"
fi
printf '%s\n' "STATIC-OK"

chmod +x "$FAKES"/*

if [ "${ATT0_HARNESS_INNER:-}" != "1" ]; then
    cp -a /usr/bin/perl /tmp/att0-real-perl
    /tmp/att0-real-perl -e 'print "perl-ok\n"' || fail "real perl"
    rm -rf /tmp/att0-ov
    mkdir -p /tmp/att0-ov/upper /tmp/att0-ov/work /tmp/att0-ov/merged
    cp "$FAKES/perl-wrapper" /tmp/att0-ov/upper/perl
    cp "$FAKES/curl" /tmp/att0-ov/upper/curl
    chmod +x /tmp/att0-ov/upper/perl /tmp/att0-ov/upper/curl
    exec unshare --user --map-root-user --mount bash -c '
        mount -t overlay overlay -o lowerdir=/usr/bin,upperdir=/tmp/att0-ov/upper,workdir=/tmp/att0-ov/work /tmp/att0-ov/merged &&
        mount --bind /tmp/att0-ov/merged /usr/bin &&
        export ATT0_HARNESS_INNER=1 &&
        exec /bin/bash "'"$0"'"
    '
fi

/usr/bin/perl -e 'print "wrapped-perl\n"' | /usr/bin/grep -q wrapped-perl || fail "perl wrapper"
printf '%s\n' "OVERLAY-OK"

# W22. Combined lsof exits 1 when any listed file is not open.
# The script must still report the held file.
ATT0_LSOF_LOG=/tmp/att0-lsof-combined.log
export ATT0_LSOF_LOG
: > "$ATT0_LSOF_LOG"
ATT0_WRITE_HELD=1
ATT0_DAILY_HELD=0
ATT0_LSOF_ERROR=0
export ATT0_WRITE_HELD ATT0_DAILY_HELD ATT0_LSOF_ERROR
set +e
"$FAKES/lsof-split" "$HOME/MailArchive/mailroom.write.lock" "$HOME/MailArchive/mailroom.daily.lock" > /tmp/att0-lsof-combined.out
_crc=$?
set -e
if [ "$_crc" -ne 1 ] || [ -s /tmp/att0-lsof-combined.out ]; then
    fail "combined lsof fixture rc=${_crc}"
fi

run_locks() {
    _name=$1
    ATT0_LSOF_LOG="/tmp/att0-lsof-${_name}.log"
    export ATT0_LSOF_LOG
    MA="/tmp/att0-lock-${_name}"
    mkdir -p "$MA"
    if [ ! -e "$MA/mailroom.write.lock" ]; then
        : > "$MA/mailroom.write.lock"
    fi
    if [ ! -e "$MA/mailroom.daily.lock" ]; then
        : > "$MA/mailroom.daily.lock"
    fi
    : > "$ATT0_LSOF_LOG"
    set +e
    _out=$(
        set +C
        # shellcheck disable=SC1090
        . "$SCRIPT"
        PFX=ATT0T
        N=0
        TRANSCRIPT=
        MA="/tmp/att0-lock-${_name}"
        S_DIR="$FIX"
        AWK=/usr/bin/awk
        PYTHON=/usr/bin/python3
        LSOF="$FAKES/lsof-split"
        locks_ok
    )
    _rc=$?
    set -e
    printf '%s\n' "$_out" > "/tmp/att0-lock-${_name}.out"
    printf '%s\n' "$_rc"
}

ATT0_WRITE_HELD=1
ATT0_DAILY_HELD=0
ATT0_LSOF_ERROR=0
export ATT0_WRITE_HELD ATT0_DAILY_HELD ATT0_LSOF_ERROR
_rc=$(run_locks write-held)
if [ "$_rc" = "0" ]; then
    fail "write held reported free"
fi
/usr/bin/grep -q 'STOP-write-lock-held' /tmp/att0-lock-write-held.out || fail "write held stop"
/usr/bin/grep -q 'LOCK-WRITE-FREE' /tmp/att0-lock-write-held.out && fail "write held printed free"
if /usr/bin/grep -q 'mailroom.daily.lock' /tmp/att0-lsof-write-held.log; then
    fail "write probe also named daily"
fi
/usr/bin/grep -q 'mailroom.write.lock' /tmp/att0-lsof-write-held.log || fail "write probe missing"

ATT0_WRITE_HELD=0
ATT0_DAILY_HELD=1
export ATT0_WRITE_HELD ATT0_DAILY_HELD
_rc=$(run_locks daily-held)
if [ "$_rc" = "0" ]; then
    fail "daily held reported free"
fi
/usr/bin/grep -q 'LOCK-WRITE-FREE' /tmp/att0-lock-daily-held.out || fail "daily held write line"
/usr/bin/grep -q 'STOP-daily-lock-held' /tmp/att0-lock-daily-held.out || fail "daily held stop"
/usr/bin/grep -q 'LOCK-DAILY-FREE' /tmp/att0-lock-daily-held.out && fail "daily held printed free"
_lines=$(/usr/bin/grep -c . /tmp/att0-lsof-daily-held.log || true)
if [ "$_lines" != "2" ]; then
    fail "daily held lsof calls ${_lines}"
fi

ATT0_WRITE_HELD=0
ATT0_DAILY_HELD=0
ATT0_LSOF_ERROR=1
export ATT0_WRITE_HELD ATT0_DAILY_HELD ATT0_LSOF_ERROR
_rc=$(run_locks lsof-error)
if [ "$_rc" = "0" ]; then
    fail "lsof error reported free"
fi
/usr/bin/grep -q 'STOP-lsof-write' /tmp/att0-lock-lsof-error.out || fail "lsof error stop"
/usr/bin/grep -q 'LSOF-ERROR' /tmp/att0-lock-lsof-error.out || fail "lsof error token"
/usr/bin/grep -q 'LOCK-WRITE-FREE' /tmp/att0-lock-lsof-error.out && fail "lsof error printed free"

ATT0_LSOF_ERROR=0
ATT0_LSOF_GARBAGE=1
export ATT0_LSOF_ERROR ATT0_LSOF_GARBAGE
_rc=$(run_locks lsof-garbage)
if [ "$_rc" = "0" ]; then
    fail "lsof garbage reported free"
fi
/usr/bin/grep -q 'LSOF-ERROR' /tmp/att0-lock-lsof-garbage.out || fail "lsof garbage token"
/usr/bin/grep -q 'LOCK-WRITE-FREE' /tmp/att0-lock-lsof-garbage.out && fail "lsof garbage printed free"
unset ATT0_LSOF_GARBAGE

ATT0_LSOF_ERROR=0
export ATT0_LSOF_ERROR
_rc=$(run_locks both-free)
if [ "$_rc" != "0" ]; then
    cat /tmp/att0-lock-both-free.out >&2
    fail "both free rc ${_rc}"
fi
/usr/bin/grep -q 'LOCK-WRITE-FREE' /tmp/att0-lock-both-free.out || fail "both free write"
/usr/bin/grep -q 'LOCK-DAILY-FREE' /tmp/att0-lock-both-free.out || fail "both free daily"
/usr/bin/grep -q 'FLOCK-WRITE-FREE' /tmp/att0-lock-both-free.out || fail "both free flock"
_lines=$(/usr/bin/grep -c . /tmp/att0-lsof-both-free.log || true)
if [ "$_lines" != "2" ]; then
    fail "both free lsof calls ${_lines}"
fi
if /usr/bin/grep 'mailroom.write.lock.*mailroom.daily.lock' /tmp/att0-lsof-both-free.log >/dev/null; then
    fail "both free combined lsof"
fi

_hold_lock=/tmp/att0-lock-flock-held/mailroom.write.lock
mkdir -p /tmp/att0-lock-flock-held
: > "$_hold_lock"
/usr/bin/python3 -c 'import fcntl, time, sys
fh = open(sys.argv[1], "a+")
fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
time.sleep(20)
' "$_hold_lock" &
_hold_pid=$!
sleep 0.2
_rc=$(run_locks flock-held)
kill "$_hold_pid" 2>/dev/null || true
wait "$_hold_pid" 2>/dev/null || true
if [ "$_rc" = "0" ]; then
    fail "flock held reported free"
fi
/usr/bin/grep -q 'STOP-flock-held' /tmp/att0-lock-flock-held.out || fail "flock held stop"
unset ATT0_WRITE_HELD ATT0_DAILY_HELD ATT0_LSOF_ERROR ATT0_LSOF_LOG
printf '%s\n' "LOCK-SPLIT-OK"

cat > /tmp/att0-pgrep-rc2 <<'EOF'
#!/bin/bash
exit 2
EOF
chmod +x /tmp/att0-pgrep-rc2
_pout=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    PGREP=/tmp/att0-pgrep-rc2
    no_writer
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_pout" | /usr/bin/grep -q 'PGREP-ERROR' || {
    printf '%s\n' "$_pout" >&2
    fail "pgrep rc 2"
}
printf '%s\n' "$_pout" | /usr/bin/grep -q 'NO-WRITER-OK' && fail "pgrep rc 2 looked absent"
printf '%s\n' "$_pout" | /usr/bin/grep -q '^RC=0$' && fail "pgrep rc 2 returned success"

cat > /tmp/att0-lc-unclear <<'EOF'
#!/bin/bash
echo "launchctl failed" >&2
exit 7
EOF
chmod +x /tmp/att0-lc-unclear
_lout=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    LAUNCHCTL=/tmp/att0-lc-unclear
    IDBIN=/usr/bin/id
    AWK=/usr/bin/awk
    daily_not_loaded
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_lout" | /usr/bin/grep -q 'DAILY-UNCLEAR' || {
    printf '%s\n' "$_lout" >&2
    fail "daily print unclear"
}
printf '%s\n' "$_lout" | /usr/bin/grep -q 'DAILY-NOT-LOADED' && fail "unclear looked not-loaded"
printf '%s\n' "$_lout" | /usr/bin/grep -q '^RC=0$' && fail "unclear returned success"

_dout=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    LAUNCHCTL=/tmp/att0-lc-unclear
    IDBIN=/usr/bin/id
    AWK=/usr/bin/awk
    daily_disabled
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_dout" | /usr/bin/grep -q 'STOP-print-disabled' || {
    printf '%s\n' "$_dout" >&2
    fail "print-disabled rc"
}
printf '%s\n' "$_dout" | /usr/bin/grep -q 'DAILY-DISABLED' && fail "print-disabled rc looked disabled"
printf '%s\n' "PROC-RC-OK"

# W23. The pinned curl argv has no imap text. The URL is on stdin.
cat > /tmp/att0-sleeper.c <<'EOF'
#include <unistd.h>
int main(void) {
    sleep(30);
    return 0;
}
EOF
gcc -O2 -o /tmp/att0-sleeper /tmp/att0-sleeper.c
printf '%s\n' 'imaps://imap.mail.me.com/INBOX' | /usr/bin/python3 -c 'import os
os.execv("/tmp/att0-sleeper", ["/usr/bin/curl", "--silent", "--show-error", "--fail-early", "-K", "-"])' &
_curl_pid=$!
sleep 0.3
/usr/bin/pgrep -af '^/usr/bin/curl( |$)' > /tmp/att0-curl-pat.out || fail "curl pat missed pinned argv"
/usr/bin/grep -F '/usr/bin/curl --silent --show-error --fail-early -K -' /tmp/att0-curl-pat.out >/dev/null || fail "curl pat missed pinned argv"
if /usr/bin/grep -F imap /tmp/att0-curl-pat.out >/dev/null; then
    kill "$_curl_pid" 2>/dev/null || true
    fail "curl pat output contains imap"
fi
if /usr/bin/pgrep -af 'curl.*imap' | /usr/bin/grep -F '/usr/bin/curl --silent --show-error --fail-early -K -' >/dev/null; then
    kill "$_curl_pid" 2>/dev/null || true
    fail "curl imap pattern matched pinned argv"
fi
kill "$_curl_pid" 2>/dev/null || true
wait "$_curl_pid" 2>/dev/null || true
printf '%s\n' "CURL-ARGV-OK"

# W23. Curl argv counts only when that process is in the fill group.
cat > /tmp/att0-ps-fillcurl <<'EOF'
#!/bin/bash
pgid=$(cat /tmp/att0-fillcurl-pgid 2>/dev/null || printf '%s\n' 0)
case "$*" in
    *command*)
        if [ "${ATT0_FILLCURL_MODE:-in}" = "in" ]; then
            printf '%s\n' "4242 ${pgid} /usr/bin/curl --silent --show-error --fail-early -K -"
        else
            printf '%s\n' "4242 1 /usr/bin/curl --silent --show-error --fail-early -K -"
        fi
        ;;
esac
exit 0
EOF
chmod +x /tmp/att0-ps-fillcurl
rm -f /tmp/att0-fillcurl-pgid /tmp/att0-fillcurl.log /tmp/att0-fillcurl-out.log
mkdir -p /tmp/att0-fillcurl-ma /tmp/att0-fillcurl-state
if [ ! -e /tmp/att0-fillcurl-ma/mailroom.write.lock ]; then
    : > /tmp/att0-fillcurl-ma/mailroom.write.lock
fi
run_fillcurl() {
    _mode=$1
    _log=$2
    ATT0_FILLCURL_MODE=$_mode
    export ATT0_FILLCURL_MODE
    set +e
    (
        set +C
        # shellcheck disable=SC1090
        . "$SCRIPT"
        set +e
        PFX=ATT0T
        N=0
        TRANSCRIPT=
        MA=/tmp/att0-fillcurl-ma
        S_DIR="$FIX"
        ATT0_STATE=/tmp/att0-fillcurl-state
        export ATT0_STATE
        AWK=/usr/bin/awk
        PYTHON=/usr/bin/python3
        PERL=/usr/bin/perl
        PGREP="$FAKES/pgrep"
        LSOF="$FAKES/lsof"
        PS=/tmp/att0-ps-fillcurl
        STAMP=20260928-130077
        run_group 30 "$_log" /bin/bash -c 'echo $$ > /tmp/att0-fillcurl-pgid; exit 0'
        printf '%s\n' "RC=$?"
    ) > "${_log}.out" 2>&1
    set -e
}
run_fillcurl in /tmp/att0-fillcurl.log
if ! /usr/bin/grep -q 'fill_curl=1' /tmp/att0-fillcurl.log.out \
    || ! /usr/bin/grep -q 'STOP-group-occupied' /tmp/att0-fillcurl.log.out \
    || /usr/bin/grep -q '^RC=0$' /tmp/att0-fillcurl.log.out; then
    cat /tmp/att0-fillcurl.log.out >&2
    fail "fill curl in group"
fi
run_fillcurl out /tmp/att0-fillcurl-out.log
if ! /usr/bin/grep -q 'fill_curl=0' /tmp/att0-fillcurl-out.log.out \
    || ! /usr/bin/grep -q 'GROUP-EMPTY' /tmp/att0-fillcurl-out.log.out \
    || /usr/bin/grep -q 'STOP-group-occupied' /tmp/att0-fillcurl-out.log.out \
    || ! /usr/bin/grep -q '^RC=0$' /tmp/att0-fillcurl-out.log.out; then
    cat /tmp/att0-fillcurl-out.log.out >&2
    fail "fill curl outside group"
fi
unset ATT0_FILLCURL_MODE
printf '%s\n' "FILLCURL-GROUP-OK"

# W24. Leader exits 0 while a grandchild ignores TERM and holds the lock.
# Continuing requires the group empty and the lock free.
: > /tmp/att0-desc-lock
rm -f /tmp/att0-desc.meta /tmp/att0-desc.log /tmp/att0-desc-helper
(
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    STAMP=20260928-130099
    write_helper_pl
    printf '%s\n' "$HELPER_PL"
) > /tmp/att0-desc-helper
_helper=$(cat /tmp/att0-desc-helper)
test -n "$_helper" && test -f "$_helper" || fail "desc helper missing"
_t0=$(/bin/date +%s)
set +e
/usr/bin/perl -e 'alarm 20; exec @ARGV or die' /usr/bin/perl "$_helper" 30 /tmp/att0-desc.log 5 /usr/bin/perl -e 'use Fcntl qw(LOCK_EX O_RDWR); my $p = fork(); die "fork\n" unless defined $p; if ($p == 0) { $SIG{TERM} = "IGNORE"; $SIG{HUP} = "IGNORE"; sysopen my $fh, "/tmp/att0-desc-lock", O_RDWR or die "open\n"; flock $fh, LOCK_EX or die "flock\n"; sleep 30; exit 0; } exit 0;' > /tmp/att0-desc.meta
set -e
_t1=$(/bin/date +%s)
if [ $((_t1 - _t0)) -gt 15 ]; then
    fail "desc reap too slow $((_t1 - _t0))"
fi
/usr/bin/grep -q '^child_rc=0$' /tmp/att0-desc.meta || fail "desc child rc"
/usr/bin/grep -q '^harness=normal$' /tmp/att0-desc.meta || fail "desc harness"
/usr/bin/grep -q '^group_empty=1$' /tmp/att0-desc.meta || fail "desc group"
/usr/bin/python3 -c 'import fcntl, sys
fh = open(sys.argv[1], "a+")
fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
' /tmp/att0-desc-lock || fail "desc lock still held"
set +e
printf '%s\n' "DESC-LOCK-OK"

seed_db() {
    /usr/bin/sqlite3 "$1" <<'SQL'
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  source TEXT,
  folder TEXT,
  present_on_server INTEGER,
  has_attachments INTEGER
);
CREATE TABLE attachment_meta_scans (message_id INTEGER);
INSERT INTO messages (id, source, folder, present_on_server, has_attachments)
SELECT n, 'imap-live',
  CASE WHEN n <= 992 THEN 'g' ELSE 'p' || (n - 992) END,
  CASE WHEN n <= 992 THEN 0 ELSE 1 END,
  0
FROM (
  WITH RECURSIVE c(n) AS (
    SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n < 1063
  )
  SELECT n FROM c
);
SQL
}

setup_tree() {
    _name=$1
    rm -f "/tmp/att0w-claimed-${P_STAMP}" \
        "/tmp/att0w-done-${ATT0_FAKE_STAMP}.OK" \
        "/tmp/att0w-state-${ATT0_FAKE_STAMP}" \
        "/tmp/att0w-s1-${ATT0_FAKE_STAMP}" \
        "/tmp/att0r-done-${ATT0_FAKE_STAMP}.OK" \
        "/tmp/att0-restore-${ATT0_FAKE_STAMP}.sh" \
        /tmp/att0-run-group-*.pl \
        /tmp/phaseP-offline-"${P_STAMP}".OK \
        /tmp/phaseP-p8-"${P_STAMP}".OK \
        /tmp/phaseP-state-"${P_STAMP}"
    HOME="/tmp/att0-home-${_name}"
    export HOME
    rm -rf "$HOME"
    mkdir -p "$HOME/MailArchive/scripts/attachments" \
        "$HOME/MailArchive/logs" \
        "$HOME/MailArchive/backups" \
        "$HOME/MailArchive/dryrun" \
        "$HOME/MailArchive/state" \
        "$HOME/Library/LaunchAgents"
    cp "$FIX/sor_writer_gate.py" "$HOME/MailArchive/scripts/sor_writer_gate.py"
    cp "$FIX/with_writer_lock.py" "$HOME/MailArchive/scripts/with_writer_lock.py"
    cp "$FIX/search_resume_watchdog.py" "$HOME/MailArchive/scripts/search_resume_watchdog.py"
    cp "$FIX/migrate_att0_schema.py" "$HOME/MailArchive/scripts/attachments/migrate_att0_schema.py"
    cp "$FIX/meta_fill.py" "$HOME/MailArchive/scripts/attachments/meta_fill.py"
    cp "$FIX/imap_tombstone.py" "$HOME/MailArchive/scripts/imap_tombstone.py"
    cp "$FIX/ask_mail.py" "$HOME/MailArchive/scripts/ask_mail.py"
    : > "$HOME/MailArchive/logs/last_daily_rag_ok"
    : > "$HOME/MailArchive/logs/last_imap_ok"
    printf '%s\n' "daily" > "$HOME/Library/LaunchAgents/com.mailroom.daily.plist"
    printf '%s\n' "search" > "$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist"
    seed_db "$HOME/MailArchive/mailroom.sqlite"
    : > "$HOME/MailArchive/mailroom.write.lock"
    mkdir -p "$HOME/MailArchive/dryrun/att0-livepath-${P_STAMP}"
    /usr/bin/sqlite3 "$HOME/MailArchive/mailroom.sqlite" ".backup '${HOME}/MailArchive/dryrun/att0-livepath-${P_STAMP}/mailroom.sqlite'"
    printf '%s\n' "$P_STAMP" > "/tmp/phaseP-offline-${P_STAMP}.OK"
    printf '%s\n' "$P_STAMP" > "/tmp/phaseP-p8-${P_STAMP}.OK"
    printf '%s\n' "state" > "/tmp/phaseP-state-${P_STAMP}"
    ATT0_STATE="$HOME/var"
    mkdir -p "$ATT0_STATE"
    export ATT0_STATE
    export PATH="${FAKES}:/usr/bin:/bin"
}

scrub() {
    _out=$1
    if /usr/bin/grep -q "$CANARY" "$_out"; then
        fail "canary in output"
    fi
    if /usr/bin/grep -R -q "$CANARY" "$HOME/MailArchive/logs" "$HOME/MailArchive/state" 2>/dev/null; then
        fail "canary in logs"
    fi
    if /usr/bin/grep -q 'fixture-user' "$_out"; then
        fail "account name in output"
    fi
}

run_mode() {
    _log=$1
    shift
    set +e
    /bin/bash "$SCRIPT" "$@" >"${_log}.out" 2>"${_log}.err"
    _rc=$?
    set -e
    cat "${_log}.out" "${_log}.err" > "$_log"
    printf '%s\n' "$_rc"
}

expect_rc() {
    _got=$1
    _want=$2
    _log=$3
    if [ "$_got" != "$_want" ]; then
        echo "---- log ----" >&2
        cat "$_log" >&2
        fail "rc got ${_got} want ${_want}"
    fi
    scrub "$_log"
}

P_STAMP=20260928-010000
ATT0_FAKE_STAMP=20260928-120000
export ATT0_FAKE_STAMP ATT0_WRITE_HELD=1
setup_tree step0
rc=$(run_mode /tmp/att0-step0 window "$P_STAMP")
expect_rc "$rc" 4 /tmp/att0-step0
/usr/bin/grep -q 'STOP-write-lock-held' /tmp/att0-step0 || fail "step0 held"
/usr/bin/grep -q 'STEP0-LOCKS-OK' /tmp/att0-step0 && fail "step0 passed while held"
/usr/bin/grep -q 'SAFE-STATE-FAIL' /tmp/att0-step0 || fail "step0 safe"
test ! -e "$HOME/MailArchive/logs/att0-window-${ATT0_FAKE_STAMP}.transcript" || fail "step0 wrote transcript"
test ! -e "$HOME/MailArchive/backups"/mailroom-pre-att0-window-* || fail "step0 wrote backup"
unset ATT0_WRITE_HELD
printf '%s\n' "STEP0-LOCK-OK"

P_STAMP=20260928-010001
ATT0_FAKE_STAMP=20260928-120001
export ATT0_FAKE_STAMP
unset ATT0_FAKE_PROBE_RC ATT0_FAKE_A3_RC ATT0_FAKE_A3_HANG ATT0_FAKE_FALLBACK ATT0_FAKE_DATE_BREACH ATT0_TEST_MAX_ALARM ATT0_FAKE_SECURITY_RC ATT0_WRITE_HELD
setup_tree happy
rc=$(run_mode /tmp/att0-happy window "$P_STAMP")
expect_rc "$rc" 0 /tmp/att0-happy
/usr/bin/grep -q 'ATT0W STAMP=20260928-120001' /tmp/att0-happy || fail "happy stamp"
/usr/bin/grep -q 'D-CHECK-OK' /tmp/att0-happy || fail "happy d-check"
/usr/bin/grep -q 'auto (rule A2)' /tmp/att0-happy || fail "happy a2 auto"
/usr/bin/grep -q 'auto (rule A3)' /tmp/att0-happy || fail "happy a3 auto"
/usr/bin/grep -q 'D_late=0' /tmp/att0-happy || fail "happy d-late"
/usr/bin/grep -q 'A3-D-OK' /tmp/att0-happy || fail "happy a3 d"
/usr/bin/grep -q 'A4-OK' /tmp/att0-happy || fail "happy a4"
/usr/bin/grep -q 'source=phasep-scratch' /tmp/att0-happy || fail "happy dp source"
/usr/bin/grep -q 'P-STAMP-AGE-OK' /tmp/att0-happy || fail "happy age"
/usr/bin/grep -q 'PHASEP-MARKERS-OK' /tmp/att0-happy || fail "happy markers"
/usr/bin/grep -q 'STAMPS-OK' /tmp/att0-happy || fail "happy stamps"
/usr/bin/grep -q 'G=992' /tmp/att0-happy || fail "happy gone"
/usr/bin/grep -q 'D-BAND D_P=27 low=27 high=77' /tmp/att0-happy || fail "happy band"
/usr/bin/grep -q 'PARTS-TRUNCATED-INFO parts_truncated=1' /tmp/att0-happy || fail "happy parts info"
/usr/bin/grep -q 'STOP-parts' /tmp/att0-happy && fail "parts_truncated gated"
/usr/bin/grep -q 'WINDOW-DONE' /tmp/att0-happy || fail "happy done"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-happy || fail "happy safe"
/usr/bin/grep -q 'RESULT ar-r=not-run search=restored' /tmp/att0-happy || fail "happy result"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120001 exit=0' /tmp/att0-happy || fail "happy summary"
/usr/bin/grep -q 'kickstart' "${ATT0_STATE}/launchctl.log" && fail "happy kickstart"
test -f /tmp/att0w-done-20260928-120001.OK || fail "happy marker"
test ! -e "${ATT0_STATE}/security.log" || fail "happy called security"
/usr/bin/grep -q 'GAP-D26-DROPPED' /tmp/att0-happy || fail "happy gap"
/usr/bin/grep -q 'NOT_DISABLED' /tmp/att0-happy || fail "happy not disabled"
/usr/bin/grep -q 'write_rc=0' /tmp/att0-happy || fail "happy write rc"
/usr/bin/grep -q 'bootout_rc=0' /tmp/att0-happy || fail "happy bootout rc"
/usr/bin/grep -q 'SEARCH_NOT_LOADED' /tmp/att0-happy || fail "happy search down"
/usr/bin/grep -q 'DEADLINE-26-MARGIN' /tmp/att0-happy || fail "happy margin"
/usr/bin/grep -q 'A2-RERUN-OK' /tmp/att0-happy || fail "happy a2 rerun"
/usr/bin/grep -q 'SCHEMA-SHA-OK' /tmp/att0-happy || fail "happy schema"
/usr/bin/grep -q 'deadline_cleared' /tmp/att0-happy || fail "happy deadline cleared"
/usr/bin/grep -q 'hits_count=0 pids=\[\]' /tmp/att0-happy || fail "happy hits"
/usr/bin/grep -q 'Regular File 1' /tmp/att0-happy || fail "happy 1c"
/usr/bin/grep -q 'LOCK-WRITE-FREE' /tmp/att0-happy || fail "happy write lock"
/usr/bin/grep -q 'LOCK-DAILY-FREE' /tmp/att0-happy || fail "happy daily lock"
/usr/bin/grep -q 'FLOCK-WRITE-FREE' /tmp/att0-happy || fail "happy flock"
/usr/bin/grep -q 'WATCHDOG-MISSING-OK' /tmp/att0-happy || fail "happy watchdog"
/usr/bin/grep -q 'STEP0-LOCKS-OK' /tmp/att0-happy || fail "happy step0"
/usr/bin/grep -q 'NO-WRITER-OK' /tmp/att0-happy || fail "happy no writer"
/usr/bin/grep -q 'PGREP-ERROR' /tmp/att0-happy && fail "happy pgrep error"
/usr/bin/grep -q 'LSOF-ERROR' /tmp/att0-happy && fail "happy lsof error"
/usr/bin/grep -q 'DAILY-UNCLEAR' /tmp/att0-happy && fail "happy daily unclear"
/usr/bin/grep -q 'FOLLOWUP-CLEAR with_writer_lock meta_fill curl security perl time script locks' /tmp/att0-happy || fail "happy followup"
/usr/bin/grep -q 'fill_curl=0' /tmp/att0-happy || fail "happy fill curl"
if ! /usr/bin/awk '
    /GROUP-EMPTY/ { ready = 1 }
    /FILL-REPORT-OK/ { if (!ready) bad = 1; n++; ready = 0 }
    END { if (n != 2 || bad) exit 1 }
' /tmp/att0-happy; then
    fail "fill verdict before group empty"
fi
/usr/bin/grep -q 'harness=normal' /tmp/att0-happy || fail "happy harness normal"
/usr/bin/grep -q 'group_empty=1' /tmp/att0-happy || fail "happy group empty"
/usr/bin/grep -q 'group_empty=0' /tmp/att0-happy && fail "happy group occupied"
/usr/bin/grep -q 'search_resume_watchdog.py arm' /tmp/att0-happy && fail "happy armed"
/usr/bin/grep -q 'search_resume_watchdog.py schedule' /tmp/att0-happy && fail "happy scheduled"
rc=$(run_mode /tmp/att0-report report 20260928-120001)
expect_rc "$rc" 1 /tmp/att0-report
/usr/bin/grep -q '^ATT0-DONE PASS$' /tmp/att0-report && fail "report overall pass"
/usr/bin/grep -q 'ATT0-DONE FAIL rule=a4-r1v2' /tmp/att0-report || fail "report r1v2 verdict"
/usr/bin/grep -q 'ATT0-DONE RULE a2-band PASS' /tmp/att0-report || fail "report band"
/usr/bin/grep -q 'ATT0-DONE RULE a2-gone PASS' /tmp/att0-report || fail "report gone"
/usr/bin/grep -q 'ATT0-DONE RULE a2-index PASS' /tmp/att0-report || fail "report index"
/usr/bin/grep -q 'ATT0-DONE RULE a2-unscanned PASS' /tmp/att0-report || fail "report unscanned"
/usr/bin/grep -q 'ATT0-DONE RULE a2-fill PASS' /tmp/att0-report || fail "report fill"
/usr/bin/grep -q 'ATT0-DONE RULE a2-identity PASS' /tmp/att0-report || fail "report identity"
/usr/bin/grep -q 'ATT0-DONE RULE d-max PASS' /tmp/att0-report || fail "report d-max"
/usr/bin/grep -q 'ATT0-DONE RULE d-late PASS' /tmp/att0-report || fail "report d-late"
/usr/bin/grep -q 'ATT0-DONE RULE d-3.5b PASS' /tmp/att0-report || fail "report d-3.5b"
/usr/bin/grep -q 'ATT0-DONE RULE a3-44 PASS' /tmp/att0-report || fail "report 44"
/usr/bin/grep -q 'ATT0-DONE RULE a3-eligible PASS' /tmp/att0-report || fail "report eligible"
/usr/bin/grep -q 'ATT0-DONE RULE a4-counts PASS' /tmp/att0-report || fail "report a4"
/usr/bin/grep -q 'ATT0-DONE RULE a4-quick PASS' /tmp/att0-report || fail "report quick"
/usr/bin/grep -q 'ATT0-DONE RULE a4-r1v2 FAIL' /tmp/att0-report || fail "report r1v2 rule"
/usr/bin/grep -q 'ATT0-DONE RULE leak-w21 PASS' /tmp/att0-report || fail "report leak"
/usr/bin/grep -q 'ATT0-DONE RULE search-restored PASS' /tmp/att0-report || fail "report search"
printf '%s\n' "HAPPY-WINDOW-OK"

ATT0_FAKE_STAMP=20260928-120011
export ATT0_FAKE_STAMP
rc=$(run_mode /tmp/att0-second window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-second
/usr/bin/grep -q 'STOP-window-already-claimed' /tmp/att0-second || fail "second claim"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-second || fail "second safe"
/usr/bin/grep -q 'SEARCH-BOOTED-OUT' /tmp/att0-second && fail "second bootout"
printf '%s\n' "SECOND-WINDOW-OK"

P_STAMP=20260928-010002
ATT0_FAKE_STAMP=20260928-120002
export ATT0_FAKE_STAMP ATT0_FAKE_PROBE_RC=1
setup_tree probe
rc=$(run_mode /tmp/att0-probe window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-probe
/usr/bin/grep -q 'STOP-access-probe' /tmp/att0-probe || fail "probe stop"
/usr/bin/grep -q 'bootout' "${ATT0_STATE}/launchctl.log" && fail "probe booted search"
ls "$HOME/MailArchive/backups"/mailroom-pre-att0-window-* >/dev/null 2>&1 && fail "probe backup"
/usr/bin/grep -q 'A2-OK' /tmp/att0-probe && fail "probe migrated"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-probe || fail "probe safe"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120002 exit=1' /tmp/att0-probe || fail "probe summary"
unset ATT0_FAKE_PROBE_RC
printf '%s\n' "PROBE-FAIL-OK"

P_STAMP=20260928-010003
ATT0_FAKE_STAMP=20260928-120003
export ATT0_FAKE_STAMP ATT0_FAKE_FALLBACK=1
setup_tree fallback
rc=$(run_mode /tmp/att0-fallback window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-fallback
/usr/bin/grep -q 'STOP-falling-back' /tmp/att0-fallback || fail "fallback stop"
/usr/bin/grep -q 'bootout' "${ATT0_STATE}/launchctl.log" && fail "fallback bootout"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-fallback || fail "fallback safe"
unset ATT0_FAKE_FALLBACK
printf '%s\n' "FALLBACK-OK"

P_STAMP=20260928-010004
ATT0_FAKE_STAMP=20260928-120004
export ATT0_FAKE_STAMP ATT0_FAKE_A3_RC=1
setup_tree a3
rc=$(run_mode /tmp/att0-a3 window "$P_STAMP")
expect_rc "$rc" 3 /tmp/att0-a3
/usr/bin/grep -q 'STOP-a3' /tmp/att0-a3 || fail "a3 stop"
/usr/bin/grep -q 'ROLLBACK-DONE' /tmp/att0-a3 || fail "a3 rollback"
/usr/bin/grep -q 'RESULT ar-r=ran search=restored' /tmp/att0-a3 || fail "a3 result"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120004 exit=3' /tmp/att0-a3 || fail "a3 summary"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-a3 || fail "a3 safe"
/usr/bin/grep -q 'kickstart' "${ATT0_STATE}/launchctl.log" && fail "a3 kickstart"
test -f /tmp/att0r-done-20260928-120004.OK || fail "a3 arr marker"
unset ATT0_FAKE_A3_RC
printf '%s\n' "A3-FAIL-OK"

P_STAMP=20260928-010005
ATT0_FAKE_STAMP=20260928-120005
export ATT0_FAKE_STAMP ATT0_FAKE_DATE_BREACH=1
setup_tree budget
rc=$(run_mode /tmp/att0-budget window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-budget
/usr/bin/grep -q 'STOP-s51' /tmp/att0-budget || fail "budget stop"
/usr/bin/grep -q 'A2-OK' /tmp/att0-budget && fail "budget wrote"
/usr/bin/grep -q 'RESULT ar-r=not-run search=restored' /tmp/att0-budget || fail "budget result"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-budget || fail "budget safe"
unset ATT0_FAKE_DATE_BREACH
printf '%s\n' "BUDGET-OK"

P_STAMP=20260928-010006
ATT0_FAKE_STAMP=20260928-120006
export ATT0_FAKE_STAMP ATT0_FAKE_A3_HANG=1 ATT0_TEST_MAX_ALARM=2
setup_tree hang
rc=$(run_mode /tmp/att0-hang window "$P_STAMP")
expect_rc "$rc" 3 /tmp/att0-hang
/usr/bin/grep -q 'harness=timeout then TERM/KILL' /tmp/att0-hang || fail "hang harness"
/usr/bin/grep -q 'child_rc=' /tmp/att0-hang || fail "hang child rc"
/usr/bin/grep -q 'ROLLBACK-DONE' /tmp/att0-hang || fail "hang rollback"
/usr/bin/grep -q 'RESULT ar-r=ran search=restored' /tmp/att0-hang || fail "hang result"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-hang || fail "hang safe"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120006 exit=3' /tmp/att0-hang || fail "hang summary"
unset ATT0_FAKE_A3_HANG ATT0_TEST_MAX_ALARM
printf '%s\n' "HANG-OK"

P_STAMP=20260928-010007
ATT0_FAKE_STAMP=20260928-120007
export ATT0_FAKE_STAMP
setup_tree restore-no
rc=$(run_mode /tmp/att0-restore-no restore-daily 20260928-120099)
expect_rc "$rc" 1 /tmp/att0-restore-no
/usr/bin/grep -q 'STOP-no-window-or-arr-marker' /tmp/att0-restore-no || fail "restore refuse"
/usr/bin/grep -q 'enable' "${ATT0_STATE}/launchctl.log" && fail "restore enabled"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-restore-no || fail "restore refuse safe"
printf '%s\n' "RESTORE-REFUSE-OK"

P_STAMP=20260928-010017
ATT0_FAKE_STAMP=20260928-120017
export ATT0_FAKE_STAMP ATT0_PDIS_FAIL_AFTER=1
setup_tree pdis
printf '%s\n' "$P_STAMP" > "/tmp/att0w-done-${ATT0_FAKE_STAMP}.OK"
rc=$(run_mode /tmp/att0-pdis restore-daily "$ATT0_FAKE_STAMP")
expect_rc "$rc" 3 /tmp/att0-pdis
/usr/bin/grep -q 'STOP-print-disabled' /tmp/att0-pdis || fail "pdis stop"
/usr/bin/grep -q 'ENABLED-OK' /tmp/att0-pdis && fail "pdis looked enabled"
/usr/bin/grep -q 'SAFE-STATE-FAIL' /tmp/att0-pdis || fail "pdis safe"
unset ATT0_PDIS_FAIL_AFTER
rm -f "${ATT0_STATE}/daily_enabled" "${ATT0_STATE}/daily_loaded" \
    "${ATT0_STATE}/daily_running" "${ATT0_STATE}/daily_pgrep" \
    "${ATT0_STATE}/pdis_n"
printf '%s\n' "PRINT-DISABLED-RC-OK"

P_STAMP=20260928-010007
ATT0_FAKE_STAMP=20260928-120007
export ATT0_FAKE_STAMP
printf '%s\n' "$P_STAMP" > /tmp/att0w-done-20260928-120007.OK
# The restore mode's window stamp is the argument, not P_STAMP.
# Reuse the tree; the done marker is what the gate reads.
rc=$(run_mode /tmp/att0-restore-yes restore-daily 20260928-120007)
expect_rc "$rc" 0 /tmp/att0-restore-yes
/usr/bin/grep -q 'RESTORE-OK' /tmp/att0-restore-yes || fail "restore ok"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-restore-yes || fail "restore safe"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120007 exit=0' /tmp/att0-restore-yes || fail "restore summary"
/usr/bin/grep -q 'enable gui/' "${ATT0_STATE}/launchctl.log" || fail "restore enable"
printf '%s\n' "RESTORE-HAPPY-OK"

P_STAMP=20260928-010008
ATT0_FAKE_STAMP=20260928-120008
export ATT0_FAKE_STAMP
setup_tree rollback
/usr/bin/sqlite3 "$HOME/MailArchive/mailroom.sqlite" ".backup '${HOME}/MailArchive/backups/mailroom-pre-att0-window-20260928-129999.sqlite'"
bk_sha=$(/usr/bin/sha256sum "$HOME/MailArchive/backups/mailroom-pre-att0-window-20260928-129999.sqlite" | /usr/bin/awk '{print $1; exit}')
printf '%s\n' \
    "bk_path=${HOME}/MailArchive/backups/mailroom-pre-att0-window-20260928-129999.sqlite" \
    "bk_sha=${bk_sha}" \
    "p_stamp=${P_STAMP}" \
    "window_stamp=20260928-129999" \
    > /tmp/att0w-state-20260928-129999
printf '%s\n' "s_epoch=1700000000" "run_id=att0-L1-20260928-129999" > /tmp/att0w-s1-20260928-129999
rm -f /tmp/att0-restore-20260928-129999.sh /tmp/att0r-done-20260928-129999.OK
rc=$(run_mode /tmp/att0-rollback rollback 20260928-129999)
expect_rc "$rc" 0 /tmp/att0-rollback
/usr/bin/grep -q 'ROLLBACK-DONE' /tmp/att0-rollback || fail "rollback done"
/usr/bin/grep -q 'RESULT ar-r=ran search=restored' /tmp/att0-rollback || fail "rollback result"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-rollback || fail "rollback safe"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120008 exit=0' /tmp/att0-rollback || fail "rollback summary"
test -f /tmp/att0r-done-20260928-129999.OK || fail "rollback marker"
printf '%s\n' "ROLLBACK-OK"

rc=$(run_mode /tmp/att0-usage)
expect_rc "$rc" 2 /tmp/att0-usage
/usr/bin/grep -q 'exit=2' /tmp/att0-usage || fail "usage summary"
printf '%s\n' "USAGE-OK"

P_STAMP=20260928-010099
ATT0_FAKE_STAMP=20260928-120099
export ATT0_FAKE_STAMP
setup_tree nomarkers
rm -f "/tmp/phaseP-offline-${P_STAMP}.OK" "/tmp/phaseP-p8-${P_STAMP}.OK" "/tmp/phaseP-state-${P_STAMP}"
rc=$(run_mode /tmp/att0-nomarkers window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-nomarkers
/usr/bin/grep -q 'STOP-no-phasep-offline' /tmp/att0-nomarkers || fail "missing offline marker"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-nomarkers || fail "nomarkers safe"
test ! -e "$HOME/MailArchive/logs/att0-window-${ATT0_FAKE_STAMP}.transcript" || fail "nomarkers wrote transcript"
printf '%s\n' "NO-PHASEP-OK"

/usr/bin/perl -e 'alarm shift; exec @ARGV or die' 1 /bin/sleep 3 >/dev/null 2>&1
_arc=$?
if [ "$_arc" != "142" ]; then
    fail "alarm rc ${_arc}"
fi
printf '%s\n' "ALARM-142-OK"

# A leader that exits 0 while a grandchild ignores TERM must still be reaped.
# The grandchild stays in the group because it does not call setsid.
rm -f /tmp/att0-orphan-normal.meta /tmp/att0-orphan-normal.log /tmp/att0-orphan-helper-path
(
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    STAMP=20260928-130001
    write_helper_pl
    printf '%s\n' "$HELPER_PL"
) > /tmp/att0-orphan-helper-path
_helper=$(cat /tmp/att0-orphan-helper-path)
test -n "$_helper" && test -f "$_helper" || fail "orphan helper missing"
_t0=$(/bin/date +%s)
set +e
/usr/bin/perl -e 'alarm 20; exec @ARGV or die' /usr/bin/perl "$_helper" 30 /tmp/att0-orphan-normal.log 5 /usr/bin/perl -e 'my $p = fork(); die "fork\n" unless defined $p; if ($p == 0) { $SIG{TERM} = "IGNORE"; $SIG{HUP} = "IGNORE"; sleep 30; exit 0; } exit 0;' > /tmp/att0-orphan-normal.meta
set -e
_t1=$(/bin/date +%s)
if [ $((_t1 - _t0)) -gt 15 ]; then
    fail "orphan reap too slow $((_t1 - _t0))"
fi
/usr/bin/grep -q '^child_rc=0$' /tmp/att0-orphan-normal.meta || fail "orphan child rc"
/usr/bin/grep -q '^harness=normal$' /tmp/att0-orphan-normal.meta || fail "orphan harness"
/usr/bin/grep -q '^group_empty=1$' /tmp/att0-orphan-normal.meta || fail "orphan group"
printf '%s\n' "ORPHAN-NORMAL-OK"

rm -f /tmp/att0-orphan-timeout.meta /tmp/att0-orphan-timeout.log
_t0=$(/bin/date +%s)
set +e
/usr/bin/perl -e 'alarm 20; exec @ARGV or die' /usr/bin/perl "$_helper" 1 /tmp/att0-orphan-timeout.log 5 /bin/sleep 30 > /tmp/att0-orphan-timeout.meta
set -e
_t1=$(/bin/date +%s)
if [ $((_t1 - _t0)) -gt 15 ]; then
    fail "timeout reap too slow $((_t1 - _t0))"
fi
/usr/bin/grep -q '^child_rc=' /tmp/att0-orphan-timeout.meta || fail "timeout child rc"
/usr/bin/grep -q '^harness=timeout then TERM/KILL$' /tmp/att0-orphan-timeout.meta || fail "timeout harness line"
/usr/bin/grep -q '^group_empty=1$' /tmp/att0-orphan-timeout.meta || fail "timeout group"
printf '%s\n' "ORPHAN-TIMEOUT-OK"

# W24. INT and TERM use the same timer-parent path as a timeout.
# TERM the live parent while the child is still running.
rm -f /tmp/att0-orphan-signal.meta /tmp/att0-orphan-signal.log
_t0=$(/bin/date +%s)
set +e
/usr/bin/perl -e 'alarm 25; exec @ARGV or die' /usr/bin/perl "$_helper" 30 /tmp/att0-orphan-signal.log 5 /bin/sleep 30 > /tmp/att0-orphan-signal.meta &
_sigpid=$!
/bin/sleep 0.8
kill -TERM "$_sigpid"
wait "$_sigpid"
set -e
_t1=$(/bin/date +%s)
if [ $((_t1 - _t0)) -gt 15 ]; then
    fail "signal reap too slow $((_t1 - _t0))"
fi
/usr/bin/grep -q '^harness=signal then TERM/KILL$' /tmp/att0-orphan-signal.meta || fail "signal harness line"
/usr/bin/grep -q '^group_empty=1$' /tmp/att0-orphan-signal.meta || fail "signal group"
/usr/bin/grep -q '^fill_curl=0$' /tmp/att0-orphan-signal.meta || fail "signal fill curl"
if /bin/ps -ax -o command= | /usr/bin/awk 'index($0, "/bin/sleep 30") && index($0, "awk") == 0 { found = 1 } END { exit found ? 0 : 1 }'; then
    fail "signal left sleep"
fi
printf '%s\n' "ORPHAN-SIGNAL-OK"

printf '%s\n' "ALL-OK"
