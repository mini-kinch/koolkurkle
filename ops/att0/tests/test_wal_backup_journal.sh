#!/bin/bash
# A .backup of a WAL database keeps WAL mode and no side files.
# sqlite3 -readonly then fails with SQLITE_CANTOPEN (14).
# PRAGMA journal_mode=DELETE switches that copy to rollback mode,
# leaves no -wal or -shm, and quick_check returns ok.
set -u
set -o pipefail

if [ "${WALTEST_INNER:-}" != "1" ]; then
    export WALTEST_INNER=1
    exec unshare --user --map-root-user --mount /bin/bash "$0" "$@"
fi

fail() {
    printf '%s\n' "FAIL $*" >&2
    exit 1
}

_d=$(mktemp -d /tmp/att0-walj.XXXXXX)
_out=$(mktemp -d /tmp/att0-walj-out.XXXXXX)
_src="${_d}/src.sqlite"
_bk="${_d}/bk.sqlite"

/usr/bin/sqlite3 "$_src" "PRAGMA journal_mode=WAL; CREATE TABLE t(v TEXT); INSERT INTO t VALUES ('walrow');" >/dev/null
/usr/bin/sqlite3 -readonly "$_src" ".backup '${_bk}'" >/dev/null
rm -f "${_src}-wal" "${_src}-shm" "${_bk}-wal" "${_bk}-shm"
_hex=$(od -An -t x1 -j 18 -N 2 "$_bk" | tr -d ' \n')
[ "$_hex" = "0202" ] || fail "wal header ${_hex}"
test ! -e "${_bk}-wal" || fail "backup left wal"
test ! -e "${_bk}-shm" || fail "backup left shm"

# This sqlite recreates -wal/-shm on a writable directory, even for
# -readonly, so the copy is bind-mounted read-only for the failing open.
mount --bind "$_d" "$_d"
mount -o remount,bind,ro "$_d"
set +e
/usr/bin/sqlite3 -readonly "$_bk" "PRAGMA quick_check;" >"${_out}/old.out" 2>"${_out}/old.err"
_old=$?
set -e
[ "$_old" = "14" ] || fail "old quick_check rc ${_old}"
/usr/bin/grep -q 'unable to open database file (14)' "${_out}/old.err" || fail "old quick_check error"
umount "$_d"

_jm=$(/usr/bin/sqlite3 "$_bk" "PRAGMA journal_mode=DELETE;") || fail "journal_mode"
[ "$_jm" = "delete" ] || fail "journal_mode ${_jm}"
test ! -e "${_bk}-wal" || fail "wal remained"
test ! -e "${_bk}-shm" || fail "shm remained"
_qc=$(/usr/bin/sqlite3 -readonly "$_bk" "PRAGMA quick_check;") || fail "quick_check rc"
[ "$_qc" = "ok" ] || fail "quick_check ${_qc}"
/usr/bin/sqlite3 "$_bk" "SELECT v FROM t;" | /usr/bin/grep -qx walrow || fail "row"

printf '%s\n' "WAL-BACKUP-JOURNAL-OK"
