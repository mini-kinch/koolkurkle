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
history (DECIDED); live modes are opt-in (`--live` additive SELECT
filter; `--lane` / `--after` / `--before` /
`--fts-only` also filter history). Mini-only slim:
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
After Action required: zero chatter until Done (after an Action required, silence until Done/Blocked/explicit reply; exceptions only STOP / hello / wake-up; do not stack chatter or routine status on an open AR): section below.
No Terminal AR for facts Shell can read (do not issue Terminal AR for facts agent Shell can read; Terminal AR only for GUI / Little Snitch / sudo / secrets in a real Terminal): section below.
Embed model (local Qwen3-Embedding-8B via Ollama; not cloud embed): section below.
Agent Shell non-interactive (no read/getpass in agent scripts; security -w last or it stores empty; secrets only in real Terminal; report wc -c only): section below.
Status/handoff reports include ETA until next Action required (honest range only; no false LOCKED ETAs; no undeliverable certainty slogans): section below.
Warn before local-exec that may trigger macOS Allow sheets (warn the operator before any local-exec / Shell / machine action that may trigger macOS permission Allow sheets; Documents/Desktop/Downloads, screen recording, microphone, camera; do not invent click-paths): section below.
No stacked ARs (never stack Action required / card-like prompts in one turn; exception only when the user explicitly asks for another AR during an active host-kept foreground job; one machine, one command, loud banner still applies): section below.
CoS Desk default theater (CoS Desk is the default theater for factory Merge ARs and status that needs user action; do not post Merge ARs to CoS private 1:1 unless the user asks for privacy; one thing at a time — no dual-window / stacked AR): section below.
Watch proof (quote last sample or say not watching; do not invent progress; do not claim LOCKED monitor; do not restart watched jobs from status reports): section below.
Factory docs batches (one combined PR per batch or stacked branches; forbid parallel PRs that all edit the same shared docs files, e.g. docs/ops-terminal.md + README): section below.
SWITCH TO Mini/MBP before machine-specific Terminal AR (detect machine only from prompt hostname / Sent-from-machine / pasted proof; loud SWITCH TO MBP/Mini callout on mismatch; agents cannot see which Terminal window is focused): section below.
Continuous keepgoing (after Done on an authorized chain, immediately issue the next AR/task; forbid soft pause fillers like "next judgment when you want"): section below.
After user PASS on a check (ack PASS and proceed to the next AR; do not re-issue the same check): section below.
IMAP tombstone never STORE Deleted / EXPUNGE (local present_on_server only; refuse IMAP STORE \Deleted, EXPUNGE, Trash-purge): section below.
with_writer_lock sole-writer wrapper (busy/lock refuse before second writer; shipping this guard is not starting rem-legacy): section below.
mailroom_copy_db rem-gated copy (Mini copy only when rem-legacy is not writing or after EXIT 0; no SMB/NFS dual-write): section below.
bind_copy_db / daily children honor MAILROOM_DB (argv=None reads sys.argv[1:]; children open the copy; refuse SoR stub): section below.
PR-5 cutover checklist (docs only — do not enable; gated on rem-legacy EXIT 0 + Mini SoR switch steps; this change does not enable cutover or RunAtLoad): section below.
sor_health_pack read-only / Mini-copy OK (read-only health; Mini on a copy DB is OK and is not a second writer): section below.

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
source. Detect the machine only from prompt hostname /
Sent-from-machine / pasted proof. Detect the host from a
**Sent-from-machine** tag or a pasted prompt **hostname** proof only.
Agents cannot see which Terminal window is focused. The operator
cannot see the focused Terminal window.

If last input was **Mini** and the task needs **MBP** (or vice versa),
issue a loud SWITCH TO MBP/Mini callout — **SWITCH TO MBP** or
**SWITCH TO Mini** — explicitly before or with the AR. Fail closed:
no host proof, no machine-specific card.

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

## After Action required: zero chatter until Done

Standing ops contract: after an Action required, silence until Done/Blocked/explicit reply. Zero further user-facing messages until Done / Blocked / explicit reply. Exceptions only STOP / hello / wake-up — answer immediately. Do not stack chatter or routine status on an open AR.

