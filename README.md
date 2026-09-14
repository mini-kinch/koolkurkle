# koolkurkle

ask_mail with citations is the product; vectors/FTS/IMAP are infrastructure.

iCloud mail retrieve scripts.

## Mini daily RAG

LaunchAgent `com.mailroom.daily` on **mac-mini.local** (set your macOS login) runs the
local IMAP → FTS → classify/bills → incremental embed chain. No Grok Bot at
runtime. **Copy-only until PR-5:** set `MAILROOM_DB` to
`mailroom-copy.sqlite` or `mailroom-daily-copy.sqlite`. Unset /
`mailroom.sqlite` is refused (hard-fail). Daily children get `--db` and
`$MAILROOM_DB` and honor them through `bind_copy_db()` (`argv=None` means
`sys.argv[1:]`) so they open that copy, not Mini's empty SoR stub. One
writer, no SMB/NFS dual-write.

Install, Keychain **name** (`mailroom.imap.app-password`), launchd Keychain
proof, Mini copy-only notes (`$HOME` only), and phase watermarks:
**[scripts/README.mailroom-daily.md](scripts/README.mailroom-daily.md)**.
Human Terminal cards, Terminal AR format (one machine, loud MBP or Mini
banner, one command per copy button, title equals body; no stacked
interactive prompts in one paste; no multi-line paste that includes
interactive read), Keychain create, ZERO personal info on GitHub
(placeholder classes only), GitHub SoR
(`mini-kinch/koolkurkle`; Build is staging), Connect ≠ ACL
(`repositories()` before CloudAgent), verify-tool-exists before
Terminal AR (named machine MBP vs Mini; do not invent tool paths),
SWITCH TO Mini/MBP before a machine-specific Terminal AR
(Sent-from-machine or hostname proof only; one machine per AR;
detect machine only from prompt hostname / Sent-from-machine / pasted proof;
loud SWITCH TO MBP/Mini callout when last input mismatches the target;
agents cannot see which Terminal window is focused),
MBP SoR vs Mini copy-only (MBP is the live Source of Record for `mailroom.sqlite`; Mini is copy-only; no Mini writers against SoR; PR-5 cutover still gated on rem-legacy EXIT 0),
curl≠gh dial bad-file-descriptor (curl 200 + Homebrew gh `dial tcp … connect: bad file descriptor` is app-level filter on `/opt/homebrew/bin/gh`; do not re-auth blindly; unauthenticated `gh api rate_limit` isolates binary network vs token),
generate process (`mlx_lm.server` on `127.0.0.1:1234`; canonical python venv-mlx at `~/MailArchive/venv-mlx/bin/python`; not LM Studio; ask_mail UI `http://127.0.0.1:8743/ui`),
IMAP live checks via `/usr/bin/curl imaps://` (never Python sockets
to imap.mail.me.com; Errno 9),
Auth/2FA mail never Junk or Trash (destination hygiene folder is Auth;
fail closed for classify/rules),
CoS HOLD Mac writers (CoS does not run Mac writer/recovery ops; CoS
orders Developer, collects status, issues user ARs only; Developer
owns Mac process ownership and installs),
Discuss ≠ authorize (discussion and how questions are not authorization;
implement only on do it / approved / implement or standing authorized process;
in CoS Desk discussion/troubleshooting, do not act until explicit),
After Action required: zero chatter until Done (after an Action required, silence until Done/Blocked/explicit reply; exceptions only STOP / hello / wake-up; do not stack chatter or routine status on an open AR),
No Terminal AR for facts Shell can read (do not issue Terminal AR for facts agent Shell can read; Terminal AR only for GUI / Little Snitch / sudo / secrets in a real Terminal),
Embed model (local Qwen3-Embedding-8B via Ollama; not cloud embed),
Agent Shell non-interactive (no read/getpass in agent scripts; security -w last or it stores empty; secrets only in real Terminal; report wc -c only),
Status/handoff reports include ETA until next Action required (honest range only; no false LOCKED ETAs; no undeliverable certainty slogans),
Warn before local-exec that may trigger macOS Allow sheets (warn the operator before any local-exec / Shell / machine action that may trigger macOS permission Allow sheets; Documents/Desktop/Downloads, screen recording, microphone, camera; do not invent click-paths),
No stacked ARs (never stack Action required / card-like prompts in one turn; exception only when the user explicitly asks for another AR during an active host-kept foreground job; one machine, one command, loud banner still applies),
CoS Desk default theater (CoS Desk is the default theater for factory Merge ARs and status that needs user action; do not post Merge ARs to CoS private 1:1 unless the user asks for privacy; one thing at a time — no dual-window / stacked AR),
Watch proof (quote last sample or say not watching; do not invent progress; do not claim LOCKED monitor; do not restart watched jobs from status reports),
Factory docs batches (one combined PR per batch or stacked branches; forbid parallel PRs that all edit the same shared docs files, e.g. docs/ops-terminal.md + README),
Continuous keepgoing (after Done on an authorized chain, immediately issue the next AR/task; forbid soft pause fillers like "next judgment when you want"),
After user PASS on a check (ack PASS and proceed to the next AR; do not re-issue the same check),
IMAP tombstone never STORE Deleted / EXPUNGE (local present_on_server only; refuse IMAP STORE \Deleted, EXPUNGE, Trash-purge),
with_writer_lock sole-writer wrapper (busy/lock refuse before second writer; shipping this guard is not starting rem-legacy),
mailroom_copy_db rem-gated copy (Mini copy only when rem-legacy is not writing or after EXIT 0; no SMB/NFS dual-write),
bind_copy_db / daily children honor MAILROOM_DB (argv=None reads sys.argv[1:]; children open the copy; refuse SoR stub),
PR-5 cutover checklist (docs only — do not enable; gated on rem-legacy EXIT 0 + Mini SoR switch steps; this change does not enable cutover or RunAtLoad),
sor_health_pack read-only / Mini-copy OK (read-only health; Mini on a copy DB is OK and is not a second writer),
Homebrew curl Little Snitch allow (Apple /usr/bin/curl Little Snitch allow does not cover Homebrew curl; BODY.PEEK `/opt/homebrew/opt/curl/bin/curl` needs its own Little Snitch allow; no live IMAP),
Post-rem embed batch bump (AFTER EXIT 0 only; first bump **32**, then 64 if stable; not 256 first; forbid mid-job bump; commit-per-batch; same qwen3-embedding:8b / 1024-d / instruction prefix),
Mini MLX embedder path (design; holdout of N frozen message_ids + cosine-agreement threshold required before cutover; fail-closed if miss; Mini RAM law: no co-reside 8B embed + 35B generate),
Compute sidecar one-writer apply (design+contract tests; never pointed at live rem SoR; missing-only INSERT; hash mismatch skip unless --reembed human go),
Embed generation key (model_tag + embed_runtime + native_dim + store_dim + instruction_prefix; 4096 native / 1024 store; refuse embedding_meta mismatch),
Mini retrieve db_mode=copy + copy_age (never imply live/SoR),
Fetch/auth error ≠ tombstone (empty fetch ≠ gone; UID+UIDVALIDITY persistence),
PR-5 integrity + rollback (docs only — do not enable; integrity pack, copy freshness, quote-strip generation match, one cutover + one rollback, RunAtLoad separate GO),
mlx_lm.server smoke codes + Mini RAM law HARD DECK (bind 1234/8743 to 127.0.0.1; no co-reside 8B embed + 35B generate),
Thread expansion cap as injection control (root + last 3, cap 8),
Rem-window freeze (sor_increment=frozen until EXIT; do not switch to interleave; with_writer_lock is process-lifetime for rem),
Post-EXIT catch-up BEFORE PR-5 (docs only; IMAP+bodies-FTS under lock, Mini←SoR copy, integrity pack, then clear frozen; EXIT 0 + human go; rem EXIT 0 handling out of scope),
Ready handoff (PASS or explicit fail-open-only; interface proof + negative smoke; Docs PR Ready ≠ permission to enable batch bump / Mini MLX / sidecar against live rem; Ready ≠ ATT implement permission),
ATT-0 attachment lane (DESIGN ONLY; schema/generation/skip; auth hard-gate `lane=auth`; history vs live tombstoned attach hits; no live SoR catalog/apply while rem; IMAP brew-curl / `\Seen` rails; never-purge `attachment_*` + disk caps; ATT-1..8 FUTURE; Ready ≠ ATT implement; [docs/att0-constraints.md](docs/att0-constraints.md)),
and Little Snitch:
**[docs/ops-terminal.md](docs/ops-terminal.md)**.

