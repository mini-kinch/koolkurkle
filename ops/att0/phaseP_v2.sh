#!/bin/bash
# Phase P contingency (phaseP_v2) for the Mac mini. Run ONLY if the ATT-0
# window slips past its 12 h freshness limit and Phase P must be re-run.
# N1 reachability, then 0f, 1c, 3.5L and the 3.5c preview, re-pinned to the
# #92 install (PIN 473b59a0, live gate blob 5bd3abcd).
# Source card: Mailroom 20:25 PT 09-27 N1 + Phase P. This file replaces its
# paste blocks with one script that Developer runs as separate calls.
#
# W22: each lock file is its own `lsof -t -- FILE`. Non-empty stdout is HELD.
#   lsof's exit status is never the freedom test. A non-blocking flock through
#   with_writer_lock.py (rc 2 or "held") is also a STOP.
# W23: curl matches argv ^/usr/bin/curl( |$) or the pinned
#   `/usr/bin/curl --silent --show-error --fail-early -K -`, and the pid is a
#   member of that process group. An imap substring in the curl argv is not a match.
# W24: P7's fill is its own process group under a live timer parent (fork and
#   setpgrp). On timeout, INT, TERM, or any exit: TERM, wait 5 s, KILL the
#   group, then require the group to be empty before any verdict line.
#
# Authority: run only after CoS relays the user's go for Developer to run this
# Phase P script on the Mini. The #92 lift (23:27 PT) covered #92 only.
#
# Invoke with /bin/bash (macOS /bin/bash 3.2). Do not rely on PATH or the
# shebang alone.
#   /bin/bash /tmp/phaseP_473b59a0.sh offline
#   /bin/bash /tmp/phaseP_473b59a0.sh fill STAMP
#
# offline: N1, P1 to P6. No network login, no Keychain. The first output line
#   is "PHASEP STAMP=<STAMP>". Writes only a new backup, a new scratch copy
#   under dryrun/, two migrate logs, a state file and /tmp markers.
# fill STAMP: re-checks P1 and P2, then P7 (iCloud headers-only meta_fill on
#   the scratch copy, 300 s process-group timer) and P8 after-checks. A macOS Keychain prompt
#   is possible in P7, so a person should be at the Mini during this call.
#   One fill per STAMP. A second try needs a new offline run.
#
# Exit codes:
#   0  every check in this mode passed
#   1  STOP: a check failed; the output names it. Nothing live was changed.
#   2  usage (missing mode, bad or unknown STAMP)
#
# P3 ancestor chain under Developer's call path: the smoke python, then
# with_writer_lock.py (its direct parent, the lock holder), then /bin/bash this
# script, then Developer's shell and its parents. The allow must be the detail
# "writer lock held by wrapper". An ancestor walk error is a STOP.
#
# Never: rm, EXPUNGE, a write to the live SoR, launchctl bootstrap, bootout,
# enable, disable, kickstart, load or unload, or printing the IMAP user, a
# password, subjects or folder names.

set -u
set -o pipefail
set -C

PIN=473b59a0bc859af2fbcf1ec4649c3af49a3ceea4
FIVE_SHAS="505382b4e23dfed9f746d70fc6f3a79626524be37168aed955ac7bd8872d8345 1e7f5aeee8dd05bca363e8f517e046cc68e22d25c18c7c4dc487d4121dba4f77 bae404ccae07857d0dcb47cbc47a107f68e367c3fe7edff07eb202a01aeb8d43 b13b3c97d968c994955c1db313707dd1db372d47668f1d301b6b7c3febcbf95d 20f3d997dfa353f738b365542b468ded85cd27e871c166beef62ff7307968f8b"
DAILY_STAMP_EXPECT="2026-09-26 16:51:58"
IMAP_STAMP_EXPECT="2026-09-26 08:50:16"
GONE_EXPECT=992

PY=/usr/bin/python3
SQLITE3=/usr/bin/sqlite3
SHASUM=/usr/bin/shasum
LAUNCHCTL=/bin/launchctl
PGREP=/usr/bin/pgrep
PKILL=/usr/bin/pkill
LSOF=/usr/sbin/lsof
STAT=/usr/bin/stat
DSCACHEUTIL=/usr/bin/dscacheutil
NC=/usr/bin/nc
OPENSSL=/usr/bin/openssl
PERL=/usr/bin/perl
TIMEBIN=/usr/bin/time
MKDIR=/bin/mkdir
AWK=/usr/bin/awk
DATE=/bin/date
ID=/usr/bin/id
CAT=/bin/cat
ENVBIN=/usr/bin/env
GREP=/usr/bin/grep
SLEEP=/bin/sleep
PS=/bin/ps
TRUEBIN=/bin/true

export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PYTHONDONTWRITEBYTECODE=1

MODE=${1:-}
STAMP=
holder_pid=
MA=
S=
SOR=
LOGS=
UIDN=
BK=
LD=
SCRATCH=
STATE=
BK_SHA=
BK_STATL=

