#!/bin/bash
# Linux stand-in for the Mac mini tools. Runs the operator script.
set -u
set -o pipefail

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
SCRIPT="$ROOT/ops/att0/att0_l1.sh"
export ATT0_REAL_META_FILL="$ROOT/scripts/attachments/meta_fill.py"
FAKES="$ROOT/ops/att0/tests/fakes"
FIX="$ROOT/ops/att0/tests/fixtures"
CANARY=CANARY-SECRET-VALUE

fail() {
    printf '%s\n' "FAIL $*" >&2
    exit 1
}

/bin/bash -n "$SCRIPT" || fail "bash -n"
/bin/bash "$ROOT/ops/att0/tests/test_leftover_patterns.sh" || fail "leftover patterns"
_r1z="$ROOT/ops/att0/r1v2_digest.zsh"
_r1tail=$(tail -n 39 "$_r1z" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1; exit}')
if [ "$_r1tail" != "f862c5ba3ad2ae2b86fe1be19ca348f34b39eea1c016bf4a56b9375fbd9e9cf0" ]; then
    fail "r1v2 tail sha ${_r1tail}"
fi
/bin/zsh "$_r1z" /tmp/no-such.sqlite /tmp/att0-r1-refuse /tmp/att0-r1-refuse.out 0 >/tmp/att0-r1-expect.out
if [ "$?" -ne 2 ]; then
    fail "r1v2 expect 0 rc"
fi
if /usr/bin/grep -n -E 'declare -A|mapfile|readarray|\[\[ -v|wait -n|\|&|\$\{[A-Za-z_][A-Za-z0-9_]*,,|\$\{[A-Za-z_][A-Za-z0-9_]*\^\^' "$SCRIPT"; then
    fail "forbidden construct"
fi
if /usr/bin/grep -n -E '/Users/|unlock-keychain|set-generic-password|partition-list' "$SCRIPT"; then
    fail "forbidden path or keychain workaround"
fi
# /usr/bin/security may appear only as the anchored pgrep pattern.
# The window script does not invoke the keychain binary.
_sec_bad=$(/usr/bin/grep -n '/usr/bin/security' "$SCRIPT" | /usr/bin/grep -v -F "SECURITY_PAT='^/usr/bin/security( |$)'" || true)
if [ -n "$_sec_bad" ]; then
    printf '%s\n' "$_sec_bad" >&2
    fail "forbidden security invocation"
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
if /usr/bin/grep -n -E 'PROBE_TIMEOUT_S|PROBE_DB|ACCESS-PROBE-OK|STOP-access-probe|STOP-probe-not-dry|STOP-probe-copy|PROBE-COPY-OK|att0w-probe-|att0-probe-' "$SCRIPT"; then
    fail "access probe still present"
fi
if ! /usr/bin/grep -F -q '^(error:|' "$SCRIPT"; then
    fail "keep list missing error prefix"
fi
/usr/bin/python3 - "$ROOT" <<'PY' || fail "refuse strings"
import ast
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
bad = re.compile(r"/Users/|@[A-Za-z0-9._+-]+\.[A-Za-z]{2,}|\b/home/")

