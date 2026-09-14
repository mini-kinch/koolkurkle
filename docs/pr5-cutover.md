# PR-5 cutover checklist (docs only — do not enable)

This file is a checklist. This change does **not** enable PR-5
cutover. This change does **not** enable RunAtLoad. Rem-legacy is
untouched. Human Terminal cards: [ops-terminal.md](ops-terminal.md).
Daily copy-only: [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
MAILROOM pointer: [MAILROOM.md](MAILROOM.md).

PR-5 cutover is still **gated on rem-legacy EXIT 0**. Do not promote
Mini to SoR and do not enable cutover while rem-legacy is live.

## Gates (all required before any later cutover)

1. rem-legacy **EXIT 0** on MBP (confirmed). Do not start, restart, or
   kill rem-legacy to force this.
2. Mini has a current copy DB (`mailroom-copy.sqlite` or
   `mailroom-daily-copy.sqlite`).
3. Mini copy from MBP only when rem-legacy is not writing, or after
   rem-legacy EXIT 0. No live copy in this change. No SMB/NFS
   dual-write.

## Mini SoR switch steps (later — not this change)

After rem-legacy EXIT 0:

1. Point Mini `MAILROOM_DB` / the copy allowlist at `mailroom.sqlite`
   (SoR name).
2. Only with CoS GO, enable `RunAtLoad` on the **installed** Mini
   plist. The checked-in template is not an enable of cutover.
3. Do not change rem-legacy LaunchAgents.

## Integrity pack + copy freshness (required before cutover)

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

## One cutover + one rollback

One cutover. One rollback. Do not invent a second cutover path.

**Rollback:** point Mini `MAILROOM_DB` back at
`mailroom-copy.sqlite` / `mailroom-daily-copy.sqlite`, leave
`mailroom.sqlite` unpromoted, keep RunAtLoad off, keep rem-legacy
LaunchAgents unchanged. Rollback does not restart rem.

## RunAtLoad is a separate GO

RunAtLoad is **not** part of the cutover enable. It needs its own
CoS GO after cutover PASS. This change does **not** enable
RunAtLoad.

## This change

- Does not enable PR-5 cutover
- Does not enable RunAtLoad
- Does not change rem-legacy
- Does not promote Mini `mailroom.sqlite`
- Docs/tests only. No live IMAP, no live MBP→Mini copy, no Keychain
  read/write.

Name the machines as MBP and Mini only. Never a login, home path, or
email.
