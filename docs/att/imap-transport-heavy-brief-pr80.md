# IMAP transport brief for an outside reviewer (PR 80, ATT-1)

## QUESTIONS FOR HEAVY

1. On curl 8.5.0, is IMAP custom-request mode (`-X` / config `request`, used instead of LIST) fundamentally unable to return a multi-line or binary FETCH literal intact, rather than a flag we failed to set?
2. Does the native URL `imaps://<imap-host>/MAILBOX;UID=n;SECTION=x` return the exact section bytes, including NUL, CR/LF, and bytes that look like IMAP tags, and does `PARTIAL=offset.length` send `BODY[section]<offset.length>`?
3. On that same native URL, can curl keep EXAMINE (read-only) and `BODY.PEEK`, or does it SELECT the mailbox and send `BODY[]`? If a prior EXAMINE is sent in the same process, does the following URL stay read-only?
4. Do `--output` or `--trace-ascii` recover literal bytes that the custom-request reader dropped, or do they observe the same truncated read?
5. Is stdlib `imaplib.IMAP4_SSL` with `select(readonly=True)` a correct reader for partial `BODY.PEEK` literals (binary, CR/LF, embedded tag-like lines)? What pitfalls matter here: per-read socket timeout versus a whole-transfer deadline, empty / NIL replies, SSL environment variables, and a historical Errno 9 when a Python socket dials the live host?
6. Which option should ATT-1 use (native curl URL, some other curl workaround, stdlib imaplib, or defer), and which tests must pass before any live run?
7. Q7 (issue 1): For the search service that writes the system of record, is a read-only window marker that pauses those writes during a lock hold, and never takes write.lock, the right long-term model, or should that service move to a sidecar database?
8. Q8 (issue 2): The Mini daily shell and daily Python driver have drifted from the repo copies. Promote the Mini bytes into the repo, or reinstall the repo copy? What diff review is required first?
9. Q9 (issue 6): Already covered by questions 1 through 6. The choice is the same one as question 6: a curl URL-form workaround, or unhold AR-B imaplib. No second question.
10. Q10 (issue 7): The literal drop reproduced on Mini curl 8.7.1 and on this cloud curl 8.5.0. The native URL-form results are only for 8.5.0. Should CI pin or emulate the Mini curl?
11. Q11 (issue 13): The first daily after the gap is about 41 hours of backlog. Should urgent texts and bill reminders on that catch-up have a per-run cap and an age limit?
12. Q12 (issue 18): A Keychain failure currently leaves the metadata fill at exit 0 with a non-empty failure list, and only a report gate requires that list to be empty. Should the fill exit non-zero on auth failure?
13. Q13 (issue 20): The Keychain read has no subprocess timeout. Add a timeout and a distinct exit code, and should an interactive prompt fail fast?
14. Q14 (issue 21): Pin the Keychain item in the daily wrapper and remove the legacy fallback, or keep the fallback with a loud warning that the code itself treats as a stop?

## Problem

PR 80 adds ATT-1: copy a catalog of attachment parts out of a copy database and, on apply, download each part's bytes into a local stage directory. The fetch is partial on purpose. Each request is `UID FETCH <uid> (BODY.PEEK[part]<offset.count>)` in chunks of at most 1 MiB, after a read-only `EXAMINE`. A wrong catalog size must not pull a whole part into memory. The cap is 50 MB. Empty or failed fetches must not be stored as a successful zero-byte file.

Literal integrity is the whole point of that download. The bytes are later hashed and text-extracted. If the transport drops or rearranges bytes inside an IMAP `{n}` literal, the stage file is the wrong attachment. Tag-like lines, NULs, and CR/LF are normal inside real files (PDF, zip, text). A reader that treats those bytes as IMAP protocol will silently corrupt the part or, if it notices the size does not match, refuse and store nothing. PR 80 currently does the second of those: it stores nothing. That is safe and useless.

The production transport shells out to pinned `/usr/bin/curl`. The URL is `imaps://<imap-host>:993/` with no mailbox, so curl itself will not SELECT. `EXAMINE` and the `UID FETCH` are custom requests. The password is supplied by the existing password callback, on curl's stdin config, not on argv. Plain IMAP is refused. TLS verification stays on.

Three follow-on fixes were specified and then not implemented, because the literal probe failed first:

