# Post-EXIT catch-up BEFORE PR-5 (Heavy 04)

Docs/tests only. **Do not run catch-up until rem-legacy EXIT 0 +
human go.** This change does not start IMAP, copy, or integrity
against live rem. Rem EXIT 0 handling is out of scope for this PR.

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
PR-5 checklist: [pr5-cutover.md](pr5-cutover.md).
Freeze label: [rem-window-freeze.md](rem-window-freeze.md).

## Order (after EXIT 0 + human go, before any PR-5)

1. **IMAP + bodies-FTS on SoR** under `with_writer_lock`.
2. **Mini ← SoR copy** (no SMB/NFS dual-write).
3. **Integrity pack** (`PRAGMA integrity_check` +
   `sor_health_pack`).
4. **Then clear `sor_increment=frozen`.**

Do not enable PR-5 in the same breath. RunAtLoad is a **separate GO**.
One cutover + one rollback live in [pr5-cutover.md](pr5-cutover.md).

```zsh
# SoR host — AFTER EXIT 0 + human go only. Catch-up is documented, not run here.
# IMAP + bodies-FTS under with_writer_lock (placeholder; not a live start)
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/with_writer_lock.py \
  --purpose post_exit_catchup -- echo 'catch-up documented only'
```

This gate does not run live IMAP, does not copy MBP→Mini, does not
write SoR, and does not change rem-legacy.
