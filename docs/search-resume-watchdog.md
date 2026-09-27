# Search-resume watchdog

FAIL-OPEN FOR SEARCH AVAILABILITY, FAIL-CLOSED FOR THE SOR.

CODE AND PLIST TEMPLATE ONLY, NO INSTALL. CODE AND TEMPLATE ONLY, NO INSTALL.
This checkout does not copy files into `$HOME`, does not write
`~/Library/LaunchAgents`, and does not call `launchctl bootstrap`.
Installing the template is a separate user approval.

`ask-mail-serve` is the Mini's local search service, LaunchAgent label
`com.mailroom.ask-mail-serve`. During a live L1 window, S1 takes that
job out of the `gui/$UID` domain and S2 loads it again. If the operator
session dies between those steps, search can stay off. Waking an agent
is not a control. This watchdog runs on the Mini.

Do not add `KeepAlive` to `ask-mail-serve`. That fights S1. This
change does not edit the ask-mail-serve plist, any other LaunchAgent
plist, an install script, or the daily job. The watchdog does not
assume a daily-job clock time.

## Deadline file

S1 writes `$HOME/MailArchive/state/search_resume_after.epoch` (override
`SEARCH_RESUME_DEADLINE_FILE`) through the helper below. The body S1
writes is three lines:

```text
run_id=<run-id>
deadline_26=<unix epoch seconds>
deadline_50=<unix epoch seconds>
```

`deadline_26` is the write clock plus 26 minutes. `deadline_50` is the
write clock plus 50 minutes. While both are present, the watchdog may
restore once `now` is at or past the earlier of the two. After `+26`
is dropped, the file keeps only `+50` as the earliest restore:

```text
run_id=<run-id>
deadline_50=<unix epoch seconds>
```

The pass then waits until `deadline_50`. It retries every
`StartInterval` (60 seconds). S2 deletes the file after search is
loaded. The watchdog does not delete it.

No file: the pass does nothing and writes no log line. A malformed
file: one log line, `search resume deadline file malformed`, and no
`launchctl` call.

## +26 drop

`+26` means the run was never confirmed. Once the run is committed,
that checkpoint is moot. Drop it on the first successful writer-lock
acquire whose run-id matches `run_id` in the deadline file. Do not
slide the deadline on each phase. Do not hold the lock across A2
and A3.

Caller input: `MAILROOM_SEARCH_RESUME_RUN_ID`. That is the only extra
input. Set it to the `run_id` line in the deadline file. There is no
second argument. If the variable is unset, the wrapper does not drop.

`scripts/with_writer_lock.py` does the drop. The hook is the delimited
block immediately after a successful acquire, before the child and
before the identity-token handoff. The first successful acquire whose
`MAILROOM_SEARCH_RESUME_RUN_ID` matches the file drops `+26`. A later
acquire with the same run-id is a no-op drop. A value that does not
match the file refuses the child and leaves the file unchanged.

The drop calls `drop_early_deadline(run_id)` in
`scripts/search_resume_watchdog.py`. That rewrites the deadline file
atomically: a temp file in the same directory, `fsync`, then
`os.replace`. The rewrite leaves `deadline_50` and removes
`deadline_26`, so `+50` is the earliest restore. It is idempotent.
It refuses a missing file or a mismatched run-id and does not change
the file in those cases.

If the variable is set and the drop fails, the wrapper does not run
the child. It releases the lock and exits non-zero. That is
fail-closed for the SoR. The message includes
`search resume +26 drop failed; child not started`.

Fallback, in the same scripted breath as the writer start:

```zsh
/usr/bin/python3 "$HOME/MailArchive/scripts/search_resume_watchdog.py" \
  arm --run-id <run-id>
```

`arm --run-id` is that same drop. It is not a card to walk away from.

This branch is stacked on PR #90 at
`fe7c076db762b440f0ffc7c524c254223b671434`. Later pushes to
`cursor/l1-writer-lock-identity-16b2` need another rebase. The hook
stays a small delimited block. PR #89 must be rebased again after
PR #90 merges into main.

