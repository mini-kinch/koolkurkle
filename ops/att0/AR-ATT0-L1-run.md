# AR ATT-0 L1 window, rollback, restore-daily, report

Phase P is `phaseP_473b59a0.sh` (sha256 `1ff2204ba32e4adcd66f725cdb8c8dce52973813f74d1da0ea4df041d99c81b2`). This card does not place or rewrite it. It already passed for `P_STAMP=20260928-011821`. This card places `ops/att0/att0_l1.sh` and runs one mode per call. There is no `phasep` mode and no `keychain-primer` mode. The clone is `/tmp/pr48b`. The branch is `cursor/att0-l1-run-scripts`.

Script sha256: `ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9`

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

Mailroom PASS for `20260928-011821` has `D_P=27`. At entry, before the probe, `window` re-checks that the stamp is under 12h, that both stamps (`2026-09-26 16:51:58` and `2026-09-26 08:50:16`) and the Phase P markers are present, and that `gone=992`. Rule A2 (6) is `D_P ≤ D_W ≤ D_P+50`. For this PASS that band is 27–77, taken from the scratch query, not from a hardcoded 27. `parts_truncated=1` is meta_fill's `--max-parts` default and is informational only.

`D_3.5b` is `D_W` from the in-window rehearsal, printed `auto (rule A2)`. `D_late` is `D_A - D_W` from the same D query on the live SoR right after A3, and it must sit in `0..D_LATE_MAX` (10). It is printed `auto (rule A3)`. `restore-daily` runs only when `/tmp/att0w-done-<W>.OK` or `/tmp/att0r-done-<W>.OK` exists. Those markers are the mechanical stand-in for Mailroom's posted verdict, which this script cannot see.

Exit codes: `0` done, `1` stopped before a live SoR write, `2` usage, `3` failure after a live SoR write, `4` SAFE-STATE failed when the work itself was 0 or 1. Exit 2 is never success.

## window

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo WINDOW-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ] && echo WINDOW-SHA-OK && /bin/bash /tmp/att0_l1.sh window 20260928-011821
```

The first line is `ATT0W STAMP=<window stamp>`. Pass that stamp to rollback, restore-daily, and report.

## rollback

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo ROLLBACK-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ] && echo ROLLBACK-SHA-OK && /bin/bash /tmp/att0_l1.sh rollback WINDOW_STAMP
```

## restore-daily

Refuses unless `/tmp/att0w-done-<WINDOW_STAMP>.OK` or `/tmp/att0r-done-<WINDOW_STAMP>.OK` exists.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo RESTORE-DAILY-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ] && echo RESTORE-DAILY-SHA-OK && /bin/bash /tmp/att0_l1.sh restore-daily WINDOW_STAMP
```

## report

`report <WINDOW_STAMP>` is the done-report checker. There is no second script. It reads that window's transcript, its `att0w-*` logs, and `/tmp/att0w-done-<WINDOW_STAMP>.OK`. It does not write, and it does not use the network or the Keychain. Mailroom's posted verdict stays the official run verdict.

Each rule is one line, `ATT0-DONE RULE <id> PASS` or `FAIL`. The last line is `ATT0-DONE PASS` or `ATT0-DONE FAIL rule=<id>`. A2–A4 are recomputed from `att0w-dp`, `att0w-dw`, `att0w-da`, `att0w-reh-fill`, `att0w-a3`, `att0w-a4`, and `att0w-r1`. `D_W` must sit in `[D_P, D_P+D_BAND]` with `D_BAND=50`. Any `falling back`, `LOGIN `, or `Subject:` line is `leak-w21` FAIL. `parts_truncated` is not a gate.

`a4-r1v2` passes only when the logs contain `K_OK`, `lines=34`, and `LOGICAL_MATCH`. Those are the plan v1.17 R1 v2 digest lines. The recipe was not uploaded, so a window that otherwise passed reports `ATT0-DONE FAIL rule=a4-r1v2`. That failure is fail-closed. It is not a live SoR failure.

Nothing in `window`, `rollback`, or `restore-daily` arms the watchdog's +26 minute search restore, or any other restore timer, in the free-lock gap between A2 and A3. That gap calls `search_resume_watchdog.py status` only. The only deadline there is the S+50 file written before search bootout, plus the S+51 budget.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ]; else git show COMMIT_SHA_PENDING:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo REPORT-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = ebcfb7542021424c0751e5801aaac7865c63765612af22d692e642d2429792e9 ] && echo REPORT-SHA-OK && /bin/bash /tmp/att0_l1.sh report WINDOW_STAMP
```