Fail closed: if an Action required is still open, do not send further user-facing messages. Stay silent until Done/Blocked/explicit reply. Do not stack chatter. Do not post routine status on an open AR.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## No Terminal AR for facts Shell can read

Standing ops contract: do not issue Terminal AR for facts agent Shell can read. Terminal AR only for GUI / Little Snitch / sudo / secrets in a real Terminal.

If the agent Shell can already read the fact (repo file, git status, local test output, workspace path class), do not issue a Terminal AR. Read it in Shell. Do not ask a human to paste that fact from a Mac Terminal.

Issue a Terminal AR only when the step needs a real Terminal for GUI / Little Snitch / sudo / secrets.

Fail closed: if the agent Shell can read the fact, do not issue a Terminal AR. Do not issue a Terminal AR for ls, cat, git status, or other Shell-readable facts.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Embed model (local Qwen3-Embedding-8B via Ollama)

Standing embed-model contract: embed model is local Qwen3-Embedding-8B via Ollama. Not cloud embed.

Official Ollama library tag is `qwen3-embedding:8b` (local Ollama only). Do not use a cloud embed API.

Fail closed: if local Ollama Qwen3-Embedding-8B is not the embed path, do not start an embed job. Do not fall through to cloud embed.

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not start embed jobs, does not start Ollama, does not run live Mac writers, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Agent Shell non-interactive (no read/getpass)

Standing Agent Shell contract: **Agent Shell is non-interactive**. Forbid `read`/`getpass` in agent scripts. Do not use `read` or `getpass` in agent scripts. Agent Shell cannot collect a secret at a prompt.

security -w last or it stores empty. Do not put the secret on the command line. Secrets only in a **real Terminal**. The operator types the secret there and reports `wc -c` only (length, never the secret). No secret values in the repo.

Fail closed: if a step needs a secret, do not prompt in Agent Shell. Do not use `read` or `getpass` in agent scripts. Do not run `security -w` from Agent Shell. Issue a real-Terminal card; the operator types the secret and reports `wc -c` only.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not write Keychain, does not run live secret entry, does not SSH a live machine, and does not change rem-legacy.

## Status/handoff: ETA until next Action required

Standing status/handoff contract: status/handoff reports include ETA until next Action required. Honest range only. No false LOCKED ETAs. No undeliverable certainty slogans.

Fail closed: if a status or handoff report cannot give an honest range until the next Action required, do not invent a LOCKED ETA. Do not claim undeliverable certainty. Give an honest range only, or say the ETA is unknown.

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Warn before local-exec that may trigger macOS Allow sheets

Standing ops contract: warn the operator before any local-exec / Shell / machine action that may trigger macOS permission Allow sheets (Documents/Desktop/Downloads, screen recording, microphone, camera, and similar Allow-sheet classes).

Fail closed: if a local-exec, Shell, or machine action may raise an Allow sheet, do not run it until the operator has been warned. Do not invent click-paths. Name the permission class. Do not click the Allow sheet.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## No stacked ARs (exception only when user asks during host-kept job)

Standing ops contract: never stack Action required / card-like prompts in one turn. Do not stack open Action requireds / problems onto the user. No stacked open ARs by default.

Exception only when the user explicitly asks for another AR during an active host-kept foreground job. Parallel next ARs while a long host-kept job runs are OK when the user asks for the next task.

One machine, one command, loud banner still applies.

Fail closed: if the user did not explicitly ask for another AR during an active host-kept foreground job, do not stack Action required / card-like prompts. Do not stack open ARs by default. Do not stack card-like prompts in one turn.

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## CoS Desk default theater (Merge ARs to Desk not 1:1)

Standing ops contract: Desk default. CoS Desk is the default theater for factory Merge ARs and status that needs user action. Merge ARs and factory status go to CoS Desk. Do not post Merge ARs to CoS private 1:1 unless the user asks for privacy. If Developer Ready arrives on a private agent wake, post Merge AR to Desk, not 1:1.