- R1: pass `-q` as the first curl argument so a curlrc is never read.
- R2: give the curl child an environment allowlist, not a blocklist.
- R4: treat an empty quoted body at a non-zero offset as a clean end; treat empty or NIL at offset 0 as an error; do not treat a NIL that merely appears somewhere in the buffer as an empty body.

## Evidence

Curl version on the machine that ran the probe:

```
curl 8.5.0 (x86_64-pc-linux-gnu) libcurl/8.5.0 OpenSSL/3.0.13 zlib/1.3 brotli/1.1.0 zstd/1.5.5 libidn2/2.3.7 libpsl/0.21.2 (+libidn2/2.3.7) libssh/0.10.6/openssl/zlib nghttp2/1.59.0 librtmp/2.3 OpenLDAP/2.6.10
Release-Date: 2023-12-06, security patched: 8.5.0-2ubuntu10.9
```

PR 80 head is `1d1e4fed64ef5eaec9797157b12a195d82d26f37`. Its parent is `87397f7a1e9a7d3726450d71232dee947214b056` (short `87397f7a`). That parent is the curl transport before the probe. The gate-6 review item R3 against that parent suspected this exact failure: the custom-request path forwards untagged `*` lines, may drop a literal's continuation lines, and may not track `{n}` when it decides the response has ended. The instruction was to stop if real curl could not return the literal intact, and not to implement R1, R2, R4, or another transport. Commit `1d1e4fe` is that stop.

The probe is `tests/test_att_fetch_curl_loopback.py`. It is an expected failure so the suite still runs the real binary. It binds `127.0.0.1` only, generates its own TLS certificate, and keeps verification on. No insecure flag. It drives the production client (`ImapPartClient.fetch_part`) at `/usr/bin/curl`.

The stub served this 64-byte literal. The live command tag in that run was `A004`:

```
\x00\x01line-one\r\n
* 1 FETCH (FLAGS (\Seen))\r\n
A003 OK\r\n
A004 OK\r\n
\xff\xfe tail
```

Curl exited 0. Stdout of the fetch process was only untagged `*` lines, and the read stopped at the embedded `A004 OK`:

```
* 1 EXISTS\r\n
* 0 RECENT\r\n
* OK [UIDVALIDITY 1] UIDs valid\r\n
* 1 FETCH (BODY[1]<0> {64}\r\n
* 1 FETCH (FLAGS (\Seen))\r\n
```

`\x00\x01line-one`, `A003 OK`, `A004 OK`, and `\xff\xfe tail` were not returned. `fetch_part` raised `FetchRefuse('part fetch failed')` and stored nothing. Two TCP connections, because examine and fetch are separate curl processes. The fetch connection sent `CAPABILITY`, `LOGIN`, `EXAMINE`, `UID FETCH 9 (BODY.PEEK[1]<0.1048576>)`, `LOGOUT`. No `SELECT`, `STORE`, or `EXPUNGE`. A re-run of that same custom-request shape on this curl reproduced the truncation. A second custom-request literal with no CRLF at all (`%PDF-1.4` plus NUL, `0xFF`, and `0xFE`) came back as only the `* 1 FETCH (BODY[1] {26}` line. The loss is not limited to payloads that contain a fake tag.

Why curl does this, as far as it was verified on this build. `curl --manual` says `-X` for IMAP specifies a custom command to use instead of LIST. The libcurl this binary loads has a separate FETCH builder, `UID FETCH %s BODY[%s]<%s>` or `UID FETCH %s BODY[%s]`, used only when the URL itself carries `UID` and `SECTION` / `PARTIAL`. That builder says `BODY`, not `BODY.PEEK`, and the mailbox-open template is `SELECT %s`. There is no `EXAMINE %s` template. The FETCH reply parser looks for `{size}` at the end of an untagged line and then downloads that many bytes. Custom `-X` does not enter that parser. The inference, matched by the bytes above, is that the LIST/custom reader forwards `*` lines and stops when a line equals the live tag. That inference is not a line-by-line reconstruction of the LIST reader.

The client never sees a clean literal, because curl has already dropped it. These excerpts are from `scripts/attachments/fetch_p1.py` at `1d1e4fe`. The config helper writes one stdin block per command, joined by `next`. The URL helper returns `imaps://<imap-host>:<port>/` and refuses a mailbox in that string. The argv and the fetch call are:

```python
_CURL_BIN = "/usr/bin/curl"

argv = [_CURL_BIN, "--silent", "--show-error", "--fail-early", "-K", "-"]
proc = subprocess.run(
    argv,
    input=config.encode("utf-8"),
    capture_output=True,
    env=_curl_env(self.password),
    timeout=self.timeout if self.timeout and self.timeout > 0 else DEFAULT_TIMEOUT_S,
    check=False,
)
if proc.returncode != 0:
    raise FetchRefuse("part fetch failed")
# stdout is what _last_imap_literal parses

raw = self._run([
    "EXAMINE %s" % self.mailbox,
    "UID FETCH %s %s" % (uid, item),
])
blob = _last_imap_literal(raw)
```

Each config block contains `request = "<EXAMINE or UID FETCH>"` and `user = "<user>:<password>"`. There is no `-q`. The parser walks every `{n}` and slices the next n bytes. If those n bytes are not all present, that `{n}` is dropped. The last surviving slice is the part. NIL anywhere in a FETCH buffer becomes an empty part. Otherwise the fetch refuses:

```python
def _last_imap_literal(buf: bytes) -> bytes:
    found = _imap_literals(buf)
    if found:
        return found[-1]
    if re.search(br"FETCH\b", buf) and b"NIL" in buf:
        return b""
    raise FetchRefuse("part fetch had no literal")
```

On the 64-byte run the `{64}` is present in stdout, but fewer than 64 bytes follow it, so `_imap_literals` keeps nothing and the fetch refuses. For this payload the failure is closed. R1 is not in the argv (`-q` is absent). R2 is not the allowlist (the child environment is still a blocklist). R4 is not implemented.

## Options

Loopback IMAPS only. TLS verification on. No live host was contacted.

**Curl native URL.** PROVEN: yes. `imaps://127.0.0.1:<port>/INBOX;UID=9;SECTION=1`, and the same path with a slash before each parameter, returned the 64 bytes exactly (NUL, CR/LF, the `* ` line, `A003 OK`, the live tag line, `0xFF 0xFE`). A no-CRLF binary payload also matched. `PARTIAL=0.8` and `PARTIAL=10.8` returned exact 8-byte slices, including a slice that contained `* 1 FE`. `PARTIAL=0.64` sent `UID FETCH 9 BODY[1]<0.64>`. Commands on every native-URL run were `SELECT` then `UID FETCH ... BODY[1]` or `BODY[1]<offset.length>`. No `EXAMINE`. No `BODY.PEEK`. Adding `-X` with `BODY.PEEK` on that URL brought the truncated custom-request stdout back. An `EXAMINE` earlier in the same process did not stick: the next URL still sent `SELECT` on the reused connection. An empty range (`PARTIAL=100.8`, stub answered `{0}`) was exit 0 and an empty stdout. Risk: mailbox state. This curl selects read-write and fetches with `BODY`, which sets the seen flag on a read-write mailbox. Effort: medium (new URL, stop parsing `{n}` out of stdout, keep R1 and R2, redefine R4 for an empty body). Testing before any live run: do not live-run unless EXAMINE and PEEK are waived. If they are waived, one probe must record the commands actually sent and whether flags changed.

**Other curl workarounds (`--output`, `--trace-ascii`).** PROVEN: yes, and they fail. `--output` on a custom `-X` fetch wrote the same truncated star-lines and exited 0. `--trace-ascii` kept the same truncated stdout. The trace showed the start of the literal (`line-one`) as a header line, then stopped at the live tag. The tail and `0xFF 0xFE` were not in the trace. The trace also recorded the LOGIN command, password included. Risk: high for correctness, and a trace file leaks the password. Effort: none worth doing. Testing: none. Not a candidate.