say() {
    printf '%s\n' "PHASEP $*"
}

stop() {
    say "STOP $1"
    say "SUMMARY mode=${MODE:-none} stamp=${STAMP:-unknown} exit=1"
    exit 1
}

usage() {
    say "USAGE offline | fill STAMP"
    exit 2
}

kill_holder() {
    if [ -n "$holder_pid" ]; then
        "$PKILL" -P "$holder_pid" >/dev/null 2>&1
        kill "$holder_pid" >/dev/null 2>&1
        wait "$holder_pid" >/dev/null 2>&1
        holder_pid=
    fi
}

on_sig() {
    kill_holder
    say "STOP signal-$1"
    say "SUMMARY mode=${MODE:-none} stamp=${STAMP:-unknown} exit=1"
    exit 1
}

trap kill_holder EXIT
trap 'on_sig INT' INT
trap 'on_sig TERM' TERM

mark() {
    : > "/tmp/phaseP-$1-$STAMP.OK" || stop "marker-$1"
    say "$2"
}

has_marker() {
    [ -f "/tmp/phaseP-$1-$STAMP.OK" ]
}

# Line equality check that reads all input, so no SIGPIPE with pipefail.
has_line() {
    printf '%s\n' "$1" | "$AWK" -v want="$2" '$0 == want { f = 1 } END { exit f ? 0 : 1 }'
}

# W22. One file per call. Non-empty stdout means HELD. lsof's rc is ignored.
lock_file_held() {
    _pids=$("$LSOF" -t -- "$1" 2>/dev/null || true)
    [ -n "$_pids" ]
}

require_lock_files_free() {
    _why=$1
    shift
    for _lf in "$@"; do
        if lock_file_held "$_lf"; then
            stop "$_why ${_lf##*/}"
        fi
    done
}

# W22. Non-blocking acquire through with_writer_lock.py. rc 2 or "held" is STOP.
# The probe output is not printed (the holder summary names a host).
probe_writer_lock_free() {
    _why=$1
    _pout=$("$PY" "$S/with_writer_lock.py" --purpose att0-lock-probe -- "$TRUEBIN" 2>&1)
    _prc=$?
    case "$_pout" in
        *held*) stop "$_why" ;;
    esac
    [ "$_prc" -eq 0 ] || stop "$_why rc=$_prc"
}

