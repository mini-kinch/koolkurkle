# ATT-0 restore

Rollback helper: `scripts/attachments/att0_restore.py` copies a backup SQLite file over the live system of record after an ATT-0 migrate or fill.

The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only).

This helper does not take the writer flock. Run it under the sole-writer wrapper. `--allow-mailroom-sqlite` is required when the destination basename is `mailroom.sqlite`. Without that flag the helper exits 2 and changes nothing. The writer gate still runs before the database is opened. A lock held by the parent `with_writer_lock.py --purpose att0-restore` process is this run. A different holder, or a rem process, is a conflict.

```
scripts/with_writer_lock.py --purpose att0-restore -- python3 scripts/attachments/att0_restore.py --src /var/backups/mailroom-backup.sqlite --dest /var/lib/mailroom/mailroom.sqlite --allow-mailroom-sqlite
```

Before the replace, a non-empty `dest-wal` is checkpointed with `PRAGMA wal_checkpoint(TRUNCATE)`. If that checkpoint is busy, the helper refuses and leaves the destination bytes unchanged. After a successful replace it removes `dest-wal` and `dest-shm` so the old WAL cannot be replayed onto the restored file. The source file is not modified or deleted. Output uses basenames only.

## Fallback manual recipe

Under the same wrapper, if the helper cannot be used:

```
scripts/with_writer_lock.py --purpose att0-restore -- sqlite3 /var/backups/mailroom-backup.sqlite ".backup /var/lib/mailroom/mailroom.sqlite.restore-tmp"
```

Then, still under that lock, move the backup into place and drop the stale WAL sidecars:

```
mv /var/lib/mailroom/mailroom.sqlite.restore-tmp /var/lib/mailroom/mailroom.sqlite
rm -f /var/lib/mailroom/mailroom.sqlite-wal /var/lib/mailroom/mailroom.sqlite-shm
```

Placeholder paths only.
