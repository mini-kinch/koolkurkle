# AR ATT-0 L1 window, rollback, restore-daily, report, keychain-primer

Phase P is not this script. It already passed for `P_STAMP=20260928-011821`. This card places `ops/att0/att0_l1.sh` and runs one mode per call. The clone is `/tmp/pr48b`. The branch is `cursor/att0-l1-run-scripts`.

Script sha256: `408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e`

Commit: `d3d1638c12da38f7641bf37bf16b3748810f54ec`

Run each one-liner from a shell that has `set -C`. Placement writes `/tmp/att0_l1.sh` only when that path is absent. If the path already exists, the sha256 must already match; a different sha stops the chain before `<MODE>-PLACED`. Each step prints its own marker. `window`, `rollback`, and `restore-daily` print a stamp, numbered step lines, a `SAFE-STATE` line, and one `SUMMARY` line. `report` and `keychain-primer` print a stamp and a `SUMMARY` line and do not run `SAFE-STATE`.

## Pins (verified at `473b59a0bc859af2fbcf1ec4649c3af49a3ceea4`)

| file | sha256 |
| --- | --- |
| scripts/sor_writer_gate.py | `505382b4e23dfed9f746d70fc6f3a79626524be37168aed955ac7bd8872d8345` |
| scripts/with_writer_lock.py | `1e7f5aeee8dd05bca363e8f517e046cc68e22d25c18c7c4dc487d4121dba4f77` |
| scripts/search_resume_watchdog.py | `bae404ccae07857d0dcb47cbc47a107f68e367c3fe7edff07eb202a01aeb8d43` |
| scripts/attachments/migrate_att0_schema.py | `b13b3c97d968c994955c1db313707dd1db372d47668f1d301b6b7c3febcbf95d` |
| scripts/attachments/meta_fill.py | `20f3d997dfa353f738b365542b468ded85cd27e871c166beef62ff7307968f8b` |

Unchanged: daily stamp `2026-09-26 16:51:58`, imap stamp `2026-09-26 08:50:16`, G=`992`, HOLD plist sha12 `660616d94c56`, baseline 44, `D_MAX=300`, `D_LATE_MAX=10`, `D_BAND=50`, `P_STAMP_MAX_AGE=12h`. Health URL `http://127.0.0.1:8743/health`.

`D_P` is re-read from `$HOME/MailArchive/dryrun/att0-livepath-<P_STAMP>/mailroom.sqlite`. The operator does not write a side file. Pass `20260928-011821` as the argument. The script checks the stamp's age at runtime and does not hardcode it.

Exit codes: `0` done, `1` stopped before a live SoR write, `2` usage, `3` failure after a live SoR write, `4` SAFE-STATE failed when the work itself was 0 or 1. Exit 2 is never success. `keychain-primer` exits `0` only when the Keychain read returns 0. Any other read rc exits `1`. An extra argument exits `2`.

## window

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ]; else git show d3d1638c12da38f7641bf37bf16b3748810f54ec:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo WINDOW-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ] && echo WINDOW-SHA-OK && /bin/bash /tmp/att0_l1.sh window 20260928-011821
```

The first line is `ATT0W STAMP=<window stamp>`. Pass that stamp to rollback, restore-daily, and report.

## rollback

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ]; else git show d3d1638c12da38f7641bf37bf16b3748810f54ec:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo ROLLBACK-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ] && echo ROLLBACK-SHA-OK && /bin/bash /tmp/att0_l1.sh rollback WINDOW_STAMP
```

## restore-daily

Refuses unless `/tmp/att0w-done-<WINDOW_STAMP>.OK` or `/tmp/att0r-done-<WINDOW_STAMP>.OK` exists.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ]; else git show d3d1638c12da38f7641bf37bf16b3748810f54ec:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo RESTORE-DAILY-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ] && echo RESTORE-DAILY-SHA-OK && /bin/bash /tmp/att0_l1.sh restore-daily WINDOW_STAMP
```

## report

Read-only. No network, no Keychain, no writer.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ]; else git show d3d1638c12da38f7641bf37bf16b3748810f54ec:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo REPORT-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ] && echo REPORT-SHA-OK && /bin/bash /tmp/att0_l1.sh report WINDOW_STAMP
```

## keychain-primer

Never called by `window`, `rollback`, `restore-daily`, or `report`. One Keychain read, the same argv `meta_fill.py` uses through `imap_keychain._run_security` at pin `473b59a0`: `/usr/bin/security find-generic-password -s mailroom.imap.app-password -w`. No `-a`, `-l`, or keychain path, and no legacy-item fallback. `perl` alarms at 120 seconds and `exec` replaces it, so the process that asks the Keychain is `/usr/bin/security`. Stdout and stderr of that process are discarded. The mode writes no file, takes no lock, and touches no database. It does not print `SAFE-STATE`.

Meanings: rc `0` access OK (script exit 0); `142` timed out waiting for a click; `36` or `51` user interaction is not allowed in this session; `44` item not found. Any other rc is `other`. A non-zero read exits `1`.

```
cd /tmp/pr48b && git fetch origin cursor/att0-l1-run-scripts && { if [ -e /tmp/att0_l1.sh ]; then [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ]; else git show d3d1638c12da38f7641bf37bf16b3748810f54ec:ops/att0/att0_l1.sh > /tmp/att0_l1.sh; fi; } && echo KEYCHAIN-PRIMER-PLACED && [ "$(shasum -a 256 /tmp/att0_l1.sh | cut -d' ' -f1)" = 408b2984df3ea63b9e8530fc1061c846c9961b1615cb8b089f7a9f50be6c895e ] && echo KEYCHAIN-PRIMER-SHA-OK && /bin/bash /tmp/att0_l1.sh keychain-primer
```

## TODO-PIN

The in-window card files (S1 v7, A2 v10, A3 v10, AR-R v11, S2 v6, plan v1.17, the D-criteria markdown) were not in the workspace. These stand-ins are named in the script and stay until those texts arrive:

- Plan v1.17 PASS items 1-8, verbatim. `report` checks the stated subset (done marker, exit 0, D_W band, D_MAX, scanned_gone as D_LATE, A2/A3/R1, leak and falling-back, search restored).
- D_LATE is `scanned_gone <= D_LATE_MAX` (10). It is not the D_W minus D_P gap. The band is `D_BAND=50`.
- Step 6 records `PRAGMA quick_check` plus the before/after sha. It does not require the bytes to differ.
- Legacy attachment rows: an absent `attachments` table counts as 0.
- The 15s access probe inside `window` is a `meta_fill` dry-run on a scratch copy (`--max-messages 1 --timeout 10`). `window`, `rollback`, `restore-daily`, and `report` do not call `/usr/bin/security`. `keychain-primer` is the only call, and it uses the default item only.