New/daily embed uses `--quote-strip` (MAILROOM §6.1 header-prefixed cleaned
body). Live rem LaunchAgents keep the old text path until EXIT — do not
restart rem for daily or change rem flags. Mini bodies-fts prefers
Homebrew curl ≥ 8.17 (`/opt/homebrew/opt/curl/bin/curl`); Apple
`/usr/bin/curl` is fail-closed for BODY.PEEK. **HARD DECK:** one
`embed_backfill` writer per `.sqlite` (`--lock` is per-batch, not
same-file 2-wide). Read the `--reembed-legacy` ops contract in
**[docs/embed-backfill.md](docs/embed-backfill.md)** before starting a
backfill. `--embed-live-only` is flag+docs only (future daily
incremental; must not delete existing tombstone embeds; shipping the
flag ≠ starting a job; guard ≠ run against rem-legacy). Long SoR embeds stay host-kept foreground (host Terminal +
`caffeinate -w <pid>`). Do not `nohup &`. Rem-legacy is not the Mini
daily path. Tombstone / never-purge: never physically
delete iCloud or server mail; local tombstone only.
**[docs/tombstone.md](docs/tombstone.md)**.

## Hybrid retrieve (MAILROOM §6.2 / PR-6 + PR-7) + ask_mail (PR-8)

