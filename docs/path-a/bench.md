# Path A benchmark harness

`scripts/path_a_bench.py` scores the reliability-spec section 8 acceptance bars for the Path A local Qwen chat server, before and after fixes. It is an offline-capable client: unit tests run it against a stub, and on a machine it only talks to the chat server you point it at.

The harness sends HTTP requests and reads system stats. It does not restart anything, does not change configuration, and does not edit LaunchAgents, server scripts, or mail scripts. It never touches launchd. Apart from the HTTP load, the optional JSONL file you pass to `--out`, and an optional `mem` baseline snapshot, it is read-only. `soak --watchdog-log` only reads a log the operator already has.

Soak must run in the foreground on the host. A 4 h soak must not run as a background job inside a remote shell. Without `--continue-on-hang`, a hang ends the run. The harness does not try to recover the server.

Python 3.9, standard library only. On macOS, `/usr/bin/python3` is enough.

## Safety

- No watchdog of its own, and it does not touch launchd. `soak --watchdog-log` is read-only.
- No server restarts and no reconfiguration.
- No writes to mail, sqlite, LaunchAgents, or model config.
- `probe` POSTs one `/v1/chat/completions` with `max_tokens` 1 and GETs `/v1/models` once at start. It does not load a paste.
- `warm`, plain `cold`, and `soak` POST the paste and GET `/v1/models` once at start.
- `cold --idle` sleeps, then sends the max-tokens-1 probe, then one paste request.
- `mem` runs `sysctl vm.swapusage`, `vm_stat`, `pgrep -x ollama`, and a TCP connect to `127.0.0.1:11434`. On non-macOS, swap and `vm_stat` are `UNKNOWN` (the process exits 1; it does not crash). `--baseline-out` writes only the JSON snapshot you asked for.
- Each run hashes `ask_mail.py` at start and end (`--ask-mail`, default `$HOME/MailArchive/scripts/ask_mail.py`). A missing file is reported as `absent` and is not a failure. If the hash changes during the run, the run fails. The file is only read.

The chat body matches `scripts/qwen_paste_chat_post.py` with thinking left off: the same system prompt, `temperature` 0.2, `chat_template_kwargs.enable_thinking` false, and `/no_think` appended to the paste. If the first reply is reasoning-only, one retry is sent the same way that script retries, and `content_len` is the stripped assistant `content` (reasoning is not substituted for content). Wall time includes that retry. Warm, plain cold, and soak do not send the separate max-tokens-1 probe, so that clock is the paste completion itself. `probe`, and `cold --idle`, send that probe on its own: user message `/no_think`, `max_tokens` 1, `temperature` 0, thinking off, no paste and no retry.

## Usage

From a clone of this repo:

```zsh
/usr/bin/python3 scripts/path_a_bench.py probe \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-probe.jsonl"

/usr/bin/python3 scripts/path_a_bench.py warm \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-warm.jsonl"

/usr/bin/python3 scripts/path_a_bench.py warm \
  --fixtures-dir tests/fixtures/path_a/paste_4k \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-warm-honest.jsonl"

/usr/bin/python3 scripts/path_a_bench.py cold \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-cold.jsonl"

/usr/bin/python3 scripts/path_a_bench.py cold \
  --idle 1800 \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-cold.jsonl"

/usr/bin/python3 scripts/path_a_bench.py soak \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-soak.jsonl"

/usr/bin/python3 scripts/path_a_bench.py soak \
  --continue-on-hang \
  --mem-at 24 \
  --watchdog-log "$HOME/path-a-watchdog.log" \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-soak.jsonl"

/usr/bin/python3 scripts/path_a_bench.py mem \
  --baseline-out "$HOME/path-a-mem-baseline.json" \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py"

/usr/bin/python3 scripts/path_a_bench.py mem \
  --baseline "$HOME/path-a-mem-baseline.json" \
  --settle 120 \
  --ask-mail "$HOME/MailArchive/scripts/ask_mail.py" \
  --out "$HOME/path-a-mem.jsonl"
```

