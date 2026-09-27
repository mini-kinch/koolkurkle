# ATT-0 restore

Rollback helper: `scripts/attachments/att0_restore.py` copies a backup SQLite file over the live system of record after an ATT-0 migrate or fill.

The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only).

This helper does not take the writer flock. Run it under the sole-writer wrapper. `--allow-mailroom-sqlite` is required when the destination basename matches `mailroom.sqlite` in any case. The check uses `casefold`, so a case-preserving APFS volume cannot skip the flag. Without that flag the helper exits 2 and changes nothing. The writer gate still runs before the database is opened. A lock held by the parent `with_writer_lock.py --purpose att0-restore` process is this run. A different holder, or a rem process, is a conflict.

```
scripts/with_writer_lock.py --purpose att0-restore -- python3 scripts/attachments/att0_restore.py --src /var/backups/mailroom-backup.sqlite --dest /var/lib/mailroom/mailroom.sqlite --allow-mailroom-sqlite
```

The source file is not modified or deleted. Use `immutable=1` only when the source `-wal` is missing or empty. That open creates no `-wal` or `-shm` beside the source. A non-empty source `-wal` is hardlinked into a private directory on that same filesystem, the `-wal` bytes are copied beside the link, and that private path is opened `mode=ro`, so uncheckpointed frames are copied. If the private directory or the hardlink cannot be created, the helper exits 2.

Free space is checked before the temp file is created. The helper needs the source size, plus the source `-wal` size, plus 64 MiB, from `shutil.disk_usage` of the destination directory. Short means exit 2 and `refuse: not enough free space`.

The temp copy is built with the SQLite backup API, integrity-checked, and sealed with `PRAGMA journal_mode=DELETE` before it replaces the destination. SQLite header bytes 18 and 19 are the file-format write and read versions: 1 is a rollback journal, 2 is WAL. DELETE mode stores 1, 1, so a later `mode=ro` open does not need a `-shm` file. A WAL database whose `-shm` was removed fails that open on SQLite 3.51.0 (`unable to open database file`) because a read-only connection cannot create the shared-memory file. The next writer sets WAL again (`scripts/sqlite_pragmas.py`).

When the destination already exists, the temp file's mode and owner are set to match it before the rename. `PermissionError` exits 2.

The live destination stays uncheckpointed. A busy checkpoint can rewrite the main file and then refuse. The durable order is: fsync the temp file, fsync the destination directory, rename the existing `dest-wal` and `dest-shm` to aside names, and restore those names if a non-empty `-wal` appears again before the swap. Then rename the temp onto the destination. If that rename fails, the aside files are renamed back and the destination inode stays in place. On success the aside files and any recreated `-wal`/`-shm` are removed, and the directory is fsynced again. On Darwin each fsync is followed by `fcntl.F_FULLFSYNC` when that flag exists. Output uses basenames only.

## Fallback manual recipe

Under the same wrapper, if the helper cannot be used:

```
scripts/with_writer_lock.py --purpose att0-restore -- sqlite3 /var/backups/mailroom-backup.sqlite ".backup /var/lib/mailroom/mailroom.sqlite.restore-tmp"
```

Then, still under that lock, seal rollback journal mode on the temp copy. Move any live sidecars aside before the main file is renamed. Skip a sidecar `mv` when that file is absent. If a non-empty wal is present again before the main rename, move the aside files back and stop. If the main rename fails, move the aside files back so the destination inode stays in place. After the rename succeeds, remove the aside files and any recreated sidecars. Sealing DELETE before the rename keeps header bytes 18 and 19 at 1, 1 so a later read-only open does not need `-shm`. The next writer sets WAL.

```
sqlite3 /var/lib/mailroom/mailroom.sqlite.restore-tmp "PRAGMA journal_mode=DELETE;"
mv /var/lib/mailroom/mailroom.sqlite-wal /var/lib/mailroom/mailroom.sqlite-wal.aside
mv /var/lib/mailroom/mailroom.sqlite-shm /var/lib/mailroom/mailroom.sqlite-shm.aside
mv /var/lib/mailroom/mailroom.sqlite.restore-tmp /var/lib/mailroom/mailroom.sqlite
rm -f /var/lib/mailroom/mailroom.sqlite-wal.aside /var/lib/mailroom/mailroom.sqlite-shm.aside /var/lib/mailroom/mailroom.sqlite-wal /var/lib/mailroom/mailroom.sqlite-shm
```

Placeholder paths only.
