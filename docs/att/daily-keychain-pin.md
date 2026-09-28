# Daily wrapper Keychain pin

`scripts/run_mailroom_daily.sh` does not compile in a Keychain item name. The name is `MAILROOM_KEYCHAIN_ITEM`, or the first non-comment line of the file named by `MAILROOM_KEYCHAIN_CONFIG`. Docs and tests say `<keychain-item>`. A local pin file such as `.mailroom-keychain-item` is gitignored.

A missing pin exits **4** with `error: keychain item is not pinned` and does not start the daily. A missing item or an empty password exits **4** with `error: keychain item is missing`. There is no legacy fallback and no `falling back` warning.

The wrapper's own `/usr/bin/security find-generic-password` dies after 15 seconds. `MAILROOM_KEYCHAIN_TIMEOUT_S` overrides that when it is a positive number. Timeout exits **5** with `error: imap keychain read timed out`. Status 2 (unset database, basename refuse, missing interpreter) is unchanged.