Common flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--base-url` | `http://127.0.0.1:1234` | Chat server origin. `/v1` is appended if missing. |
| `--model` | first id from `GET /v1/models` | Override the model id. |
| `--fixture` | `tests/fixtures/path_a/paste_4k_synthetic.txt` | One DATA+QUESTION paste. Omit both paste flags to use this default. |
| `--fixtures-dir` | (none) | Directory of `.txt` pastes for `warm`, `cold`, and `soak`. Sorted by filename. Request `i` (counting from 0) uses file `i mod n`. |
| `--max-tokens` | `512` | Completion cap (retry uses at least 768, same as the paste client). |
| `--timeout` | probe 60s, warm 60s, cold 120s, soak 300s | Per-request urllib timeout. `cold --idle` always probes with a 60s timeout; `--timeout` applies to the paste request. |
| `--out` | (none) | JSONL path. One object per line. |
| `--ask-mail` | `$HOME/MailArchive/scripts/ask_mail.py` | File to hash at start and end. |
| `-n` / `--n` | warm 10, soak 48 | Request count. |
| `--interval` | soak 300 | Seconds to wait between soak requests. |
| `--idle` | (omit) | `cold`: seconds to sleep before a probe and one paste request. A countdown line prints every 60 seconds. Omit it and cold stays a single paste request. |
| `--continue-on-hang` | off | `soak`: record a timeout as `hung` and keep going. Without it, a timeout stops the run (exit 4). |
| `--watchdog-log` | (none) | `soak`: read-only log. With `--continue-on-hang`, a hang is recovered only if the log has a `restart kickstart` line within 5 minutes. `ok latency=` lines do not count. |
| `--mem-at` | (none) | `soak`: after request K (1-based), sample swap and Ollama. |
| `--baseline-out` | (none) | `mem`: write a JSON snapshot of the measurement just taken. |
| `--baseline` | (none) | `mem`: snapshot taken before the model was loaded. |
| `--settle` | `0` | `mem`: seconds to wait after a clean baseline, then measure. A countdown line prints every 30 seconds. Requires `--baseline`. |

Passing `--fixture` and `--fixtures-dir` together is a config error (exit 2). A missing or unreadable `--baseline` file is also exit 2.

Plain `cold` is one request. With `--fixtures-dir` it uses the first sorted file. Idle time is the operator's job; the harness prints a note and does not check it. `cold --idle SECONDS` sleeps first (the operator must boot out any watchdog beforehand; the harness never touches launchd), then probes, then sends one paste request.

A bad fixture, a bad flag, or a chat server that is unreachable at start (request modes) exits 2. `mem` does not contact the chat server.

## Phase mapping

| Phase | Command |
| --- | --- |
| Phase A / B warm | `warm` (single fixture is warm cached) |
| Phase B clock gate | `warm --fixtures-dir tests/fixtures/path_a/paste_4k` |
| Memory gate | `mem --baseline-out` before load, then `mem --baseline FILE --settle SECONDS` |
| Phase D | `cold --idle SECONDS` (sleep, probe, then one full request) |
| Phase E | `soak --continue-on-hang --mem-at 24 --watchdog-log FILE` |

Run the Phase E soak in the foreground on the host. A 4 h soak must not run as a background job inside a remote shell.

Operator runbooks for the live host: `docs/path-a/ar7-drills.md` (Phase C), `docs/path-a/ar8-cold.md` (Phase D), `docs/path-a/ar9-soak.md` (Phase E). The harness flags those runbooks call are already this script: `cold --idle`, `soak --continue-on-hang`, and `soak --mem-at`.

## Modes and bars

### probe

One POST, `max_tokens` 1, timeout default 60 seconds. No paste.

PASS means HTTP 200 and a parseable choice within the timeout. Any `finish_reason` is accepted. Content length is not a bar.

FAIL means an HTTP error, an empty body, or a body that is not a choice. A timeout prints a `WEDGE` line with the local time and exits 4.

### warm

N sequential paste requests (default 10).

