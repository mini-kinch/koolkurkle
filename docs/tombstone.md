# Tombstone / never-purge SoR contract

Never physically delete iCloud or server mail. Local tombstone only.

When IMAP no longer lists a message, SoR keeps the local row and marks
`messages.present_on_server` (existing column). It does not IMAP
`STORE \Deleted`, `EXPUNGE`, Trash-purge, or otherwise remove the
server copy.

Daily headers child: `imap_tombstone.py`. The GitHub tree is copy-only
bind (`bind_copy_db`); it does not open IMAP or Keychain. Do not
implement live IMAP delete here.

Human Terminal cards: [ops-terminal.md](ops-terminal.md). Daily
pipeline: [README.mailroom-daily.md](../scripts/README.mailroom-daily.md).
