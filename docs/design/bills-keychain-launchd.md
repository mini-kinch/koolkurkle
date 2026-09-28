# Bills phone Keychain under the daily LaunchAgent

Status: design plus a repo-only error classification. Nothing in this change
is installed on a Mac. The diagnosis plan and the install steps below are
future approval requests. They are text only.

Base: `main` at `9c8a454823582c8f2b67a0b3727930ceee53f555`.

The nightly job is LaunchAgent `com.mailroom.daily`
(`launchd/com.mailroom.daily.plist`). Its bills step runs
`scripts/notify_bills.py`. Since 09-23 the bills step has exited 1 on 3 of 3
nights. The LaunchAgent stderr is
`Keychain mailroom.notify.phone missing or unreadable`. The same
`security find-generic-password` read succeeds from an interactive login
shell. No text is sent.

## What the log is actually showing

On `main`, `keychain_phone()` runs `security` and, on any non-zero status,
raises `SystemExit("Keychain mailroom.notify.phone missing or unreadable")`
(`scripts/notify_bills.py:46-47` at `9c8a454`). That string is the Python
process's own stderr. The exit code 1 is `SystemExit` with a string message
(Python exits 1). `security`'s status and stderr are discarded.

`mailroom_daily.py` `run_step` (`scripts/mailroom_daily.py:485-503`, the
child `subprocess.run` at line 501) logs `step exit: bills rc=<child>`. It
does not read the child's stderr. The LaunchAgent already points
`StandardErrorPath` at the daily stderr log
(`launchd/com.mailroom.daily.plist:47-49`), so the only Keychain line in
that log is the generic `SystemExit` text.

Callers on `main`:

- `scripts/notify_bills.py:171` and `:188` call `keychain_phone()` then
  `send_imessage()` (`:70-91`, `osascript` at `:82-88`).
- `scripts/path_a_failalert.py` is not in this tree at `9c8a454`, and it is
  not on any remote branch. The operator description says a failure-alert
  hook at about line 115 calls
  `notify_bills.send_imessage(notify_bills.keychain_phone(), body)` and is
  started with `launchctl submit` in the user session. That hook, wherever
  it lives on the Mac, shares `keychain_phone()`. This document does not
  invent a line number for a file the repo does not contain.

`send_imessage` runs only after `keychain_phone()` returns. A failed read
sends no message.

## Ranked cause hypotheses

Each item is an explanation of the failed read. The last item is a confirmed
observability bug. It is why the nightly log cannot prove which of the
earlier items fired.

### 1. `errSecInteractionNotAllowed` (-25308), seen as exit 36

Most likely. An interactive login shell can show a Keychain prompt. The
LaunchAgent job cannot. The read then fails before a value is returned.