# W23. Argv ^/usr/bin/curl( |$) (covers the pinned -K - argv) AND the pid is
# listed in that process group. An imap substring is not the curl match.
assert_no_curl_process_group() {
    _why=$1
    _rows=$("$PS" -axo pid=,pgid=,args= | "$AWK" '
        {
            pid = $1
            pgid = $2
            cmd = $0
            sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", cmd)
            if (pgid ~ /^[0-9]+$/ && cmd ~ /^\/usr\/bin\/curl( |$)/) {
                print pid, pgid
            }
        }
    ') || stop "$_why ps-error"
    [ -n "$_rows" ] || return 0
    _ifs=$IFS
    IFS='
'
    for _row in $_rows; do
        [ -n "$_row" ] || continue
        IFS=' '
        set -- $_row
        _cpid=$1
        _cpgid=$2
        IFS=$_ifs
        if "$PS" -o pid= -g "$_cpgid" 2>/dev/null | "$AWK" -v p="$_cpid" '$1 == p { f = 1 } END { exit f ? 0 : 1 }'; then
            stop "$_why"
        fi
        stop "$_why"
    done
    IFS=$_ifs
}

assert_writers_clear() {
    _tag=$1
    _pat=$2
    "$PGREP" -fl "$_pat"
    _rc=$?
    [ "$_rc" -eq 1 ] || stop "$_tag rc=$_rc"
    assert_no_curl_process_group "$_tag-curl-process-group"
}

load_tools() {
    for _t in "$PY" "$SQLITE3" "$SHASUM" "$LAUNCHCTL" "$PGREP" "$PKILL" "$LSOF" "$STAT" \
        "$DSCACHEUTIL" "$NC" "$OPENSSL" "$PERL" "$TIMEBIN" "$MKDIR" "$AWK" "$DATE" "$ID" \
        "$CAT" "$ENVBIN" "$GREP" "$SLEEP" "$PS" "$TRUEBIN"; do
        [ -x "$_t" ] || stop "tool-missing $_t"
    done
    [ -n "${HOME:-}" ] || stop "home-unset"
    MA="$HOME/MailArchive"
    S="$MA/scripts"
    SOR="$MA/mailroom.sqlite"
    LOGS="$MA/logs"
    [ -d "$MA" ] && [ -d "$S" ] && [ -d "$LOGS" ] || stop "mailarchive-dirs"
    UIDN=$("$ID" -u) || stop "id"
    cd /tmp || stop "cd-tmp"
}

derive_paths() {
    BK="$MA/backups/mailroom-pre-att0-live-$STAMP.sqlite"
    LD="$MA/dryrun/att0-livepath-$STAMP"
    SCRATCH="$LD/mailroom.sqlite"
    STATE="/tmp/phaseP-state-$STAMP"
}

# Ambient environment must not steer the gate, wrapper or lock path.
env_check() {
    for _v in SOR_FORCE_LIVE_CHECKS MAILROOM_SEARCH_RESUME_RUN_ID MAILROOM_WRITER_LOCK_TOKEN \
        MAILROOM_WRITER_LOCK_PID MAILROOM_WRITER_LOCK_PURPOSE MAILROOM_WRITE_LOCK MAILROOM_DB \
        MAILROOM_IMAP_MAILBOX PYTHONPATH PYTHONHOME; do
        eval "_set=\${$_v+x}"
        [ -z "$_set" ] || stop "env-set $_v"
    done
    say "ENV-CLEAN-OK"
}

n1() {
    _out=$("$DSCACHEUTIL" -q host -a name imap.mail.me.com 2>/dev/null)
    printf '%s\n' "$_out" | "$AWK" '/^ip_address/ { f = 1 } END { exit f ? 0 : 1 }' || stop "n1-dns"
    say "DNS-OK"
    "$NC" -z -G 5 imap.mail.me.com 993 >/dev/null 2>&1 || stop "n1-tcp993"
    say "TCP993-OK"
    _g=$(printf 'a1 LOGOUT\r\n' | "$PERL" -e 'alarm shift; exec @ARGV or die "exec failed\n"' 15 \
        "$OPENSSL" s_client -quiet -connect imap.mail.me.com:993 -servername imap.mail.me.com 2>/dev/null)
    printf '%s\n' "$_g" | "$AWK" 'NR == 1 && /^\* OK/ { f = 1 } END { exit f ? 0 : 1 }' || stop "n1-imap-greeting"
    mark n1 "IMAP-GREETING-OK"
}

p1() {
    _tag=$1
    _out=$("$LAUNCHCTL" print "gui/$UIDN/com.mailroom.daily" 2>&1)
    _rc=$?
    case "$_out" in
        *"Could not find service"*) _nf=1 ;;
        *) _nf=0 ;;
    esac
    if [ "$_rc" -eq 113 ] || [ "$_nf" -eq 1 ]; then
        say "DAILY_NOT_LOADED"
    else
        stop "p1-daily-loaded-or-unclear rc=$_rc"
    fi
    _out=$("$LAUNCHCTL" print-disabled "gui/$UIDN" 2>&1) || stop "p1-print-disabled-error"
    printf '%s\n' "$_out" | "$AWK" '{ gsub(/^[ \t]+|[ \t]+$/, ""); if ($0 == "\"com.mailroom.daily\" => disabled" || $0 == "\"com.mailroom.daily\" => true") f = 1 } END { exit f ? 0 : 1 }' || stop "p1-daily-not-disabled"
    say "DAILY_DISABLED"
    assert_writers_clear "p1-writer-running-or-pgrep-error" \
        '[m]ailroom_daily|[r]un_mailroom_daily|[i]map_newmail|[i]map_tombstone|[i]map_fetch_bodies|[n]otify_bills|[r]em-legacy|[m]eta_fill|[m]igrate_att0|[e]mbed_backfill|[e]mbed_merge_shards|[e]mbed_sidecar_apply|[p]ost_rem_embed_batch|[w]ith_writer_lock|[/]usr/bin/curl --silent --show-error --fail-early -K -'
    say "NO-WRITER-OK"
    [ -f "$MA/mailroom.write.lock" ] || stop "p1-write-lock-file-missing"
    require_lock_files_free "p1-lock-HELD" "$MA/mailroom.daily.lock" "$MA/mailroom.write.lock"
    if [ -e "$MA/ACTION_REQUIRED" ] || [ -L "$MA/ACTION_REQUIRED" ]; then
        stop "p1-action-required-present"
    fi
    probe_writer_lock_free "p1-lock-HELD flock"
    say "LOCKS-FREE-OK"
    say "NO-ACTION-REQUIRED-OK"
    [ "$("$STAT" -f '%Sm' -t '%F %T' "$LOGS/last_daily_rag_ok" 2>/dev/null)" = "$DAILY_STAMP_EXPECT" ] || stop "p1-stamp-moved-daily"
    say "DAILY-STAMP-OK"
    [ "$("$STAT" -f '%Sm' -t '%F %T' "$LOGS/last_imap_ok" 2>/dev/null)" = "$IMAP_STAMP_EXPECT" ] || stop "p1-stamp-moved-imap"
    mark "p1$_tag" "IMAP-STAMP-OK"
}