The restore flow overwrites the deadline file with a new run-id of
the form `att0-L1-<STAMP>-R`. Its first acquire uses purpose
`att0-restore` and must set `MAILROOM_SEARCH_RESUME_RUN_ID` to that
`-R` run-id. A stale S1 run-id no longer matches after the overwrite.

The wrapper accepts purpose `att0-restore` (any non-empty purpose is
stored). The SoR gate does not. `att0-restore` is not in
`WRITER_PURPOSE_ALLOWLIST` in `scripts/sor_writer_gate.py` on this
base. This change does not edit that allowlist. A wrapped child that
presents the lock token with purpose `att0-restore` is refused
(`purpose not allowed`) until Mailroom adds that purpose.

## +50 backstop

`+50` stays after the drop. It is the committed-run backstop. It is
not a writer kill, and it does not mean the writer must finish by
`+50`.

While the writer lock is held, a tick at `+50` is a no-op: search
stays off, and the pass logs the held line. The watchdog restores on
the first tick after the lock is free. It never kills the writer.
S2 is what brings search back on a clean finish before `+50`. `+50`
fires only if S2 never happens.

## S1 commands

The exact S1 command list is in
`docs/heavy/20260927-2227-pr89-heavy-followup-S1-and-AR-R.md`.
S1 is bootout only. Its launchctl verbs are `print`, `print-disabled`,
and `bootout`. There is no `disable` or `enable`, and the plist is
never edited. The watchdog restore adds no enable.

TODO: Heavy has not vetoed or approved the three runbook changes in
that doc. The S1 and AR-R drafts are not armed.

The helpers this script provides, which S1 and S2 may call, are:

```zsh
/usr/bin/python3 "$HOME/MailArchive/scripts/search_resume_watchdog.py" \
  write --run-id <run-id>
```

```zsh
/usr/bin/python3 "$HOME/MailArchive/scripts/search_resume_watchdog.py" clear
```

`<run-id>` is a short id (`A-Za-z0-9`, `.`, `_`, `-`). It is not a
lock token.

Read-only status, which prints the run-id, both checkpoints, and
whether `+26` is still live:

```zsh
/usr/bin/python3 "$HOME/MailArchive/scripts/search_resume_watchdog.py" status
```

A live file prints four lines and exits 0:

```text
run_id=att0-L1-EXAMPLE
deadline_26=100
deadline_50=200
plus_26_live=yes
```

After `+26` is dropped, `deadline_26=absent` and `plus_26_live=no`.
A missing file prints `deadline file missing` and exits non-zero. An
unparseable file prints `deadline file unreadable` and exits
non-zero. `status` does not print a token, does not write, does not
open sqlite, and does not take the writer lock.

## AR-R

The AR-R step order, with the restore steps marked, is in
`docs/heavy/20260927-2227-pr89-heavy-followup-S1-and-AR-R.md`.
Step 2b writes a fresh `-R` deadline before the write. Step 5 is the
only write. Step 8 restores search only after step 5 exits, with no
writer and the lock free. AR-R is terminal. Do not restore search
while a later write step is still pending. The lock-held guard is not
enough for a free-lock gap between two writes.

## What each fire does

The template `launchd/com.mailroom.search-resume-watchdog.plist.template`
runs `watch` on `StartInterval` 60. `RunAtLoad` is false. There is no
`KeepAlive` on this job. One pass then exits.

Restore happens only when all three are true:

1. `now` is at or past the earliest deadline (`+26` until it is
   dropped, then `+50`).
2. `ask-mail-serve` is not loaded, using the rule below.
3. The writer lock is not held, using the detector table below.

Before the deadline the pass does not call `launchctl` or `lsof`.

### Restore steps

The domain is `gui/$UID`. Not the user domain. One bootstrap attempt
per tick. No `-k`. No retry. No enable subcommand.

1. `launchctl print gui/$UID/com.mailroom.ask-mail-serve`.
2. Not loaded only when that command exits 113, or its output contains
   `Could not find service`. Exit 0 means the job is loaded: the pass
   does nothing and writes no log line. Any other print result is
   logged (`search resume launchctl print skipped rc=<rc>`) and the
   tick stops.
3. Read the writer lock as in the detector table. A detection fault
   logs the loud line and stops. A held lock logs the held line and
   stops.
