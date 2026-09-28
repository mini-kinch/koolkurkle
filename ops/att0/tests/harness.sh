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
if /usr/bin/grep -n -E 'curl\.\*imap|run_mailroom_daily' "$SCRIPT"; then
    fail "fail-open process pattern"
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

P_STAMP=20260928-010001
ATT0_FAKE_STAMP=20260928-120001
export ATT0_FAKE_STAMP
unset ATT0_FAKE_PROBE_RC ATT0_FAKE_A3_RC ATT0_FAKE_A3_HANG ATT0_FAKE_FALLBACK ATT0_FAKE_DATE_BREACH ATT0_TEST_MAX_ALARM ATT0_FAKE_SECURITY_RC
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
/usr/bin/grep -q 'FOLLOWUP-CLEAR with_writer_lock meta_fill curl security perl time script locks' /tmp/att0-happy || fail "happy followup"
/usr/bin/grep -q 'harness=normal' /tmp/att0-happy || fail "happy harness normal"
/usr/bin/grep -q 'group_empty=1' /tmp/att0-happy || fail "happy group empty"
/usr/bin/grep -q 'group_empty=0' /tmp/att0-happy && fail "happy group occupied"
/usr/bin/grep -q 'search_resume_watchdog.py arm' /tmp/att0-happy && fail "happy armed"
rc=$(run_mode /tmp/att0-report report 20260928-120001)
expect_rc "$rc" 0 /tmp/att0-report
/usr/bin/grep -q 'ATT0-DONE PASS' /tmp/att0-report || fail "report pass"
/usr/bin/grep -q 'ATT0-DONE RULE a2-band PASS' /tmp/att0-report || fail "report band"
/usr/bin/grep -q 'ATT0-DONE RULE d-late PASS' /tmp/att0-report || fail "report d-late"
/usr/bin/grep -q 'ATT0-DONE RULE d-3.5b PASS' /tmp/att0-report || fail "report d-3.5b"
/usr/bin/grep -q 'ATT0-DONE RULE leak-w21 PASS' /tmp/att0-report || fail "report leak"
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

printf '%s\n' "ALL-OK"
