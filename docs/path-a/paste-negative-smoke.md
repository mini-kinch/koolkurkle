# Path A paste negative smoke

Offline stdlib `unittest` for `scripts/ask_mail_paste.sh` and
`scripts/ask_mail_paste_fmt.py`. No network, no mail database, no model
server. Synthetic data only (`sender@example.com`). The harness sets
`HOME` to a temporary directory. The scripts themselves are not modified.

## Run

```
python3 -m unittest tests.path_a.test_paste_negative -v
```

Shell cases call `zsh` on `scripts/ask_mail_paste.sh`. If `zsh` is not
installed those cases `skipTest` with `zsh is not installed`. Formatter
cases still run. Python 3.9 and 3.12 are both in range (no 3.10-only
syntax).

The shell harness is a temporary `MAILARCHIVE`:

- stub `.venv/bin/python` that execs the test interpreter
- stub `scripts/ask_mail.py` that records argv and prints JSON
- the real `scripts/ask_mail_paste_fmt.py` copied in
- stub `sqlite3` on `PATH` that simulates `.backup` by creating the snap
  file (and `-journal` / `-wal` / `-shm`) and recording the path

`ASK_MAIL_PASTE_K` is left unset. The child environment is built fresh
so a parent value cannot leak in.

## What each case guards

1. **No query args** (`test_no_query_args_exits_2_with_usage`). Exit 2.
   Stderr is `usage: ask_mail_paste.sh <query words...>`. Stdout is
   empty. sqlite and `ask_mail.py` are not started, and no snap file is
   left under `TMPDIR`.

2. **Missing pieces** (`test_missing_python_exits_2`,
   `test_missing_ask_mail_exits_2`, `test_missing_fmt_exits_2`,
   `test_missing_sor_exits_2`). Each exits 2 with stderr
   `error: missing <path>` naming that file, before `.backup`. The
   checks are the venv python (executable), `scripts/ask_mail.py`,
   `scripts/ask_mail_paste_fmt.py`, and the SoR file
   (`$MAILARCHIVE/mailroom.sqlite` when `MAILROOM_DB` is unset).

3. **Snapshot removed on exit** (`test_snapshot_removed_on_success`,
   `test_snapshot_removed_on_ask_failure`). The stub creates
   `$TMPDIR/mailroom-paste-<pid>.sqlite` plus the three sidecars. The
   script's EXIT trap removes all four. Success is exit 0 and the real
   formatter's DATA + QUESTION block. Failure is the ask stub exiting 1
   after it has written JSON (pipeline status 1); the snap is still
   gone. The recorded sqlite invocation is
   `.backup '<snap>'` of the SoR path.

4. **Formatter edges** (direct `ask_mail_paste_fmt.py`, not the shell).
   - Zero hits (`{}`, `{"hits":[]}`, `{"hits":null}`): exit 0, empty
     stderr, `DATA:` / `(no hits)` / `QUESTION:` / the query.
   - Odd hit fields: a hit with no subject and no date prints those
     lines blank. A non-ASCII subject is kept. A snippet longer than
     220 characters is capped at 220 after scrub. A very long `body`
     is not read and does not appear. Exit 0, no traceback.
   - Empty stdin and malformed JSON: see expectedFailure below.

5. **Default k and flags**
   (`test_default_k_is_20_and_flags_always_passed`, also checked on the
   success and ask-failure runs). With `ASK_MAIL_PASTE_K` unset the
   stub argv is:

   `ask_mail.py --db <snap> --fts-only --no-generate --no-rerank --json --k 20 -- <query>`

   `--fts-only`, `--no-generate`, and `--no-rerank` are present on
   every run that reaches the stub. The query is one argument after
   `--`.

## expectedFailure

`ask_mail_paste_fmt.py` does not catch `json.load` errors. These tests
assert the contract (no traceback; either a DATA + QUESTION block or a
clear nonzero error on stderr) and are marked `expectedFailure`
because the script currently crashes. This fixture does not change the
script. Drop the decorator when the contract holds (unittest will
report an unexpected success until then).

- `test_fmt_empty_stdin_no_traceback` — empty stdin raises
  `json.JSONDecodeError`. Stderr is a traceback. Exit status is 1.
  Stdout is empty.
- `test_fmt_malformed_json_no_traceback` — a truncated object
  (`{"hits":`) does the same.

Odd fields, zero hits, and the shell cases are not expected failures.