p2() {
    _tag=$1
    _h=$(cd "$S" && "$SHASUM" -a 256 sor_writer_gate.py with_writer_lock.py search_resume_watchdog.py attachments/migrate_att0_schema.py attachments/meta_fill.py | "$AWK" '{ printf "%s%s", (NR > 1 ? " " : ""), $1 }') || stop "p2-shasum-error"
    [ "$_h" = "$FIVE_SHAS" ] || stop "p2-fix-shas"
    say "FIX-SHAS-OK"
    "$LAUNCHCTL" print "gui/$UIDN/com.mailroom.search-resume-watchdog" >/dev/null 2>&1 || stop "p2-watchdog-not-loaded"
    say "WD-LOADED-OK"
    _out=$("$PY" "$S/search_resume_watchdog.py" status 2>&1)
    _rc=$?
    [ "$_out" = "status=missing" ] && [ "$_rc" -eq 1 ] || stop "p2-watchdog-status rc=$_rc"
    say "WD-STATUS-MISSING-OK"
    [ -L "$SOR" ] && stop "p2-sor-is-symlink"
    [ "$("$STAT" -f '%HT %l' "$SOR" 2>/dev/null)" = "Regular File 1" ] || stop "p2-sor-not-plain-file"
    say "SOR-PLAIN-FILE-OK"
    _rp=$("$PY" -c 'import os,sys; home=os.path.realpath(os.path.expanduser("~")); want=os.path.join(home, "MailArchive", "mailroom.sqlite"); print("REALPATH-OK" if os.path.realpath(sys.argv[1]) == want else "REALPATH-BAD")' "$SOR")
    [ "$_rp" = "REALPATH-OK" ] || stop "p2-sor-realpath"
    mark "p2$_tag" "REALPATH-OK"
}

p3() {
    _out=$("$PY" "$S/with_writer_lock.py" --purpose att0-migrate -- "$ENVBIN" PYTHONPATH="$S" "$PY" -c 'import os, sor_writer_gate as g
p = os.path.expanduser("~/MailArchive/mailroom.sqlite")
print("is_live_sor=%s" % g.is_live_sor(p))
held, detail = g.writer_lock_held()
print("lock_decision_refuse=%s detail=%s" % (held, detail))
g.refuse_if_sor_writer_conflict(p)
print("SELF_OK")' 2>&1)
    _rc=$?
    printf '%s\n' "$_out" | "$AWK" '/^(is_live_sor=|lock_decision_refuse=|SELF_OK)/ { print "PHASEP P3 " $0 }'
    [ "$_rc" -eq 0 ] || stop "p3-rc=$_rc"
    case "$_out" in
        *CONFLICT*) stop "p3-conflict" ;;
    esac
    has_line "$_out" "is_live_sor=True" || stop "p3-not-live"
    has_line "$_out" "lock_decision_refuse=False detail=writer lock held by wrapper" || stop "p3-allow-not-by-wrapper"
    has_line "$_out" "SELF_OK" || stop "p3-no-self-ok"
    mark p3 "P3-SELF-OK"
}

p4() {
    "$PY" "$S/with_writer_lock.py" --purpose att0-foreign-hold -- "$SLEEP" 15 >/dev/null 2>&1 &
    holder_pid=$!
    _i=0
    _held=0
    while [ "$_i" -lt 20 ]; do
        # The wrapper opens the file before flock, so wait for its payload line.
        if "$GREP" -qx 'purpose=att0-foreign-hold' "$MA/mailroom.write.lock" 2>/dev/null && [ -n "$("$LSOF" -t "$MA/mailroom.write.lock" 2>/dev/null)" ]; then
            _held=1
            break
        fi
        "$SLEEP" 0.5
        _i=$((_i + 1))
    done
    [ "$_held" -eq 1 ] || stop "p4-holder-never-held"
    _out=$("$ENVBIN" -u MAILROOM_WRITER_LOCK_TOKEN PYTHONPATH="$S" "$PY" -c 'import os, sor_writer_gate as g
p = os.path.expanduser("~/MailArchive/mailroom.sqlite")
try:
    g.refuse_if_sor_writer_conflict(p)
    print("FOREIGN_NOT_REFUSED")
except g.SorWriterRefuse as exc:
    t = str(exc)
    ok = ("writer lock held" in t) and ("purpose=att0-foreign-hold" in t)
    print("FOREIGN_REFUSED_OK" if ok else "FOREIGN_REFUSED_WRONG_REASON")' 2>&1)
    say "P4 $_out"
    [ "$_out" = "FOREIGN_REFUSED_OK" ] || stop "p4-negative"
    wait "$holder_pid"
    _rc=$?
    holder_pid=
    [ "$_rc" -eq 0 ] || stop "p4-holder-rc=$_rc"
    mark p4 "HOLDER-RC-0-OK"
}

p5() {
    require_lock_files_free "p5-lock-HELD" "$MA/mailroom.write.lock"
    probe_writer_lock_free "p5-lock-HELD flock"
    say "LOCK-FREE-AGAIN-OK"
    for _f in "$BK" "$BK-wal" "$BK-shm" "$BK-journal"; do
        [ -e "$_f" ] && stop "p5-backup-exists"
    done
    "$SQLITE3" -readonly "$SOR" ".backup '$BK'" || stop "p5-backup-failed"
    _qc=$("$SQLITE3" "$BK" "PRAGMA quick_check;" 2>&1)
    [ "$_qc" = "ok" ] || stop "p5-quick-check"
    say "BACKUP-QUICK-CHECK-OK"
    BK_SHA=$("$SHASUM" -a 256 "$BK" | "$AWK" '{ print $1 }') || stop "p5-shasum"
    BK_STATL=$("$STAT" -f '%z %m' "$BK") || stop "p5-stat"
    say "BK_SHA12=${BK_SHA:0:12} bk_size_mtime=$BK_STATL"
    { printf 'STAMP=%s\nBK_SHA=%s\nBK_STATL=%s\n' "$STAMP" "$BK_SHA" "$BK_STATL"; } > "$STATE" || stop "p5-state-file"
    mark p5 "STATE-WRITTEN-OK"
}

# Runs one wrapped migrate into a new log; prints only the report lines.
migrate_once() {
    _log=$1
    [ -e "$_log" ] && stop "p6-log-exists"
    : > "$_log" || stop "p6-log-create"
    "$PY" "$S/with_writer_lock.py" --purpose att0-migrate -- "$ENVBIN" SOR_FORCE_LIVE_CHECKS=1 PYTHONPATH="$S:$S/attachments" "$PY" "$S/attachments/migrate_att0_schema.py" --db "$SCRATCH" --allow-mailroom-sqlite >> "$_log" 2>&1
    _mrc=$?
    "$AWK" '/^(att0 schema migrate|db_basename=|message_embeddings=|user_version=|fts=|legacy_attachments=|attachment|error:)/ { print "PHASEP P6 " $0 }' "$_log"
    [ "$_mrc" -eq 0 ] || stop "p6-migrate-rc=$_mrc"
    _mlog=$("$CAT" "$_log")
    case "$_mlog" in
        *CONFLICT*) stop "p6-conflict" ;;
    esac
}

