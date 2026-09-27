# ATT-0 restore

Rollback helper: `scripts/attachments/att0_restore.py` copies a backup SQLite file over the live system of record after an ATT-0 migrate or fill.

The Mini daily job is the sole SoR writer; the MBP is a non-writer (rollback, read-only).

This helper does not take the writer flock. Run it under the sole-writer wrapper. `--allow-mailroom-sqlite` is required when the destination basename matches `mailroom.sqlite` in any case. The check uses `casefold`, so a case-preserving APFS volume cannot skip the flag. Without that flag the helper exits 2 and changes nothing. The writer gate still runs before the database is opened. A lock held by the parent `with_writer_lock.py --purpose att0-restore` process is this run. A different holder, or a rem process, is a conflict.

```
scripts/with_writer_lock.py --purpose att0-restore -- python3 scripts/attachments/att0_restore.py --src /var/backups/mailroom-backup.sqlite --dest /var/lib/mailroom/mailroom.sqlite --allow-mailroom-sqlite
```

The source file is not modified or deleted. Use `immutable=1` only when the source `-wal` is missing or empty. That open creates no `-wal` or `-shm` beside the source. A non-empty source `-wal` is staged as a byte copy of the main file and the `-wal` in a private directory in the source directory. Both files are new inodes, not hardlinks. That private path is opened `mode=ro`, so uncheckpointed frames are copied. SQLite's unix VFS keeps one shared-memory node per device and inode for the whole process. A hardlink would reuse the source inode and write read-marks into the source `-shm`. The stage's `(st_dev, st_ino)` differs from the source. If the private directory or the copy cannot be created, the helper exits 2. If the source main file or `-wal` changes during that copy, including a `-wal` that appears or disappears, the helper removes the stage and exits 2 with `refuse: source changed during stage`.

A SIGKILL during staging can leave a hidden directory beside the source database. The name matches `.att0-restore-` plus eight characters from `tempfile.mkdtemp` (lowercase letters, digits, and underscore), for example `.att0-restore-a1b2c3d4`. That directory is a full copy of the main file and the `-wal` (`db.sqlite` and `db.sqlite-wal`). It is safe to delete when no helper is running.

Free space is checked before the temp file is created. The destination directory must have the source size, plus the source `-wal` size, plus 64 MiB, from `shutil.disk_usage`. When the source `-wal` is non-empty, the stage is a byte copy of the main file and the `-wal`, so a different source directory must also have the source size, plus the source `-wal` size, plus 64 MiB free. When both files already share a directory, the destination check is that same size and covers the stage. Short means exit 2 and `refuse: not enough free space`.

## Precondition: no reader holds the destination

Any live system-of-record use requires no daily/rem/lock holder, ask-mail-serve booted out, and lsof empty first.

The writer gate detects writers (the lock, a rem process, the daily job). It does not detect readers. A reader that still has the old file open, and is the last connection to close after the swap, unlinks `<dest>-wal` by name. That can remove a `-wal` the next writer created for the new file.

Any operator card that uses this helper must first stop the serve LaunchAgent, then confirm `lsof` shows no process holding the destination, the destination `-wal`, or the destination `-shm`:

```
launchctl bootout gui/<uid>/com.mailroom.ask-mail-serve
lsof /var/lib/mailroom/mailroom.sqlite /var/lib/mailroom/mailroom.sqlite-wal /var/lib/mailroom/mailroom.sqlite-shm
```

`lsof` must print no process. Do not run the helper while any process still has those paths open.

The helper also runs a best-effort check with the pinned binary `/usr/sbin/lsof` (no `PATH` lookup, with `-n` and `-P`) before it creates the temp file and again before it parks sidecars. It passes the destination, `<dest>-wal`, and `<dest>-shm`. A pid other than this process is `refuse: dest file is open` and exit 2, not a warning. This process's own pid is ignored. If `/usr/sbin/lsof` cannot be executed, the helper does not refuse and does not warn; the operator `lsof` above is still required.

The temp copy is built with the SQLite backup API, integrity-checked, and sealed with `PRAGMA journal_mode=DELETE` before it replaces the destination. SQLite header bytes 18 and 19 are the file-format write and read versions: 1 is a rollback journal, 2 is WAL. DELETE mode stores 1, 1, so a later `mode=ro` open does not need a `-shm` file. A WAL database whose `-shm` was removed fails that open on SQLite 3.51.0 (`unable to open database file`) because a read-only connection cannot create the shared-memory file. The next writer sets WAL again (`scripts/sqlite_pragmas.py`).

When the destination already exists, the temp file's mode and owner are set to match it before the rename. `PermissionError` exits 2.

The live destination stays uncheckpointed. A busy checkpoint can rewrite the main file and then refuse. The durable order is: fsync the temp file, fsync the destination directory, rename the existing `dest-wal` and `dest-shm` to aside names, and restore those names if a non-empty `-wal` appears again before the swap. Then rename the temp onto the destination. If that rename fails, the aside files are renamed back and the destination inode stays in place. On success the aside files and any recreated `-wal`/`-shm` are removed, and the directory is fsynced again. On Darwin each fsync is followed by `fcntl.F_FULLFSYNC` when that flag exists. Output uses basenames only.

A crash after the park and before the replace leaves the old main file in place and its `-wal` under `.att0-restore-aside-<hex>-<name>-wal` in the destination directory. `<name>` is the destination basename and `<hex>` is 16 hex characters. The `-shm`, if it was parked, is `.att0-restore-aside-<hex>-<name>-shm` with its own hex. Recognise a leftover by the `.att0-restore-aside-` prefix. To put the live sidecars back, rename that `-wal` aside back to `<name>-wal` (and the `-shm` aside back to `<name>-shm`). To finish the rollback instead, rerun this helper. A rerun does not adopt a leftover aside; after it succeeds, remove any remaining `.att0-restore-aside-*` files. A leftover `.att0-restore-*.sqlite` temp from that crash can be removed once the main file is in place.

## No hand-rolled swap

Do not replace this helper with a manual `sqlite3 .backup` and `mv` sequence. That sequence has no fsync step and no commands that rename the aside files back when the swap does not finish. Operator cards run `scripts/attachments/att0_restore.py` under the wrapper above.

Placeholder paths only.