Single-fixture warm (`--fixture`, or the default paste) is **warm cached**: the same prompt every request, so a prompt-cache hit. Treat a fast median as falsifier-2 style evidence, not as a cold clock. The harness prints `NOTE warm_cached`.

`--fixtures-dir` warm is the **honest clock gate**. Requests rotate through the sorted `.txt` files in that directory (ten distinct pastes live in `tests/fixtures/path_a/paste_4k/`). Different prompts, so the wall clock is not a cache hit on one prefix. The harness prints `NOTE warm_honest`. `tests/fixtures/path_a/paste_4k_synthetic.txt` stays the single-fixture default and is not part of that directory.

Per request, PASS means all of:

- HTTP success
- `finish_reason` is `stop`
- stripped content length is greater than 300
- wall time is at most 45 seconds

Overall PASS also requires the median wall time to be at most 35 seconds, and the `ask_mail.py` hash to be unchanged (or `absent` both times).

### cold

One paste request. Leave the server idle for 30 minutes first; this command does not do that for you unless you pass `--idle`.

PASS means HTTP success, `finish_reason` `stop`, content length greater than 300, and wall time at most 90 seconds, plus an unchanged `ask_mail.py` hash.

`cold --idle SECONDS` sleeps `SECONDS` first (countdown every 60 seconds), then runs a probe with a fixed 60 second timeout, then one full paste request on the cold bar. Overall PASS needs the probe and the paste request. A probe timeout prints `WEDGE` and exits 4 without sending the paste. A probe that returns a bad body still sends the paste, and the run fails. Boot out any watchdog before this command. The harness never touches launchd.

### soak

N paste requests (default 48), waiting `--interval` seconds after each one except the last (default 300). Run it in the foreground on the host. A 4 h soak must not run as a background job inside a remote shell.

A request that exceeds `--timeout` (default 300 seconds) is recorded as `hung` with the local time. Without `--continue-on-hang`, a `WEDGE` line is printed and the run stops. Nothing is restarted. Exit code is 4.

With `--continue-on-hang`, the hung row is kept and the next scheduled request still runs. The harness does not restart anything. The `PASS soak` / `FAIL soak` line is timing and hangs only: `failures=` on that line does not count a reply whose only miss is `content_len` <= 300 (`CONTENT_MIN`). HTTP 200 and `finish_reason` `stop` inside the timeout is a timing pass even when the body is short. That short-content check is its own line, `PASS soak_content short=0` or `FAIL soak_content short=<n> limit=content_len<=300 idx=<n> content_len=<len>`. A short answer still fails the run (exit 1) through that line. It does not flip `PASS soak` or a hang's `next=pass`. Seen live: 34 s, HTTP 200, `finish=stop`, `content_len=202` is a content FAIL and a timing pass. Overall PASS on this flag means the timing line passed (zero timing failures, and every hang followed by a timing pass), the content line passed, and the other bars passed. If `--watchdog-log FILE` is set, that file must also contain a `restart kickstart` line whose timestamp is within 5 minutes after the hang. The timestamp is the watchdog `log()` form `[YYYY-MM-DD HH:MM:SS]` (square brackets) or a bare `YYYY-MM-DD HH:MM:SS` at the start of a line. An `ok latency=` line in that window does not count. The file is only read, at the end of the run. A missing file, no `restart kickstart` line in that window, or a stamp outside that window fails the run (exit 1). The summary lists each hang with its local time and the recovery evidence (the next timing verdict, and the `restart kickstart` timestamp, `none`, or `missing`). If the log flag is omitted, recovery is only the next request. If nothing hung, a missing log is not a failure.

`--mem-at K` takes one memory sample after request K finishes (1-based), including when that request was hung. The summary bar is mid-soak idle swap under 1024 MB. Ollama is reported on the same line (`down`, `up`, or `unknown`). Swap of 1024 MB or more, or an unknown swap, fails that bar. If the run never reaches K, the line is `FAIL soak_mem_at missing`. K less than 1 or greater than N is exit 2 before any request. A wedge (exit 4) stays exit 4 even when this bar fails.

PASS without those flags means zero hung requests and zero failures (same HTTP, finish, and content checks as warm, without the 45 second / median bars). The timeout is the hang line.