def consts(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return consts(node.left)
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
        return out
    return []

msgs = []
for rel, names in (
    ("scripts/attachments/meta_fill.py", ("FillRefuse",)),
    ("scripts/imap_curl.py", ("CurlImapError",)),
):
    path = root / rel
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name not in names or not node.exc.args:
            continue
        found = consts(node.exc.args[0])
        if found:
            msgs.extend(found)
            continue
        arg = node.exc.args[0]
        forwarded = (
            name == "FillRefuse"
            and isinstance(arg, ast.Call)
            and getattr(arg.func, "id", None) == "str"
        )
        if not forwarded:
            sys.stderr.write("unparsed %s in %s\n" % (name, rel))
            raise SystemExit(1)
if len(msgs) < 8:
    sys.stderr.write("too few refuse strings\n")
    raise SystemExit(1)
for msg in msgs:
    if bad.search(msg):
        sys.stderr.write("secret in refuse string\n")
        raise SystemExit(1)
PY
printf '%s\n' "REFUSE-STRINGS-OK"
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

# Fixture fill logs come from the real format_report, not hand-written lines.
write_fill_log() {
    /usr/bin/python3 - "$ATT0_REAL_META_FILL" "$1" "$2" <<'PY'
import importlib.util
import sys
src, dest, mode = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("att0_real_meta_fill", src)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
report = {
    "dry_run": False,
    "source": "imap",
    "db_basename": "mailroom.sqlite",
    "messages": 0,
    "parts": 0,
    "has_attachments": 0,
    "filenames": 0,
    "bytes_stored": 0,
    "scanned": 1063,
    "stopped": "",
    "capped": 0,
    "errors": 1063,
    "eligible": 1063,
    "skipped": 0,
    "partial": False,
    "partial_banner": "",
    "parts_truncated": 1,
    "uidvalidity_mismatch": 0,
    "curl_failures": [],
    "literal_dropped": 0,
    "literal_truncated": 0,
    "literal_folders": [],
}
if mode == "curl":
    report["curl_failures"] = ["imap login failed"]
if mode == "partial":
    report["partial"] = True
    report["partial_banner"] = "PARTIAL: scanned 0 of 1063 (limit 1)"
text = mod.format_report(report)
lines = text.splitlines()
if mode == "missing":
    lines = [ln for ln in lines if not ln.startswith("summary_json=")]
elif mode == "dup":
    lines.append(next(ln for ln in lines if ln.startswith("summary_json=")))
elif mode == "badjson":
    lines = ["summary_json={" if ln.startswith("summary_json=") else ln for ln in lines]
text = "\n".join(lines) + "\n"
open(dest, "w").write(text)
PY
}

_kvlog=/tmp/att0-kv-good.log
write_fill_log "$_kvlog" clean
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-good.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'FILL-REPORT-OK' || {
    printf '%s\n' "$_kv" >&2
    fail "kv good fill"
}
printf '%s\n' "$_kv" | /usr/bin/grep -q '^RC=0$' || fail "kv good rc"
printf '%s\n' 'capped=0' >> "$_kvlog"
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-good.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-capped' || fail "kv duplicate"
printf '%s\n' "$_kv" | /usr/bin/grep -q '^RC=0$' && fail "kv duplicate rc"
sed -i '/^capped=/d' "$_kvlog"
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-good.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-capped' || fail "kv missing"
printf '%s\ncapped=1\n' "$(cat "$_kvlog")" > "$_kvlog"
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-good.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-capped' || fail "kv nonzero"
write_fill_log /tmp/att0-kv-curl.log curl
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-curl.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-curl-failures' || fail "kv curl"
write_fill_log /tmp/att0-kv-miss.log missing
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-miss.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-curl-failures' || fail "kv summary missing"
write_fill_log /tmp/att0-kv-dupjson.log dup
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-dupjson.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-curl-failures' || fail "kv summary dup"
write_fill_log /tmp/att0-kv-badjson.log badjson
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-badjson.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-curl-failures' || fail "kv summary bad"
write_fill_log /tmp/att0-kv-partial.log partial
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_fill /tmp/att0-kv-partial.log 1
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-partial' || fail "kv partial"
write_fill_log /tmp/att0-kv-good.log clean
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    fill_gates_ok /tmp/att0-kv-good.log
    printf 'GATE=%s\n' "$?"
    fill_gates_ok /tmp/att0-kv-curl.log
    printf 'GATECURL=%s\n' "$?"
    fill_gates_ok /tmp/att0-kv-partial.log
    printf 'GATEPART=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q '^GATE=0$' || fail "report fill gate"
printf '%s\n' "$_kv" | /usr/bin/grep -q '^GATECURL=0$' && fail "report curl gate"
printf '%s\n' "$_kv" | /usr/bin/grep -q '^GATEPART=0$' && fail "report partial gate"
printf '%s\nuser_version=1\nuser_version=1\nlegacy_attachments=renamed_empty\n' x > /tmp/att0-kv-mig.log
_kv=$(
    set +e
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    AWK=/usr/bin/awk
    log_ok_migrate /tmp/att0-kv-mig.log first
    printf 'RC=%s\n' "$?"
)
printf '%s\n' "$_kv" | /usr/bin/grep -q 'STOP-user-version' || fail "kv migrate dup"
printf '%s\n' "KV-ONE-OK"

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

# A refused child prints error: and exits 2. The keep list must retain that
# line and still drop anything else. The sample is a FillRefuse schema line.
rm -f /tmp/att0-error-child.log /tmp/att0-error-child.out
mkdir -p /tmp/att0-error-ma /tmp/att0-error-state
: > /tmp/att0-error-ma/mailroom.write.lock
: > /tmp/att0-error-ma/mailroom.daily.lock
set +e
(
    set +C
    # shellcheck disable=SC1090
    . "$SCRIPT"
    set +e
    PFX=ATT0T
    N=0
    TRANSCRIPT=
    MA=/tmp/att0-error-ma
    S_DIR="$FIX"
    ATT0_STATE=/tmp/att0-error-state
    export ATT0_STATE
    AWK=/usr/bin/awk
    PYTHON=/usr/bin/python3
    PERL=/usr/bin/perl
    PGREP="$FAKES/pgrep"
    LSOF="$FAKES/lsof"
    PS=/bin/ps
    STAMP=20260928-130088
    run_group 30 /tmp/att0-error-child.log /usr/bin/python3 -c 'import sys; sys.stderr.write("error: messages.has_attachments is missing; migrate first\n"); sys.stdout.write("NOT-KEPT-LINE\n"); raise SystemExit(2)'
    printf '%s\n' "RC=$?"
) > /tmp/att0-error-child.out 2>&1
set -e
/usr/bin/grep -q '^error: messages.has_attachments is missing; migrate first$' /tmp/att0-error-child.log || {
    cat /tmp/att0-error-child.log /tmp/att0-error-child.out >&2
    fail "error line dropped"
}
/usr/bin/grep -q 'NOT-KEPT-LINE' /tmp/att0-error-child.log && fail "non-kept line retained"
/usr/bin/grep -q '/Users/\|@' /tmp/att0-error-child.log && fail "secret in error log"
/usr/bin/grep -q '^RC=0$' /tmp/att0-error-child.out && fail "error child rc 0"
printf '%s\n' "ERROR-LINE-OK"

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
CREATE TABLE attachments (id INTEGER PRIMARY KEY);
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
    _pad=1
    while [ "$_pad" -le 23 ]; do
        _name=$(printf 'pad_%02d' "$_pad")
        /usr/bin/sqlite3 "$1" "CREATE TABLE ${_name} (id INTEGER);"
        _pad=$((_pad + 1))
    done
}

