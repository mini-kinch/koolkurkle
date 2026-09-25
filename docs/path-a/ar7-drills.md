# AR7 Phase C drills

Foreground drills for the live Path A server on the target Mac. They take `127.0.0.1:1234` down on purpose and watch `scripts/qwen-mlx-watchdog.sh` bring it back. Nothing in this repo runs them against a live server.

## Target

- Listener: `mlx_lm.server` on `127.0.0.1:1234`, LaunchAgent `com.mailroom.mlx-lm-server`.
- Watchdog: `scripts/qwen-mlx-watchdog.sh` under LaunchAgent `com.mailroom.qwen-watchdog` (template `launchd/com.mailroom.qwen-watchdog.plist.template`, `StartInterval` 300).
- HOLD file: `$HOME/qwen-mlx/HOLD`.
- Watchdog state directory: `$HOME/qwen-mlx/watchdog`.
- Watchdog log: `$HOME/MailArchive/logs/qwen-watchdog.log`.

The drill script is `scripts/path_a_drill.sh`. It is bash 3.2 compatible. Run it in the foreground. It does not background itself.

## Preconditions

- You are at a terminal on the target Mac, not inside a remote background shell.
- `com.mailroom.mlx-lm-server` is loaded and `GET http://127.0.0.1:1234/v1/models` returns HTTP 200.
- `com.mailroom.qwen-watchdog` is loaded.
- `$HOME/qwen-mlx/HOLD` is absent. The drill refuses to start if it exists.
- Ollama is not running.
- The `hf-qwen-stage` LaunchAgent stays disabled. Do not bootstrap it.

## What the quiet gate means here

A chat hang that still answers `/v1/models` waits for `WD_QUIET_SECS` (900) before a chat probe, then two failing passes. Worst case from a hang to `kickstart -k` is about 1750 seconds. Do not change that quiet gate for these drills.

`stop-cont` and `stop-hold` (after HOLD is removed) make `/v1/models` fail while the same pid is still the job. The watchdog counts that failure with no quiet gate (`WD_FAILS_BEFORE_RESTART` is 2, `StartInterval` is 300). Budget one pass of up to 300 seconds, another pass, then kickstart and model load. The default `--wait` for those commands is 900 seconds.

`loop3` also waits out `WD_POST_RESTART_GRACE` (600 seconds) after each kickstart before the next failure can count. The default `--wait` per phase is 1800 seconds. Each phase is `kill -STOP` on the current listener, including the new pid after each kickstart.

The live server LaunchAgent has `KeepAlive` true, so `kill <pid>` is not a watchdog test. launchd relaunches the listener within seconds and the watchdog writes no `restart kickstart` line. These drills do not change that plist. Preflight reads it and prints the value:

```zsh
plutil -extract KeepAlive raw "$HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist"
```

Override the path with `DRILL_SERVER_PLIST` if the agent file is not in that default location. A missing file or a missing key prints `preflight keepalive=unknown`.

`port-kill` accepts either recovery. HTTP 200 and a new listener pid within `--wait` is PASS. `recovered_by=launchd-keepalive` means a new pid and no new `restart kickstart` line. `recovered_by=watchdog` means a new `restart kickstart` line as well. With KeepAlive true, expect `launchd-keepalive` and a few seconds, not a watchdog line. The 900 second bound is the ceiling for a host whose plist does not relaunch the job.

`stop-cont`, `stop-hold`, and `loop3` use `kill -STOP`. A stopped pid is still the job, so KeepAlive does not replace it and `/v1/models` stays down until the watchdog kickstarts. That is the failure HOLD and the loop guard can see.

## Commands

From a checkout that matches the scripts installed under `$HOME/MailArchive/scripts` (or from that directory):

```zsh
scripts/path_a_drill.sh --dry-run port-kill
scripts/path_a_drill.sh --dry-run stop-hold
scripts/path_a_drill.sh --dry-run port-kill-hold
scripts/path_a_drill.sh --dry-run stop-cont
scripts/path_a_drill.sh --dry-run loop3

scripts/path_a_drill.sh \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  port-kill

scripts/path_a_drill.sh \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  stop-hold

scripts/path_a_drill.sh \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  stop-cont

scripts/path_a_drill.sh \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  loop3
```

`port-kill-hold` is an alias of `stop-hold`. It prints that, then runs `stop-hold`. It does not plain-kill the listener.

`--dry-run` prints the commands and does not call `launchctl`, `plutil`, `curl`, `kill`, `ps`, or `lsof`.

`--ask-mail` is optional. When it is set, the drill hashes that file at start and end and FAILs if the hash changes. It never writes the file.

## What each command does

`port-kill` records the listener pid, does not create HOLD, and `kill`s that pid (no signal). It polls `GET /v1/models` and the listener pid until HTTP 200 and a new pid, or the wait bound. PASS does not require a watchdog line. A new `restart kickstart` line in the watchdog log is `recovered_by=watchdog`. A new pid with no new `restart kickstart` line is `recovered_by=launchd-keepalive`.

