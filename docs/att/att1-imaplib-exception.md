# ATT-1 imaplib exception

`docs/ops-terminal.md` §"IMAP live checks via curl imaps (never Python sockets)" still binds ATT-0. This file is the one written exception.

`scripts/attachments/imaplib_part.py` may open a Python TLS socket for ATT-1 part bytes only: readonly `EXAMINE` and `UID FETCH (BODY.PEEK[part]<offset.count>)`. ATT-0 metadata stays on pinned `/usr/bin/curl`. There is no second socket path and no curl `-X` fallback. A live dial is not part of landing this client. Errno 9 is `FetchRefuse` and nothing is stored.

The item name for the password callback is not written here. The placeholder is `<keychain-item>`.