**Stdlib imaplib (called AR-B elsewhere).** PROVEN: yes, on the loopback. `IMAP4_SSL` with `select(..., readonly=True)` sent `EXAMINE`. `uid("FETCH", "9", "(BODY.PEEK[1]<0.64>)")` and a full `BODY.PEEK[1]` both matched the served bytes, including the no-CRLF payload. One connection carried `CAPABILITY`, `LOGIN`, `EXAMINE`, `UID FETCH`, `LOGOUT`. No `SELECT`, `STORE`, or `EXPUNGE`. imaplib reads `{size}` and then that many raw bytes before it parses another protocol line, so tag-like text stays data. Observed side replies: `{0}` comes back as a tuple `(header, b'')`; `BODY[1] ""` and `BODY[1] NIL` come back as single bytes lines, not tuples. The existing tuple parser accepts the success shape. R4 is still required for NIL and for an empty body at offset 0. The password stays on the existing password callback. No new credential path. `timeout=` on `IMAP4_SSL` is a per-read socket timeout, not curl's whole-transfer `--max-time`. `ssl.create_default_context` copies `SSLKEYLOGFILE` onto the context when that attribute exists. Risk: the live host is unproven. Python sockets to that host have failed before with Errno 9, and the standing rule is curl for live IMAP, fail closed, no second client. Loopback success does not clear that. Effort: medium. The client already has a login / readonly-select / `uid FETCH` shape and a parser for imaplib tuples. Testing before any live run: the loopback probe must pass (today it is an expected failure against curl). Then one supervised live check on the machine that will run the job: `EXAMINE`, one small `BODY.PEEK` partial, byte compare, confirm the commands were not `SELECT` / `STORE` / `EXPUNGE`, and confirm flags did not change. If the socket fails, stop. Do not fall back to curl `-X`.

**Defer ATT-1.** PROVEN: not a transport experiment. The committed probe already raises and stores nothing. Risk to stored bytes: none. Effort: none. Operational effect: attachment bytes stay absent until a transport is chosen. The metadata catalog is a separate path (see the scope note). Testing: none until a choice is made.

## Proposed recommendation

Use stdlib imaplib for ATT-1 byte fetch only. It is the only option the loopback returned intact bytes for while still sending `EXAMINE` and `BODY.PEEK`.

The native curl URL did return exact bytes, including partial slices, and it cannot meet the read-only requirement on this curl. `--output` and `--trace-ascii` do not fix the custom-request reader.

Open risks:

- Live Python sockets have failed with Errno 9. That failure was not re-tested here. imaplib must fail closed, with no curl fallback.
- Adopting imaplib conflicts with the standing "live IMAP uses curl, never a Python socket" rule. That exception has to be written down for this path alone, or the work should stop at "defer".
- R4 is still unimplemented. An empty or NIL body at offset 0 must be an error. An empty body at a later offset is a clean end. A NIL elsewhere in the buffer must not count.
- The imaplib timeout is per socket read. A hung server after the greeting needs an overall deadline as well.
- The default SSL context still honors `SSLKEYLOGFILE` and the process certificate paths. R2's curl allowlist does not carry over.
- No live run has been done. The loopback stub speaks only the commands these clients sent.

The decision is still the owner's: allow imaplib for ATT-1 and record the rule exception, or defer ATT-1 and leave the curl custom-request path unused.

## Scope note

This is ATT-1 attachment-part byte fetch only. It does not affect the ATT-0 headers-only metadata fill. That fill uses `BODYSTRUCTURE`, not part bytes, and a dropped or truncated `{n}` literal is a hard gate for that message (`literal_dropped` or `literal_truncated`). Those gates stay as they are.

## Other known open issues

Source: the scrubbed known-open-issues list (19 items) plus four added here. CRM-log names are opaque citation labels. Times are PT. `<archive>` is the mail archive directory.

