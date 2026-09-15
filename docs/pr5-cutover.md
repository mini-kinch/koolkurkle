# PR-5 cutover checklist (docs only — do not enable)

This file is a checklist. This change does **not** enable PR-5
cutover. This change does **not** enable RunAtLoad. Rem-legacy is
untouched. Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Daily copy-only: [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
MAILROOM pointer: [MAILROOM.md](MAILROOM.md).
Embed / single-writer: [embed-backfill.md](embed-backfill.md).
Catch-up: [post-exit-catchup.md](post-exit-catchup.md).
Dry verify: [pr5_preflight.py](../scripts/pr5_preflight.py).

PR-5 cutover is still **gated on rem-legacy EXIT 0**. Do not promote
Mini to SoR and do not enable cutover while rem-legacy is live.

## NON-GO (this PR)

**NON-GO.** This PR does not enable PR-5 / RunAtLoad / flip SoR host.
It does not set RunAtLoad, does not promote Mini
`mailroom.sqlite`, and does not restart rem. Docs/tests/verify only.

EXIT ≠ cutover GO. Catch-up Done ≠ cutover GO. #40 gate live ≠
cutover GO. Each needs its **own CoS GO**. Do not enable with
catch-up.

## Standing status (cite, not enable)

Operator standing (cite in this checklist; **not** an enable):

- rem-legacy EXIT completed (`17223/17223`)
- #40 fail-closed `sor_writer_gate` live on operator MBP
- Mailroom catch-up Done after rem EXIT
- HOLD still: PR-5 enable / RunAtLoad / SoR host flip / ATT implement
  / money / external send

Citing these facts is not permission to enable. Topology stays
**SoR=MBP until CoS says** otherwise. Generate stays localhost
`mlx_lm.server`. Name the machines as MBP and Mini only.

## Order (after rem EXIT, before any enable)

`EXIT → gate ALLOW → catch-up → later copy+integrity → then checklist`

1. rem-legacy **EXIT 0** (standing: `17223/17223` completed).
2. #40 `sor_writer_gate` **ALLOW** on future SoR writes (gate live).
3. Mailroom catch-up **Done** after rem EXIT.
4. **Later** copy freshness + integrity pack (read-only verify).
5. **Then** this checklist. Not enable with catch-up.

Do not collapse catch-up and cutover into one breath. RunAtLoad is a
**separate GO** after a later cutover PASS.

## Post-rem gates (all required before anyone considers enable)

Fail-closed. Missing evidence = do not consider enable.

1. rem-legacy **EXIT 0** (`17223/17223` completed). Do not start,
   restart, or kill rem-legacy to force this.
2. **#40 gate live** — fail-closed `sor_writer_gate` on operator MBP
   for **future SoR writes**. Live rem refuses. A stale dead-PID lock
   file is **not** a false `CONFLICT` (flock free ⇒ not held).
3. Mailroom **catch-up Done** after rem EXIT
   ([post-exit-catchup.md](post-exit-catchup.md)).
4. **Single-writer HARD DECK** survives prep — one writer per
   `.sqlite`; never two-wide writers on one file. Shipping the guard
   ≠ starting a writer.
5. **flock free** — writer lock probe without steal; leftover file
   with a dead PID and no flock is free.

Gates true ≠ cutover GO. This change still does **not** enable PR-5
cutover and does **not** enable RunAtLoad.

## Gates (all required before any later cutover)

1. rem-legacy **EXIT 0** on MBP (confirmed). Do not start, restart, or
   kill rem-legacy to force this.
2. Mini has a current copy DB (`mailroom-copy.sqlite` or
   `mailroom-daily-copy.sqlite`).
3. Mini copy from MBP only when rem-legacy is not writing, or after
   rem-legacy EXIT 0. No live copy in this change. No SMB/NFS
   dual-write.

## Operator-facing preflight (dry / read-only)

Run [pr5_preflight.py](../scripts/pr5_preflight.py) only. It never
enables LaunchAgents, never sets RunAtLoad, never writes SoR, and
never opens live IMAP.

1. **Installed daily plist** — absent **or** `RunAtLoad` disabled on
   the *installed* LaunchAgent. The checked-in template is not an
   enable (`RunAtLoad` on the template is not cutover GO).
2. **Path existence** — copy DB path exists (inject / mock in tests).
   Missing copy path fails closed. SoR basename `mailroom.sqlite` is
   reported, never promoted, never written.
3. **#40 future SoR writes** — live rem cmdline ⇒ `CONFLICT`. Stale
   dead-PID lock ≠ false `CONFLICT`. flock held ⇒ `CONFLICT`. Copy
   DBs allowed.
4. **Read-only integrity / freshness / embed key** — labels only
   (`PRAGMA integrity_check`, `copy_age`, quote-strip generation
   key). No live SoR write. No live MBP→Mini copy.
5. **Mini copy-only until promote GO** — unset / SoR stub
   `mailroom.sqlite` remains `db_mode=refused`.
6. **ask_mail** default is **history**; live is opt-in. No
   `purge` / `EXPUNGE` in this PR.
7. **Soft-delete landmines** — never-purge; Deleted-folder ≠
   `present=0`; frozen `icloud_mail_all.jsonl` is immutable.
8. **Mini BODY.PEEK rails** — Homebrew curl ≥ 8.17; Apple
   `/usr/bin/curl` fail-closed for BODY.PEEK; Keychain item **name**
   only (`mailroom.imap.app-password`). No live IMAP this PR.
9. **Auth hard-gate** restated — `lane=auth` must not enter
   extract→FTS/chunk retrieve by default.
10. Verdict is **NON-GO** unless a later CoS GO (not this PR).

## Mini SoR switch steps (later — not this change)

After rem-legacy EXIT 0:

1. Point Mini `MAILROOM_DB` / the copy allowlist at `mailroom.sqlite`
   (SoR name).
2. Only with CoS GO, enable `RunAtLoad` on the **installed** Mini
   plist. The checked-in template is not an enable of cutover.
3. Do not change rem-legacy LaunchAgents.

Mini stays copy-only until **promote GO**. Refuse the SoR stub.
Topology: **SoR=MBP until CoS says**; mlx generate localhost; MBP and
Mini names only.

## Integrity checks before cutover

**Integrity pack + copy freshness** (required before cutover).
Read-only verify needles only in this PR.

Before any later cutover, after rem-legacy EXIT 0 + human go:

1. **Integrity pack** — `PRAGMA integrity_check` is `ok` on SoR and
   on the Mini copy. `sor_health_pack` read-only PASS.
2. **Copy freshness** — Mini copy age is labeled (`copy_age`) and is
   from the post-EXIT SoR copy, not a stale rem-window copy.
3. **Quote-strip generation match** — Mini retrieve/generate uses the
   same embed generation key as SoR (`qwen3-embedding:8b`, 4096
   native / 1024 store, same instruction prefix).

Post-EXIT catch-up (IMAP+bodies-FTS under lock, Mini←SoR copy,
integrity pack, then clear `sor_increment=frozen`) runs **before**
this checklist. See [post-exit-catchup.md](post-exit-catchup.md).
Catch-up Done does not skip later copy+integrity and does not enable.

## One cutover + one rollback

One cutover. One rollback. Do not invent a second cutover path.

## Rollback procedure

**Rollback:** point Mini `MAILROOM_DB` back at
`mailroom-copy.sqlite` / `mailroom-daily-copy.sqlite`, leave
`mailroom.sqlite` unpromoted, keep RunAtLoad off, keep rem-legacy
LaunchAgents unchanged. Rollback does not restart rem. Rollback does
not flip SoR host. Rollback does not enable PR-5.

## RunAtLoad is a separate GO

RunAtLoad is **not** part of the cutover enable. It needs its own
CoS GO after cutover PASS. This change does **not** enable
RunAtLoad.

## This change

- Does not enable PR-5 cutover
- Does not enable RunAtLoad
- Does not change rem-legacy
- Does not promote Mini `mailroom.sqlite`
- Does not flip SoR host
- Does not implement ATT / MSG / NOTE
- Docs/tests only. No live IMAP, no live MBP→Mini copy, no Keychain
  read/write.

Name the machines as MBP and Mini only. Never a login, home path, or
email.
