# Keychain read timeout

`scripts/imap_keychain.py` bounds `/usr/bin/security find-generic-password` at 15 seconds (`KEYCHAIN_TIMEOUT_S`). `MAILROOM_KEYCHAIN_TIMEOUT_S` overrides that when it is a positive number. The child is killed when the deadline fires.

The metadata fill then exits **5** (`KEYCHAIN_TIMEOUT_EXIT`). Stderr is `error: imap keychain read timed out`. The report is still printed. Status 2 is unchanged. This check runs before any IMAP auth attempt and before auth-failure status 4.

An interactive Keychain prompt that does not return inside the deadline is this timeout. The item name is `<keychain-item>`, read from `MAILROOM_KEYCHAIN_ITEM` or `MAILROOM_KEYCHAIN_CONFIG`. It is not compiled into this module. There is no legacy fallback.
