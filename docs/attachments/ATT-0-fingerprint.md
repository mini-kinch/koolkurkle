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

Read-only. The database is opened with a `file:...?mode=ro` URI and `PRAGMA query_only=ON`. The process does not run SQL writes. Stats for the main file, `-wal`, and `-shm` are taken before that open, and the JSON uses basenames only.

Each ordinary table (`sqlite_master` type `table`, names that do not start with `sqlite_`) gets a `count` and a `sha256`. Columns are in `cid` order. Rows are ordered by the primary key, or by `rowid` when there is no primary key. The hash covers SQLite `quote()` of those columns, one row per line. An empty table hashes the empty byte string.

Defaults, always recorded in `exclusions` and extended by the repeatable flags:

- `--exclude-table` adds to `ask_audit` and `drafts` (written by `ask_mail.py` serve).
- `--exclude-column TABLE.COL` adds to `messages.has_attachments`.

Changing only those tables or that column does not change table counts or hashes. Changing any other row does.

When `messages` has a `source` column, the JSON also has count fields: `imap_live_total`, `imap_live_null_folder`, `imap_live_folders_n`, and `imap_live_not_on_server` (the last two only when `folder` and `present_on_server` exist). A blank folder counts as null. The folder name is not written.

JSON is one document on stdout. `--out PATH` writes that document to PATH instead and leaves stdout empty. `--out` must not be the database file.

Exit 0 on success. Exit 2 when the path is missing, not SQLite, or a flag is invalid.

On a database already in WAL mode, opening it can create an empty `-wal` and a `-shm` file. That is SQLite's read-mark behavior. The main-file sha256 is unchanged. `att0_fpdiff.py` treats a new empty `-wal` and an `-shm` change as notes, not as a content change.

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

SQLite may also add FTS shadow tables. Pass `--allow-added-table` once for each name the diff reports as added.

Without `--logical`, `stat_main` and an already-present `-wal` must match. A new `-wal` of size 0 is `note wal_created_empty_by_ro_reader`. A new non-empty `-wal` is a difference. An `-shm` change is `note shm_changed(reader read-marks; not proof of a write)`. When `stat_main` matches and `main_sha256` does not, the line is `DIFF main_sha256`.

---

## Backup (`att0_backup.py`)

Copies `src` into a new file at `dest`. The source is opened `mode=ro` with `query_only`. `sqlite3.Connection.backup` writes a temporary file in the destination directory. `PRAGMA quick_check` must return `ok`. The temp file is fsync'd, then `os.replace` publishes it as `dest`.

Reading the system of record is allowed. The script does not take the writer lock. The operator decides whether to wrap the run in `with_writer_lock.py`. Before the copy, the script calls `sor_writer_gate.refuse_if_sor_writer_conflict` on the source, the same gate as `scripts/attachments/meta_fill.py`. A source named `mailroom.sqlite` is refused when that gate reports a conflict.

Exit 2 when:

- `dest` already exists (never overwrite), or `dest-wal` / `dest-shm` / `dest-journal` already exists
- the destination basename is `mailroom.sqlite`
- `dest` is the same file as `src`
- the destination directory is missing
- `src` is missing or not SQLite
- the writer gate reports a conflict

A failure before replace removes the temp file and does not create `dest`. Anything other than `quick_check=ok` is an exit 1 and does not create `dest`.

Stdout on success is one line of `key=value` tokens, basenames only:

```
backup_ok src=db.sqlite dest=att0-snapshot.sqlite seconds=0.0 size=8192 quick_check=ok journal_mode=delete
```

---

## Logical shell recipe

No-install fallback. Per-table inclusion and ordering match `att0_fp.py`. The Python digest does not have to be byte-identical to `shasum`. The Python tool also skips internal tables whose names start with `sqlite_`. This recipe does not.

```bash
DB=/path/to/db.sqlite
sqlite3 -readonly "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('ask_audit','drafts') ORDER BY name;" |
while IFS= read -r t; do
  [ -n "$t" ] || continue
  ident=$(printf '%s' "$t" | sed 's/"/""/g'); lit=$(printf '%s' "$t" | sed "s/'/''/g")
  count=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM \"$ident\";")
  if [ "$t" = "messages" ]; then filt="name != 'has_attachments'"; else filt="1=1"; fi
  cols=$(sqlite3 -readonly "$DB" "SELECT '\"' || replace(name,'\"','\"\"') || '\"' FROM pragma_table_info('$lit') WHERE $filt ORDER BY cid;" | paste -sd, -)
  order=$(sqlite3 -readonly "$DB" "SELECT '\"' || replace(name,'\"','\"\"') || '\"' FROM pragma_table_info('$lit') WHERE pk>0 ORDER BY pk;" | paste -sd, -)
  [ -n "$order" ] || order="rowid"
  hash=$(printf '.mode quote\n.headers off\nSELECT %s FROM "%s" ORDER BY %s;\n' "$cols" "$ident" "$order" | sqlite3 -readonly "$DB" | shasum -a 256 | awk '{print $1}')
  printf '%s\t%s\t%s\n' "$t" "$count" "$hash"
done
```
