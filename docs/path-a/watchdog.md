# Path A mlx-lm-server watchdog (build only)

One pass per run of `scripts/qwen-mlx-watchdog.sh`. A LaunchAgent `StartInterval` of 300 seconds is the driver. The script does not stay running, does not load anything, and does not talk to any address other than `WD_HOST:WD_PORT` (default `127.0.0.1:1234`).

This repository change does not install or start the agent. Nothing here is run against a live server.

## What it does

On each pass, in order:

1. Take a single-instance lock (`mkdir` on `$WD_STATE_DIR/lock`). A lock older than 900 seconds is removed and logged (`stale lock removed`). If the lock is held, the pass logs `lock busy, skip` and exits 0.
2. If the HOLD file exists, log `hold` and exit 0. No probe and no kickstart.
3. If `launchctl print gui/<uid>/com.mailroom.mlx-lm-server` fails, the agent is not loaded (for example after `qwen-chat-down.sh`). Log `agent not loaded, skip`, reset the consecutive-fail counter, and exit 0. The watchdog never bootstraps or loads an agent.
4. If this watchdog's own last restart is still inside `WD_POST_RESTART_GRACE` (default 600 seconds), log `grace` and exit 0. The 27B weights need time to page in (about 7–40 seconds when cold; decode is about 7.5 tok/s).
5. Before any watchdog HTTP call, sample the mtime of `WD_SERVER_LOG` (`m_before`). The log contents are never read. Two state files track activity:
   - `$WD_STATE_DIR/self_log_mtime` is the log mtime immediately after this watchdog's own last request.
   - `$WD_STATE_DIR/last_activity` is the epoch of the last external activity.
   If the log exists and `self_log_mtime` does not yet, set `last_activity` to `m_before` (conservative: the first sight of the log counts as activity). If the log exists and `m_before` differs from `self_log_mtime`, someone else wrote after our last request, so set `last_activity` to `m_before`. Our own lines never advance `last_activity`.
6. `GET /v1/models` with `WD_MODELS_TIMEOUT` (default 10 seconds). A non-200 counts as a fail with no quiet gate, including when the server log is recent. After the GET, including on this failure path, record the current log mtime into `self_log_mtime`.
7. If `/v1/models` is HTTP 200 and the server is not quiet: log `ok models-only (active <N>s ago)`, reset the consecutive-fail counter, and exit 0. No chat probe. `N` is `now - last_activity`. Quiet means the log is missing, or `now - last_activity >= WD_QUIET_SECS`.
8. Otherwise: `POST /v1/chat/completions` with a fixed `ping` message, `max_tokens` 1, `temperature` 0, `stream` false, and `WD_PROBE_TIMEOUT` (default 240 seconds). 240 seconds covers a user request already queued ahead of the probe plus a cold page-in. Success is HTTP 200 and a `choices` array. This is never a full generation. On success, reset the consecutive-fail counter and log `ok` with latency. After the chat probe, including on failure, record the current log mtime into `self_log_mtime` again.
9. On failure: increment the consecutive-fail counter. Restart only when it reaches `WD_FAILS_BEFORE_RESTART` (default 2).

A restart is foreground, in that same pass (no background child). The consecutive-fail counter is reset after the kickstart, so the next streak starts only once the grace window has passed. The only restart command is:

```sh
launchctl kickstart -k gui/<uid>/com.mailroom.mlx-lm-server
```

That is the only restart command. It relaunches the already-loaded plist. It does not call `qwen-chat-up.sh`. It does not touch `com.mailroom.ask-mail-serve` or any other label. It does not bootout, bootstrap, load, or unload. It does not kill by port. It does not write a plist.

A watchdog restart does not bounce retrieval (`:8743`). Only the `com.mailroom.mlx-lm-server` label is kickstarted.

## Pinned 27B snapshot

`WD_MODEL_PATH` defaults to:

`$HOME/.cache/huggingface/hub/models--mlx-community--Qwen3.8-27B-4bit/snapshots/10c35caafbb80f7dc6a7a432cdd11af10a6d4818`

The watchdog never selects a model with a `models--*Qwen* | head -1` glob and never lists the Hugging Face hub directory. Manual start in `qwen-chat-up.sh` pins that same snapshot. See [model-pin.md](model-pin.md).

Before any restart the pass preflights:

1. `WD_MODEL_PATH` is a directory that contains at least one resolvable `*.safetensors`. Hugging Face snapshot names are symlinks into `blobs/`; the check follows links (`find -L`) and counts a file only when the target exists. A dangling symlink does not count. The model repo directory (the `models--…` parent of `snapshots/<rev>`) must also contain no `*.incomplete`.
2. The loaded agent's `arguments = { … }` block from `launchctl print` has `--model` immediately followed by exactly `WD_MODEL_PATH`.

If the file check fails, the pass does not kickstart. It writes HOLD with the reason `pin: model path missing` and logs that line. If the loaded `--model` is anything else, it does not kickstart. It writes HOLD with `pin: loaded agent model != pinned path` and logs that line. Kickstart only happens after both checks pass, so the plist that relaunches is the one whose `--model` was just verified equal to the pin.