Private 1:1 only for privacy from Jumpseat or a card rooms cannot show. When rooms cannot show cards, say the card is in CoS 1:1 because rooms cannot show cards (never call it private).

One thing at a time. No dual-window / stacked AR.

Fail closed: if the user did not ask for privacy, do not post the Merge AR to CoS private 1:1. Post Merge ARs and factory status to CoS Desk. If Developer Ready arrives on a private agent wake, post Merge AR to Desk, not 1:1. Do not open a dual-window. Do not stack an AR. When rooms cannot show cards, say the card is in CoS 1:1 because rooms cannot show cards — never call it private.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Watch proof: quote last sample or say not watching

Standing watch-proof contract: when reporting on a watched job (e.g. rem-legacy / host-kept embed), quote the last real sample line (`PT | job | alive/stalled/EXIT | n/N`) or explicitly say not watching. Do not invent progress. Do not claim LOCKED monitor. Do not restart watched jobs from status reports.

Placeholder classes only: `PT`, `<job>`, `n/N`. Never a live numeric PID as a standing example.

Fail closed: if a watch claim cannot quote a last real sample line, explicitly say not watching. Do not invent progress. Do not claim LOCKED monitor. Do not restart watched jobs from status reports.

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, does not sample a live watch, and does not change rem-legacy.

## Factory docs batches (one combined PR per batch or stacked)

Standing factory-docs contract: factory docs batches = one combined PR per batch OR stacked branches; forbid parallel PRs that all edit the same shared docs files (e.g. docs/ops-terminal.md + README).

Do not open parallel same-file docs PRs. A factory docs batch lands in one combined PR, or as stacked branches that merge one at a time. Parallel PRs that all edit `docs/ops-terminal.md` and README collide.

Fail closed: if a factory docs batch would open parallel PRs that all edit the same shared docs files, do not open them. Use one combined PR per batch, or stacked branches.

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Continuous keepgoing: immediate next AR after Done

Standing keepgoing contract: after Done on an authorized chain, immediately issue the next AR/task; forbid soft pause fillers like "next judgment when you want".

When a task on an authorized chain completes (Done), CoS immediately issues the next Action required / next task. Do not insert a soft pause between steps of an authorized chain.

Fail closed: if a task on an authorized chain is Done, immediately issue the next AR/task. Do not insert a soft pause filler. Do not say "next judgment when you want".

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## After user PASS on a check: ack and next AR — no re-ask

Standing check-PASS contract: after user PASS / paste for a check, ack PASS and proceed to the next AR; do not re-issue the same check.

If the user already PASSed or pasted proof for a check (e.g. `rate_limit`), ack PASS and ship the next AR. Do not re-ask the same check.

Fail closed: if the user already PASSed / pasted for a check, ack PASS and proceed to the next AR. Do not re-issue the same check. Do not re-ask the same check.

Name the machines as MBP and Mini only. Never a login, home path, or email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Soft-delete / never-purge CLI refuse

Soft-delete is **DECIDED** (not a standby contract). History default
is **DECIDED**. Hard-refuse CLI verbs `purge` | `expunge` |
`empty-trash` | `delete-gone` | `drop-messages`. Never physically
purge. **Deleted-folder ≠ present=0.** `--live` is an additive SELECT
filter only. Canonical one-pager: [soft-delete.md](soft-delete.md).
See [MAILROOM.md](MAILROOM.md) and [tombstone.md](tombstone.md).

SQL helpers fail-closed refuse `DELETE FROM messages` /
`DROP TABLE messages` / `TRUNCATE`. Frozen `icloud_mail_all.jsonl`
is immutable (no rewrite / reconcile). `--live-mailboxes` and
`--trash-live` are opt-in read-side SELECT filters. Q2 trash-in-live
default is deferred.

## IMAP tombstone never STORE Deleted / EXPUNGE

Standing tombstone contract: `imap_tombstone` / the tombstone path is
local `present_on_server` only. Never IMAP `STORE \Deleted`,
`EXPUNGE`, or Trash-purge. Refuse those verbs.

Fail closed: if argv names IMAP `STORE`, `\Deleted`, `EXPUNGE`, or
Trash-purge, refuse. Do not implement live IMAP delete here.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## with_writer_lock sole-writer wrapper