`retrieve(query, k=20, lane=None, after=None, before=None)` in
`scripts/semantic_search.py` fuses FTS5 BM25 + sqlite-vec KNN with RRF.
Rerank default is in-process **CrossEncoder**
(`Qwen/Qwen3-Reranker-0.6B`, optional `requirements-rerank.txt`).
Live floats set `Hit.rerank` and `rerank_mode=crossencoder`. Missing
torch/weights or predict failure **fail-opens** (`rerank=None`, RRF,
`rerank_mode=fail_open`). `--no-rerank` forces `rerank_mode=none`.
Ollama generate/chat **cannot** score Qwen3-Reranker. Practice + traps:
**[docs/rerank.md](docs/rerank.md)**,
**[docs/model-runtime-gates.md](docs/model-runtime-gates.md)**.

Retrieve default is **history** (Q1 **DECIDED**; local SoR); live
modes are opt-in. `--live` is an additive SELECT filter only
(`present_on_server=1`). No `--history` flag. Deleted-folder ≠
present=0. `--live-mailboxes` / `--trash-live` are further opt-in
read-side SELECT filters (Q2 trash-in-live deferred). Existing retrieve args
`--lane` / `--after` / `--before` / `--fts-only` also filter history.
Soft-delete / never-purge + MAILROOM sync:
**[docs/MAILROOM.md](docs/MAILROOM.md)**,
**[docs/soft-delete.md](docs/soft-delete.md)**,
**[docs/ask_mail.md](docs/ask_mail.md)**.
ATT-0 attachment-search DESIGN (docs/tests only; ATT-1..8 FUTURE):
**[docs/att0-constraints.md](docs/att0-constraints.md)**.