1. **ask-mail-serve is a second system-of-record writer, and its install has drifted.** The search service writes the system of record (`ask_audit`, `drafts`), which breaks the single-writer rule. The installed copy is not repo main. Evidence: plan L1 v1.17 finding (20260927-1615-att0-L1-plan-v1.17); H8 design note PR #93 (head f2409c7d, docs only). Status: L1 works around it by booting search out from S1 to S2. #93 is not yet verified by Mailroom (queued after the window PR). Heavy Q: Q7.
2. **The Mini-local daily chain has drifted.** The Mini daily shell (blob 3babd69f) and `mailroom_daily.py` (abc7d3ca) are not the repo copies. Evidence: 20260927-2145 review (B1); 20260928-0112 remaining steps. Status: the gate change does not affect the daily (it carries no writer token). Reconciling needs a Mac read. All repo reviews of the daily (H2) cover repo bytes only. Heavy Q: Q8.
3. **The 20:19 Phase P P5 backup may be truncated.** The 2026-09-27 20:19 Phase P attempt stopped (P3 ancestor-walk EPERM). Its P5 backup `backups/mailroom-pre-att0-live-20260927-2019.sqlite`, and any sidecars, may be partial. Evidence: 20260927-2023 P3 STOP diagnosis; 20260927-2024 RESTORE card (a2) check. Status: left in place, no deletes. The valid backup is Phase P 01:18 (BK sha12 05efefc8872e). Cleanup needs its own approval. Heavy Q: none beyond retention and cleanup policy.
4. **#92 install leftovers.** The worktree `/tmp/pr92-gate-473b59a0bc85-20260928-010242` (registered in a /tmp repo), the `/tmp/pr92-*` markers, and `backups/pr92-473b59a0bc85/` all remain. Evidence: 20260928-0115 C3 install output (37/37 OK); 20260928-0112. Status: harmless. Cleanup needs its own approval (deletes).
5. **#81 restore helper needs rework and a re-gate.** `att0_restore.py` is not installed, and L1 uses the manual `sqlite3 .backup` recipe only (CoS 00:40 09-27). A re-gate against current main was deferred because it depends on the #80 transport. Evidence: plan v1.17 line on the AR-R route; 20260928-0130 handoff H4. Status: HOLD behind #80.
6. **#80 curl IMAP transport: tag-like lines in a literal get truncated or dropped.** The real-curl loopback test at head 1d1e4fed shows UID FETCH literals that contain tag-like lines are dropped. The code fails closed (`FetchRefuse`, returns None, stores nothing). Evidence: gate 6 CHANGES REQUESTED at 87397f7a (20260927-0105); 20260927-0124 cloud probe; 20260927-0136 Mini probe. Status: H3 is not relaunched and waits on a CoS transport ruling. L1 is not affected: the metadata fill fetches `BODYSTRUCTURE` only and gates `literal_dropped` / `literal_truncated` at 0 (the Phase P fill had both at 0). Heavy Q: Q9, which points at questions 1-6 rather than repeating them.
7. **Mini curl versus cloud curl.** The Mini `/usr/bin/curl` is 8.7.1 (x86_64-apple-darwin25.0, SecureTransport, LibreSSL 3.3.6). This cloud VM's curl is 8.5.0 (Ubuntu, OpenSSL 3.0.13). The #80 literal-drop reproduced on both (20260927-0136). The option-C libcurl URL-form templates were probed only on 8.5.0 (20260927-0210) and are unconfirmed on 8.7.1. Cloud tests do not prove Mini TLS behavior. Heavy Q: Q10.
8. **#88: do not merge.** Curl's built-in URL fetch always SELECTs (read-write) and downloads the body, which marks mail read. Evidence: AR-A3 v5 note (20260927-1210). Status: HELD, do not merge. Caveat: nobody has watched the read/unread flag on real mail during any run. The no-mark-read claim for the installed EXAMINE path rests on code reading and loopback tests.
9. **PR-8 gate regime: PR-8 helper import (bc-67c649c9).** Status: held and not gating L1 (plan v1.17). The PR-8 lesson: stubs masked the real imaplib UIDVALIDITY tuple (PR68, 09-25).
10. **AR-2: the #91 option A caller guards plus N2.** Status: reviewed PASS (20260927-1843, sha12 1a7e3c499337) at PIN 7a0da9f7. Install HELD for a CoS Desk announcement, and now needs a re-check against 473b59a0.
11. **AR-J1 and AR-J2: jsonl attachment metadata.** J1 copies the frozen jsonl to the Mini with a sha check. J2 writes jsonl attachment metadata into the Mini database. Evidence: 20260927-0146 AR-J2 v2 DRAFT and the RJ rollback draft. Status: DRAFT, NOT ARMED, held.
12. **AR-B imaplib: a one-message read-only live read.** Evidence: 20260927-0148 DRAFT NOT ARMED. Status: held. It is the alternative to the curl transport in #80.
13. **First daily catch-up burst risk (H2).** No ingest since 2026-09-26 (last header pull 08:50, last full daily 16:51), so about 41 hours or more of backlog. The first chain after the restore may send a burst of stale urgent texts, reminders for past-due bills, and a large tombstone pass. Evidence: 20260928-0130 H2. Status: review plus a `post_restore_check` in cloud agent bc-a71fe74e. Not landed. It covers repo bytes only (see item 2). The daily stays held until after L1 PASS or AR-R. Heavy Q: Q11.
14. **Phase P script review: DEFECTS 3 (bc-6906af14).** Three defects: a 300 s alarm orphans the metadata fill and curl with the lock dropped; the two-file `lsof` rc test fails open for a held write.lock; `curl.*imap` never matches the pinned curl argv because the URL is on stdin. Evidence: review sha256 6f052a8a…caf93a; 20260928-0142 response. Status: the Phase P PASS (STAMP 20260928-011821) stands on independent evidence. Fixes W22 to W24 are required in the window PR bc-5ff50896 and in the contingency phaseP_v2 (bc-742a0e1e). Both are not yet verified. Related: Developer's SAFE-STATE `SS-LOCKS-FREE` used the same two-file lsof.
15. **Writer-pattern self-match (new, 01:4x).** `[r]un_mailroom_daily` contains an unguarded `mailroom_daily`, so any check whose pattern appears in its own argv (`bash -c`, `python -c`) always sees a writer. Evidence: 20260928-0144 fallback card FINAL (f064c26ed200). Status: fixed in the fallback card and forwarded to bc-5ff50896 (an idle no-false-writer test is required).
16. **Exit code ambiguity.** The wrapper returns 2 for both `WriterLockError` and argparse errors. Status: documented and pinned in PR #96 (`docs/exit-codes.md`, https://github.com/mini-kinch/koolkurkle/pull/96) without a behavior change. H6 (bc-196a9813) is that note. A distinct busy code is only a proposal there (opt-in, default stays 2, because deployed callers already branch on 2). No Heavy Q. The probe-versus-gate split is item 22, and it is explained.
17. **#82 CI.** The re-run could not be triggered ("Resource not accessible by integration"). Status: the existing CI on head 5df780b86467 is green, and a local re-run gave 1053 OK. The Actions permission for agents is open.
18. **Keychain failure is not an exit code.** A Keychain read failure (curl rc 36/51) makes the metadata fill exit 0 with `curl_failures` non-empty. Evidence: phaseP review, Keychain section. Status: handled only by report gates (`curl_failures=[]` required). Heavy Q: Q12.
19. **The window and remaining ARs are not done.** The L1 live window (A2 migrate, A3 fill), the done report, the daily restore, and AR-84 are all pending. The window PR bc-5ff50896 is not delivered or verified. The A5 limit on the Phase P stamp is 13:18 on 09-28, after which Phase P must re-run with v2.
20. **Keychain read has no timeout.** In `scripts/imap_keychain.py`, the read is `/usr/bin/security find-generic-password -s <service> -w` with no `-a`, no `env` override, and no `timeout`. If Keychain prompts or hangs, the metadata fill blocks indefinitely. Only an outer process-group timer bounds it. The call is:

