# Mailroom — beginner guide

**ask_mail with citations is the product.** Vectors, FTS, and IMAP are infrastructure.

**Audience:** someone new to this repo  
**PII:** ZERO — no personal addresses, phone numbers, login names, or message contents  
**Machines:** **MBP** (MacBook Pro) and **Mini** (Mac mini) only  

Repo: https://github.com/mini-kinch/koolkurkle

---

## About (short)

**koolkurkle** is a **local** personal-mail search and assistant toolkit. It indexes your **iCloud / Apple Mail** mailbox on your own Macs, then lets you ask questions with **citations** back to real messages. It does **not** replace Apple Mail, does **not** change your email address, and does **not** send mail for you (drafts only).

---

## What it does

Three layers:

1. **Your real mailbox** — Apple Mail / iCloud. That is the human inbox.
2. **A local library** — a SQLite database on the Mac: headers, searchable text, and meaning-based embeddings so you can find mail by topic, not only exact keywords.
3. **Ask tools** — command line and a small local web UI (`ask_mail`) that search that library and can draft replies **without sending**.

### Good for

- “Find that bill / apology / conversation about X” with citations back to the message
- Keeping **history** after you delete mail in Apple Mail (soft-delete / never-purge)
- Daily refresh on the Mini without putting the live database at risk (copy-only until a future cutover)

### Not

- Not Gmail or Outlook as the source of truth
- Not a cloud host or a new MX record
- Not an auto-send bot
- Not a place that stores secrets in git (passwords stay in **macOS Keychain**; docs name the Keychain **item**, never the secret)
- Not attachment search, iMessage search, or Notes search — those are **design-only**

---

## The two Macs

| Machine | Role today |
|---|---|
| **MBP** | Holds the live **Source of Record** (`mailroom.sqlite`). A long rem-legacy embed backfill may be running here. |
| **Mini** | Daily jobs against a **copy** of that database until a future cutover (PR-5). Must not write the live SoR. |

If a recipe says Mini and points at `mailroom.sqlite`, it is wrong until cutover. Mini uses a **copy** path.

---

## How mail gets into the library

1. **Headers / new mail** — iCloud IMAP via system `curl` (not Python sockets).
2. **Bodies** — fetch text for search (Mini prefers Homebrew `curl` for large bodies).
3. **Tombstones** — if the server no longer lists a message, mark `present_on_server=0` and **keep** the row. These tools never empty trash or EXPUNGE iCloud.
4. **Classify / bills** — lanes (auth, money, people, …) and a local bills table.
5. **Embed** — local `qwen3-embedding:8b` (store 1024-d). **One writer at a time** per database file.

Body embeddings exist. A rem-legacy backfill on the MBP is filling the older long-body band. While that job holds the live-database lock, do not start a second embed writer on the same file. Attachment / iMessage / Notes embeddings are not shipped.

---

## Day to day

### A. Search / ask

**Mini (copy database until cutover):**

```zsh
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/ask_mail.py 'your question here'
```

**MBP (live SoR):**

```zsh
$HOME/MailArchive/.venv/bin/python scripts/ask_mail.py 'your question here'
```

- Default search is **history** (includes mail no longer on the server).
- `--live` limits to mail still on iCloud right now (a filter, not a new IMAP fetch).
- `--json` for machine-readable output.
- Local UI (when served): `http://127.0.0.1:8743/ui`  
  Generate answers need `mlx_lm.server` on `127.0.0.1:1234`.

More recipes: [ask_mail.md](ask_mail.md)

### B. Hybrid search only (no chat answer)

```zsh
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py 'invoice'
```

Retrieve is one ranked list (FTS ∪ vectors). Not two separate products.

### C. Health check (read-only)

```zsh
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/sor_health_pack.py
```

Safe on Mini against a copy. See [sor-health.md](sor-health.md).

### D. Daily automation (Mini)

LaunchAgent runs new mail → bodies → classify/bills → light embed on the **copy**.  
Setup: [../scripts/README.mailroom-daily.md](../scripts/README.mailroom-daily.md)

---

## Safety rails

- **Never physically delete** iCloud mail from these scripts (no EXPUNGE / empty-trash).
- **Auth / 2FA mail** stays out of Junk/Trash. The assistant does not text codes.
- **Secrets** stay in Keychain. Docs may name `mailroom.imap.app-password`. Never commit the password.
- **One embed writer** per SQLite file.
- **GitHub** stays free of personal data (no real subjects, addresses, phones, or chat handles).

Deeper design:

- Soft-delete: [MAILROOM.md](MAILROOM.md), [soft-delete.md](soft-delete.md), [tombstone.md](tombstone.md)
- Embeds: [embed-backfill.md](embed-backfill.md)
- Attachments / unified search: **design only** — [att0-constraints.md](att0-constraints.md), [unified-search-design.md](unified-search-design.md)

---

## README wiring

1. Put the **About (short)** paragraph at the top of `README.md` and in the GitHub About field.
2. Link this file as “Beginner guide.”
3. Keep operator HARD DECKs **below** the lede, or in [ops-terminal.md](ops-terminal.md). Do not lead the README with crew-ops rules.

---

*End guide. ZERO PII. Rem untouched.*
