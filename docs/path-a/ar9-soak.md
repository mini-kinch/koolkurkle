# AR9 Phase E soak

A four hour soak of the live 27B server as a transient `launchctl submit` job. The watchdog stays loaded. There is no plist file. The job is gone after `launchctl remove` or a reboot.

## Target

- Server: `com.mailroom.mlx-lm-server` on `127.0.0.1:1234`.
- Watchdog: `com.mailroom.qwen-watchdog`, left loaded for the whole run.
- Job label: `com.mailroom.path-a-soak`.
- Wrapper: `scripts/path_a_soak_ar.sh`.
- Harness: `scripts/path_a_bench.py soak --continue-on-hang` with `-n 48`, `--interval 300`, `--mem-at 24`, and `--watchdog-log`.

Default logs:

- stdout and stderr: `$HOME/MailArchive/logs/path-a-soak.log`
- JSONL: `$HOME/MailArchive/logs/path-a-soak.jsonl`
- watchdog log the harness reads: `$HOME/MailArchive/logs/qwen-watchdog.log`

## Preconditions

- The model server is up: `GET http://127.0.0.1:1234/v1/models` returns HTTP 200.
- `com.mailroom.qwen-watchdog` is loaded. Do not boot it out for this AR.
- `$HOME/qwen-mlx/HOLD` is absent, or the watchdog will skip every pass and a hang will not be restarted.
- Ollama is not running. `--mem-at 24` fails the soak if swap is at least 1024 MB or Ollama is up.
- The `hf-qwen-stage` LaunchAgent stays disabled.
- You start the job from the target Mac. Do not leave a four hour soak in the foreground of a remote shell. `launchctl submit` is the process that outlives the shell. The wrapper itself returns as soon as the job is submitted.

## Commands

```zsh
scripts/path_a_soak_ar.sh --dry-run start

scripts/path_a_soak_ar.sh \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  start

scripts/path_a_soak_ar.sh status
scripts/path_a_soak_ar.sh remove
```

`--dry-run start` prints the `launchctl submit` line and does not submit. `status` is `launchctl print gui/<uid>/com.mailroom.path-a-soak`. `remove` is:

```zsh
launchctl remove com.mailroom.path-a-soak
```

The submit line is:

```zsh
launchctl submit -l com.mailroom.path-a-soak \
  -o "$HOME/MailArchive/logs/path-a-soak.log" \
  -e "$HOME/MailArchive/logs/path-a-soak.log" -- \
  /usr/bin/python3 scripts/path_a_bench.py soak \
  --continue-on-hang \
  --mem-at 24 \
  --watchdog-log "$HOME/MailArchive/logs/qwen-watchdog.log" \
  -n 48 \
  --interval 300 \
  --out "$HOME/MailArchive/logs/path-a-soak.jsonl"
```

`--ask-mail` is appended when you pass it. `--n` and `--mem-at` must satisfy `mem-at <= n` (the default 24 is inside 48). A smaller `-n` without a smaller `--mem-at` is a usage error and does not submit.

## PASS

`start` prints:

```text
PASS soak-start label=com.mailroom.path-a-soak n=48 interval_s=300 mem_at=24
watchdog=left-loaded
```

That only means the job was submitted. The soak result is in the log when the job exits. PASS for the run is the bench summary:

- `PASS soak` with `failures=0`. `hung` may be greater than 0. This line is timing and hangs only.
- Every hung request is followed by a timing pass (`next=pass`): the next request returned inside the timeout with HTTP 200 and `finish_reason` `stop`.
- If a request hung, the watchdog log has a line whose timestamp (`YYYY-MM-DD HH:MM:SS` at the start of the line) falls within 5 minutes after the hang. The summary prints `HUNG soak idx=<n> local_time=<ts> next=pass watchdog=<stamp>`.
- `PASS soak_content short=0`. A reply can be fast and still fail the content check. `path_a_bench.py` marks `content_len` <= 300 (`CONTENT_MIN`) as FAIL even when the clock, HTTP status, and finish reason pass. Seen live: 34 s, HTTP 200, `finish=stop`, `content_len=202` -> content FAIL. With `--continue-on-hang` that is its own line, `FAIL soak_content short=1 limit=content_len<=300 idx=<n> content_len=202`, and it does not flip `PASS soak` or `next=pass`. `OVERALL` is still FAIL while that line is FAIL.
- `PASS soak_mem_at k=24` with `swap_used_mb` under 1024 and `ollama=down`.
- `PASS ask_mail` and `OVERALL PASS`.

`status` prints `PASS status` when the job is still loaded. `remove` prints `PASS remove label=com.mailroom.path-a-soak`.

## Hang timing versus the live watchdog

The harness window is 5 minutes (`WATCHDOG_WINDOW_S` is 300). It reads the log at the end. It does not restart anything.

On the installed watchdog, a hang that still answers `/v1/models` is behind the quiet gate. Worst case from that hang to `kickstart -k` is about 1750 seconds, which is outside the 5 minute window. A restart line later than 5 minutes fails this AR (`watchdog=none`) even though the watchdog is behaving as installed. Do not tighten `WD_QUIET_SECS` to make the bar pass.

A failure that makes `/v1/models` non-200 skips the quiet gate and kickstarts after two passes (about 10 minutes). That is also longer than 5 minutes, so the same FAIL applies. The log line the harness accepts is any timestamp in the window, not only a `restart kickstart` line. Read the log yourself for `restart kickstart` when you score the run.

## Restore

Stop the transient job. Leave the watchdog loaded.

```zsh
scripts/path_a_soak_ar.sh remove
# or, the same thing:
launchctl remove com.mailroom.path-a-soak
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1234/v1/models
```

If the model port is not 200, kickstart only the model server:

```zsh
launchctl kickstart -k "gui/$(id -u)/com.mailroom.mlx-lm-server"
```

There is no plist to delete. A reboot also drops the submitted job.

## Time

48 requests times 300 seconds between them is 4 hours, plus each request's own time (the per-request timeout defaults to 300 seconds). A hang adds that timeout and then the run continues. Budget an afternoon, and longer if several requests hang.

## What not to touch

- `127.0.0.1:8743` and `com.mailroom.ask-mail-serve`.
- `ask_mail.py`, except the read-only hash from `--ask-mail`.
- The `hf-qwen-stage` agent. Leave it disabled.
- Ollama. Do not start it while Qwen is up. `--mem-at` records whether it is down.
- `com.mailroom.qwen-watchdog`. This AR does not boot it out and does not edit the quiet gate.
- The model pin in the chat session up script.
