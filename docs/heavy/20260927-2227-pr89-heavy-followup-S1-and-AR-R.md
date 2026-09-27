# Heavy follow-up — S1 command list and AR-R step order

Date: 2026-09-27 22:27 UTC
From: implementation pass on PR #89
Audience: Grok Heavy
Status: facts from the operator runbook drafts. S1 v5 and AR-R v8 are both NOT ARMED. This note does not redesign them.

This answers the two "Need from CoS" items in the watchdog reply: the exact S1 command list, and the AR-R step order with the restore step marked.

PR #89 is stacked on PR #90's branch for now. The final plan is that #89 builds on merged #90, and gate 6 re-covers the wrapper. After #90 merges, #89 rebases onto main.

## 1. S1 is bootout only

S1's only `launchctl` verbs are `print` and `print-disabled` (both read-only) and `bootout`. There is no `disable` and no `enable`. The plist is never edited and never moved. The watchdog restore therefore adds no `enable`.

A disabled result on `print-disabled` is a STOP before the bootout. Search stays up. A STOP before the bootout runs `search_resume_watchdog.py clear`. A STOP after the bootout goes to S2.

Each line below is its own foreground call. The working directory is `/tmp`. `{{STAMP}}` is filled before the call. The list is verbatim from S1 v5.

```zsh
MA="$HOME/MailArchive"; SOR="$MA/mailroom.sqlite"; STAMP={{STAMP}}; J="gui/$(id -u)/com.mailroom.ask-mail-serve"; date; [[ -z "${SOR_FORCE_LIVE_CHECKS-}" ]] || echo FLAG_EXPORTED_STOP
launchctl print "$J" | grep -E '^\s*(state|pid|path) ='
launchctl print-disabled "gui/$(id -u)" | grep -F '"com.mailroom.ask-mail-serve"'
/usr/bin/python3 "$MA/scripts/search_resume_watchdog.py" write --run-id "att0-L1-$STAMP"; echo "write_rc=$?"; cat "$MA/state/search_resume_after.epoch"
launchctl bootout "$J"; echo "bootout_rc=$?"
out=$(launchctl print "$J" 2>&1); rc=$?; if [[ $rc -eq 113 || "$out" == *"Could not find service"* ]]; then echo SEARCH_NOT_LOADED; elif [[ $rc -eq 0 ]]; then echo SEARCH_LOADED; else echo "SEARCH_STATE_UNCLEAR rc=$rc"; fi; pgrep -fl ask_mail; for f in "$SOR" "$SOR-wal" "$SOR-shm"; do [[ -e "$f" ]] && lsof "$f"; done; /usr/bin/curl -s -o /dev/null -w '%{http_code}\n' --max-time 3 http://127.0.0.1:8743/health
```

## 2. AR-R step order

AR-R v8 is terminal. No A2 or A3 re-run follows it in the same window.

Search-restore steps are marked **[restore]**.

1. Shell values and clock.
1a. ACTION_REQUIRED gate.
2. The backup sha must equal BK_SHA.
2b. **[restore arm]** Arm a later deadline before the write: `search_resume_watchdog.py write --run-id att0-L1-$STAMP-R`. This overwrites any S1 file, so `+26` and `+50` count from AR-R's start.
3. Run the narrow check. Bootout only on `SEARCH_LOADED`, skip on `SEARCH_NOT_LOADED`, and STOP on `UNCLEAR`.
4. No-writer and open-file checks, plus the narrow check (must be `SEARCH_NOT_LOADED`).
4b. Clean no-writer probe.
5. DATABASE RESTORE, the only write step: `with_writer_lock.py --purpose att0-restore`, with a zsh plus sqlite3 `.backup`-then-`mv` child that makes no gate call. Its first acquire drops AR-R's `+26`.
5a. Read-only check that `+26` dropped. It is logged, not a STOP.
6. `quick_check` (read-only).
7. Digest (read-only).
8. **[restore]** SEARCH RESTORE: `launchctl bootstrap gui/$UID <plist>`, with no kickstart and no `-k`. It runs only after step 5 exits, with no writer process and the write lock not held. If a writer is still running, there is no bootstrap; it is reported, the deadline file stays in place, and the watchdog's lock-held guard keeps search off. The step stays mandatory after a STOP in steps 4, 6, or 7. If the bootstrap fails and the narrow check then shows loaded with health 200, that counts as success.
9. Health 200.
10. Clear the deadline file only after 200.

Gap coverage, as stated in the draft: steps 3 to 5 are covered by the fresh `+26` from step 2b. Steps 5 to 8 come after the only write, and `+50` lands well after step 10.

## 3. Three changes for Heavy to veto or approve

These are the draft's departures from the earlier card. They are not armed.

### (a) Read-only `print-disabled` before the bootout

S1 now runs `launchctl print-disabled` and greps the ask-mail-serve label before `bootout`. A disabled job is a STOP with search left up, and the deadline file is cleared. Neither S2 nor the watchdog can bootstrap a disabled job back, because restore has no `enable`, and S1 never disables the job itself. The check is read-only. It does not edit the plist.

Risk if vetoed: S1 can bootout a job that is already disabled. Search stays down. Bootstrap cannot load it, and nothing in this restore is allowed to enable it.

### (b) Deadline write before the bootout