`stop-hold` is the HOLD test. KeepAlive masks a plain kill, so this command does not use one. It touches `$HOME/qwen-mlx/HOLD`, runs `kill -STOP` on the listener, and waits the full bound. A new `restart kickstart` line in that window is a FAIL: the watchdog must skip the pass while HOLD exists. It then removes HOLD and waits for a new `fail consecutive=` line, then a new `restart kickstart` line, HTTP 200, and a new pid. The EXIT trap always runs `kill -CONT` on the stopped pid. `port-kill-hold` is the same command.

`stop-cont` runs `kill -STOP` on the listener pid with no HOLD file. PASS needs a new `fail consecutive=` line, then a new `restart kickstart` line, HTTP 200, and a new pid. The EXIT trap always runs `kill -CONT` on the stopped pid.

`loop3` runs `kill -STOP` on the listener and waits for a watchdog restart, three times. After each kickstart the new pid is stopped again, so launchd KeepAlive never replaces the job and the watchdog loop guard is the thing that runs. The fourth `kill -STOP` must not produce another `restart kickstart` line. PASS needs `$HOME/qwen-mlx/HOLD` to contain `loop guard` (the watchdog writes `loop guard: HOLD written`). The trap then `kill -CONT`s every pid this run stopped and removes that HOLD.

## PASS / FAIL

A passing run prints a PASS line:

```text
PASS port-kill elapsed_s=<seconds> pid_before=<pid> pid_after=<pid> recovered_by=launchd-keepalive
PASS port-kill elapsed_s=<seconds> pid_before=<pid> pid_after=<pid> recovered_by=watchdog
PASS stop-hold elapsed_s=<seconds> pid_before=<pid> pid_after=<pid> hold_elapsed_s=<seconds> recovery_elapsed_s=<seconds>
PASS stop-cont elapsed_s=<seconds> pid_before=<pid> pid_after=<pid>
PASS loop3 elapsed_s=<seconds> restarts_seen=<n> hold=loop-guard
```

`port-kill-hold` prints `port-kill-hold alias of stop-hold: ...` and then the `PASS stop-hold` line.

`loop3` also prints `loop3 hold_text=loop guard: HOLD written`.

Poll lines look like `poll cmd=<command> elapsed_s=<seconds> models=<code> restarts=<n>`.

Exit 0 is PASS. Exit 1 is FAIL (`FAIL <command> elapsed_s=<seconds> reason=<why>`). Exit 2 is a bad flag.

## Restore

The EXIT trap runs after the drill has disturbed the server (not after a preflight refusal):

1. `kill -CONT` on every pid this run stopped (`stop-hold`, `port-kill-hold`, `stop-cont`, and each `loop3` cycle).
2. Remove HOLD if this run created it (`stop-hold` / `port-kill-hold`) or if this run is `loop3`.
3. `GET /v1/models`. If the status is not 200: `launchctl kickstart -k gui/<uid>/com.mailroom.mlx-lm-server`.

If the drill process itself is killed before the trap runs, do this by hand. `kill -CONT` every pid printed on an `action kill -STOP` line (a plain `port-kill` has none). The first pid is also the `preflight pid=` line:

```zsh
kill -CONT <pid>
rm -f "$HOME/qwen-mlx/HOLD"
code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:1234/v1/models || true)
if [ "$code" != "200" ]; then
  launchctl kickstart -k "gui/$(id -u)/com.mailroom.mlx-lm-server"
fi
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1234/v1/models
```

Do not restore with the chat session up script. That script also bounces retrieval and clears HOLD.

## Time

| Command | Default wait | Operator budget |
| --- | --- | --- |
| `port-kill` | 900s ceiling | KeepAlive true: a few seconds (`recovered_by=launchd-keepalive`). KeepAlive false or unknown: about 15 minutes for two watchdog passes, kickstart, and model load (`recovered_by=watchdog`) |
| `stop-hold` (`port-kill-hold`) | 900s with HOLD, then 900s after HOLD is removed | about 30 minutes. The silence window covers two 300s watchdog passes with no `restart kickstart`. Recovery is the same budget as `stop-cont`. KeepAlive does not shorten either half |
| `stop-cont` | 900s | about 15 minutes. KeepAlive does not mask `kill -STOP` |
| `loop3` | 1800s per phase, four phases of `kill -STOP` | about 75–90 minutes; the four full waits are 2 hours. Each phase includes post-restart grace (600s) before the next stop can count. KeepAlive does not replace a stopped pid |

## What not to touch

- `127.0.0.1:8743` and `com.mailroom.ask-mail-serve`.
- `ask_mail.py`. The optional `--ask-mail` flag only hashes it.
- The `hf-qwen-stage` agent. Leave it disabled.
- Ollama. Do not start it while Qwen is up.
- The model pin in the chat session up script, and the watchdog quiet-gate defaults (`WD_QUIET_SECS` and the access-log mtime check).
- The watchdog state files under `$HOME/qwen-mlx/watchdog`. Do not hand-edit `restarts` to fake the loop guard.
