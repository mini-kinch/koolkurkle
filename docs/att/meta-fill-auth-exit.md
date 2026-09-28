# Metadata fill auth exit

`scripts/attachments/meta_fill.py` process status **4** (`AUTH_EXIT`) means the IMAP client did not open because the Keychain password was missing, login was refused, or curl reported authentication failure (rc 67).

Stderr is exactly `error: imap auth failed`. The report is still printed on stdout, including `curl_failures`.

Status 2 is unchanged (argparse, the writer lock, and the other refuses). A connect, certificate, or other non-auth curl failure stays status 0 with `curl_failures` set. `imap keychain read timed out` is not status 4.