### mem

Read-only host check.

- `sysctl vm.swapusage`: PASS when used swap is under 1024 MB.
- `vm_stat`: free, active, and wired pages are printed as `INFO` and do not change the result.
- Ollama is down when `pgrep -x ollama` finds no process and nothing accepts a TCP connection on `127.0.0.1:11434`.

Without `--baseline`, PASS means swap is under 1 GB and Ollama is down, and the `ask_mail.py` hash did not change. On non-macOS, swap and `vm_stat` are `UNKNOWN` and the overall result is FAIL (exit 1) without a crash.

`--baseline-out FILE` writes a snapshot of the measurement just taken: `timestamp`, `swap_used_mb`, `vm_stat` (`free_pages`, `active_pages`, `wired_pages`), and `ollama_down` (true only when the process is down and port 11434 is closed). Take this **before** loading the model, while the machine is still clean.

`--baseline FILE --settle SECONDS` reads that snapshot, then waits `SECONDS` (a `settle remaining_s=` line every 30 seconds), then measures.

- **INVALID** (exit 5) if the baseline swap was already at least 1024 MB. The machine was dirty before model load; the harness says so explicitly and does not treat the later number as evidence. It returns before the settle sleep.
- **PASS** (exit 0) if the baseline was clean and the new measurement has swap under 1024 MB and Ollama down.
- **FAIL** (exit 1) otherwise (swap at or above 1024 MB, Ollama up, an unconfirmed check, or an `ask_mail.py` hash change).

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `pass` | The request met the bar for that mode. |
| `fail` | HTTP error, empty body, malformed JSON, bad response shape, `finish_reason` other than `stop`, content length 300 or less, warm/cold wall over the limit, or a non-timeout transport error. The run continues (except soak, which stops only on `hung`). |
| `hung` | The request exceeded its timeout. Soak without `--continue-on-hang`, `probe`, and the `cold --idle` probe stop the run (exit 4). Soak with `--continue-on-hang` records the row and continues. |

`error` on a JSONL row is null for `pass`. Otherwise it is a short reason such as `timeout`, `empty body`, `malformed json`, `bad response shape`, `HTTP 500`, `finish_reason=length`, or `content_len=300`. Response text is not stored.

`ask_mail` lines:

- `PASS ask_mail sha256=<hex>` unchanged file
- `PASS ask_mail sha256=absent` file missing at start and end
- `FAIL ask_mail sha256 changed start=... end=...` content changed during the run

## JSONL

Request rows (`kind` `request`) include: local ISO timestamp (`ts`), `mode`, `idx`, `fixture` (filename only), `wall_s`, `http_status`, `finish_reason`, `content_len`, `verdict`, `error`. `reasoning_len` is present when the message included a reasoning string. `prompt_tokens` and `completion_tokens` are present when the server returned `usage`. A `probe` row uses `fixture` `probe`. `cold --idle` writes that probe row and then the paste row (`role` `generate`).

The last line of a chat run is `kind` `summary` (counts, min/median/max wall, both ask-mail hashes, overall). `mem` writes one `kind` `mem` row.

## Exit codes

| Code | When |
| --- | --- |
| 0 | Overall PASS |
| 1 | Overall FAIL (a bar missed, or `mem` could not confirm the bars) |
| 2 | Usage or config error: bad flags, both paste flags, bad fixture, missing or unreadable baseline, chat server unreachable at start, JSONL or baseline path not writable |
| 4 | Wedge. A soak request hung and `--continue-on-hang` was not set, or a `probe` (including the probe inside `cold --idle`) timed out. The run stopped. |
| 5 | `mem --baseline` is INVALID: baseline swap was already >= 1024 MB (machine dirty before model load) |

The human summary ends with one line per bar (`PASS` or `FAIL` plus n, min/median/max wall, failures, and hung where those apply) and an `OVERALL` line.

## Offline check

```zsh
python3 -m unittest tests.path_a.test_path_a_bench
```

The tests start a local stub. They do not contact a real model.