`scripts/ask_mail.py` is the PR-8 CLI + HTTP `127.0.0.1:8743` (GET /ui
same-origin POST /ask; GET /message?id=... for citation click-through;
8744 if bound) + MCP (`ask_mail`,
`hybrid_search`, `get_thread`, non-sending `draft_reply`). `--serve`
and `--mcp` both block — two processes. Preferred generate **process** is
`mlx_lm.server` on `http://127.0.0.1:1234/v1/chat/completions` when
`$MAILROOM_GENERATE_MODEL` is set; canonical python is **venv-mlx**
(`~/MailArchive/venv-mlx/bin/python`). Not LM Studio. ask_mail UI
pointer: `http://127.0.0.1:8743/ui`. Soft-fail to labeled `fail-open-only`
hits-only if down. Ollama is embed-only (never generate). Client path
strings `llmster-headless` / `fail-open-only` stay in code — they are
**not** the process name; withhold the product-name claim
`llmster-headless`. One-command MBP install (copy scripts, stage
LaunchAgent, bootstrap, kickstart, `GET /v1/models`):
**[scripts/install-mlx-generate.sh](scripts/install-mlx-generate.sh)**
— generate-down is `./scripts/install-mlx-generate.sh down` (bootout,
not kill; KeepAlive); `status` prints dest paths and the listener.
Smoke is **retrieve+rerank, then generate** — do
not co-pin Ollama embed 8b, CrossEncoder, and 35B-class generate;
unload embed/rerank between phases. Recipes + DoD:
**[docs/ask_mail.md](docs/ask_mail.md)**,
**[docs/generate-mlx.md](docs/generate-mlx.md)**.
`rerank_mode` is `crossencoder` when live floats land, else labeled
fail-open / none / off (RRF citations; scores not claimed). Do not
co-pin embed + 35B + rerank.

Lane + date: FTS **pre-filter** on `messages.lane` / `messages.date_utc`;
vec **post-filter** after KNN. `lane=None` infers money / people / none.
If the lane was inferred and vec is empty after that filter, vec is re-run
without the lane filter (live SoR lanes are sparse; FTS stays filtered).
Explicit `--lane` stays strict. Recency `exp(-0.002 * age_days)` is skipped
when `after`/`before` is set. Vec KNN selects `message_id, distance` only
(live vec0 has no `v.rowid`).

Mac smoke (Mini venv — Apple `/usr/bin/python3` cannot load sqlite-vec).
Until PR-5, Mini retrieve/ask recipes set
`MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite` (or
`mailroom-daily-copy.sqlite`). Do not default Mini to
`mailroom.sqlite` (empty SoR stub). Recipes that use
`mailroom.sqlite` are **MBP-SoR-only**.

```zsh
# Mini — hybrid retrieve (copy DB until PR-5; Mini SoR is an empty stub)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py 'SDGE bill'
```

```zsh
# Mini — hybrid retrieve JSON (copy DB until PR-5)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py --json --k 20 'Caddell'
```

```zsh
# Mini — hybrid retrieve lane + after (copy DB until PR-5)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py --lane money --after 2024-01-01 'invoice'
```

```zsh
# Mini — hybrid retrieve cosine (copy DB until PR-5)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py --cosine 'SDGE bill'
```

```zsh
# Mini — hybrid retrieve without rerank (copy DB until PR-5)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py --no-rerank 'SDGE bill'
```

```zsh
# Mini — hybrid retrieve (horse; copy DB until PR-5)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py 'horse'
```

```zsh
# Mini — optional, once: populate messages_ids.identifiers (additive; no column rename)
~/MailArchive/.venv/bin/python scripts/messages_ids.py --db ~/MailArchive/mailroom.sqlite --backfill
```

```zsh
# Mini — ask_mail (copy DB until PR-5; generate_mode/rerank_mode always labeled)
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/ask_mail.py --json 'SDGE bill'
```

```zsh
# Mini — CrossEncoder CRM smoke (fail-open-only without weights)
~/MailArchive/.venv/bin/python scripts/rerank_smoke.py
```

## SoR health (integrity + FTS/hybrid smoke)

Read-only check of `$HOME/MailArchive/mailroom.sqlite` (or `$MAILROOM_DB`).
Recipes, Mini copy-DB note, and exit codes:
**[docs/sor-health.md](docs/sor-health.md)**.

```zsh
# MBP — SoR health + hybrid smoke
~/MailArchive/.venv/bin/python ~/MailArchive/scripts/sor_health_pack.py
```

```zsh
# Mini — SoR health + hybrid smoke (copy DB is OK; not a second writer)
~/MailArchive/.venv/bin/python ~/MailArchive/scripts/sor_health_pack.py
```

## macos-slim (Mini only)

SIP-safe Photos/media-analysis slimming on the Mac Mini M4 24GB (Tahoe
~26.3): **[macos-slim/README.md](macos-slim/README.md)**. Default
`mode=off` after install. Not for the MBP.