4. `launchctl bootstrap gui/$UID` with the installed plist
   (`SEARCH_RESUME_PLIST`, default
   `$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist`).
5. If bootstrap does not return 0, print once. If that print exits 0,
   the job is already loaded (the S2 race): log `search already loaded`
   as info, not as an error, and stop. Do not bootstrap again. Do not
   kickstart.
6. If bootstrap returns 0, `launchctl kickstart gui/$UID/com.mailroom.ask-mail-serve`
   with no `-k`.
7. If kickstart says not found (exit 113, or output containing
   `Could not find service`), print once. If that print exits 0, this
   is a success no-op: `RunAtLoad` already started the job. Log
   `search already loaded` as info. Do not bootstrap again.

```zsh
launchctl print "gui/$UID/com.mailroom.ask-mail-serve"
launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist"
launchctl kickstart "gui/$UID/com.mailroom.ask-mail-serve"
```

`SEARCH_RESUME_PLIST` overrides the installed plist path. The default
is the rendered agent, not the repo template. The checked-in
ask-mail-serve template has `RunAtLoad` true, so bootstrap may already
start it. Kickstart without `-k` starts the job when it is loaded and
not running, and it does not kill a running instance.

## Writer lock, read-only

Detection does not take `mailroom.write.lock`. There is no exclusive
probe, including a non-blocking one. The pass uses read-only `lsof -t`
on the lock path and `kill -0` (`os.kill(pid, 0)`) on the recorded
pid. Signal 0 does not deliver a signal and does not lock anything.

A live recorded pid is held on its own. `lsof` is how a dead recorded
pid, or a missing lock file, is told apart from a holder.

| What you see | Treat as | Why |
|---|---|---|
| Live recorded pid (`kill -0` succeeds, including permission denied) | held | writer present |
| Missing lock file, and `lsof` shows no holder | free | nothing is holding the path |
| Recorded pid is dead, and `lsof` shows no holder | free | crashed wrapper; do not park search off |
| `lsof` lists a pid | held | a process has the lock path open |
| `lsof`, parse, or tool failure | held (detection fault) | doubt; do not start search on a possible writer |

`lsof` exit 1 with empty output means no holder. That is the normal
macOS result when nothing has the file open. Non-pid output, a
non-zero `lsof` status other than that empty exit 1, a timeout, an
unreadable lock file, or a lock file with no usable pid is a detection
fault.

A detection fault does not restore. Every such tick appends and writes
to stderr:

```text
detection-fault, search left off
```

There is no "start anyway after N faults" escape. The next 60-second
tick tries again. A human S2 can also bring search back. Leaving
search off is fail-closed for the SoR and fail-open for availability.

If the deadline has passed, search is not loaded, and the lock is
held by a live pid or an `lsof` holder, the pass does not restore. It
appends and writes to stderr:

```text
search still off: writer lock held pid=<pid> purpose=<purpose>
```

The next interval tries again. This process does not kill the writer.

Lock-file keys `token`, `lock_token`, and `writer_token` are ignored.
They are never written to the log or to stderr. The held line is built
only from the pid and the purpose.

## What it never does

- It never opens a database and never writes the SoR.
- It never takes `mailroom.write.lock` and never opens that file for
  writing.
- It never kills a writer.
- It never calls `launchctl` with `-k`, and it never uses the user
  domain.
- It never adds `KeepAlive` to `ask-mail-serve`.

The log file is `$HOME/MailArchive/logs/search_resume_watchdog.log`
(`SEARCH_RESUME_LOG`). Launchd's own stdout/stderr for the watchdog
go to `$HOME/MailArchive/logs/search-resume-watchdog.launchd.log`
after install. `SEARCH_RESUME_NOW` is a test clock. The template does
not set it.

## Install is separate

CODE AND TEMPLATE ONLY, NO INSTALL. When an operator chooses to
install, the steps are outside this change: copy
`scripts/search_resume_watchdog.py` to `$HOME/MailArchive/scripts/`,
substitute `__HOME__` in the template, copy the result to
`$HOME/Library/LaunchAgents/`, and bootstrap label
`com.mailroom.search-resume-watchdog`. This repository change does not
do that.
