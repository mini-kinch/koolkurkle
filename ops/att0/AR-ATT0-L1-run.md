# AR ATT-0 L1 window, rollback, restore-daily, report

Phase P is `phaseP_473b59a0.sh` (sha256 `1ff2204ba32e4adcd66f725cdb8c8dce52973813f74d1da0ea4df041d99c81b2`). This card does not place or rewrite it. It already passed for `P_STAMP=20260928-011821`. This card places `ops/att0/att0_l1.sh` and runs one mode per call. There is no `phasep` mode and no `keychain-primer` mode. The clone is `/tmp/pr48b`. The branch is `cursor/att0-l1-run-scripts`.

Script sha256: `1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa`

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

`D_P` is re-read from `$HOME/MailArchive/dryrun/att0-livepath-<P_STAMP>/mailroom.sqlite`. The operator does not write a side file. Pass `20260928-011821` as the argument. The script checks the stamp's age at runtime and does not hardcode it.

Exit codes: `0` done, `1` stopped before a live SoR write, `2` usage, `3` failure after a live SoR write, `4` SAFE-STATE failed when the work itself was 0 or 1. Exit 2 is never success.

## window

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo WINDOW-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ] && echo WINDOW-SHA-OK && /bin/bash /tmp/att0_l1.sh window 20260928-011821
```

The first line is `ATT0W STAMP=<window stamp>`. Pass that stamp to rollback, restore-daily, and report.

## rollback

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo ROLLBACK-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ] && echo ROLLBACK-SHA-OK && /bin/bash /tmp/att0_l1.sh rollback WINDOW_STAMP
```

## restore-daily

Refuses unless `/tmp/att0w-done-<WINDOW_STAMP>.OK` or `/tmp/att0r-done-<WINDOW_STAMP>.OK` exists.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo RESTORE-DAILY-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ] && echo RESTORE-DAILY-SHA-OK && /bin/bash /tmp/att0_l1.sh restore-daily WINDOW_STAMP
```

## report

Read-only. No network, no Keychain, no writer.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo REPORT-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 1e63013f4c398d15ed858417f39a42645f4131fe848e1a15145b2e92417e6baa ] && echo REPORT-SHA-OK && /bin/bash /tmp/att0_l1.sh report WINDOW_STAMP
```

## TODO-PIN

The in-window card files (S1 v7, A2 v10, A3 v10, AR-R v11, S2 v6, plan v1.17, the D-criteria markdown) were not in the workspace. These stand-ins are named in the script and stay until those texts arrive:

- Plan v1.17 PASS items 1-8 need the R1 v2 digest recipe (`K_OK lines=<EXPECT_PRE>` and `K_OK lines=34`). That recipe file was not in this upload, so step 6 stays `PRAGMA quick_check` plus the before/after sha, and `report` checks the stated subset (done marker, exit 0, D_W band, D_MAX, scanned_gone as D_LATE, A2/A3/R1, leak and falling-back, search restored).
- D_LATE is `scanned_gone <= D_LATE_MAX` (10). It is not the D_W minus D_P gap. The band is `D_BAND=50`. `d_check` STOPs on a miss.
- Step 6 records `PRAGMA quick_check` plus the before/after sha. It does not require the bytes to differ. A2 also compares the live schema sha with the rehearsal copy.
- Legacy attachment rows: an absent `attachments` table counts as 0.
- The 15s access probe inside `window` is a `meta_fill` dry-run on a scratch copy (`--max-messages 1 --timeout 10`). This script does not call the Keychain binary. Phase P `fill` is the Keychain read.
