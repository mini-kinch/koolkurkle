# ATT-0 fingerprint, diff, and backup

**Status:** helpers and tests. They do not migrate a database and they do not restore a backup.
**PII:** fingerprints emit counts and hashes. They do not emit folder names, subjects, addresses, or message ids. `imap_live_by_folder` is not a field.

Placeholder paths only. Run from a clone. No install.

```
python3 scripts/attachments/att0_fp.py /path/to/db.sqlite
python3 scripts/attachments/att0_fp.py /path/to/db.sqlite --out /tmp/att0-before.json
python3 scripts/attachments/att0_fpdiff.py /tmp/att0-before.json /tmp/att0-after.json
python3 scripts/attachments/att0_fpdiff.py /tmp/att0-before.json /tmp/att0-after.json --logical
python3 scripts/attachments/att0_backup.py /path/to/db.sqlite /tmp/att0-snapshot.sqlite
```

`PYTHONPATH` is not required. Each script inserts what it needs.

---

## Fingerprint (`att0_fp.py`)

Read-only. The helper does not create a `-wal` or a `-shm` beside the database, and it does not change the main-file bytes. Stats for the main file, `-wal`, and `-shm` are taken before the open. The JSON uses basenames only. `PRAGMA query_only=ON` is set.

When the `-wal` is missing or empty, the open is `file:...?mode=ro&immutable=1`. That open does not read uncheckpointed frames, and there are none. `immutable=1` is required on SQLite 3.51.0: a plain `mode=ro` open of a WAL database with no `-shm` fails there (`unable to open database file`), because a read-only connection cannot create the shared-memory file.

When the `-wal` is non-empty, `immutable=1` is not used. It would hide committed frames that are only in the `-wal`. The helper copies the main file and the `-wal` into a private directory and opens that copy `mode=ro`. The main file is a new inode, not a hardlink. SQLite's unix VFS keeps one shared-memory node per device and inode for the whole process. A hardlink would attach to the source `-shm` when any other connection in the process already has the source open, and that open rewrites the source `-shm`. The private directory is removed after the read. The fingerprinted path is not opened in place, so it gains no sidecar. Before the copy, the source directory must have free space for the main file, the `-wal`, and a 64 MiB margin. If it does not, the process exits 2 and stderr is `refuse: not enough free space`. If the private directory or the copy cannot be created, the process exits 2 and stderr is `refuse: cannot stage source wal`.

`journal_mode` is `wal` when header bytes 18 and 19 are 2. An `immutable=1` connection reports `delete` even for a WAL file, so those bytes are the source of truth. Otherwise `journal_mode` is the `PRAGMA journal_mode` value (`delete` for a rollback file).

Each ordinary table gets a `count` and a `sha256`. The table list is `sqlite_master` rows with `type='table'`, names that do not start with `sqlite_`, and `sql` that is not a `CREATE VIRTUAL TABLE` statement. `sql IS NULL` stays included. FTS5 and vec0 virtual tables are skipped and listed in `skipped_virtual_tables` (names only). Their shadow tables, for example `messages_fts_data` and `message_embeddings_rowids`, are ordinary tables and are hashed. Columns are in `cid` order. Rows are ordered by the primary key, or by `rowid` when there is no primary key. The hash covers SQLite `quote()` of those columns, one row per line. An empty table hashes the empty byte string.

Defaults, always recorded in `exclusions` and extended by the repeatable flags:

- `--exclude-table` adds to `ask_audit` and `drafts` (written by `ask_mail.py` serve).
- `--exclude-column TABLE.COL` adds to `messages.has_attachments`.

Changing only those tables or that column does not change table counts or hashes. Changing any other row does.

When `messages` has a `source` column, the JSON also has count fields: `imap_live_total`, `imap_live_null_folder`, `imap_live_folders_n`, and `imap_live_not_on_server` (the last two only when `folder` and `present_on_server` exist). A blank folder counts as null. The folder name is not written.

JSON is one document on stdout. `--out PATH` writes that document to PATH instead and leaves stdout empty. `--out` must not be the database file.