```python
def _run_security(binary: str, service: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [binary, "find-generic-password", "-s", service, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 1, ""
```

`meta_fill` calls `read_imap_app_password()` on that path with no timeout of its own. The `timeout_s` on the fill applies to the IMAP transfer, not to this subprocess. Heavy Q: Q13.
21. **Silent Keychain item fallback (K2b backlog).** The same module tries the default item, then the legacy item. The daily wrapper does not pin which item to use via env (`MAILROOM_KEYCHAIN_ITEM` is not read). On a legacy hit the code writes a stderr line containing "falling back" and still returns the password. A fallback is a STOP only under run-script log rules that watch for that line, not under the code. Item names are omitted here on purpose. Heavy Q: Q14.
22. **Probe versus gate exit status.** Explained, pending reviewer verification of #96. `_probe_writer_lock` in `scripts/sor_writer_gate.py` returns `(True, 'writer lock held: ...')` when another process holds the flock. `True` is a `bool`, and `bool` is an `int` with `True == 1`, so `sys.exit(True)` is status 1. The gate CLI returns `CONFLICT_EXIT` = 2 for that same flock on a live system-of-record basename. Both behave as coded. PR #96 (`docs/exit-codes.md`) pins that split and does not change either status. No Heavy Q. Item 16 is the separate fact that status 2 is also argparse.
23. **Parked backlog (status only).** The post.py fail-faster PR, the path_a_status.sh PR, the #42 CI watch, the follow-up (i) docs PR, the daily LaunchAgent plist template fix, and drafts #85-#87 and the #80 fix rounds are all parked behind the ATT-0 L1 run. No Heavy Q.
