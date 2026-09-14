# Tombstone / never-purge SoR contract

Never physically delete iCloud or server mail. Local tombstone only.
Soft-delete is **DECIDED** (not a standby contract). Canonical
one-pager: [soft-delete.md](soft-delete.md). See
[MAILROOM.md](MAILROOM.md) §5.

When IMAP no longer lists a message, SoR keeps the local row and marks
`messages.present_on_server` (existing column). The tombstone path is
local `present_on_server` only. Never IMAP `STORE \Deleted`,
`EXPUNGE`, or Trash-purge. Refuse those verbs. It does not otherwise
remove the server copy.

**Deleted-folder ≠ present=0.** A message sitting in Deleted is still
present on the server. Tombstone (`present_on_server=0`) is not the
same as "in Deleted".

Hard-refuse destructive CLI verbs: `purge` | `expunge` | `empty-trash`
| `delete-gone` | `drop-messages` (`scripts/refuse_destructive.py`).

Daily headers child: `imap_tombstone.py`. The GitHub tree is copy-only
bind (`bind_copy_db`); it does not open IMAP or Keychain. Do not
implement live IMAP delete here.

ask_mail retrieve default is history (Q1 **DECIDED**); live modes are
opt-in (`--live` is an additive SELECT filter only)
([ask_mail.md](ask_mail.md)).

Human Terminal cards: [ops-terminal.md](ops-terminal.md). Daily
pipeline: [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