setup_tree() {
    _name=$1
    rm -f "/tmp/att0w-claimed-${P_STAMP}" \
        "/tmp/att0w-done-${ATT0_FAKE_STAMP}.OK" \
        "/tmp/att0w-state-${ATT0_FAKE_STAMP}" \
        "/tmp/att0w-s1-${ATT0_FAKE_STAMP}" \
        "/tmp/att0r-done-${ATT0_FAKE_STAMP}.OK" \
        "/tmp/att0r-verified-${ATT0_FAKE_STAMP}.OK" \
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

rm -f /tmp/att0-r1-count.sqlite /tmp/att0-r1-count.tsv /tmp/att0-r1-count.tsv.stderr
seed_db /tmp/att0-r1-count.sqlite
if ! /bin/zsh "$ROOT/ops/att0/r1v2_digest.zsh" /tmp/att0-r1-count.sqlite /tmp/att0-r1-count.tsv 25 > /tmp/att0-r1-count.out; then
    cat /tmp/att0-r1-count.out >&2
    fail "r1v2 pre count"
fi
/usr/bin/grep -q '^K_OK lines=25$' /tmp/att0-r1-count.out || fail "r1v2 pre line"
test ! -s /tmp/att0-r1-count.tsv.stderr || fail "r1v2 pre stderr"
printf '%s\n' "R1V2-PRECOUNT-OK"

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
unset ATT0_FAKE_REH_CURL ATT0_FAKE_A3_RC ATT0_FAKE_A3_HANG ATT0_FAKE_FALLBACK ATT0_FAKE_DATE_BREACH ATT0_TEST_MAX_ALARM ATT0_FAKE_SECURITY_RC ATT0_WRITE_HELD
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
/usr/bin/grep -q 'P-STAMP-AGE-OK' /tmp/att0-happy && fail "happy age gate"
/usr/bin/grep -q 'STOP-p-stamp-age' /tmp/att0-happy && fail "happy age stop"
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
/usr/bin/grep -E '^ATT0W [0-9]+ R1V2-BEFORE K_OK lines=25$' /tmp/att0-happy || fail "happy r1 before"
/usr/bin/grep -E '^ATT0W [0-9]+ R1V2-POSTA2 K_OK lines=34$' /tmp/att0-happy || fail "happy r1 posta2"
/usr/bin/grep -E '^ATT0W [0-9]+ R1V2-AFTER K_OK lines=34$' /tmp/att0-happy || fail "happy r1 after"
/usr/bin/grep -E '^ATT0W [0-9]+ LOGICAL_MATCH_BK=YES$' /tmp/att0-happy || fail "happy logical bk"
/usr/bin/grep -E '^ATT0W [0-9]+ LOGICAL_MATCH=YES$' /tmp/att0-happy || fail "happy logical"
/usr/bin/grep -E 'LOGICAL_MATCH=NO$' /tmp/att0-happy && fail "happy logical no"
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
expect_rc "$rc" 0 /tmp/att0-report
/usr/bin/grep -q '^ATT0-DONE PASS$' /tmp/att0-report || fail "report overall pass"
/usr/bin/grep -q 'ATT0-DONE FAIL' /tmp/att0-report && fail "report overall fail"
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
/usr/bin/grep -q 'ATT0-DONE RULE a4-r1v2 PASS' /tmp/att0-report || fail "report r1v2 rule"
/usr/bin/grep -q 'ATT0-DONE RULE leak-w21 PASS' /tmp/att0-report || fail "report leak"
/usr/bin/grep -q 'ATT0-DONE RULE search-restored PASS' /tmp/att0-report || fail "report search"
if /usr/bin/grep -E 'PROBE-COPY-OK|STOP-probe-copy|STOP-access-probe|STOP-probe-not-dry|ACCESS-PROBE-OK|att0w-probe-|att0-probe-' /tmp/att0-happy; then
    fail "happy probe line"
fi
test ! -e "$HOME/MailArchive/logs/att0w-probe-${ATT0_FAKE_STAMP}.log" || fail "happy probe log"
test ! -d "$HOME/MailArchive/dryrun/att0-probe-${ATT0_FAKE_STAMP}" || fail "happy probe copy"
if ! /usr/bin/awk '
    $1 != "ATT0W" { next }
    {
        msg = $0
        sub(/^ATT0W [0-9]+ /, "", msg)
        num = $2 + 0
    }
    msg ~ /^imap_user_set len=/ && base == 0 {
        base = num
        next
    }
    msg ~ /^imap_user_set len=/ && base != 0 { exit 1 }
    base > 0 && filled == 0 {
        i++
        if (num != base + i) bad = 1
        got[i] = msg
    }
    base > 0 && filled == 0 && msg == "FILL-REPORT-OK" { filled = num }
    msg == "DEADLINE-OK" { deadline = num }
    msg == "SEARCH-BOOTED-OUT" { booted = num }
    END {
        if (base == 0 || filled == 0 || bad != 0 || i != 11) exit 1
        if (got[1] != "GROUP-EMPTY") exit 1
        if (got[2] != "FLOCK-WRITE-FREE") exit 1
        if (got[3] != "FOLLOWUP-CLEAR with_writer_lock meta_fill curl security perl time script locks") exit 1
        if (got[4] != "LEFTOVER-CLEAR") exit 1
        if (got[5] != "MIGRATE-OK") exit 1
        if (got[6] != "GROUP-EMPTY") exit 1
        if (got[7] != "FLOCK-WRITE-FREE") exit 1
        if (got[8] != "FOLLOWUP-CLEAR with_writer_lock meta_fill curl security perl time script locks") exit 1
        if (got[9] != "LEFTOVER-CLEAR") exit 1
        if (got[10] != "PARTS-TRUNCATED-INFO parts_truncated=1") exit 1
        if (got[11] != "FILL-REPORT-OK") exit 1
        if (deadline <= filled || booted <= deadline) exit 1
        printf "PROBE-SLOT imap=%d rehearse-migrate=%d rehearse-fill=%d deadline=%d bootout=%d\n", base, base + 5, filled, deadline, booted
    }
' /tmp/att0-happy; then
    fail "rehearsal step numbers"
fi
printf '%s\n' "HAPPY-WINDOW-OK"

ATT0_FAKE_STAMP=20260928-120011
export ATT0_FAKE_STAMP
rc=$(run_mode /tmp/att0-second window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-second
/usr/bin/grep -q 'STOP-window-already-claimed' /tmp/att0-second || fail "second claim"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-second || fail "second safe"
/usr/bin/grep -q 'SEARCH-BOOTED-OUT' /tmp/att0-second && fail "second bootout"
printf '%s\n' "SECOND-WINDOW-OK"

# Rehearsal fill is the Keychain and IMAP gate before S. A child that
# returns 0 with a non-empty curl_failures list stops here.
P_STAMP=20260928-010002
ATT0_FAKE_STAMP=20260928-120002
export ATT0_FAKE_STAMP ATT0_FAKE_REH_CURL=1
setup_tree reh-curl
rc=$(run_mode /tmp/att0-reh-curl window "$P_STAMP")
expect_rc "$rc" 1 /tmp/att0-reh-curl
/usr/bin/grep -q 'STOP-curl-failures' /tmp/att0-reh-curl || fail "reh curl stop"
/usr/bin/grep -E 'write_rc=|DEADLINE-OK|bootout_rc=|SEARCH-BOOTED-OUT|DEADLINE-26-MARGIN|A2-OK|BACKUP-OK|D-CHECK-OK' /tmp/att0-reh-curl && fail "reh curl reached S"
/usr/bin/grep -q 'bootout' "${ATT0_STATE}/launchctl.log" && fail "reh curl booted search"
test ! -e "/tmp/att0w-s1-${ATT0_FAKE_STAMP}" || fail "reh curl wrote s1"
ls "$HOME/MailArchive/backups"/mailroom-pre-att0-window-* >/dev/null 2>&1 && fail "reh curl backup"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-reh-curl || fail "reh curl safe"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120002 exit=1' /tmp/att0-reh-curl || fail "reh curl summary"
if /usr/bin/grep -E 'PROBE-COPY-OK|STOP-probe-copy|STOP-access-probe|STOP-probe-not-dry|ACCESS-PROBE-OK|att0w-probe-|att0-probe-' /tmp/att0-reh-curl; then
    fail "reh curl probe line"
fi
test ! -e "$HOME/MailArchive/logs/att0w-probe-${ATT0_FAKE_STAMP}.log" || fail "reh curl probe log"
test ! -d "$HOME/MailArchive/dryrun/att0-probe-${ATT0_FAKE_STAMP}" || fail "reh curl probe copy"
unset ATT0_FAKE_REH_CURL
printf '%s\n' "REH-CURL-OK"

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
test -f /tmp/att0r-verified-20260928-120004.OK || fail "a3 arr marker"
/usr/bin/grep -q 'ROLLED-BACK-VERIFIED' /tmp/att0-a3 || fail "a3 verified"
/usr/bin/grep -E '^ATT0W [0-9]+ R1V2-ARR K_OK lines=25$' /tmp/att0-a3 || fail "a3 r1 arr"
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

# P_STAMP is 48h before the fake window clock. There is no start deadline.
P_STAMP=20260926-120018
ATT0_FAKE_STAMP=20260928-120018
export ATT0_FAKE_STAMP
setup_tree old-stamp
rc=$(run_mode /tmp/att0-old-stamp window "$P_STAMP")
expect_rc "$rc" 0 /tmp/att0-old-stamp
/usr/bin/grep -q 'STOP-p-stamp-age' /tmp/att0-old-stamp && fail "old stamp age stop"
/usr/bin/grep -q 'P-STAMP-AGE-OK' /tmp/att0-old-stamp && fail "old stamp age gate"
/usr/bin/grep -q 'PHASEP-MARKERS-OK' /tmp/att0-old-stamp || fail "old stamp markers"
/usr/bin/grep -q 'D-BAND D_P=27 low=27 high=77' /tmp/att0-old-stamp || fail "old stamp band"
/usr/bin/grep -q 'STEP0-LOCKS-OK' /tmp/att0-old-stamp || fail "old stamp locks"
/usr/bin/grep -q 'BUDGET-OK a2' /tmp/att0-old-stamp || fail "old stamp budget"
/usr/bin/grep -q 'WINDOW-DONE' /tmp/att0-old-stamp || fail "old stamp done"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120018 exit=0' /tmp/att0-old-stamp || fail "old stamp summary"
printf '%s\n' "OLD-STAMP-OK"

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

# att0r-done alone is not the gate. att0w-done or att0r-verified is.
P_STAMP=20260928-010019
ATT0_FAKE_STAMP=20260928-120019
export ATT0_FAKE_STAMP
setup_tree arr-done-only
printf '%s\n' "$ATT0_FAKE_STAMP" > "/tmp/att0r-done-${ATT0_FAKE_STAMP}.OK"
rc=$(run_mode /tmp/att0-arr-done-only restore-daily "$ATT0_FAKE_STAMP")
expect_rc "$rc" 1 /tmp/att0-arr-done-only
/usr/bin/grep -q 'STOP-no-window-or-arr-marker' /tmp/att0-arr-done-only || fail "arr-done alone"
/usr/bin/grep -q 'enable' "${ATT0_STATE}/launchctl.log" && fail "arr-done enabled"
printf '%s\n' "ARR-DONE-ONLY-REFUSE-OK"

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
_pm=$(/usr/bin/sqlite3 "$HOME/MailArchive/mailroom.sqlite" "SELECT COUNT(*) FROM messages;")
_pr=$(/usr/bin/sqlite3 "$HOME/MailArchive/mailroom.sqlite" "SELECT coalesce(MAX(rowid),0) FROM messages;")
_pg=$(/usr/bin/sqlite3 "$HOME/MailArchive/mailroom.sqlite" "SELECT sum(present_on_server=0) FROM messages WHERE source='imap-live' AND folder IS NOT NULL AND trim(folder)<>'';")
test "$_pm" = "1063" && test "$_pr" = "1063" && test "$_pg" = "992" || fail "rollback pre counts ${_pm} ${_pr} ${_pg}"
printf '%s\n' "pre_messages=${_pm}" "pre_max_rowid=${_pr}" "pre_gone=${_pg}" >> /tmp/att0w-state-20260928-129999
rm -f "$HOME/MailArchive/logs/att0w-r1v2-before-20260928-129999.tsv" \
    "$HOME/MailArchive/logs/att0w-r1v2-before-20260928-129999.tsv.stderr"
if ! /bin/zsh "$ROOT/ops/att0/r1v2_digest.zsh" \
    "$HOME/MailArchive/backups/mailroom-pre-att0-window-20260928-129999.sqlite" \
    "$HOME/MailArchive/logs/att0w-r1v2-before-20260928-129999.tsv" \
    25 > /tmp/att0-rollback-r1.out; then
    cat /tmp/att0-rollback-r1.out >&2
    fail "rollback before digest"
fi
/usr/bin/grep -q '^K_OK lines=25$' /tmp/att0-rollback-r1.out || fail "rollback before line"
rm -f /tmp/att0-restore-20260928-129999.sh /tmp/att0r-verified-20260928-129999.OK
rc=$(run_mode /tmp/att0-rollback rollback 20260928-129999)
expect_rc "$rc" 0 /tmp/att0-rollback
/usr/bin/grep -q 'ROLLBACK-DONE' /tmp/att0-rollback || fail "rollback done"
/usr/bin/grep -q 'RESULT ar-r=ran search=restored' /tmp/att0-rollback || fail "rollback result"
/usr/bin/grep -q 'SAFE-STATE' /tmp/att0-rollback || fail "rollback safe"
/usr/bin/grep -q 'SUMMARY stamp=20260928-120008 exit=0' /tmp/att0-rollback || fail "rollback summary"
test -f /tmp/att0r-verified-20260928-129999.OK || fail "rollback marker"
/usr/bin/grep -q 'ROLLED-BACK-VERIFIED' /tmp/att0-rollback || fail "rollback verified"
/usr/bin/grep -E '^ATT0R [0-9]+ R1V2-ARR K_OK lines=25$' /tmp/att0-rollback || fail "rollback r1 arr"
printf '%s\n' "ROLLBACK-OK"

# D3. A verify miss prints the backup path, does not swap again, and still restores search.
P_STAMP=20260928-010020
ATT0_FAKE_STAMP=20260928-120020
export ATT0_FAKE_STAMP
setup_tree rollback-bad
/usr/bin/sqlite3 "$HOME/MailArchive/mailroom.sqlite" ".backup '${HOME}/MailArchive/backups/mailroom-pre-att0-window-20260928-129998.sqlite'"
bk_sha=$(/usr/bin/sha256sum "$HOME/MailArchive/backups/mailroom-pre-att0-window-20260928-129998.sqlite" | /usr/bin/awk '{print $1; exit}')
printf '%s\n' \
    "bk_path=${HOME}/MailArchive/backups/mailroom-pre-att0-window-20260928-129998.sqlite" \
    "bk_sha=${bk_sha}" \
    "p_stamp=${P_STAMP}" \
    "window_stamp=20260928-129998" \
    "pre_messages=1063" \
    "pre_max_rowid=1063" \
    "pre_gone=1" \
    > /tmp/att0w-state-20260928-129998
printf '%s\n' "s_epoch=1700000000" "run_id=att0-L1-20260928-129998" > /tmp/att0w-s1-20260928-129998
rm -f "$HOME/MailArchive/logs/att0w-r1v2-before-20260928-129998.tsv" \
    "$HOME/MailArchive/logs/att0w-r1v2-before-20260928-129998.tsv.stderr"
if ! /bin/zsh "$ROOT/ops/att0/r1v2_digest.zsh" \
    "$HOME/MailArchive/backups/mailroom-pre-att0-window-20260928-129998.sqlite" \
    "$HOME/MailArchive/logs/att0w-r1v2-before-20260928-129998.tsv" \
    25 > /tmp/att0-rollback-bad-r1.out; then
    cat /tmp/att0-rollback-bad-r1.out >&2
    fail "rollback-bad before digest"
fi
rm -f /tmp/att0-restore-20260928-129998.sh /tmp/att0r-verified-20260928-129998.OK
rc=$(run_mode /tmp/att0-rollback-bad rollback 20260928-129998)
expect_rc "$rc" 3 /tmp/att0-rollback-bad
/usr/bin/grep -q "ROLLBACK-FAILED verify backup=${HOME}/MailArchive/backups/mailroom-pre-att0-window-20260928-129998.sqlite" /tmp/att0-rollback-bad || fail "verify fail line"
_swaps=$(/usr/bin/grep -c 'RESTORE-SWAPPED' "$HOME/MailArchive/logs/att0-arr-20260928-129998.log" || true)
test "$_swaps" = "1" || fail "verify fail swaps ${_swaps}"
/usr/bin/grep -q 'ROLLED-BACK-VERIFIED' /tmp/att0-rollback-bad && fail "verify fail looked verified"
test ! -e /tmp/att0r-verified-20260928-129998.OK || fail "verify fail marker"
/usr/bin/grep -q 'search=restored' /tmp/att0-rollback-bad || fail "verify fail search"
printf '%s\n' "ROLLBACK-VERIFY-FAIL-OK"

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
