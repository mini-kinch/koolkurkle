# ATT-1 IMAP transport: decision note for PR #80

**Status:** decision note only. No code change.
**As-of:** 2026-09-28
**Evidence:** PR #80 head `1d1e4fed64ef5eaec9797157b12a195d82d26f37`, plus loopback checks here against `/usr/bin/curl` 8.5.0 (libcurl 8.5.0, OpenSSL 3.0.13, patch 8.5.0-2ubuntu10.9).
**PII:** none.

PR #80 fetches attachment bytes with pinned `/usr/bin/curl`. The mailbox stays out of the URL so curl will not `SELECT`. Each fetch sends `EXAMINE`, then `UID FETCH ... (BODY.PEEK[part]<offset.count>)`, as curl `-X` blocks (`scripts/attachments/fetch_p1.py` on that head, `_CurlPartConn`). The password comes from `read_imap_app_password` on curl stdin config, never on argv. R1 (`-q` first), R2 (curl env allowlist), and R4 (empty-part / NIL rules) were left unimplemented after the loopback probe failed.

## 1. Why the literal is lost

**Verified** in `curl --manual` for this 8.5.0 build, and in `libcurl.so.4.8.0` (the library this curl loads):

- `-X` for IMAP “specifies a custom IMAP command to use instead of LIST.”
- The default command template is `LIST "%s" *`. A different builder emits `UID FETCH %s BODY[%s]<%s>` or `UID FETCH %s BODY[%s]` only when the URL carries `UID` plus `SECTION` / `PARTIAL`. Both say `BODY`, not `BODY.PEEK`. No UID fails with `Cannot FETCH without a UID.`
- URL keys parsed there are `UIDVALIDITY`, `UID`, `MAILINDEX`, `SECTION`, and `PARTIAL`.
- The FETCH parser looks for `{` on an untagged line, reads a decimal size, and requires `}` then CR at the end of that line, then downloads that many bytes. No `{` fails with `Failed to parse FETCH response.`
- The mailbox-open template is `SELECT %s`. There is no `EXAMINE %s` template. The word `EXAMINE` is only compared when a custom command is classified.

**Inferred,** and matched by the loopback: `-X` stays on the LIST/custom reader. That reader forwards untagged `* ` lines and treats a line equal to the live command tag as the end of the reply. It does not enter the FETCH `{n}` download. A literal line that does not start with `* ` is dropped. A literal line that is the live tag stops the read. This is the manual sentence plus the two builders plus the `{n}` check. It is not a full reconstruction of the LIST writer.

## 2. Evidence

`tests/test_att_fetch_curl_loopback.py` on that head is an expected failure. The stub’s 64-byte literal held a NUL, `* 1 FETCH (FLAGS (\Seen))`, `A003 OK`, the live tag `A004 OK`, and `0xFF 0xFE`. Curl exited 0. Stdout was only untagged `*` lines and stopped at the embedded `A004 OK`. `fetch_part` raised `FetchRefuse('part fetch failed')` and stored nothing. `EXAMINE` stayed on that one connection. No `SELECT`, `STORE`, or `EXPUNGE`.

Re-run here, same shape: stdout was the `EXISTS` / `RECENT` / `UIDVALIDITY` lines plus `* 1 FETCH (BODY[1] {64}` and the embedded flags line. The other literal bytes were absent. A second custom-request literal, `%PDF-1.4` plus NUL / `0xFF` / `0xFE` and no CRLF, returned only `* 1 FETCH (BODY[1] {26}`. Ordinary attachment bytes are dropped even when nothing looks like a tag.

## 3. Curl workarounds actually run

Loopback IMAPS only. TLS verification on. Password in stdin config.

| Try | Tested | Result | Limits, and R1 / R2 / R4 |
| --- | --- | --- | --- |
| Native URL `imaps://host/INBOX;UID=9;SECTION=1` (slash form too) | Yes | Exact 64-byte match, including NUL, the `* ` line, both tag-like lines, and `0xFF 0xFE`. The no-CRLF payload matched too. | Commands were `SELECT INBOX` and `UID FETCH 9 BODY[1]`. No `EXAMINE`, no `BODY.PEEK`. Adding `-X` with `BODY.PEEK` brought the truncated output back. A prior `EXAMINE` in the same process did not stick: the next URL still sent `SELECT`. Stdout is the raw section, so the current `{n}` parser cannot consume it. R1 and R2 still apply. An empty section is exit 0 and an empty body (`PARTIAL=100.8` answered as `{0}`), so R4’s offset-0 rule still belongs in the caller. |
| Native `PARTIAL=0.8` and `PARTIAL=10.8` | Yes | Exact 8-byte slices, including a slice that contained `* 1 FE`. `PARTIAL=0.64` sent `BODY[1]<0.64>`. | Same `SELECT` and non-PEEK limits. Partial syntax works. Read-only `EXAMINE` does not. |
| `--output` on custom `-X` | Yes | File held the same truncated star-lines. Exit 0. | Does not recover the literal. |
| `--trace-ascii` on custom `-X` | Yes | Stdout stayed truncated. The trace showed `line-one`, then stopped at the live tag. The tail was absent. The trace also recorded the `LOGIN` line, password included. | Not a body capture. Unsafe to keep. |

