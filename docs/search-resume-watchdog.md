# Search-resume watchdog

FAIL-OPEN FOR SEARCH AVAILABILITY, FAIL-CLOSED FOR THE SOR.

CODE AND PLIST TEMPLATE ONLY, NO INSTALL. This checkout does not copy
files into `$HOME`, does not write `~/Library/LaunchAgents`, and does
not call `launchctl bootstrap`. Installing the template is a separate
user approval.

`ask-mail-serve` is the Mini's local search service, LaunchAgent label
`com.mailroom.ask-mail-serve`. During a live L1 window, step S1 boots
that job out and step S2 bootstraps it back. If the operator session
dies between those steps, search can stay off. Waking an agent is not
a control. This watchdog runs on the Mini.

Do not add `KeepAlive` to `ask-mail-serve`. That fights S1. This
change does not edit the ask-mail-serve plist, any other LaunchAgent
plist, an install script, or the daily job.

## Deadline file

S1 writes `$HOME/MailArchive/state/search_resume_after.epoch` (override
`SEARCH_RESUME_DEADLINE_FILE`). The body is three lines:

```text
run_id=<run-id>
deadline_26=<unix epoch seconds>
deadline_50=<unix epoch seconds>
```

`deadline_26` is the write clock plus 26 minutes. `deadline_50` is the
write clock plus 50 minutes. Both are the L1 checkpoints. The watchdog
may restore once `now` is at or past the earlier of the two, which is
the +26 mark when S1 used the helper below. It then retries every
`StartInterval` (60 seconds), so the +50 mark is covered if the writer
lock was still held at +26. S2 deletes the file after search is loaded.
The watchdog does not delete it.

No file: the pass does nothing and writes no log line. A malformed
file: one log line, `search resume deadline file malformed`, and no
`launchctl` call.

## S1 write and S2 delete

L1 procedure files are not edited here. S1 and S2 call these helpers.

S1, after `launchctl bootout` of `com.mailroom.ask-mail-serve`:

```zsh
/usr/bin/python3 "$HOME/MailArchive/scripts/search_resume_watchdog.py" \
  write --run-id <run-id>
```

S2, after search is loaded:

```zsh
/usr/bin/python3 "$HOME/MailArchive/scripts/search_resume_watchdog.py" clear
```

`<run-id>` is a short id (`A-Za-z0-9`, `.`, `_`, `-`). It is not a
lock token. The helpers read and write only the deadline file.

## What each fire does

The template `launchd/com.mailroom.search-resume-watchdog.plist.template`
runs `watch` on `StartInterval` 60. `RunAtLoad` is false. There is no
`KeepAlive` on this job. One pass then exits.

Restore happens only when all three are true:

1. `now` is at or past the earlier deadline.
2. `launchctl print gui/$UID/com.mailroom.ask-mail-serve` exits non-zero
   (the job is not loaded). Exit 0 means it is loaded, and the pass
   does nothing.
3. The writer lock is not held.

`launchctl kickstart` does not start a job that S1 has booted out. The
job is gone from the gui domain, so kickstart has nothing to start.
The restore that works on macOS is bootstrap of the installed plist,
then kickstart without `-k`:

```zsh
launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist"
launchctl kickstart "gui/$UID/com.mailroom.ask-mail-serve"
```

`SEARCH_RESUME_PLIST` overrides the installed plist path. The default
is `$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist`, the
rendered agent, not the repo template. Bootstrap loads that plist back
into `gui/$UID`. The checked-in ask-mail-serve template has
`RunAtLoad` true, so bootstrap may already start it. Kickstart is
still issued, without `-k`. `-k` would kill the process bootstrap just
started. Without `-k`, kickstart starts the job if it is loaded and
not running, and it does not kill a running instance. If bootstrap
fails, kickstart is not called; the next interval retries.

Before the deadline the pass does not call `launchctl` or `lsof`.

## Writer lock, read-only

Detection does not take `mailroom.write.lock`. There is no flock
probe. The pass runs `lsof -t` on the lock path and, when the file
records a pid, checks that pid with signal 0 (`os.kill(pid, 0)`),
which does not deliver a signal and does not lock anything.

The lock counts as free only when `lsof` shows no holder and the
recorded pid is not live (or the lock file is absent and `lsof` is
clear). `lsof` exit 1 with empty output means no holder. That is the
normal macOS result when nothing has the file open.

The lock counts as held when any of these are true:

- `lsof` lists a pid
- the recorded pid is live (signal 0 succeeds, including `EPERM`)
- `lsof` or the pid check errors, times out, or returns something
  that is not a pid list
- the lock file exists but cannot be read, or has no usable pid

That last group is fail-closed for the SoR. The pass then does not
bootstrap search.

If the deadline has passed, search is not loaded, and the lock is
held, the pass does not restore. It appends and writes to stderr:

```text
search still off: writer lock held pid=<pid> purpose=<purpose>
```

The next interval tries again. This process does not kill the writer.

A `token=` field in the lock file is ignored. It is never written to
the log or to stderr. The log line is built only from the pid and the
purpose.

## What it never does

- It never opens a database and never writes the SoR.
- It never takes `mailroom.write.lock` and never opens that file for
  writing.
- It never calls `launchctl bootout`.
- It never edits the ask-mail-serve plist.

The log file is `$HOME/MailArchive/logs/search_resume_watchdog.log`
(`SEARCH_RESUME_LOG`). Launchd's own stdout/stderr for the watchdog
go to `$HOME/MailArchive/logs/search-resume-watchdog.launchd.log`
after install. `SEARCH_RESUME_NOW` is a test clock. The template does
not set it.

## Install is separate

When an operator chooses to install, the steps are outside this
change: copy `scripts/search_resume_watchdog.py` to
`$HOME/MailArchive/scripts/`, substitute `__HOME__` in the template,
copy the result to `$HOME/Library/LaunchAgents/`, and bootstrap label
`com.mailroom.search-resume-watchdog`. This repository change does not
do that.
