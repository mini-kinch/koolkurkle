# Path A benchmark harness

`scripts/path_a_bench.py` scores the reliability-spec section 8 acceptance bars for the Path A local Qwen chat server, before and after fixes. It is an offline-capable client: unit tests run it against a stub, and on a machine it only talks to the chat server you point it at.

The harness sends HTTP requests and reads system stats. It does not restart anything, does not change configuration, and does not edit LaunchAgents, server scripts, or mail scripts. Apart from the HTTP load and the optional JSONL file you pass to `--out`, it is read-only.

Soak must run in the foreground on the host. Do not start it as a background job inside a remote shell. A hang ends the run; the harness does not try to recover the server.

Python 3.9, standard library only. On macOS, `/usr/bin/python3` is enough.

## Safety

- No watchdog.
- No server restarts and no reconfiguration.
- No writes to mail, sqlite, LaunchAgents, or model config.
- `warm`, `cold`, and `soak` POST `/v1/chat/completions` and GET `/v1/models` once at start.
- `mem` runs `sysctl vm.swapusage`, `vm_stat`, `pgrep -x ollama`, and a TCP connect to `127.0.0.1:11434`. On non-macOS, swap and `vm_stat` are `UNKNOWN` (the process exits 1; it does not crash). `--baseline-out` writes only the JSON snapshot you asked for.
- Each run hashes `ask_mail.py` at start and end (`--ask-mail`, default `$HOME/MailArchive/scripts/ask_mail.py`). A missing file is reported as `absent` and is not a failure. If the hash changes during the run, the run fails. The file is only read.

The chat body matches `scripts/qwen_paste_chat_post.py` with thinking left off: the same system prompt, `temperature` 0.2, `chat_template_kwargs.enable_thinking` false, and `/no_think` appended to the paste. If the first reply is reasoning-only, one retry is sent the same way that script retries, and `content_len` is the stripped assistant `content` (reasoning is not substituted for content). Wall time includes that retry. The separate max-tokens-1 probe from that script is not sent, so the clock is the paste completion itself.

## Usage

From a clone of this repo:

```zsh
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

/usr/bin/python3 scripts/path_a_bench.py soak \
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
| `--timeout` | warm 60s, cold 120s, soak 300s | Per-request urllib timeout. |
| `--out` | (none) | JSONL path. One object per line. |
| `--ask-mail` | `$HOME/MailArchive/scripts/ask_mail.py` | File to hash at start and end. |
| `-n` / `--n` | warm 10, soak 48 | Request count. |
| `--interval` | soak 300 | Seconds to wait between soak requests. |
| `--baseline-out` | (none) | `mem`: write a JSON snapshot of the measurement just taken. |
| `--baseline` | (none) | `mem`: snapshot taken before the model was loaded. |
| `--settle` | `0` | `mem`: seconds to wait after a clean baseline, then measure. A countdown line prints every 30 seconds. Requires `--baseline`. |

Passing `--fixture` and `--fixtures-dir` together is a config error (exit 2). A missing or unreadable `--baseline` file is also exit 2.

`cold` is always one request. With `--fixtures-dir` it uses the first sorted file. Idle time is the operator's job; the harness prints a note and does not check it.

A bad fixture, a bad flag, or a chat server that is unreachable at start (request modes) exits 2. `mem` does not contact the chat server.

## Modes and bars

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

One paste request. Leave the server idle for 30 minutes first; this command does not do that for you.

PASS means HTTP success, `finish_reason` `stop`, content length greater than 300, and wall time at most 90 seconds, plus an unchanged `ask_mail.py` hash.

### soak

N paste requests (default 48), waiting `--interval` seconds after each one except the last (default 300). Run it in the foreground on the host.

A request that exceeds `--timeout` (default 300 seconds) is recorded as `hung` with the local time, a `WEDGE` line is printed, and the run stops. Nothing is restarted. Exit code is 4.

PASS means zero hung requests and zero failures (same HTTP, finish, and content checks as warm, without the 45 second / median bars). The timeout is the hang line.

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
| `hung` | Soak only. The request exceeded `--timeout`. The run stops. |

`error` on a JSONL row is null for `pass`. Otherwise it is a short reason such as `timeout`, `empty body`, `malformed json`, `bad response shape`, `HTTP 500`, `finish_reason=length`, or `content_len=300`. Response text is not stored.

`ask_mail` lines:

- `PASS ask_mail sha256=<hex>` unchanged file
- `PASS ask_mail sha256=absent` file missing at start and end
- `FAIL ask_mail sha256 changed start=... end=...` content changed during the run

## JSONL

Request rows (`kind` `request`) include: local ISO timestamp (`ts`), `mode`, `idx`, `fixture` (filename only), `wall_s`, `http_status`, `finish_reason`, `content_len`, `verdict`, `error`. `reasoning_len` is present when the message included a reasoning string. `prompt_tokens` and `completion_tokens` are present when the server returned `usage`.

The last line of a chat run is `kind` `summary` (counts, min/median/max wall, both ask-mail hashes, overall). `mem` writes one `kind` `mem` row.

## Exit codes

| Code | When |
| --- | --- |
| 0 | Overall PASS |
| 1 | Overall FAIL (a bar missed, or `mem` could not confirm the bars) |
| 2 | Usage or config error: bad flags, both paste flags, bad fixture, missing or unreadable baseline, chat server unreachable at start, JSONL or baseline path not writable |
| 4 | Soak wedge. A request hung. The run stopped. |
| 5 | `mem --baseline` is INVALID: baseline swap was already >= 1024 MB (machine dirty before model load) |

The human summary ends with one line per bar (`PASS` or `FAIL` plus n, min/median/max wall, failures, and hung where those apply) and an `OVERALL` line.

## Offline check

```zsh
python3 -m unittest tests.path_a.test_path_a_bench
```

The tests start a local stub. They do not contact a real model.