p6() {
    [ -e "$LD" ] && stop "p6-scratch-dir-exists"
    "$MKDIR" "$LD" || stop "p6-mkdir"
    "$SQLITE3" -readonly "$BK" ".backup '$SCRATCH'" || stop "p6-copy"
    say "SCRATCH-MADE-OK"
    _a=$(PYTHONPATH="$S" "$PY" -c 'import sys, sor_writer_gate as g; p=sys.argv[1]; print("is_live_sor=%s live_checks_noflag=%s" % (g.is_live_sor(p), g.live_checks_apply(p)))' "$SCRATCH" 2>&1)
    _b=$(SOR_FORCE_LIVE_CHECKS=1 PYTHONPATH="$S" "$PY" -c 'import sys, sor_writer_gate as g; print("live_checks_flag=%s" % g.live_checks_apply(sys.argv[1]))' "$SCRATCH" 2>&1)
    say "P6 $_a $_b"
    [ "$_a $_b" = "is_live_sor=True live_checks_noflag=True live_checks_flag=True" ] || stop "p6-live-flags"
    migrate_once "$LOGS/att0_livepath_migrate_$STAMP.log"
    for _l in "user_version=1" "fts=ensured" "legacy_attachments=renamed_empty" "attachments=created" "attachment_extracts=created" "attachment_chunks=created" "attachment_meta_scans=created" "attachment_folder_uidvalidity=created"; do
        has_line "$_mlog" "$_l" || stop "p6-migrate-expect $_l"
    done
    mark p6a "MIGRATE-OK"
    migrate_once "$LOGS/att0_livepath_migrate_rerun_$STAMP.log"
    for _l in "user_version=1" "fts=ensured" "legacy_attachments=unchanged" "attachments=exists" "attachment_extracts=exists" "attachment_chunks=exists" "attachment_meta_scans=exists" "attachment_folder_uidvalidity=exists"; do
        has_line "$_mlog" "$_l" || stop "p6-rerun-expect $_l"
    done
    mark p6b "MIGRATE-RERUN-OK"
}

run_offline() {
    STAMP=$("$DATE" +%Y%m%d-%H%M%S) || stop "date"
    say "STAMP=$STAMP"
    derive_paths
    [ -e "$STATE" ] && stop "state-exists"
    say "START $("$DATE" '+%F %T %Z') pin=${PIN:0:12}"
    env_check
    n1
    p1 ""
    p2 ""
    p3
    p4
    p5
    p6
    mark offline "OFFLINE-OK"
    say "NEXT fill $STAMP (a person at the Mini for a possible Keychain Allow)"
    say "SUMMARY mode=offline stamp=$STAMP exit=0"
    exit 0
}

state_value() {
    "$AWK" -v k="$1" 'index($0, k "=") == 1 { print substr($0, length(k) + 2); n++ } END { exit n == 1 ? 0 : 1 }' "$STATE"
}

