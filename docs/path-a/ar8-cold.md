# AR8 Phase D cold

Idle the live 27B server for 30 minutes with the watchdog booted out, then run a 60 second max-tokens-1 probe and one full paste request. The wrapper always bootstraps the watchdog plist on the way out.

## Target

- Server: `com.mailroom.mlx-lm-server` on `127.0.0.1:1234`. Leave this agent loaded.
- Watchdog only: `com.mailroom.qwen-watchdog`, plist `$HOME/Library/LaunchAgents/com.mailroom.qwen-watchdog.plist`.
- Harness: `scripts/path_a_bench.py cold --idle 1800`.
- Wrapper: `scripts/path_a_cold_ar.sh`.

`cold --idle` sleeps, sends the max-tokens-1 probe with a 60 second timeout, then one paste request. The paste timing bar is 90 seconds. A probe timeout is a wedge (bench exit 4) and the paste is not sent. The harness never calls `launchctl`.

The paste request is three lines, read separately. Thresholds are unchanged (90 seconds, content length greater than 300):

- `PASS cold_request` / `FAIL cold_request` is timing: the paste returned inside 90 seconds. A short body or `finish_reason` `length` does not flip this line.
- `PASS cold_truncated truncated=0` or `FAIL cold_truncated truncated=<n> idx=<n> finish=length content_len=<len> completion_tokens=<n>`. `finish_reason` `length` is a cut-off. This line scores the paste only.
- `PASS cold_content short=0 limit=content_len<=300` or `FAIL cold_content short=<n> limit=content_len<=300 idx=<n> content_len=<len>`. Content length 300 or less is a short answer. A `finish=stop` reply with 79 completion tokens and `content_len` 202 is short, not truncated. One reply can fail both lines.

`cold_probe` is not part of the length check. A max-tokens-1 probe can finish `length` with a one-character body and still be `PASS cold_probe`. Its progress `length=` is `na`. The probe row still records `finish_reason`, `content_len`, and `completion_tokens`.

Before `OVERALL`, cold prints `FAILS` and `INVALIDS`. A clean run is `FAILS none` and `INVALIDS none`. A cut-off paste is `FAILS cold_truncated`. A short paste is `FAILS cold_content`. Both is `FAILS cold_truncated cold_content`. A slow paste is `FAILS cold_request` as well.

## Preconditions

- Foreground terminal on the target Mac.
- `GET http://127.0.0.1:1234/v1/models` returns HTTP 200 before you start.
- The watchdog LaunchAgent is loaded, and the plist basename is `com.mailroom.qwen-watchdog.plist`. The wrapper refuses any other basename.
- `$HOME/qwen-mlx/HOLD` may exist or not. This AR does not create it. The watchdog is booted out, so HOLD is not what keeps it quiet.
- Ollama is not running.
- The `hf-qwen-stage` LaunchAgent stays disabled.

## Commands

```zsh
scripts/path_a_cold_ar.sh --dry-run

scripts/path_a_cold_ar.sh \
  --idle 1800 \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/MailArchive/logs/path-a-cold.jsonl"
```

`--idle` defaults to 1800 when omitted. `--dry-run` prints `launchctl print`, `launchctl bootout`, the bench command, and `launchctl bootstrap`. It does not run them.

The wrapper:

1. Confirms `launchctl print gui/<uid>/com.mailroom.qwen-watchdog` succeeds.
2. `launchctl bootout gui/<uid> "$HOME/Library/LaunchAgents/com.mailroom.qwen-watchdog.plist"`.
3. Confirms the watchdog label is no longer loaded. If it is still loaded, the bench does not run.
4. Runs `/usr/bin/python3 scripts/path_a_bench.py cold --idle 1800` with the flags you passed.
5. On the way out, success or failure: `launchctl bootstrap gui/<uid>` of that same plist.

## PASS

The bench prints `PASS cold_probe`, `PASS cold_request` with `limit_s=90.000`, `PASS cold_truncated truncated=0`, `PASS cold_content short=0`, `PASS ask_mail`, `FAILS none`, `INVALIDS none`, and `OVERALL PASS`. The probe must not print `WEDGE`. The wrapper then prints:

```text
PASS cold elapsed_s=<seconds> idle=1800
restore bootstrap label=com.mailroom.qwen-watchdog
```

Bench exit 0 and wrapper exit 0 are the PASS. A probe wedge is bench exit 4 and `FAIL cold reason=bench_rc=4`. The bootstrap line is still printed.

## Restore

Bootstrap of `com.mailroom.qwen-watchdog` is the trap. It runs if the wrapper had already decided the watchdog was loaded, including when the bench exits non-zero or the watchdog did not actually boot out.

The wrapper does not kickstart the model server. If `GET /v1/models` is not 200 after the script returns:

```zsh
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.mailroom.qwen-watchdog.plist"
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1234/v1/models
# only if that code is not 200:
launchctl kickstart -k "gui/$(id -u)/com.mailroom.mlx-lm-server"
```

Do not restore with the chat session up script. That bounces retrieval on `:8743`.

If the watchdog was not loaded at the start, the wrapper exits 1 and does not bootstrap it. Do not use this script to install the watchdog.

## Time

About 35 minutes: 1800 seconds idle, then a probe of at most 60 seconds, then one request of at most 90 seconds.

## What not to touch

- `127.0.0.1:8743` and `com.mailroom.ask-mail-serve`. The wrapper never bootouts that label.
- `com.mailroom.mlx-lm-server`, except the manual kickstart above if the port is still down after the run.
- `ask_mail.py`. `--ask-mail` only hashes it, inside the bench.
- The `hf-qwen-stage` agent. Leave it disabled.
- Ollama. Do not start it while Qwen is up.
- The model pin in the chat session up script, and the watchdog quiet-gate behaviour. This AR boots the watchdog out; it does not edit it.