Exit 0 on success. Exit 1 when SQLite cannot read one table: stderr is `ERROR` plus that table name, and stdout has no fingerprint. The tool does not write a count or a hash for that table, and it does not hash empty input after a failed read. Exit 2 when the path is missing, not SQLite, a flag is invalid, the source directory does not have room to copy a non-empty `-wal` (`refuse: not enough free space`), or that copy cannot be staged (`refuse: cannot stage source wal`).

---

## Diff (`att0_fpdiff.py`)

Compares two fingerprint JSON files (before, then after).

| Exit | Meaning |
| --- | --- |
| 0 | `SOR_FINGERPRINT=IDENTICAL` |
| 1 | `SOR_FINGERPRINT=CHANGED` |
| 2 | Usage error, including two fingerprints whose exclusion sets differ |

`DIFF` lines name the table or field. Notes are not failures.

`--logical` ignores file bytes, mtime, and stat (`main_sha256`, `stat_main`, `stat_wal`, `stat_shm`). It compares table counts and hashes only. Exclusion sets must still match. Added and removed tables are still reported. Use it for a backup copy versus the original, where the files differ by design.

A table on the after side only is `DIFF table NAME added`. A table on the before side only is `DIFF table NAME removed`. `--allow-added-table NAME` (repeatable) expects NAME on the after side. The note is `note added_table NAME allowed`, and that add is not a difference. A later hash change on that table is still a difference. Removing a table is still a difference.

ATT-0 tables from `scripts/attachments/schema.sql` that a migration can add:

- `attachments`
- `attachment_extracts`
- `attachment_chunks`
- `attachment_meta_scans`
- `attachment_folder_uidvalidity`
- `attachment_chunks_fts`

`attachment_chunks_fts` is virtual. It is listed in `skipped_virtual_tables` and is not a hashed table. Its shadow tables are hashed. Pass `--allow-added-table` once for each shadow table the diff reports as added. `skipped_virtual_tables` is on the fingerprint. Both diff modes ignore it. A missing list, an empty list, and a different list are not differences. Shadow-table counts and hashes still compare.

Without `--logical`, `stat_main` and an already-present `-wal` must match. `att0_fp.py` does not create a `-wal` or an `-shm`. A new `-wal` of size 0 is still `note wal_created_empty_by_ro_reader` when a fingerprint recorded one. A new non-empty `-wal` is a difference. An `-shm` change is `note shm_changed(reader read-marks; not proof of a write)`. When `stat_main` matches and `main_sha256` does not, the line is `DIFF main_sha256`.

---

## Backup (`att0_backup.py`)

Copies `src` into a new file at `dest`. The source is opened with the same rule as the fingerprint (`mode=ro` and `immutable=1` when the source `-wal` is missing or empty; a private copy of the main file and a copied `-wal` opened `mode=ro` when the `-wal` is non-empty). The staged main file is a new inode, not a hardlink, so the open does not rewrite the source `-shm`. The source gains no sidecar. The source directory must have free space for the main file, the `-wal`, and a 64 MiB margin. `sqlite3.Connection.backup` writes a temporary file in the destination directory. That temp copy is sealed with `PRAGMA journal_mode=DELETE` before it is closed, so header bytes 18 and 19 are 1, 1. `PRAGMA quick_check` then opens it `mode=ro` and must return `ok`. A WAL header left in place, with the temp `-shm` removed, fails that open on SQLite 3.51.0. The temp file is fsync'd, then `os.replace` publishes it as `dest`. The next writer re-enables WAL (`scripts/sqlite_pragmas.py`).

Reading the system of record is allowed. The script does not take the writer lock. The operator decides whether to wrap the run in `with_writer_lock.py`. Before the copy, the script calls `sor_writer_gate.refuse_if_sor_writer_conflict` on the source, the same gate as `scripts/attachments/meta_fill.py`. A source named `mailroom.sqlite` is refused when that gate reports a conflict.

Exit 2 when:

- `dest` already exists (never overwrite), or `dest-wal` / `dest-shm` / `dest-journal` already exists
- the destination basename is `mailroom.sqlite`
- `dest` is the same file as `src`
- the destination directory is missing
- `src` is missing or not SQLite
- the source directory does not have room to copy a non-empty `-wal` (`refuse: not enough free space`)
- a non-empty source `-wal` cannot be staged (`refuse: cannot stage source wal`)
- the writer gate reports a conflict