## Loop guard

Restart epochs are appended to the state directory. If `WD_MAX_RESTARTS` (default 3) restarts already happened inside `WD_RESTART_WINDOW` (default 3600 seconds), the pass does not restart. It writes HOLD with the reason line `loop guard: HOLD written` and logs that same line.

## HOLD

| How HOLD appears | What it means |
| --- | --- |
| Operator creates `$HOME/qwen-mlx/HOLD` | Pause. The next pass logs `hold` and exits 0. |
| `qwen-chat-up.sh` | A deliberate up removes HOLD near the start and logs that. |
| Loop guard | Writes HOLD with `loop guard: HOLD written` and does not restart. |
| Pin preflight | Writes HOLD with `pin: model path missing` or `pin: loaded agent model != pinned path` and does not restart. |

Delete the HOLD file to resume. The watchdog does not delete HOLD itself.

## Config

| Variable | Default |
| --- | --- |
| `WD_HOST` | `127.0.0.1` |
| `WD_PORT` | `1234` |
| `WD_LABEL` | `com.mailroom.mlx-lm-server` |
| `WD_HOLD` | `$HOME/qwen-mlx/HOLD` |
| `WD_STATE_DIR` | `$HOME/qwen-mlx/watchdog` |
| `WD_LOG` | `$HOME/MailArchive/logs/qwen-watchdog.log` |
| `WD_SERVER_LOG` | `$HOME/MailArchive/logs/mlx_lm_server_1234.log` |
| `WD_QUIET_SECS` | `900` |
| `WD_MODELS_TIMEOUT` | `10` |
| `WD_PROBE_TIMEOUT` | `240` |
| `WD_FAILS_BEFORE_RESTART` | `2` |
| `WD_MAX_RESTARTS` | `3` |
| `WD_RESTART_WINDOW` | `3600` |
| `WD_POST_RESTART_GRACE` | `600` |
| `WD_LAUNCHCTL` | `launchctl` |
| `WD_CURL` | `curl` |
| `WD_MODEL_PATH` | `$HOME/.cache/huggingface/hub/models--mlx-community--Qwen3.8-27B-4bit/snapshots/10c35caafbb80f7dc6a7a432cdd11af10a6d4818` |

Log lines are append-only text: timestamp, then the event.

## Prompt cache on the session script

`scripts/qwen-chat-up.sh` passes `--prompt-cache-size` `1` in the `mlx_lm.server` plist it writes (two program arguments, after the existing `--chat-template-args` `{"enable_thinking":false}` pair). The default of 10 distinct KV caches is the wrong size for this 27B 4-bit model. Model selection in that script is the pinned snapshot, and the thinking-off pair stays.

## Prompt cache interaction

`--prompt-cache-size 1` keeps one prompt-cache entry. A 1-token chat probe every 5 minutes would replace the user's cached ~4k-token paste prompt, which defeats that setting. The chat probe runs only when the server has been quiet.

The watchdog uses the mtime of `WD_SERVER_LOG` and never reads or parses the log. `mlx_lm.server` appends an access-log line for every request, including this watchdog's own `GET /v1/models` and its own chat POST, and it writes the POST line when the request starts (nothing is written during decode). Sampling mtime after the GET would always look fresh (`active 0s ago`), and because every pass writes a GET line the log would never stay quiet for `WD_QUIET_SECS`, so a chat hang would never be probed. The pass therefore samples `m_before` before its own requests and stores the mtime after them in `self_log_mtime`. Only an mtime that is not our own line updates `last_activity`. A wedged generation does not add an external line, so after `WD_QUIET_SECS` the chat probe fires and detection proceeds.

Worst-case detection is the quiet window + up to 1 pass + probe timeout + 1 pass + probe timeout: `WD_QUIET_SECS` (900s), then up to one StartInterval (300s) until the first probe, plus `WD_PROBE_TIMEOUT` (240s), then up to one more StartInterval (300s) and a second `WD_PROBE_TIMEOUT` (240s), about 1980 seconds.

## Install (separate user approval)

This checkout does not install or load anything. There is no installer.

Files copied, only when an operator chooses to install: `scripts/qwen-chat-up.sh`, `scripts/qwen-mlx-watchdog.sh`, and the rendered plist into `~/Library/LaunchAgents` (substitute `__HOME__` in `launchd/com.mailroom.qwen-watchdog.plist.template` with `$HOME`).

Whether a restart happens: the cache flag only takes effect the next time qwen-chat-up.sh is run, which restarts mlx_lm.server and bounces retrieval; the watchdog plist does nothing until someone runs launchctl bootstrap on it.

Whether the live mlx-lm-server plist is touched: only when up.sh next runs, since up.sh regenerates it.

## Rollback

1. Restore the backed-up `qwen-chat-up.sh`.
2. Bootout and delete the watchdog plist from `~/Library/LaunchAgents`.
3. Delete the watchdog script.
4. `rm` the HOLD file and the watchdog state directory (`$HOME/qwen-mlx/HOLD` and `$HOME/qwen-mlx/watchdog`).
