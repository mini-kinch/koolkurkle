# Fetch/auth error ≠ tombstone + UIDVALIDITY (KOO-60)

Docs/tests only. No live IMAP. Rem untouched.

Canonical tombstone: [tombstone.md](tombstone.md).
Soft-delete: [soft-delete.md](soft-delete.md).
MAILROOM §5: [MAILROOM.md](MAILROOM.md).

## Rules

1. **Fetch error ≠ tombstone.** An IMAP/BODY.PEEK failure does not
   mark `present_on_server=0`.
2. **Auth error ≠ tombstone.** Login / Keychain / 2FA failure is not
   gone.
3. **Empty fetch ≠ gone.** An empty BODY.PEEK or empty UID set is not
   proof the message left the server.
4. **UID + UIDVALIDITY persistence.** Store both. UID alone is not
   identity across a UIDVALIDITY change.

Helper: `scripts/imap_fetch_error.py`. Fail-closed. No sockets.

Human Terminal cards: [ops-terminal.md](ops-terminal.md).
