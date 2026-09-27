# ATT-0 restore

Rollback helper: `scripts/attachments/att0_restore.py` copies a backup SQLite file over the live system of record after an ATT-0 migrate or fill.

The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only).

This helper does not take the writer flock. Run it under the sole-writer wrapper. `--allow-mailroom-sqlite` is required when the destination basename is `mailroom.sqlite`. Without that flag the helper exits 2 and changes nothing. The writer gate still runs before the database is opened. A lock held by the parent `with_writer_lock.py --purpose att0-restore` process is this run. A different holder, or a rem process, is a conflict.

```
scripts/with_writer_lock.py --purpose att0-restore -- python3 scripts/attachments/att0_restore.py --src /var/backups/mailroom-backup.sqlite --dest /var/lib/mailroom/mailroom.sqlite --allow-mailroom-sqlite
```

Before the replace, a non-empty `dest-wal` is checkpointed with `PRAGMA wal_checkpoint(TRUNCATE)`. If that checkpoint is busy, the helper refuses and leaves the destination bytes unchanged. The temp copy is then sealed with `PRAGMA journal_mode=DELETE` before the atomic replace. SQLite header bytes 18 and 19 are the file-format write and read versions: 1 is a rollback journal, 2 is WAL. DELETE mode stores 1, 1, so a later `mode=ro` open does not need a `-shm` file. A WAL database whose `-shm` was removed fails that open on SQLite 3.51.0 (`unable to open database file`) because a read-only connection cannot create the shared-memory file. After a successful replace the helper removes `dest-wal` and `dest-shm` so the old WAL cannot be replayed onto the restored file. The next writer sets WAL again (`scripts/sqlite_pragmas.py`). The source file is not modified or deleted. Output uses basenames only.

## Fallback manual recipe

Under the same wrapper, if the helper cannot be used:

```
scripts/with_writer_lock.py --purpose att0-restore -- sqlite3 /var/backups/mailroom-backup.sqlite ".backup /var/lib/mailroom/mailroom.sqlite.restore-tmp"
```

Then, still under that lock, seal rollback journal mode on the temp copy, move it into place, and drop the stale WAL sidecars. Sealing DELETE before the move keeps header bytes 18 and 19 at 1, 1 so a later read-only open does not need `-shm`. The next writer sets WAL.

```
sqlite3 /var/lib/mailroom/mailroom.sqlite.restore-tmp "PRAGMA journal_mode=DELETE;"
mv /var/lib/mailroom/mailroom.sqlite.restore-tmp /var/lib/mailroom/mailroom.sqlite
rm -f /var/lib/mailroom/mailroom.sqlite-wal /var/lib/mailroom/mailroom.sqlite-shm
```

Placeholder paths only.
