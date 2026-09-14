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

**Fetch/auth error ≠ tombstone.** Empty fetch ≠ gone. Persist UID +
UIDVALIDITY together. UID alone is not identity.
[fetch-error-tombstone.md](fetch-error-tombstone.md).

ask_mail retrieve default is history (Q1 **DECIDED**); live modes are
opt-in (`--live` is an additive SELECT filter only)
([ask_mail.md](ask_mail.md)). Attachment hits for tombstoned parents
follow the same mode: visible under history; hidden only under
`--live`. Do not permanently hide attach hits solely because the
parent is tombstoned. Never-purge `attachment_*` tables/rows (same
never-purge spirit as `messages`); skip/`too_big` beats disk purge.
[att0-constraints.md](att0-constraints.md).
Fetch/replica fail ≠ “conversation deleted” for future MSG/NOTE
replicas (status column, not tombstone-as-gone). Unified-search
freshness: mail `--live` is `present_on_server`; imsg/note is
`replica_age` only. [unified-search-design.md](unified-search-design.md).

Human Terminal cards: [ops-terminal.md](ops-terminal.md). Daily
pipeline: [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
