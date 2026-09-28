# AR ATT-0 L1 window, rollback, restore-daily, report

Phase P is `phaseP_473b59a0.sh` (sha256 `1ff2204ba32e4adcd66f725cdb8c8dce52973813f74d1da0ea4df041d99c81b2`). This card does not place or rewrite it. It already passed for `P_STAMP=20260928-011821`. This card places `ops/att0/att0_l1.sh` and runs one mode per call. There is no `phasep` mode and no `keychain-primer` mode. The clone is `/tmp/pr48b`. The branch is `cursor/att0-l1-run-scripts`.

Script sha256: `7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8`

Commit: `COMMIT_SHA_PENDING`

Run each one-liner from a shell that has `set -C`. Placement writes `/tmp/att0_l1.sh` only when that path is absent. If the path already exists, the sha256 must already match; a different sha stops the chain before `<MODE>-PLACED`. Each step prints its own marker. `window`, `rollback`, and `restore-daily` print a stamp, numbered step lines, a `SAFE-STATE` line, and one `SUMMARY` line. `report` prints a stamp and a `SUMMARY` line and does not run `SAFE-STATE`. The script does not call the Keychain binary. `window` refuses, before it creates a directory or a transcript, unless `/tmp/phaseP-offline-<P_STAMP>.OK`, `/tmp/phaseP-p8-<P_STAMP>.OK`, and `/tmp/phaseP-state-<P_STAMP>` are present.

## Pins (verified at `473b59a0bc859af2fbcf1ec4649c3af49a3ceea4`)

| file | sha256 |
| --- | --- |
| scripts/sor_writer_gate.py | `505382b4e23dfed9f746d70fc6f3a79626524be37168aed955ac7bd8872d8345` |
| scripts/with_writer_lock.py | `1e7f5aeee8dd05bca363e8f517e046cc68e22d25c18c7c4dc487d4121dba4f77` |
| scripts/search_resume_watchdog.py | `bae404ccae07857d0dcb47cbc47a107f68e367c3fe7edff07eb202a01aeb8d43` |
| scripts/attachments/migrate_att0_schema.py | `b13b3c97d968c994955c1db313707dd1db372d47668f1d301b6b7c3febcbf95d` |
| scripts/attachments/meta_fill.py | `20f3d997dfa353f738b365542b468ded85cd27e871c166beef62ff7307968f8b` |

Unchanged: daily stamp `2026-09-26 16:51:58`, imap stamp `2026-09-26 08:50:16`, G=`992`, HOLD plist sha12 `660616d94c56`, baseline 44, `D_MAX=300`, `D_LATE_MAX=10`, `D_BAND=50`, `P_STAMP_MAX_AGE=12h`. Health URL `http://127.0.0.1:8743/health`.

The day is Monday, so plan O3 is the window, and the 44 rule stays the O1 form (G=`992`). The daily is held, so the 18:55 start fence and the 19:50–20:20 fence are not checked. The only hard limit is S+51. `d_check` stays fail-closed: a miss is a STOP. `{{FIX_SHAS}}` is the five pins above, with the gate sha `505382b4e23dfed9f746d70fc6f3a79626524be37168aed955ac7bd8872d8345` in place of `d5833fd137bf3eb7482e1dc13c1b8efb0d16f0c86acd961755eb8e9013e9cebb`.

`D_P` is re-read by the P8 D query, read-only, on Phase P's scratch copy `$HOME/MailArchive/dryrun/att0-livepath-<P_STAMP>/mailroom.sqlite`. The operator does not write `/tmp/att0-dp-<P_STAMP>`. Pass `20260928-011821` as the argument. The script checks the stamp's age at runtime (`P_STAMP_MAX_AGE` is 12h) and does not hardcode it.

`D_3.5b` is `D_W` from the in-window rehearsal, printed `auto (rule A2)`. `D_late` is `D_A - D_W` from the same D query on the live SoR right after A3, and it must sit in `0..D_LATE_MAX` (10). It is printed `auto (rule A3)`. `restore-daily` runs only when `/tmp/att0w-done-<W>.OK` or `/tmp/att0r-done-<W>.OK` exists. Those markers are the mechanical stand-in for Mailroom's posted verdict, which this script cannot see.

Exit codes: `0` done, `1` stopped before a live SoR write, `2` usage, `3` failure after a live SoR write, `4` SAFE-STATE failed when the work itself was 0 or 1. Exit 2 is never success.

## window

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo WINDOW-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ] && echo WINDOW-SHA-OK && /bin/bash /tmp/att0_l1.sh window 20260928-011821
```

The first line is `ATT0W STAMP=<window stamp>`. Pass that stamp to rollback, restore-daily, and report.

## rollback

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo ROLLBACK-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ] && echo ROLLBACK-SHA-OK && /bin/bash /tmp/att0_l1.sh rollback WINDOW_STAMP
```

## restore-daily

Refuses unless `/tmp/att0w-done-<WINDOW_STAMP>.OK` or `/tmp/att0r-done-<WINDOW_STAMP>.OK` exists.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo RESTORE-DAILY-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ] && echo RESTORE-DAILY-SHA-OK && /bin/bash /tmp/att0_l1.sh restore-daily WINDOW_STAMP
```

## report

Read-only. No network, no Keychain, no writer.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo REPORT-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 7cf4baee620e59d096c93ed044d2614d5fe244115c66b9631540d8d09aa394d8 ] && echo REPORT-SHA-OK && /bin/bash /tmp/att0_l1.sh report WINDOW_STAMP
```

## TODO-PIN

- Plan v1.17 PASS items that need the R1 v2 digest recipe (`K_OK lines=<EXPECT_PRE>`, AFTER `K_OK lines=34`, and `LOGICAL_MATCH`) stay open. That recipe file was not in the upload. Step 6 does `PRAGMA quick_check`, the before/after sha, non-ATT-0 table identity, and the A4 count rules. It prints `R1-V2-TODO-PIN recipe-absent` and does not invent the digest. AR-R verifies `quick_check=ok` inside the restore and does not invent `K_OK lines=<EXPECT_PRE>`.
- Verbatim S1 v7, A2 v10, A3 v10, AR-R v11, and S2 v6 fence strings that asked a person to read a diff are replaced by the mechanical checks in Part A. The 18:55 and 19:50–20:20 fences stay off because the daily is held.
- Legacy attachment rows: an absent `attachments` table counts as 0.
- The 15s access probe inside `window` is a `meta_fill` dry-run on a scratch copy (`--max-messages 1 --timeout 10`), in its own process group. This script does not call the Keychain binary. A `falling back` line is a STOP. Phase P `fill` is the Keychain read.