Apple documents `errSecInteractionNotAllowed` as "Interaction with the
Security Server is not allowed"
([Security Framework Result Codes](https://developer.apple.com/documentation/security/security-framework-result-codes)).
The macOS SDK header `SecBase.h` assigns that constant `-25308`.

Apple's `security` tool returns the command's `OSStatus` from `main`
([SecurityTool `security.c`](https://github.com/apple-oss-distributions/Security/blob/main/SecurityTool/macOS/security.c)).
A process exit status keeps the low 8 bits, so `-25308` is reported as
`36`. The tool's stderr for that status is the `sec_perror` line
`User interaction is not allowed.` (`sec_perror` in the same file).
`find-generic-password` uses the legacy keychain search API and, on a miss,
reports `errSecItemNotFound` via `SecKeychainSearchCopyNext`. An interaction
failure happens earlier, when copying the secret needs a prompt.

Two repo facts make a prompt likely:

- The checked-in re-store line is
  `security add-generic-password -a mailroom -s mailroom.notify.phone -U -w`
  (`scripts/notify_bills.py:63-66` on `main`). It passes neither `-A` nor
  `-T`.
- Apple's own usage text for `add-generic-password` says: "By default, the
  application which creates an item is trusted to access its data without
  warning." `-T` adds a trusted application path. `-A` means "Allow any
  application to access this item without warning (insecure, not
  recommended!)" (`security.c`, `add-generic-password` help). An item whose
  trust list is empty, or whose trust list names a different binary than the
  one the agent runs, asks every time.

The agent is `ProcessType` `Background`
(`launchd/com.mailroom.daily.plist:50-51`). `launchd.plist(5)` defines
`ProcessType` as a resource class. `Background` means "work that was not
directly requested by the user," with tighter CPU and I/O limits. It does
not select the session. A prompt still has to go through the Security
Server. When that interaction is refused, the status is
`errSecInteractionNotAllowed`, independent of the resource-limit text.

`RunAtLoad` is true (`launchd/com.mailroom.daily.plist:40-41`) and the
calendar is 20:05 local (`:34-39`). The three failures are the nightly
runs, so this is not only a boot-time race. The interactive shell still
succeeds, which fits a context that can present UI.

### 2. Login keychain locked, or absent from the search list

Next most likely, and the locked-keychain case overlaps hypothesis 1.

`find-generic-password` with no keychain path "uses the default search
list" (Apple `security.c` usage text). This repo calls it with `-s` and
`-a` only (`scripts/notify_bills.py:30-39` on `main`). There is no
`login.keychain-db` argument.

Apple documents two different statuses:

- `errSecItemNotFound` — "The item cannot be found." SDK value `-25300`.
  The `security` exit status is `44`. Stderr from
  `SecKeychainSearchCopyNext` is "The specified item could not be found in
  the keychain." That is what a search list that does not contain the
  login keychain looks like, and what a genuinely missing item looks like.
- `errSecAuthFailed` — "Authorization and/or authentication failed." SDK
  value `-25293`. The `security` exit status is `51`. The usual stderr is
  "The user name or passphrase you entered is not correct." That is the
  failed-unlock path. A locked login keychain whose unlock dialog cannot
  appear is hypothesis 1 (`-25308` / exit 36) rather than 51.

`launchd.plist(5)` `LimitLoadToSessionType` limits which sessions load an
agent. This template does not set the key. Technical Note TN2083
([Daemons and Agents](https://developer.apple.com/library/archive/technotes/tn2083/_index.html))
says that when the key is omitted, launchd assumes `Aqua`. An Aqua agent
"has access to all GUI services." The per-user `Background` session type
in that note is a different knob from `ProcessType` `Background`. This
plist is therefore an Aqua-default agent with a Background resource class,
not a Background-session agent.

That weakens "the login keychain is not in the search list because the job
is a Background session." It leaves "the keychain is locked at 20:05" as a
live possibility: the unlock UI is the same Security Server interaction as
hypothesis 1. The swallowed stderr is why these two cannot be separated
from the current log.

### 3. The item ACL trusts a different binary than the one the agent runs

Apple's default, quoted above, trusts the application that created the
item. `SecTrustedApplication` records that program's path and designated
requirement. A later read by a different binary is a prompt, and a prompt
the agent cannot show is hypothesis 1.

On `main`, `keychain_phone()` execs the bare name `security`
(`scripts/notify_bills.py:32`). The plist `PATH` is
`/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`
(`launchd/com.mailroom.daily.plist:20-22`), so the nightly job resolves
`/usr/bin/security` if `PATH` is the one in the template. An interactive
shell can have a different `PATH`. `security` is not a Homebrew formula in
this repo's install cards, so a path mismatch is less likely than a trust
list that names Terminal, a Python binary, or an empty list (`-T ""`,
which Apple documents as removing the default trust).

The creating command in the script's own error string has no `-T
/usr/bin/security`. If the item was created by that command, the trusted
application is whichever `security` binary ran in that Terminal. If it was
created some other way, the trust list is unknown. This stays third
because both the shell and the agent usually end at `/usr/bin/security`,
and the symptom (shell works, agent fails) is explained by UI permission
even when the binary path matches.

### 4. `HOME` or another launchd environment variable changes the search list

Least likely for this plist.

`launchd.plist(5)` `EnvironmentVariables` adds variables. It does not
unset the variables launchd already provides to a user agent. TN2083
describes an agent as a process that can reach the user's home directory,
which a daemon cannot. This template sets `PATH`, `PYTHONUNBUFFERED`,
`MAILARCHIVE`, `MAILROOM_DB`, `MAILROOM_KEYCHAIN_ITEM`, and `OLLAMA_HOST`
(`launchd/com.mailroom.daily.plist:18-32`). It does not set `HOME`.
`MAILROOM_KEYCHAIN_ITEM` is `mailroom.imap.app-password`. `notify_bills.py`
does not read that variable. The phone service name is the constant
`mailroom.notify.phone` (`scripts/notify_bills.py:19` on `main`).

`security` without a keychain path uses the session search list, not
`$HOME/Library/Keychains/...`. A wrong `HOME` would matter if a caller
passed an explicit path under `$HOME`. This call does not. A gui-domain
agent (`launchctl bootstrap gui/<uid>`,
`scripts/README.mailroom-daily.md` install card) should already have the
user's `HOME`. The diagnosis plan records whether `HOME` is set, and does
not record the path.

### 5. `security` stderr is swallowed, so the real status is lost

Confirmed in the source. It is not a reason `security` itself fails. It is
why every failure looks the same.

`scripts/notify_bills.py:46-47` on `main` ignores `result.returncode`'s
specific value and ignores `result.stderr`. The nightly line cannot be 44,
36, or 51, because those codes never leave the child. The operator-visible
code is always 1, and the operator-visible text is always the generic
sentence. Hypotheses 1–4 stay hypotheses until a run under the same
launchd context keeps the `security` status and a redacted stderr.

## Diagnosis plan (future approval request)

This is an approval request. This pull request does not run it, does not
add a LaunchAgent, and does not read a Keychain.

Run it on the Mini, in a real Terminal, after the machine banner matches
Mini. One paste per fence. The commands are read-only against the phone
item: stdout of `security -w` goes to `/dev/null`. They print the exit
code and redacted stderr. They do not print the item value, `$HOME`, or
`$USER`.

Afterward, paste back only lines that start with `probe=`,
`security_rc=`, `home_set=`, `security_bin=`,
`login_keychain_search_hits=`, or `keychain_info=`. If any other line
contains a slash or a run of digits, delete the log and stop.

The two probes are different contexts on purpose. `launchctl submit` is
the user-session context described for the alert hook. The temporary
agent copies the nightly template's `ProcessType` `Background` and, like
that template, omits `LimitLoadToSessionType`.

```zsh
# Mini — write the read-only diagnosis script (no secret, stdout of security discarded)
cat > "$HOME/MailArchive/logs/bills-keychain-diag.sh" << 'EOF'
#!/bin/zsh
set +e
probe="${1:-unset}"
err="$HOME/MailArchive/logs/bills-keychain-diag-${probe}.err"
out="$HOME/MailArchive/logs/bills-keychain-diag-${probe}.log"
/usr/bin/security find-generic-password -s mailroom.notify.phone -a mailroom -w >/dev/null 2>"$err"
rc=$?
if [ -n "${HOME:-}" ]; then home_set=yes; else home_set=no; fi
if [ -x /usr/bin/security ]; then security_bin=yes; else security_bin=no; fi
hits=$(/usr/bin/security list-keychains 2>/dev/null | /usr/bin/grep -c 'login.keychain' || true)
info=$(/usr/bin/security show-keychain-info "$HOME/Library/Keychains/login.keychain-db" 2>&1 || true)
info=$(printf '%s\n' "$info" | /usr/bin/sed -E 's#/[^[:space:]"]*#[path]#g')
red=$(/usr/bin/sed -E 's/[0-9]{7,}/[redacted]/g; s/[0-9][0-9. -]{8,}[0-9]/[redacted]/g' "$err")
{
  echo "probe=${probe}"
  echo "security_rc=${rc}"
  echo "home_set=${home_set}"
  echo "security_bin=${security_bin}"
  echo "login_keychain_search_hits=${hits}"
  echo "keychain_info=${info}"
  echo "stderr<<EOF"
  printf '%s\n' "$red"
  echo "EOF"
} > "$out"
rm -f "$err"
exit 0
EOF
```

```zsh
# Mini — make the diagnosis script executable
chmod 700 "$HOME/MailArchive/logs/bills-keychain-diag.sh"
```

```zsh
# Mini — interactive login shell, same script, stdout of security discarded
zsh "$HOME/MailArchive/logs/bills-keychain-diag.sh" interactive
```

```zsh
# Mini — print the interactive diagnosis log with slash paths removed
/usr/bin/sed -E 's#/[^[:space:]"]*#[path]#g' "$HOME/MailArchive/logs/bills-keychain-diag-interactive.log"
```

```zsh
# Mini — user-session one-shot, same family as launchctl submit
launchctl submit -l com.mailroom.bills-keychain-diag -o "$HOME/MailArchive/logs/bills-keychain-submit.out" -e "$HOME/MailArchive/logs/bills-keychain-submit.err" -- /bin/zsh "$HOME/MailArchive/logs/bills-keychain-diag.sh" submit
```

```zsh
# Mini — print the submit diagnosis log with slash paths removed
/usr/bin/sed -E 's#/[^[:space:]"]*#[path]#g' "$HOME/MailArchive/logs/bills-keychain-diag-submit.log"
```

```zsh
# Mini — remove the submit job (this label only)
launchctl remove com.mailroom.bills-keychain-diag
```

Temporary agent, matching the nightly resource class. This plist is not
the repo template. It is a one-shot under `$HOME/Library/LaunchAgents`
and is removed in the next fences. It does not touch `com.mailroom.daily`.

```zsh
# Mini — write a one-shot Background agent that only runs the diagnosis script
cat > "$HOME/Library/LaunchAgents/com.mailroom.bills-keychain-diag.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.mailroom.bills-keychain-diag</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$HOME/MailArchive/logs/bills-keychain-diag.sh</string>
    <string>launchagent</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$HOME/MailArchive/logs/bills-keychain-agent.out</string>
  <key>StandardErrorPath</key>
  <string>$HOME/MailArchive/logs/bills-keychain-agent.err</string>
</dict>
</plist>
EOF
```

```zsh
# Mini — bootstrap the diagnosis agent only
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.mailroom.bills-keychain-diag.plist"
```

```zsh
# Mini — print the agent diagnosis log with slash paths removed
/usr/bin/sed -E 's#/[^[:space:]"]*#[path]#g' "$HOME/MailArchive/logs/bills-keychain-diag-launchagent.log"
```

```zsh
# Mini — bootout the diagnosis label only
launchctl bootout gui/$(id -u)/com.mailroom.bills-keychain-diag
```

```zsh
# Mini — delete the diagnosis plist, script, and logs
rm -f "$HOME/Library/LaunchAgents/com.mailroom.bills-keychain-diag.plist" "$HOME/MailArchive/logs/bills-keychain-diag.sh" "$HOME/MailArchive/logs"/bills-keychain-diag-*.log "$HOME/MailArchive/logs"/bills-keychain-diag-*.err "$HOME/MailArchive/logs/bills-keychain-submit.out" "$HOME/MailArchive/logs/bills-keychain-submit.err" "$HOME/MailArchive/logs/bills-keychain-agent.out" "$HOME/MailArchive/logs/bills-keychain-agent.err"
```

How to read the three `security_rc` lines:

| `security_rc` | Class | Hypothesis |
|---|---|---|
| 0 | read worked in that context | that context is not the failing one |
| 36, or stderr contains `-25308` / `User interaction is not allowed` | `interaction_not_allowed` | hypothesis 1 |
| 51, or stderr contains `-25293` / passphrase text | `keychain_locked` | hypothesis 2, unlock failed |
| 44, or stderr contains `-25300` / `could not be found` | `item_not_found` | search list or missing item (hypothesis 2 or 3) |
| anything else | `other` | keep the redacted stderr; do not guess |

If `interactive` is 0 and `launchagent` is 36, the nightly context is
refusing UI. If `submit` is 0 and `launchagent` is not, the alert hook's
`launchctl submit` context is a different question from the nightly agent.
If all three are 0, the failure is not reproduced by a one-shot read and
the next nightly log (after the install AR) is the evidence.

## Fix options

### A. Classify the `security` status and keep redacted stderr

`keychain_phone()` records the exit code, maps it to `item_not_found`,
`interaction_not_allowed`, `keychain_locked`, or `other`, and raises
`KeychainPhoneError` (a `SystemExit`) whose message contains the class,
the code, and stderr after the value is removed. The binary is the
constant `/usr/bin/security`. There is no environment override and no
second source.

Tradeoff: the next failure tells us which hypothesis fired. It does not
by itself make the read succeed. The nightly job still exits non-zero
when the read fails. That is the fail-closed behavior.

This is the change in the pull request. It is repo-testable with a fake
`security` binary. It does not touch the installed agent.

### B. Pass an explicit login-keychain path

`security find-generic-password` accepts a keychain path. Passing
`$HOME/Library/Keychains/login.keychain-db` bypasses a session search
list that omitted the login keychain.

Tradeoff: it addresses hypothesis 2's search-list reading only. A locked
keychain and an ACL prompt still fail. The path is machine-local and
must be built from `$HOME` at runtime, never committed. Do this only
after a diagnosis `security_rc` of 44. Not in this pull request.

### C. Set `LimitLoadToSessionType` `Aqua`, or `ProcessType` `Standard` / `Interactive`

TN2083 already assumes `Aqua` when `LimitLoadToSessionType` is absent, so
writing `Aqua` explicitly matches the documented default. It would stop
the job from loading if someone later bootstraps it into another session.

`ProcessType` `Standard` is "equivalent to no ProcessType being set"
(`launchd.plist(5)`). `Interactive` is the app resource class. Either
change applies to the whole nightly chain (headers, bodies, classify,
bills, embed), because there is one agent. `Background` today is a
resource limit so that chain does not compete with UI.

Tradeoff: a session or resource-class edit is a reasonable follow-up if
the diagnosis class is `interaction_not_allowed` and an Aqua prompt is
the intended fix. It is the wrong first edit while the status is still
discarded. This pull request does not change
`launchd/com.mailroom.daily.plist`.

### D. Fix the item ACL (operator step)

After the class is `interaction_not_allowed`, the operator can recreate
the item so `/usr/bin/security` is the trusted application (`-T
/usr/bin/security`), matching Apple's default "the application which
creates an item is trusted." `-A` allows any application. Apple marks
`-A` insecure and not recommended. This design does not use `-A`.

Tradeoff: this is a Keychain write on the Mac. It is out of scope for a
repo change. Do it only after the diagnosis names the class, and only in
a later approval request. The re-store command must keep `-w` last so the
value is typed at the prompt.

### E. A mode-0600 file outside the repo

A local file could hold the phone number when Keychain fails.

This design does not do that. A second store is a second secret, and an
automatic switch would be a silent fallback. `keychain_phone()` fails
closed. There is no fallback source.

## Recommendation

Ship option A now. Keep the phone number in the Keychain, pin
`/usr/bin/security`, and raise a classified error that includes the
`security` exit code and redacted stderr. Do not change the plist, do not
pass a keychain path, and do not add a file fallback until the diagnosis
approval request returns a `security_rc`.

The matching Mac follow-up, after that `security_rc` is known:

- `interaction_not_allowed` (36 or `-25308` text): ACL approval request
  (option D). Consider option C only if the ACL already trusts
  `/usr/bin/security` and the agent context still cannot show UI.
- `keychain_locked` (51): unlock / lock-policy approval request. Option C
  does not unlock a keychain.
- `item_not_found` (44): option B, or recreate the item if the search list
  already contains the login keychain.
- `other`: read the redacted stderr before choosing.

## Draft install approval request

Text only. This pull request does not copy files onto the Mini and does
not bootstrap, bootout, or kickstart any label.

Install one file. The LaunchAgent runs
`$HOME/MailArchive/scripts/notify_bills.py` via `mailroom_daily.py`
(`find_script` searches `$MAILARCHIVE/scripts` first). The repo template
`launchd/com.mailroom.daily.plist` is unchanged. Do not bootout
`com.mailroom.daily`.

| File | Role | sha256 |
|---|---|---|
| `scripts/notify_bills.py` (this commit) | file to copy | `a083dda717e0762920be5e55aeb7bb0b5833130b3a819612456942d6d1c84b0c` |
| `scripts/notify_bills.py` on `main` `9c8a454` | rollback target when the live file matches `main` | `fa4317ed6ddadf896b84f914b24c6ae31861cd1b52a3144b358f93a71a3d7bb8` |

Tests and this design doc stay in git. They are not copied to MailArchive.

```zsh
# Mini — record the live bills script before replacing it
shasum -a 256 "$HOME/MailArchive/scripts/notify_bills.py"
```

```zsh
# Mini — keep a rollback copy next to the live script
cp "$HOME/MailArchive/scripts/notify_bills.py" "$HOME/MailArchive/scripts/notify_bills.py.bak-keychain-class"
```

```zsh
# Mini — confirm the checkout file matches this commit
shasum -a 256 scripts/notify_bills.py
```

The checkout line must print
`a083dda717e0762920be5e55aeb7bb0b5833130b3a819612456942d6d1c84b0c`. If it
does not, stop.

```zsh
# Mini — install the classified bills script
cp scripts/notify_bills.py "$HOME/MailArchive/scripts/notify_bills.py"
```

```zsh
# Mini — confirm the live file matches this commit
shasum -a 256 "$HOME/MailArchive/scripts/notify_bills.py"
```

Leave the agent loaded. The next calendar run picks up the new file. Do
not `launchctl kickstart` the daily label from this card (that runs the
whole chain). Do not read the Keychain from the install card.

Rollback, if the live sha recorded in the first fence matches
`fa4317ed6ddadf896b84f914b24c6ae31861cd1b52a3144b358f93a71a3d7bb8` or
matches the backup copy:

```zsh
# Mini — restore the previous bills script
cp "$HOME/MailArchive/scripts/notify_bills.py.bak-keychain-class" "$HOME/MailArchive/scripts/notify_bills.py"
```

```zsh
# Mini — confirm the restored sha
shasum -a 256 "$HOME/MailArchive/scripts/notify_bills.py"
```

```zsh
# Mini — remove the backup after the restored sha matches the recorded one
rm -f "$HOME/MailArchive/scripts/notify_bills.py.bak-keychain-class"
```

If the recorded live sha matches neither `main` nor the backup, restore
the backup and stop. Do not bootout `com.mailroom.daily`.