Standing writer-lock contract: `with_writer_lock` is the sole-writer
wrapper. Busy/lock refuse before a second writer. Shipping this guard
is not starting rem-legacy.

Fail closed: if the writer lock is busy or held, refuse before a
second writer. Do not steal. Do not start rem-legacy from this wrapper.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## mailroom_copy_db rem-gated copy

Standing rem-gated copy contract: Mini copy only when rem-legacy is
not writing, or after rem-legacy **EXIT 0**. No SMB/NFS dual-write.
No live MBP→Mini copy from this gate.

Fail closed: if rem-legacy is still writing, do not live-copy
MBP→Mini. Use `mailroom-daily-copy.sqlite` when rem still holds
`mailroom-copy.sqlite`. Do not dual-write over SMB/NFS.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## bind_copy_db / daily children honor MAILROOM_DB

Standing bind contract: `bind_copy_db(argv=None)` reads `sys.argv[1:]`.
Daily children open the copy DB. Refuse the SoR stub
(`mailroom.sqlite`). Tests only; no live SoR open.

Fail closed: if `argv` is `None`, honor process `--db` via
`sys.argv[1:]`. Do not treat `None` like `[]`. Do not open the SoR
stub path.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## PR-5 cutover checklist (docs only — do not enable)

Standing PR-5 contract: cutover checklist is docs only. Gated on
rem-legacy **EXIT 0** plus Mini SoR switch steps. This change does **not** enable PR-5 cutover and does **not** enable RunAtLoad.
Checklist: [pr5-cutover.md](pr5-cutover.md).

Fail closed: if rem-legacy has not EXIT 0, do not enable cutover.
Do not enable RunAtLoad in this change. Do not promote Mini
`mailroom.sqlite` while rem-legacy is live.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## sor_health_pack read-only / Mini-copy OK

Standing health-pack contract: sor_health_pack is read-only. Mini
on a copy DB is OK and is not a second writer.

Fail closed: if a step would write the DB or treat Mini copy as a
second writer, do not run it. Health is read-only. Mini copy is a
replica, not a second live writer.

Name the machines as MBP and Mini only. Never a login, home path, or
email.

This gate is docs/tests only. It does not run live Mac writers, does not run live classify, does not run live IMAP, does not open MailArchive or live sqlite, does not write embed/SoR data, does not read Keychain, does not SSH a live machine, and does not change rem-legacy.

## Mini bodies-fts curl + Keychain name

BODY.PEEK prefers Homebrew curl ≥ 8.17 at
`/opt/homebrew/opt/curl/bin/curl`. Apple `/usr/bin/curl` is
fail-closed for BODY.PEEK. Apple `/usr/bin/curl` Little Snitch allow
does not cover Homebrew curl. BODY.PEEK Homebrew curl needs its own
Little Snitch allow. Keychain item **name** only:
`mailroom.imap.app-password`. Never secret values. No live IMAP. No
Keychain read/write from this gate.

## Homebrew curl Little Snitch allow

Operator checklist (docs/tests only; no live IMAP):

1. Apple `/usr/bin/curl` Little Snitch allow does not cover Homebrew curl.
2. BODY.PEEK uses `/opt/homebrew/opt/curl/bin/curl` and needs its own
   Little Snitch allow.
3. Headers may still use Apple `/usr/bin/curl`.
4. No live IMAP from this gate. No Keychain read/write from this gate.

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

1. **One writer per `.sqlite`.** Shipping guard: lockfile or busy
   refuse before a second `embed_backfill`. Shipping the guard ≠
   starting a writer. Rem-legacy ≠ Mini daily: do not restart rem
   for daily; daily uses `--quote-strip`; rem keeps old text until
   EXIT. `--lock` is a per-batch heartbeat. It
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
7. **`--embed-live-only`.** Flag + docs for a future daily incremental.
   Must not delete existing tombstone embeds. Shipping the flag ≠
   starting a job. Guard ≠ run against rem-legacy. Do not start an
   embed job from this gate.

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