sql_d() {
    "$CAT" <<'SQL'
WITH f AS (SELECT folder, ROW_NUMBER() OVER (ORDER BY folder) AS idx FROM (SELECT DISTINCT folder FROM messages WHERE source='imap-live' AND folder IS NOT NULL AND trim(folder)<>'')),
m AS (SELECT f.idx, x.present_on_server AS p, (x.id IN (SELECT message_id FROM attachment_meta_scans)) AS s FROM messages x JOIN f ON x.folder=f.folder WHERE x.source='imap-live')
SELECT 'idx='||idx||' present='||sum(p=1)||' scanned_present='||sum(p=1 AND s)||' missing_present='||sum(p=1 AND NOT s)||' gone='||sum(p=0)||' scanned_gone='||sum(p=0 AND s) FROM m GROUP BY idx ORDER BY idx;
WITH m AS (SELECT x.present_on_server AS p, (x.id IN (SELECT message_id FROM attachment_meta_scans)) AS s FROM messages x WHERE x.source='imap-live' AND x.folder IS NOT NULL AND trim(x.folder)<>'')
SELECT 'TOTAL present='||sum(p=1)||' missing_present='||sum(p=1 AND NOT s)||' D_total='||(sum(p=1 AND NOT s)-44)||' gone='||sum(p=0)||' scanned_gone='||sum(p=0 AND s)||' unscanned_all='||sum(NOT s) FROM m;
SQL
}

fill_check_py() {
    "$CAT" <<'PYEOF'
import json, sys
kv, js, bad = {}, None, []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.rstrip("\n")
    if line.startswith("PARTIAL:"):
        bad.append("partial-banner")
    if "CONFLICT" in line:
        bad.append("conflict")
    if line.startswith("summary_json="):
        js = json.loads(line[len("summary_json="):])
    elif "=" in line and " " not in line.split("=", 1)[0]:
        k, v = line.split("=", 1)
        kv.setdefault(k, v)
def n(k):
    try:
        return int(kv[k])
    except Exception:
        bad.append("missing-" + k)
        return -1
m, e, c, el = n("messages"), n("errors"), n("capped"), n("eligible")
if m + e + c != el:
    bad.append("sum-not-eligible")
for k in ("capped", "uidvalidity_mismatch", "bytes_stored", "partial", "dry_run"):
    if n(k) != 0:
        bad.append(k + "-not-0")
if js is None:
    bad.append("no-summary-json")
elif js.get("curl_failures") != []:
    bad.append("curl-failures=%d" % len(js.get("curl_failures") or []))
print("R_messages=%d R_errors=%d R_capped=%d R_eligible=%d" % (m, e, c, el))
print("FILL-REPORT-OK" if not bad else "FILL-REPORT-BAD " + ",".join(sorted(set(bad))))
PYEOF
}

