#!/bin/zsh
DB="$1"; OUT="$2"; ERR="$OUT.stderr"; EXPECT="$3"
case "$EXPECT" in
  ''|*[!0-9]*) exit 2 ;;
esac
[ "$EXPECT" -gt 0 ] || exit 2
set -o noclobber
for f in "$OUT" "$ERR"; do
  if [[ -e "$f" || -L "$f" ]]; then set +o noclobber; echo REFUSE_EXISTS; exit 1; fi
done
( set -o pipefail
  emptyhash=$(printf '' | shasum -a 256 | awk '{print $1}') || exit 1
  bad=$(sqlite3 -readonly "$DB" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT IN ('ask_audit','drafts') AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' AND (instr(name, char(10)) OR instr(name, char(13)));") || { echo "ERROR names" >&2; exit 1; }
  [ "$bad" = 0 ] || { echo "ERROR oddname" >&2; exit 1; }
  sqlite3 -readonly "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('ask_audit','drafts') AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name;" |
  while IFS= read -r t; do
    [ -n "$t" ] || continue
    ident=$(printf '%s' "$t" | sed 's/"/""/g')
    lit=$(printf '%s' "$t" | sed "s/'/''/g")
    count=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM \"$ident\";") || { echo "ERROR count $t" >&2; exit 1; }
    case "$count" in ''|*[!0-9]*) echo "ERROR count $t" >&2; exit 1;; esac
    if [ "$t" = "messages" ]; then filt="name != 'has_attachments'"; else filt="1=1"; fi
    cols=$(sqlite3 -readonly "$DB" "SELECT '\"' || replace(name,'\"','\"\"') || '\"' FROM pragma_table_info('$lit') WHERE $filt ORDER BY cid;" | paste -sd, -) || { echo "ERROR cols $t" >&2; exit 1; }
    [ -n "$cols" ] || { echo "ERROR cols $t" >&2; exit 1; }
    order=$(sqlite3 -readonly "$DB" "SELECT '\"' || replace(name,'\"','\"\"') || '\"' FROM pragma_table_info('$lit') WHERE pk>0 ORDER BY pk;" | paste -sd, -) || { echo "ERROR order $t" >&2; exit 1; }
    [ -n "$order" ] || order="rowid"
    hash=$(sqlite3 -readonly "$DB" <<SQL | shasum -a 256 | awk '{print $1}'
.mode quote
.headers off
SELECT $cols FROM "$ident" ORDER BY $order;
SQL
) || { echo "ERROR hash $t" >&2; exit 1; }
    [ -n "$hash" ] || { echo "ERROR emptyhash $t" >&2; exit 1; }
    [ "$count" -eq 0 ] || [ "$hash" != "$emptyhash" ] || { echo "ERROR emptyhash $t" >&2; exit 1; }
    printf '%s\t%s\t%s\n' "$t" "$count" "$hash"
  done ) > "$OUT" 2> "$ERR"; rc=$?
set +o noclobber
lines=$(wc -l < "$OUT" | tr -d ' ')
errb=$(wc -c < "$ERR" | tr -d ' ')
if [[ $rc -eq 0 && $errb -eq 0 && $lines -gt 0 && ( -z "${EXPECT:-}" || $lines -eq ${EXPECT} ) ]]; then
  echo "K_OK lines=$lines"
else
  echo "K_FAIL rc=$rc stderr_bytes=$errb"
  exit 1
fi