No curl flag tried here both returns the literal and sends `EXAMINE` plus `BODY.PEEK`.

## 4. imaplib (AR-B)

`imaplib.IMAP4_SSL` on this interpreter, same stub: `select(..., readonly=True)` sent `EXAMINE`. `uid("FETCH", "9", "(BODY.PEEK[1]<0.64>)")` and a full `BODY.PEEK[1]` both matched the served bytes, including the no-CRLF payload. One connection: `CAPABILITY`, `LOGIN`, `EXAMINE`, `UID FETCH`, `LOGOUT`. No `SELECT`, `STORE`, or `EXPUNGE`.

`imaplib.py` `_get_response` matches `{size}` at the end of a line and `read(size)` takes those bytes before the next protocol line, so tag-like text stays data. Observed returns: `{0}` is `(header, b'')`; `BODY[1] ""` and `BODY[1] NIL` are single bytes lines. `parse_fetch_literal` on the PR #80 head already accepts imaplib’s tuple. NIL and the quoted empty string still need R4: empty at offset 0 is an error, empty later is a clean end, and a NIL match must be the FETCH item itself. That policy is not implemented.

Credentials stay on `password_fn`, default `read_imap_app_password` (`scripts/imap_keychain.py`, `/usr/bin/security`, item `<keychain-item>`, one fallback name). No new Keychain item, flag, or environment variable. `login` keeps the password in-process on the TLS session and must not log it.

Timeouts: `IMAP4_SSL(..., timeout=)` is a per-read socket timeout via `socket.create_connection`, not curl’s whole-transfer `--max-time`. A stall should still become `FetchRefuse` with nothing stored.

Work: use `IMAP4_SSL` and `ssl.create_default_context()` (verification and hostname check on), keep port 993 and the partial `BODY.PEEK` item, and turn the loopback probe into a pass. R1 does not apply. R2 does not come free: `create_default_context` copies `SSLKEYLOGFILE` onto the context when that attribute exists, and default verify paths follow OpenSSL. Proxy variables are unused. R4 still needs tests.

This conflicts with `docs/ops-terminal.md` (“IMAP live checks via curl imaps (never Python sockets)”): Python sockets to the live host have failed with Errno 9, and that rule says fail closed rather than retry on another client. Loopback success does not clear that. A new transport is an owner decision (`docs/review-gates.md`).

## 5. Options

| Option | Correctness risk | Effort | Operational risk | Testing before any live run |
| --- | --- | --- | --- | --- |
| Curl native URL | Bytes matched here, including partial slices. `SELECT` plus `BODY[]` drops the `EXAMINE` + `BODY.PEEK` guarantee. `-X` cannot add PEEK without losing the literal. | Medium. New URL, drop the `{n}` parser for this stdout, keep R1 and R2, redefine R4 for an empty body. | Stays on `/usr/bin/curl` and inside the current live-IMAP rule. A live mailbox would be selected read-write. | Do not live-run unless `EXAMINE` and `PEEK` are waived. Even then, one probe must record the real commands and whether flags changed. |
| Curl `--output` or `--trace-ascii` | High. Both failed to return the literal. The trace stored the login password. | None worth doing. | A trace file would leak the password. | None. Not a candidate. |
| imaplib over TLS (AR-B) | Low on the loopback: literal, `EXAMINE`, and `BODY.PEEK` held on one connection. The live host is unproven. | Medium. The client already has a factory-shaped login / readonly select / `uid FETCH` path and a tuple parser. Swap the connection, keep `password_fn`, add R4, retarget the probe. | Breaks the “never Python sockets” rule. Historical failure is Errno 9. Fail closed, with no curl fallback. | Loopback probe passes first. Then one supervised live check on the machine that will run the job: `EXAMINE`, one small `BODY.PEEK` partial, byte compare, and a confirm of no `SELECT` / `STORE` / `EXPUNGE` and no flag change. If the socket fails, stop. |
| Defer ATT-1 | None. The probe already stores nothing. | None. Leave the expected-failure probe on PR #80. | No live fetch. Attachment bytes stay absent. | None until a transport is chosen. |

## 6. Recommendation

Use imaplib for ATT-1 byte fetch only. It is the only option this loopback returned intact bytes for while still sending `EXAMINE` and `BODY.PEEK`.

The owner chooses one:

1. ATT-1 may use `imaplib.IMAP4_SSL`, and the live-IMAP rule gets a written exception for that path alone. ATT-0 stays on curl.
2. Defer ATT-1. Leave the curl custom-request path unused.

The native curl URL needs a separate waiver of `EXAMINE` and `BODY.PEEK`. These runs show it cannot meet both.

## Scope

This is ATT-1 attachment-byte fetch only. It does not change the ATT-0 metadata fill (`scripts/attachments/meta_fill.py`): headers-only `BODYSTRUCTURE`, with `literal_dropped` and `literal_truncated` still hard gates for that UID.
