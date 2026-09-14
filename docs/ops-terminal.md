# Human Terminal cards (Mac ops)

Checklist for Action-required cards a human pastes on a Mac. Keychain
names and Mini daily install:
[scripts/README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
Model/runtime gates (interface proof, neg smoke, fail-open-only):
[model-runtime-gates.md](model-runtime-gates.md). Rerank default is
CrossEncoder (fail-open if the optional extra is missing); Ollama
cannot score Qwen3-Reranker: [rerank.md](rerank.md).
ask_mail probe: [ask_mail.md](ask_mail.md). Generate process
(`mlx_lm.server` on `127.0.0.1:1234`, venv-mlx, not LM Studio):
[generate-mlx.md](generate-mlx.md). Retrieve default is
history; live modes are opt-in (`--lane` / `--after` / `--before` /
`--fts-only` filter history only; no `--live` flag). Mini-only slim:
[macos-slim/README.md](../macos-slim/README.md).
`embed_backfill` single-writer HARD DECK (read this **before** starting
a backfill), including the `--reembed-legacy` ops contract and the
host-kept foreground embed ops contract:
[embed-backfill.md](embed-backfill.md).
Tombstone / never-purge (never physically delete iCloud or server mail;
local tombstone only): [tombstone.md](tombstone.md).
MBP SoR vs Mini copy-only (MBP is the live Source of Record for `mailroom.sqlite`; Mini is copy-only; No Mini writers against SoR; PR-5 cutover still gated on rem-legacy EXIT 0): section below.
Auth/2FA mail never Junk or Trash (destination hygiene folder is Auth; fail closed for classify/rules): section below.
CoS HOLD Mac writers (CoS does not run Mac writer/recovery ops; CoS orders Developer, collects status, issues user ARs only; Developer owns Mac process ownership and installs): section below.
Discuss ≠ authorize (discussion and how questions are not authorization; implement only on do it / approved / implement or standing authorized process; in CoS Desk discussion/troubleshooting, do not act until explicit): section below.

These cards are chat/operator steps. They are not the writer-lock file
`~/MailArchive/ACTION_REQUIRED` (see
[pr0/with_writer_lock_DESIGN.md](pr0/with_writer_lock_DESIGN.md)).

## One card at a time

Work one Action-required card at a time. A single card may list several
steps for **one** machine.

Write the next action (a paste-ready command). Prefer **TO DO** over a
list of don'ts — a stuck human needs the next step.

When a human is stuck, first check whether the card's instructions
were wrong or aimed at the other machine. Fix the card, then re-issue
one Action-required.

## One machine per card

- One host per card. A loud **MBP** or **Mini** banner on the first line
  keeps the paste target obvious.
- Open a new card when switching machines. Keychain items and `$HOME`
  paths then stay on the host they belong to.

## SWITCH TO Mini/MBP before machine-specific Terminal AR

Before a machine-specific Terminal AR, check the last user input
source. Detect the host from a **Sent-from-machine** tag or a pasted
prompt **hostname** proof only. The operator cannot see the focused
Terminal window.

If last input was **Mini** and the task needs **MBP** (or vice versa),
call out **SWITCH TO MBP** or **SWITCH TO Mini** explicitly before or
with the AR. Fail closed: no host proof, no machine-specific card.

One machine per Terminal AR. Open a new card when switching hosts.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not SSH a live machine, open
MailArchive or live sqlite, read Keychain, or change rem-legacy.

## Verify tool exists before Terminal AR

Before a Terminal AR or a "run this tool" instruction, confirm the
binary or script exists on the named machine (**MBP** vs **Mini**).
Do not invent tool paths. Fail closed: no existence proof, no card.

Name the machines as MBP and Mini only. Never a login, home path, or
email. If a path class is needed, use placeholders only
(`/usr/bin/<tool>`, `$HOME` as generic, `/Users/<operator>/...`).

```zsh
# Mini — prove the named binary exists before the run card
command -v <tool>
```

```zsh
# MBP — prove the named binary exists before the run card
command -v <tool>
```

This gate is docs/tests only. It does not SSH a live machine, open
MailArchive or live sqlite, read Keychain, or change rem-legacy.

## IMAP live checks via curl imaps (never Python sockets)

Standing contract: IMAP live checks use `/usr/bin/curl imaps://`.
Never open a Python socket client to `imap.mail.me.com`.

Python `socket` / `imaplib` clients to `imap.mail.me.com` fail with
**Errno 9** (bad file descriptor). That is the failure mode.
Do not retry with another Python socket. Fail closed: no
`/usr/bin/curl imaps://`, no IMAP live check.

Name the machines as MBP and Mini only. Never a login, home path, email,
secret, credential, or app password on the card or in git.

```zsh
# Mini — prove Apple curl exists before an IMAP live-check card
test -x /usr/bin/curl
```

```zsh
# MBP — prove Apple curl exists before an IMAP live-check card
test -x /usr/bin/curl
```

This gate is docs/tests only. It does not run live IMAP, does not
connect to `imap.mail.me.com`, does not run curl against IMAP, does
not read Keychain, does not open MailArchive or live sqlite, and does
not change rem-legacy.

## One command per fence

Put each Terminal command in its own fenced code block. Chat copy
buttons paste the whole fence; one command per fence keeps a single
line on the clipboard. One command fence per copy button. Two
`security` (or other) lines in one fence become one paste.

```zsh
# Mini — example banner (first line of the card)
hostname
```

```zsh
# MBP — example banner (new card after switching machines)
hostname
```

## Terminal AR format

Standing format for Terminal Action-required cards. One machine per
card. Fail closed: if a card cannot follow this format, do not issue it.

- **One host banner.** The first line is a loud **MBP** or **Mini** banner so the paste target is obvious. Name machines MBP and Mini only.
- **One command fence per copy button.** Each fenced block is one command. Chat copy buttons paste the whole fence.
- **Title equals body.** The card title is the same text as the body paste (title=body).
- **No stacked interactive prompts in one paste.** Do not stack prompts that wait for input in one paste.
- **No multi-line paste that includes interactive read.** A paste that includes an interactive read must not be multi-line.

This gate is docs/tests only. It does not SSH a live machine, open
MailArchive or live sqlite, read Keychain, or change rem-legacy.

## MBP SoR vs Mini copy-only

Standing ops contract: **MBP** is the live Source of Record for `mailroom.sqlite`. **Mini** is copy-only until PR-5. No Mini writers against SoR. PR-5 cutover is still gated on rem-legacy **EXIT 0**.

- MBP holds the live SoR DB (`$HOME/MailArchive/mailroom.sqlite` as
  a path class).
- Mini writers use `mailroom-copy.sqlite` or
  `mailroom-daily-copy.sqlite` only. Unset or `mailroom.sqlite` is a
  hard refuse (`db_mode=refused`).
- Do not start a Mini writer against SoR. Do not promote Mini
  `mailroom.sqlite` while rem-legacy is live.

This gate is docs/tests only. It does not open MailArchive or live
sqlite, write embed/SoR data, read Keychain, SSH a live machine, or
change rem-legacy.

## Auth/2FA mail never Junk or Trash (Auth folder)

Standing classify/rules contract: auth/2FA mail must not be classified into Junk or Trash. The destination hygiene folder is Auth.

Fail closed: if classify or rules cannot place auth/2FA mail into Auth, do not classify it into Junk or Trash. Do not guess Junk. Do not fall through to Trash.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live classify, does not
open MailArchive or live sqlite, does not write embed/SoR data, does
not read Keychain, does not SSH a live machine, and does not change
rem-legacy.

## CoS HOLD Mac writers (Developer owns Mac ops)

Standing ops contract: CoS HOLD on specialist Mac writer ops. CoS does not run Mac writer/recovery ops. CoS orders Developer, collects status, issues user ARs only. Developer owns Mac process ownership and installs.

Fail closed: if a Mac writer, recovery, process, or install step would require CoS to run it, do not run it. Order Developer. Collect status. Issue a user AR only.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Discuss ≠ authorize (implement only on do it / approved)

Standing authorization contract: **Discuss ≠ authorize**. Discussion and how questions are not authorization. Implement only on do it / approved / implement or standing authorized process. An already-authorized standing process is authorization.

In CoS Desk discussion/troubleshooting, do not act until explicit.

Fail closed: if the request is discussion or a how question, do not implement. Do not treat discussion as authorization. Wait for do it / approved / implement, or an already-authorized standing process.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Mini daily (copy-only)

Preferred practice: the Mini daily job writes **only** a copy. Set
`MAILROOM_DB` to `$HOME/MailArchive/mailroom-copy.sqlite` or
`mailroom-daily-copy.sqlite`. Unset or `mailroom.sqlite` is a hard refuse
(`db_mode=refused`) until SoR cutover (PR-5). Why: Mini SoR may be empty
and rem embed may still hold the copy — a silent default would write the
wrong file. Daily children must use that same copy (`--db` and
`$MAILROOM_DB`, via `mailroom_copy_db.py` `bind_copy_db`) so
IMAP/classify/bills do not open the empty SoR stub. `bind_copy_db()`
reads `sys.argv[1:]` when `argv` is `None` — otherwise process `--db`
is ignored.

Keychain must unlock from **launchd** (`launchctl start com.mailroom.daily`).
Terminal-only `security` success is not enough. Substitute `__HOME__`,
bootstrap the same label, and do **not** bootout while rem embed is live —
use `mailroom-daily-copy.sqlite` then. Full cards:
[README.mailroom-daily.md](../scripts/README.mailroom-daily.md).

## embed_backfill — one writer per sqlite (HARD DECK)

Read [embed-backfill.md](embed-backfill.md) **before** starting
`embed_backfill.py`. Preferred practice:

1. **One writer per `.sqlite`.** `--lock` is a per-batch heartbeat. It
   does not make same-file 2-wide safe.
2. **Shard on separate files** (copy host `mailroom-copy.sqlite` vs
   SoR-named `mailroom.sqlite`), then `embed_merge_shards.py` after
   **both EXIT 0**. Pause the other writer for the merge window only.
3. **Same-file parallel char-bands are HARD DECK.** Sequential bands on
   one file, or parallel only on separate files.
4. **Do not merge-back a malformed working copy.** Set it aside, recopy
   from a known-good source, then start a writer only after
   `integrity_check` is `ok`.
5. **`--reembed-legacy` ops contract.** Requires `--quote-strip`.
   Default skip. Not the daily path. Never two writers on one sqlite.
6. **Host-kept foreground.** Long SoR embeds stay in a host Terminal.
   Do not `nohup` or `&`. Keep the host awake with `caffeinate -w <pid>`
   (placeholder, not a live PID). Rem-legacy is not the daily path.

```zsh
# SoR host — prevent sleep while the foreground embed PID is live
caffeinate -w <pid>
```

```zsh
# copy host — integrity before a new embed_backfill
sqlite3 "$HOME/MailArchive/mailroom-copy.sqlite" 'PRAGMA integrity_check;'
```

```zsh
# SoR host — integrity before a new embed_backfill
sqlite3 "$HOME/MailArchive/mailroom.sqlite" 'PRAGMA integrity_check;'
```

## Keychain create

Preferred service name: `mailroom.imap.app-password`. Keep `-w` last so
the secret is typed only at the interactive prompt.

```zsh
# Mini — create IMAP Keychain item (type the secret at the prompt)
security add-generic-password -a "$USER" -s mailroom.imap.app-password -w
```

```zsh
# MBP — create IMAP Keychain item (type the secret at the prompt)
security add-generic-password -a "$USER" -s mailroom.imap.app-password -w
```

Verify **length only**. Never print or paste the secret into chat or git.

```zsh
# Mini — Keychain length check (no secret on stdout)
security find-generic-password -s mailroom.imap.app-password -w | wc -c
```

```zsh
# MBP — Keychain length check (no secret on stdout)
security find-generic-password -s mailroom.imap.app-password -w | wc -c
```

Apple app-specific passwords are typically ~16–19 characters.
`security -w` may add a trailing newline, so `wc -c` can read one
higher. An ~8-character secret will not authenticate to IMAP
(Login denied); regenerate at appleid.apple.com.

Prefer the new name. Leave legacy `mailroom.icloud.app-password` in
place until IMAP smoke PASSes on the new name. The daily wrapper still
falls back to the legacy item when the default name is missing or empty.

## Privacy on GitHub

**ZERO personal info on GitHub** anywhere — code, tests, docs, PR
titles, PR bodies, comments, commits, and branch names. Docs and
tests may use **placeholder classes only**. Never a real email, real
login name, real home directory, or secret.

Forbidden classes (placeholders only in examples):

- personal email: `user@example.com` / `<operator>@example.com`
- home paths: `/Users/<operator>/...`, `~/...` as generic
- Keychain material: `<service>` / `<account>` (names only; never
  the secret)

Also fine as generics: `EXAMPLE_USER_LOCAL`, `example.invalid`,
`$HOME` / `__HOME__`, `USERNAME`.

This gate is docs/tests only. It does not read Keychain, open
MailArchive or live sqlite, or change rem-legacy.

## GitHub SoR / PR description

Build is **staging**. GitHub `main` on
[mini-kinch/koolkurkle](https://github.com/mini-kinch/koolkurkle) is the
source of record. Clone and remote URLs under
`9zjf9jpv7z-glitch/koolkurkle` are parked/historical — not live SoR.

### repositories() before CloudAgent (Connect ≠ ACL)

Cursor **Connect Done is not repo ACL Done** (Connect ≠ ACL). Connect
status does not mean a CloudAgent may launch against this tree.

Before any CloudAgent launch, confirm `repositories()` includes
`mini-kinch/koolkurkle`. If that listing omits this repo, do not
launch. Do not treat `9zjf9jpv7z-glitch/koolkurkle` (parked/historical)
as live SoR.

This gate is ops practice. It does not add CloudAgent tooling and does
not change rem-legacy, MailArchive, or sqlite writers.

To change a PR description, edit the first Conversation comment
(⋯ → Edit). The title pencil edits the title only.

After a Build merge, fold new preferred practices into the existing
topical docs in the same cycle (generate / ask_mail / ops-terminal) —
not a standalone lessons dump.

After a PR merges to the wrong base, open compare `main...branch` and
merge that PR so `main` receives the commits. Once a PR number exists,
only **Merge** remains — do not re-instruct Create.

## Generate process (mlx_lm.server, not LM Studio)

Standing generate process is **`mlx_lm.server`** on `127.0.0.1:1234`
(`POST /v1/chat/completions`). Canonical python is **venv-mlx**
(`~/MailArchive/venv-mlx/bin/python` — placeholder path only).
This is not LM Studio. Do not open LM Studio.app; do not
`lms server start`. Ollama is embed-only.

ask_mail UI pointer (when `--serve` is already up):
`http://127.0.0.1:8743/ui`. No secrets.

Recipes and generate-down: [generate-mlx.md](generate-mlx.md).
ask_mail probe + UI: [ask_mail.md](ask_mail.md).

This gate is docs/tests only. It does not start generate, open
MailArchive or live sqlite, write embed/SoR data, read Keychain,
SSH a live machine, change rem-legacy, or hit `:1234` or `:8743`.

## Early-error traps (model / runtime)

Before Ready or merge on generate or rerank, run the gates in
[model-runtime-gates.md](model-runtime-gates.md). CoS withholds merge
AR without interface-proof PASS **or** an explicit **fail-open-only**
label.

1. **Interface proof** — `curl` or `ask_mail.py --probe` against the
   official path (`mlx_lm.server` `/v1/chat/completions` on
   `127.0.0.1:1234`, locked model id). Path string `llmster-headless`
   is not the process. Do not open LM Studio.app; do not
   `lms server start`. Ollama is embed-only. Legacy JSON enum
   `generate_mode=lm_studio` still means OpenAI-compatible `:1234`
   success (do not rename). Live generate PASS is an official paste
   (human Terminal paste or explicit accept of CoS JSON). Merge may
   be labeled **fail-open-only** when live is not re-run.
2. **Negative smoke** — garbage / stopped / wrong model / port closed /
   unreachable must fail or labeled-fail-open.
3. **Official path named** — community GGUF
   (`dengcao/Qwen3-Reranker-0.6B:Q8_0` or `:F16`; no untagged `latest`)
   is insufficient without trap 1 PASS. Ollama generate/chat is the
   wrong rerank interface.
4. **fail-open-only must be labeled** — `generate_mode` + `rerank_mode`
   on every ask_mail response.
5. **CoS withholds merge AR** without PASS or that label.

```zsh
# MBP — mlx_lm.server interface proof (locked model id)
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/ask_mail.py --probe
```

Do **not** paste `ollama pull` / `ollama cp` of the community reranker
as a working-scorer card. GGUF present ≠ scores. Rerank default is
in-process CrossEncoder; missing extra/weights fail-open
([rerank.md](rerank.md)). Ollama cannot score Qwen3-Reranker.

## Ollama embed + Little Snitch

Official **embed** pull is `qwen3-embedding:8b` (not a reranker). If
`ollama pull` fails with `dial tcp … connect: bad file descriptor`
while `curl` / `nc` to `registry.ollama.ai:443` succeed, allow
Ollama.app / `ollama` outbound to `registry.ollama.ai:443` in
Little Snitch, then retry the embed pull.

```zsh
# Mini — official embed pull (not a reranker)
ollama pull qwen3-embedding:8b
```

```zsh
# MBP — official embed pull (not a reranker)
ollama pull qwen3-embedding:8b
```

## curl≠gh dial bad-file-descriptor / app filter

Standing ops contract: `curl` to `api.github.com` can return **200** while Homebrew `gh` fails with `dial tcp … connect: bad file descriptor`. That is not a token reject and not basic network down — treat as app-level filter (e.g. Little Snitch) on `/opt/homebrew/bin/gh`.

Diagnostic ladder:

1. **curl 200 + gh dial bad-file-descriptor** — do not re-auth blindly.
2. Check app filter / Allow for `gh` (Little Snitch outbound for
   `/opt/homebrew/bin/gh` to `api.github.com`).
3. Unauthenticated `gh api rate_limit` isolates binary network vs token.

This gate is docs/tests only. It does not run `gh auth`, open
MailArchive or live sqlite, write embed/SoR data, read Keychain,
SSH a live machine, or change rem-legacy.

## ask_mail sequential smoke (do not pin embed + rerank + chat)

Retrieve+rerank may keep Ollama embed `qwen3-embedding:8b` resident
with the in-process CrossEncoder. Then **unload** both before generate
(`mlx_lm.server` / `$MAILROOM_GENERATE_MODEL`). Do not co-pin embed +
CrossEncoder + `mlx_lm.server` 35B-class chat. Do not open
LM Studio.app; do not `lms server start`. Ollama is embed-only.
Recipes: [ask_mail.md](ask_mail.md).

```zsh
# MBP — ask_mail phase 1 retrieve + rerank (embed resident)
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/ask_mail.py --phase retrieve --json 'SDGE bill'
```

```zsh
# MBP — unload embed before mlx_lm.server generate
ollama stop qwen3-embedding:8b
```

```zsh
# Mini — unload embed before mlx_lm.server generate
ollama stop qwen3-embedding:8b
```

## Heavy packets

Canonical on the Mac Desktop: `$HOME/Desktop/Heavy-Bot/to-bot`.
Before a box / cloud agent reads a packet, sync that directory into
`/workspace`. A Desktop file that was never synced is not visible to
the box. Do not `git add` packet contents.

Handoffs to a human use a named chat attachment with a download link.
A Desktop path alone is not delivery.

```zsh
# MBP — canonical Heavy packets (Desktop)
ls "$HOME/Desktop/Heavy-Bot/to-bot"
```

```zsh
# MBP — sync Heavy packets into /workspace before box read
rsync -a "$HOME/Desktop/Heavy-Bot/to-bot/" /workspace/
```
