# PR review gates: Developer self-gate (PR-8) and Mailroom review

Status: CANONICAL. Written by Ops on CoS order 2026-09-26 4:12 PM PT.

Why: PR #68 merged with three review misses (TLS verification, UIDVALIDITY
response shape, IMAP transport). `meta_fill.py` used Python `imaplib`, but
`docs/ops-terminal.md` §"IMAP live checks via curl imaps (never Python
sockets)" (lines 129-155 on main; next heading at 157) allows
`/usr/bin/curl imaps://` only.
"Merges are always approved unless CoS has real choices to offer." so review
is the only merge gate. It has to catch contract breaks.

## 1. When the check applies (touchpoints)

A PR has a touchpoint if its diff adds or changes code that does any of these:

- Network: any socket, `imaplib`, `smtplib`, `ssl`, `http`/`urllib`/`requests`,
  or a `curl` subprocess, including localhost model servers (`:1234`, Ollama,
  `mlx_lm.server`).
- Keychain: any `security` CLI call or anything else that reads, writes, or
  names a Keychain item.
- SoR: anything that opens or writes `mailroom.sqlite` or a copy DB, chooses
  `db_mode`, or takes or skips the writer lock.

A docs-only or tests-only PR still has a touchpoint if it changes what these
paths are allowed to do.

## 2. Developer self-gate: PR-8 gate 6 "ops contract"

The existing gates still apply: interface proof on the target Mac (py3.9),
negative smoke, `ask_mail.py` not a stub, PII grep clean, and the Ready label.
Gate 6 is added for any PR with a touchpoint:

1. Cite. The PR body has an `Ops contract` block with one line per touchpoint:
   `file:line | touchpoint | ops-terminal.md §heading (lines on main) | how the code complies`
   Example: `path/to/module.py:NN | IMAP fetch | §IMAP live checks via curl imaps (lines 129-155) | subprocess /usr/bin/curl imaps://, no Python socket`
2. No fitting rule means stop. If no ops-terminal.md section covers the
   touchpoint, or the section forbids what the code does, the PR is not Ready.
   Developer routes it to CoS as a rule question; CoS posts the user approval
   request (AR) on Desk. A new transport or a rule change takes that route.
3. Real transport shape. At least one test per touchpoint exercises the real
   transport shape: the same client the code ships with (the real `curl` argv,
   or the real library against a local fake server), with response fixtures
   recorded from that real client and PII-scrubbed, never hand-typed. A
   hand-written stub alone does not pass. Where the old code was wrong,
   include a fail-on-old-code proof: the new test fails against the old code
   and passes against the new.
4. Fail closed. The test covers the failure path the section names (for
   example, no curl or a TLS error means refuse, with no retry on another
   client).
5. Report. Developer merge report to CoS (Jumpseat CC'd): `PT | PR# | merge sha | gates 1-6 quoted`.

## 3. Mailroom review checklist (PRs with a touchpoint)

Mailroom reviews every PR with a mail, Keychain, or SoR touchpoint before
merge. It checks the code, not just the PR body.

1. The `Ops contract` block exists and lists every touchpoint in the diff.
   The reviewer greps the diff for `socket`, `imaplib`, `ssl`, `urllib`,
   `requests`, `curl`, `security `, `sqlite3.connect`, `db_mode`, and
   `writer_lock`. Any hit missing from the block is a FAIL.
2. For each line, the reviewer opens the cited ops-terminal.md section on
   main and checks that the code does what it says. The transport, binary
   path, Keychain item name (name only), `db_mode` refusal, and writer lock
   must match.
3. The real-transport-shape test exists, runs (not skipped), and uses
   recorded fixtures or a real client against a fake server.
4. The failure path fails closed as the section says.
5. Mailroom posts to CoS (Jumpseat CC'd): `PT | PR# | head sha | Mailroom review PASS or FAIL | touchpoints checked | sections cited`.

Merge order for touchpoint PRs: developer gates 1-6 PASS, then Mailroom PASS
on the same head sha, then merge. A new commit resets Mailroom PASS.

## 4. Not changed

A merge is still never an install. Every install, restart, plist load, or
`:1234` contact stays a separate approval. Developer routes it to CoS; CoS
posts the user approval request (AR) on Desk. PRs without a touchpoint follow
the existing gates only.