## W-criteria

W22, W23, and W24 are required on top of W1–W21.

| id | criterion | implemented | tested |
| --- | --- | --- | --- |
| W22 | One `lsof -t -- FILE 2>&1` per lock. An all-digit line means held, which is a STOP. rc 1 with empty output means free. Anything else is `LSOF-ERROR`. A missing write lock is a STOP. A missing daily lock is free. A non-blocking flock goes through `with_writer_lock.py` (`LOCK_EX\|LOCK_NB`, released immediately). rc 2 or held is a STOP. lsof rc alone is not success. Step 0 of `window` runs the writer, daily, lock, action-required, hits, health, and watchdog re-checks and stops before any directory, transcript, or SoR write. | `lock_probe_file`, `flock_probe`, `step0_locks` | `LOCK-SPLIT-OK`, `STEP0-LOCK-OK` |
| process | `pgrep -f` uses the full P1 writer list once, bracket-guarded, with no redundant alternative that contains another name unguarded. rc 0 is present, rc 1 is absent, any other rc is `PGREP-ERROR`. `launchctl print` on the daily: rc 0 is loaded, rc 113 or `Could not find service` is not-loaded, anything else is `DAILY-UNCLEAR`. A non-zero `print-disabled` rc is a STOP. Health is exactly `200`. `ACTION_REQUIRED` is absent for both `-e` and `-L`. `rem_process_hits()` is empty. At window entry the watchdog is `status=missing` with rc 1. The same helpers feed step 0, SAFE-STATE, rollback, and restore-daily. | `pgrep_state`, `no_writer`, `daily_print_state`, `daily_disabled`, `hits_clear`, `health_ok`, `watchdog_missing`, `safe_state` | `STATIC-OK` writer argv check, `PROC-RC-OK`, `PRINT-DISABLED-RC-OK` |
| W23 | Curl matches argv `^/usr/bin/curl( \|$)` or the pinned argv `/usr/bin/curl --silent --show-error --fail-early -K -`, and membership of the fill group. A `curl.*imap` pattern alone is a failure. | `CURL_PAT`, `fill_curl_in_group` in the timer parent | `CURL-ARGV-OK`, `FILLCURL-GROUP-OK`, happy `fill_curl=0` |
| W24 | The 3.5L-b rehearsal fill and the live fill after A2 each run in their own process group under a live timer parent (`fork` plus `setpgrp`, not alarm-then-exec). On timeout, INT, TERM, or any exit: TERM, wait 5 seconds, KILL the group, then assert the group is empty before a verdict line. INT and TERM share that path. | `write_helper_pl`, `run_group` `GROUP-EMPTY` | `DESC-LOCK-OK`, `ORPHAN-NORMAL-OK`, `ORPHAN-TIMEOUT-OK`, `ORPHAN-SIGNAL-OK`, happy `GROUP-EMPTY` before both `FILL-REPORT-OK` lines |

## TODO-PIN

- Plan v1.17 PASS items that need the R1 v2 digest recipe (`K_OK lines=<EXPECT_PRE>`, AFTER `K_OK lines=34`, and `LOGICAL_MATCH`) stay open. That recipe file was not in the upload. Step 6 does `PRAGMA quick_check`, the before/after sha, non-ATT-0 table identity, and the A4 count rules. It prints `R1-V2-TODO-PIN recipe-absent` and does not invent the digest. `report` records that gap as `ATT0-DONE FAIL rule=a4-r1v2`. AR-R verifies `quick_check=ok` inside the restore and does not invent `K_OK lines=<EXPECT_PRE>`.
- Verbatim S1 v7, A2 v10, A3 v10, AR-R v11, and S2 v6 fence strings that asked a person to read a diff are replaced by the mechanical checks in Part A. The 18:55 and 19:50–20:20 fences stay off because the daily is held.
- Legacy attachment rows: an absent `attachments` table counts as 0.
- The 15s access probe inside `window` is a `meta_fill` dry-run on a scratch copy (`--max-messages 1 --timeout 10`), in its own process group. This script does not call the Keychain binary. A `falling back` line is a STOP. Phase P `fill` is the Keychain read.

On every child exit, including a normal one, the process-group helper sends TERM, waits 5 seconds, then KILL to the child's process group. A timeout prints `child_rc=` and `harness=timeout then TERM/KILL` on separate lines. A normal exit prints `harness=normal`. The follow-up check requires `with_writer_lock`, `meta_fill`, `/usr/bin/curl`, `security`, `perl`, `/usr/bin/time`, and this script to be absent, and both lock files to be free.