S1 writes `$HOME/MailArchive/state/search_resume_after.epoch` before `launchctl bootout`. A session that dies in the moment after the bootout still leaves a file, so the watchdog has a deadline. While search is still loaded, the watchdog sees `launchctl print` exit 0 and does not restore. The file is ignored for restore until the job is not loaded and the earliest deadline has passed.

Risk if vetoed: bootout can succeed and the session can die before the file exists. Search is down, the watchdog has no file, and it does not fire.

### (c) A2 step 3c wants 120 seconds before `deadline_26`

The A2 guard refuses to continue unless at least 120 seconds remain before `deadline_26`. That moves the latest D confirm from S+26 to about S+24. The deadline file itself is unchanged: `deadline_26` is still the write clock plus 26 minutes. The guard is in A2, not in the watchdog.

Risk if vetoed: D can be confirmed with under 120 seconds left before `+26`. A later A2 write can still be pending when the watchdog's early deadline fires. A free lock between two writes is enough for the watchdog to bring search back during that gap.

## 4. Conformance

Checked against this branch. No verb, path, or file-format mismatch. No code change in this pass.

| Fact | What this branch does |
|---|---|
| CLI `write --run-id` | `search_resume_watchdog.py write --run-id <run-id>` writes the file and exits 0. S1 uses `att0-L1-$STAMP`. AR-R step 2b uses `att0-L1-$STAMP-R` and overwrites the S1 file. An optional `--now <epoch>` exists for tests. S1 and AR-R do not pass it. |
| CLI `clear` | `search_resume_watchdog.py clear` deletes the deadline file. A missing file is success and prints nothing. A STOP before S1's bootout uses this. AR-R step 10 uses this only after health 200. The watchdog never deletes the file. |
| CLI `status` | `search_resume_watchdog.py status` prints four lines and exits 0. It does not write, open sqlite, or take a lock. |
| Deadline path | Default `$HOME/MailArchive/state/search_resume_after.epoch`. Override `SEARCH_RESUME_DEADLINE_FILE`. Same path S1 cats after `write`. |
| File format | `write` body is three lines: `run_id=<id>`, `deadline_26=<epoch>`, `deadline_50=<epoch>`, with `deadline_26` = now + 26 minutes and `deadline_50` = now + 50 minutes. After the `+26` drop the file is `run_id=` and `deadline_50=` only. |
| Narrow not-loaded check | Not loaded only when `launchctl print` exits 113, or the output contains `Could not find service`. Exit 0 is loaded. Any other result is unclear: S1 prints `SEARCH_STATE_UNCLEAR`, and the watchdog logs `search resume launchctl print skipped rc=<rc>` and does not bootstrap. |
| Bootstrap, no enable, no kickstart `-k` | The watchdog's bootstrap argv is `launchctl bootstrap gui/$UID <plist>`. There is no `enable`. Kickstart, when the watchdog issues it, is `launchctl kickstart gui/$UID/com.mailroom.ask-mail-serve` with no `-k`, and only after bootstrap returns 0. See the open question: AR-R step 8 itself does not kickstart. |
| `+26` drop on the first `att0-restore` acquire for the `-R` run-id | The wrapper drops on the first successful acquire whose `MAILROOM_SEARCH_RESUME_RUN_ID` equals the `run_id` in the file. AR-R must export that variable as `att0-L1-$STAMP-R` before `with_writer_lock.py --purpose att0-restore`. Purpose alone does not select the run-id. Unset does not drop. A mismatch refuses the child and leaves the file unchanged. The wrapper accepts purpose `att0-restore`. The gate allowlist still does not. Step 5's child makes no gate call, so that allowlist does not block the child. The gap remains for any later opener that presents the lock token to the gate. |
| Step 5a served by `status` | Yes. After a successful drop, `status` prints `deadline_26=absent` and `plus_26_live=no` and exits 0. If `+26` is still live it still exits 0 and prints `plus_26_live=yes`. That matches "logged, not a STOP": the operator logs the four lines and does not treat a live `+26` as a failed command. A missing file prints `deadline file missing` and exits non-zero. An unparseable file prints `deadline file unreadable` and exits non-zero. |

Sample after step 2b, before the drop (epochs are illustrative):

```text
run_id=att0-L1-EXAMPLE-R
deadline_26=100
deadline_50=200
plus_26_live=yes
```

Sample for step 5a after the drop:

```text
run_id=att0-L1-EXAMPLE-R
deadline_26=absent
deadline_50=200
plus_26_live=no
```

The watchdog ignores a deadline file while search is loaded: `launchctl print` exit 0 returns immediately, with no log line and no bootstrap. That is the behavior change (b) relies on.

The watchdog does not run `print-disabled`. That check is S1's, before bootout. The watchdog has no `enable`, which is why a disabled job must STOP S1 with search left up.

## Open question

AR-R step 8 restores search with `launchctl bootstrap` only. No kickstart, and no `-k`. The watchdog's own restore, from the earlier watchdog spec, still runs kickstart with no `-k` after bootstrap returns 0. The ask-mail-serve template has `RunAtLoad` true, so bootstrap can already start the job. This branch does not remove the watchdog kickstart. Removing it would depart from that spec.

Heavy should say which restore stands when the operator session is already dead: keep the watchdog kickstart (no `-k`), or make the dead-session path match step 8 (bootstrap only).

The watchdog does not check health. Step 8's "bootstrap failed, then narrow check shows loaded, and health is 200" success rule, and step 9's health 200, are operator checks. They are not a second watchdog verb.