A failure before replace removes the temp file and does not create `dest`. Anything other than `quick_check=ok` is an exit 1 and does not create `dest`.

Stdout on success is one line of `key=value` tokens, basenames only:

```
backup_ok src=db.sqlite dest=att0-snapshot.sqlite seconds=0.0 size=8192 quick_check=ok journal_mode=delete
```

---

## Logical shell recipe

No-install fallback. Per-table inclusion and ordering match `att0_fp.py`, including the skip of `sqlite_%` names and of `CREATE VIRTUAL TABLE` statements. `sql IS NULL` stays included, so shadow tables are hashed. Virtual table names are not printed. The Python digest does not have to be byte-identical to `shasum`. Python records those names in `skipped_virtual_tables`.

`sqlite3 -readonly` is a plain `mode=ro` open. On SQLite 3.51.0 that open fails for a WAL database whose `-shm` is missing, and it can create a `-wal` and a `-shm` beside the file. This recipe opens `file:...?mode=ro&immutable=1` instead, and only when the `-wal` is missing or empty. A non-empty `-wal` is not opened here. Use `att0_fp.py`, which stages that `-wal` and still sees its rows. `immutable=1` does not see those rows.

`set -o pipefail` is on. Each `sqlite3` invocation captures stderr. If a count is not a non-negative integer, a hash is not 64 hex digits, or captured stderr is non-empty, the recipe prints `ERROR` plus the table name on stderr and exits 1. It does not print a count or a hash for that table. Rows already read stay in a temp file and are printed only after every table succeeds.

```bash
set -o pipefail
DB=/path/to/db.sqlite
if [ -s "${DB}-wal" ]; then
  printf 'refuse: non-empty wal; use att0_fp.py\n' >&2
  exit 2
fi
DBURI="file:${DB}?mode=ro&immutable=1"
err=$(mktemp)
out=$(mktemp)
trap 'rm -f "$err" "$out"' EXIT
fail() { printf 'ERROR %s\n' "$1" >&2; exit 1; }
names=$(sqlite3 "$DBURI" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' AND (sql IS NULL OR sql NOT LIKE 'CREATE VIRTUAL TABLE%') AND name NOT IN ('ask_audit','drafts') ORDER BY name;" 2>"$err") || fail sqlite_master
if [ -s "$err" ]; then fail sqlite_master; fi
while IFS= read -r t; do
  [ -n "$t" ] || continue
  ident=$(printf '%s' "$t" | sed 's/"/""/g')
  lit=$(printf '%s' "$t" | sed "s/'/''/g")
  : >"$err"
  count=$(sqlite3 "$DBURI" "SELECT COUNT(*) FROM \"$ident\";" 2>"$err") || fail "$t"
  if [ -s "$err" ] || ! printf '%s' "$count" | grep -Eq '^[0-9]+$'; then fail "$t"; fi
  if [ "$t" = "messages" ]; then filt="name != 'has_attachments'"; else filt="1=1"; fi
  : >"$err"
  cols=$(sqlite3 "$DBURI" "SELECT '\"' || replace(name,'\"','\"\"') || '\"' FROM pragma_table_info('$lit') WHERE $filt ORDER BY cid;" 2>"$err" | paste -sd, -) || fail "$t"
  if [ -s "$err" ]; then fail "$t"; fi
  : >"$err"
  order=$(sqlite3 "$DBURI" "SELECT '\"' || replace(name,'\"','\"\"') || '\"' FROM pragma_table_info('$lit') WHERE pk>0 ORDER BY pk;" 2>"$err" | paste -sd, -) || fail "$t"
  if [ -s "$err" ]; then fail "$t"; fi
  [ -n "$order" ] || order="rowid"
  : >"$err"
  hash=$(printf '.mode quote\n.headers off\nSELECT %s FROM "%s" ORDER BY %s;\n' "$cols" "$ident" "$order" | sqlite3 "$DBURI" 2>"$err" | shasum -a 256 | awk '{print $1}') || fail "$t"
  if [ -s "$err" ] || ! printf '%s' "$hash" | grep -Eq '^[0-9a-f]{64}$'; then fail "$t"; fi
  printf '%s\t%s\t%s\n' "$t" "$count" "$hash" >>"$out"
done <<END_ATT0_TABLES
$names
END_ATT0_TABLES
cat "$out"
```