run_fill() {
    STAMP=${1:-}
    case "$STAMP" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) say "USAGE bad STAMP"; exit 2 ;;
    esac
    say "STAMP=$STAMP"
    derive_paths
    [ -f "$STATE" ] && [ ! -L "$STATE" ] && [ -O "$STATE" ] || { say "USAGE unknown STAMP (no state file)"; exit 2; }
    for _m in n1 p1 p2 p3 p4 p5 p6a p6b offline; do
        has_marker "$_m" || stop "fill-missing-offline-marker-$_m"
    done
    [ "$(state_value STAMP)" = "$STAMP" ] || stop "fill-state-stamp"
    BK_SHA=$(state_value BK_SHA) || stop "fill-state-bk-sha"
    BK_STATL=$(state_value BK_STATL) || stop "fill-state-bk-stat"
    case "$BK_SHA" in
        *[!0-9a-f]*|"") stop "fill-state-bk-sha-format" ;;
    esac
    [ "${#BK_SHA}" -eq 64 ] || stop "fill-state-bk-sha-length"
    [ -f "$SCRATCH" ] || stop "fill-scratch-missing"
    : > "/tmp/phaseP-fillstart-$STAMP.OK" || stop "fill-already-attempted-run-offline-again"
    say "START $("$DATE" '+%F %T %Z') pin=${PIN:0:12}"
    env_check
    p1 f
    p2 f
    [ "$("$SHASUM" -a 256 "$BK" | "$AWK" '{ print $1 }')" = "$BK_SHA" ] || stop "fill-bk-changed-before"
    say "BK-UNCHANGED-BEFORE-OK"

    # P7: IMAP env (prints only a length), no-writer probe, then the fill.
    MAILROOM_IMAP_USER=$("$PY" -c 'import ast,sys
for n in ast.parse(open(sys.argv[1]).read()).body:
    if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "EMAIL" for t in n.targets):
        print(n.value.value); break' "$S/imap_tombstone.py" 2>/dev/null)
    [ -n "$MAILROOM_IMAP_USER" ] || stop "p7-imap-user-missing"
    export MAILROOM_IMAP_USER
    export MAILROOM_IMAP_HOST=imap.mail.me.com
    say "IMAP_USER_SET len=${#MAILROOM_IMAP_USER}"
    _hits=$(PYTHONPATH="$S" "$PY" -c 'import os, sor_writer_gate as g; h=g.rem_process_hits(); print("hits_count=%d" % len(h))' 2>&1)
    say "P7 $_hits"
    [ "$_hits" = "hits_count=0" ] || stop "p7-rem-hits"
    FLOG="$LOGS/att0_livepath_apply_$STAMP.log"
    [ -e "$FLOG" ] && stop "p7-log-exists"
    : > "$FLOG" || stop "p7-log-create"
    say "P7 FILL-START $("$DATE" '+%T %Z') (Keychain Allow if a prompt appears on the Mini)"
    # 300 s is the cap. PHASEP_FILL_ALARM may only shorten it (1..300).
    _fill_alarm=300
    if [ -n "${PHASEP_FILL_ALARM:-}" ]; then
        case "$PHASEP_FILL_ALARM" in
            ''|*[!0-9]*) stop "p7-alarm-bad" ;;
        esac
        if [ "$PHASEP_FILL_ALARM" -gt 300 ] || [ "$PHASEP_FILL_ALARM" -lt 1 ]; then
            stop "p7-alarm-bad"
        fi
        _fill_alarm=$PHASEP_FILL_ALARM
    fi
    _p7status="/tmp/phaseP-p7harness-$STAMP"
    # Live timer parent. alarm+exec would signal only the wrapper and leave
    # meta_fill and curl in the scratch write. Fork, setpgrp, and on timeout,
    # INT, TERM, or any exit: TERM the group, wait 5 s, KILL, then require the
    # group to be empty. N1's alarm+exec wraps only openssl and stays as it is.
    "$TIMEBIN" -l "$PERL" -e '
        my $sec = shift @ARGV;
        my $status = shift @ARGV;
        my $psbin = shift @ARGV;
        defined(my $pid = fork()) or die "fork failed\n";
        if ($pid == 0) {
            setpgrp(0, 0) or die "setpgrp failed\n";
            exec @ARGV or die "exec failed\n";
        }
        my $outcome = "normal";
        my $child_status = 0;
        my $reaped = 0;
        my $done = 0;
        my $kill_group = sub {
            kill "TERM", -$pid;
            select(undef, undef, undef, 5);
            kill "KILL", -$pid;
        };
        my $group_empty = sub {
            my $attempt;
            for ($attempt = 0; $attempt < 50; $attempt++) {
                my $busy = 0;
                open(my $fh, "-|", $psbin, "-axo", "pid=,pgid=") or return 0;
                while (<$fh>) {
                    my ($p, $g) = split;
                    if (defined $g && $g == $pid && defined $p && $p != $$) {
                        $busy = 1;
                    }
                }
                close($fh);
                return 1 if !$busy;
                select(undef, undef, undef, 0.1);
            }
            return 0;
        };
        my $finish = sub {
            my ($why) = @_;
            return if $done;
            $done = 1;
            $outcome = $why if defined $why && $why ne "";
            $kill_group->();
            if (!$reaped) {
                waitpid($pid, 0);
                $child_status = $?;
                $reaped = 1;
            }
            my $empty = $group_empty->();
            my $fill_rc;
            if ($child_status & 127) {
                $fill_rc = 128 + ($child_status & 127);
            } else {
                $fill_rc = $child_status >> 8;
            }
            my $label = "normal";
            if ($outcome eq "timeout") {
                $label = "timeout then TERM/KILL";
            } elsif ($outcome eq "int") {
                $label = "int then TERM/KILL";
            } elsif ($outcome eq "term") {
                $label = "term then TERM/KILL";
            }
            open(my $sf, ">", $status) or die "status open failed\n";
            print $sf "fill_rc=$fill_rc\n";
            print $sf "harness=$label\n";
            if ($empty) {
                print $sf "group_empty=yes\n";
            } else {
                print $sf "group_empty=no\n";
            }
            close($sf);
            if (!$empty) {
                exit 91;
            }
            if ($outcome eq "timeout") {
                exit 142;
            }
            if ($outcome eq "int") {
                exit 130;
            }
            if ($outcome eq "term") {
                exit 143;
            }
            exit $fill_rc;
        };
        $SIG{ALRM} = sub { $finish->("timeout"); };
        $SIG{INT} = sub { $finish->("int"); };
        $SIG{TERM} = sub { $finish->("term"); };
        alarm($sec);
        waitpid($pid, 0);
        $child_status = $?;
        $reaped = 1;
        alarm(0);
        $finish->("normal");
    ' "$_fill_alarm" "$_p7status" "$PS" \
        "$PY" "$S/with_writer_lock.py" --purpose 'att0 meta fill' -- \
        "$ENVBIN" SOR_FORCE_LIVE_CHECKS=1 PYTHONPATH="$S:$S/attachments" \
        "$PY" "$S/attachments/meta_fill.py" --db "$SCRATCH" --source imap --max-messages 0 --timeout 0 --apply --allow-mailroom-sqlite >> "$FLOG" 2>&1
    _frc=$?
    [ -f "$_p7status" ] || stop "p7-harness-missing"
    _fill_rc=$("$AWK" 'index($0, "fill_rc=") == 1 { print substr($0, length("fill_rc=") + 1); n++ } END { exit n == 1 ? 0 : 1 }' "$_p7status") || stop "p7-harness-fill-rc"
    _hout=$("$AWK" 'index($0, "harness=") == 1 { print substr($0, length("harness=") + 1); n++ } END { exit n == 1 ? 0 : 1 }' "$_p7status") || stop "p7-harness-outcome"
    _gempty=$("$AWK" 'index($0, "group_empty=") == 1 { print substr($0, length("group_empty=") + 1); n++ } END { exit n == 1 ? 0 : 1 }' "$_p7status") || stop "p7-harness-group"
    [ "$_gempty" = "yes" ] || stop "p7-group-not-empty"
    say "P7 FILL-RC=$_fill_rc"
    say "P7 HARNESS=$_hout"
    say "P7 FILL-END $("$DATE" '+%T %Z') rc=$_frc"
    case "$_hout" in
        normal) ;;
        *) stop "p7-harness" ;;
    esac
    "$AWK" '/^(PARTIAL:|att0 meta fill|dry_run=|source=|db_basename=|messages=|parts=|has_attachments=|filenames=|bytes_stored=|scanned=|eligible=|stopped=|capped=|skipped=|errors=|partial=|parts_truncated=|uidvalidity_mismatch=|capped: |literal_dropped=|literal_truncated=|error:)/ { print "PHASEP P7 " $0 } / real / { print "PHASEP P7 time " $0 }' "$FLOG"
    [ "$_frc" -eq 0 ] || stop "p7-fill-rc=$_frc"
    _chk=$(fill_check_py | "$PY" - "$FLOG")
    say "P7 $(printf '%s' "$_chk" | "$AWK" 'NR == 1')"
    _verdict=$(printf '%s\n' "$_chk" | "$AWK" 'NR == 2')
    say "P7 $_verdict"
    [ "$_verdict" = "FILL-REPORT-OK" ] || stop "p7-report"
    R_ERRORS=$(printf '%s\n' "$_chk" | "$AWK" 'NR == 1 { for (i = 1; i <= NF; i++) if ($i ~ /^R_errors=/) { sub(/^R_errors=/, "", $i); print $i } }')
    mark p7 "P7-OK"

    # P8: nothing left running, no leak, backup unchanged, D preview.
    assert_writers_clear "p8-leftover-process" \
        '[/]usr/bin/curl --silent --show-error --fail-early -K -|[m]eta_fill|[w]ith_writer_lock'
    require_lock_files_free "p8-lock-HELD" "$MA/mailroom.write.lock"
    probe_writer_lock_free "p8-lock-HELD flock"
    say "NOTHING-LEFT-RUNNING-OK"
    _leak=$("$GREP" -c -i -F -e "$MAILROOM_IMAP_USER" -e 'LOGIN ' -e 'Subject:' "$FLOG")
    [ "$_leak" = "0" ] || stop "p8-leak-scan count=$_leak"
    say "LEAK-SCAN-0-OK"
    [ "$("$SHASUM" -a 256 "$BK" | "$AWK" '{ print $1 }')" = "$BK_SHA" ] || stop "p8-bk-changed"
    [ "$("$STAT" -f '%z %m' "$BK")" = "$BK_STATL" ] || stop "p8-bk-stat-changed"
    say "BK-UNCHANGED-OK"
    _d=$(sql_d | "$SQLITE3" -readonly "$SCRATCH" 2>&1) || stop "p8-d-query"
    printf '%s\n' "$_d" | "$AWK" '{ print "PHASEP P8 " $0 }'
    printf '%s\n' "$_d" | "$AWK" '/^idx=/ && $0 !~ / scanned_gone=0$/ { b = 1 } END { exit b ? 1 : 0 }' || stop "p8-scanned-gone"
    printf '%s\n' "$_d" | "$AWK" -v g="$GONE_EXPECT" -v e="$R_ERRORS" '/^TOTAL / { t++; if (index($0, " gone=" g " ") && index($0, " scanned_gone=0 ") && $NF == "unscanned_all=" e) ok++ } END { exit (t == 1 && ok == 1) ? 0 : 1 }' || stop "p8-total"
    mark p8 "P8-OK"
    say "SUMMARY mode=fill stamp=$STAMP exit=0"
    exit 0
}

case "$MODE" in
    offline) [ "$#" -eq 1 ] || usage; load_tools; run_offline ;;
    fill) [ "$#" -eq 2 ] || usage; load_tools; run_fill "$2" ;;
    *) usage ;;
esac
