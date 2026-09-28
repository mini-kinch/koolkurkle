#!/bin/bash
# Leftover pgrep patterns must ignore macOS system daemons and still
# see a real /usr/bin/security child. Patterns are read from att0_l1.sh.
# Matching is POSIX ERE, the same class macOS pgrep -f uses.
set -u
set -o pipefail

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
SCRIPT="$ROOT/ops/att0/att0_l1.sh"

fail() {
    printf '%s\n' "FAIL $*" >&2
    exit 1
}

pat_of() {
    /usr/bin/awk -F"'" -v key="$1" 'index($0, key "='\''") == 1 { print $2; exit }' "$SCRIPT"
}

SECURITY_PAT=$(pat_of SECURITY_PAT)
PERL_PAT=$(pat_of PERL_PAT)
TIME_PAT=$(pat_of TIME_PAT)
CURL_PAT=$(pat_of CURL_PAT)
WRITER_PAT=$(pat_of WRITER_PAT)

[ "$SECURITY_PAT" = '^/usr/bin/security( |$)' ] || fail "SECURITY_PAT ${SECURITY_PAT}"
[ "$PERL_PAT" = '^/usr/bin/perl( |$)' ] || fail "PERL_PAT ${PERL_PAT}"
[ "$TIME_PAT" = '^/usr/bin/time( |$)' ] || fail "TIME_PAT ${TIME_PAT}"
[ "$CURL_PAT" = '^/usr/bin/curl( |$)' ] || fail "CURL_PAT ${CURL_PAT}"
[ -n "$WRITER_PAT" ] || fail "WRITER_PAT empty"

# leftover_clear must use the variables, not the bare substring.
if /usr/bin/awk '
    /^leftover_clear\(\)/ { on = 1 }
    on && /\[s\]ecurity/ { bad = 1 }
    on && /"\$SECURITY_PAT"/ { sec = 1 }
    on && /"\$PERL_PAT"/ { perl = 1 }
    on && /"\$TIME_PAT"/ { tim = 1 }
    on && /^}/ { exit }
    END { if (bad || !sec || !perl || !tim) exit 1 }
' "$SCRIPT"; then
    :
else
    fail "leftover_clear is not using the anchored patterns"
fi

matches() {
    printf '%s\n' "$2" | /usr/bin/grep -E -q -- "$1"
}

# First matching leftover pattern, in leftover_clear order. Empty if clear.
first_hit() {
    _line=$1
    for _p in "$WRITER_PAT" "$CURL_PAT" "$SECURITY_PAT" "$PERL_PAT" "$TIME_PAT"; do
        if matches "$_p" "$_line"; then
            printf '%s\n' "$_p"
            return 0
        fi
    done
    return 1
}

must_clear() {
    if _hit=$(first_hit "$1"); then
        fail "false leftover hit: $1  pat=${_hit}"
    fi
}

must_match() {
    if ! matches "$1" "$2"; then
        fail "missed: $2  pat=$1"
    fi
}

must_miss() {
    if matches "$1" "$2"; then
        fail "anchored pattern hit: $2  pat=$1"
    fi
}

# The retired substring still matches securityd. The anchored pattern must not.
if ! matches '[s]ecurity' '/usr/libexec/securityd'; then
    fail "fixture no longer shows the unanchored securityd hit"
fi
if matches "$SECURITY_PAT" '/usr/libexec/securityd'; then
    fail "anchored security pattern matches securityd"
fi

# Clean-Mini system commands. None of these is the window script.
while IFS= read -r _cmd; do
    [ -n "$_cmd" ] || continue
    must_clear "$_cmd"
done <<'EOF'
/usr/libexec/securityd
/usr/sbin/securityd
/usr/bin/securityd
/usr/libexec/secd
/System/Library/Frameworks/Security.framework/Versions/A/Resources/secd
/System/Library/Frameworks/Security.framework/Versions/A/XPCServices/com.apple.security.XPCKeychainSandboxCheck.xpc/Contents/MacOS/com.apple.security.XPCKeychainSandboxCheck
/usr/libexec/trustd
/usr/libexec/timed
/usr/sbin/timed
/usr/bin/perl5.34 /usr/libexec/periodic-wrapper daily
/Applications/Xcode.app/Contents/Developer/usr/bin/perl /usr/bin/periodic
/bin/bash /tmp/att0_l1.sh window 20260928-011821
/bin/bash
-zsh
EOF

# Confirmed Mini pgrep -fl '[s]ecurity' lines, then the same commands
# without the pid. pgrep -f matches the command; -l only prefixes the
# pid in the printout. This harness applies the regex to the given
# string, so both forms are subjects.
while IFS= read -r _line; do
    [ -n "$_line" ] || continue
    if ! matches '[s]ecurity' "$_line"; then
        fail "retired substring missed confirmed line: ${_line}"
    fi
    must_clear "$_line"
    must_miss "$SECURITY_PAT" "$_line"
    must_miss "$PERL_PAT" "$_line"
    must_miss "$TIME_PAT" "$_line"
done <<'EOF'
386 /usr/sbin/securityd -i
8552 /usr/libexec/securityd_system
/usr/sbin/securityd -i
/usr/libexec/securityd_system
EOF

# Real leftover children. The command field starts with the pinned binary.
must_match "$SECURITY_PAT" "/usr/bin/security find-generic-password -s example -w"
if ! first_hit "/usr/bin/security find-generic-password -s example -w" >/dev/null; then
    fail "real security child was clear"
fi
must_match "$PERL_PAT" "/usr/bin/perl -e 'print 1'"
if ! first_hit "/usr/bin/perl -e 'print 1'" >/dev/null; then
    fail "real perl child was clear"
fi
must_match "$TIME_PAT" "/usr/bin/time -l /usr/bin/true"
if ! first_hit "/usr/bin/time -l /usr/bin/true" >/dev/null; then
    fail "real time child was clear"
fi
must_match "$CURL_PAT" "/usr/bin/curl -s -o /dev/null -w %{http_code} --max-time 3 http://127.0.0.1:8743/health"

# Neighbor binaries that share a prefix must stay clear.
must_clear "/usr/bin/securityd"
must_clear "/usr/bin/perl5.34"
must_clear "/usr/bin/timed"

printf '%s\n' "LEFTOVER-PATTERNS-OK"
