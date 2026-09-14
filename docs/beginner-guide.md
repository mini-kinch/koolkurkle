# Mailroom — beginner guide (GitHub)

**Status:** docs for README / repo About  
**Audience:** someone new to this repo  
**PII:** ZERO — no personal addresses, phone numbers, login names, or message contents  
**Machines:** **MBP** (MacBook Pro) and **Mini** (Mac mini) only  

Repo: https://github.com/mini-kinch/koolkurkle

---

## About (short — paste into GitHub “About” / README lead)

**koolkurkle** is a **local** personal-mail search and assistant toolkit. It indexes your **iCloud / Apple Mail** mailbox on your own Macs, then lets you ask questions with **citations** back to real messages. It does **not** replace Apple Mail, does **not** change your email address, and does **not** send mail for you (drafts only).

---

## What it does (plain language)

Think of three layers:

1. **Your real mailbox** — still Apple Mail / iCloud. That is the human inbox.
2. **A local library** — a SQLite database on the Mac that stores headers, searchable text, and (when ready) meaning-based “embeddings” so you can find mail by topic, not only exact keywords.
3. **Ask tools** — command-line and a small local web UI (`ask_mail`) that search that library and can draft replies **without sending**.

### What it is good for

- “Find that bill / apology / conversation about X” with links back to the message
- Keeping a **history** of mail even after you delete it in Apple Mail (soft-delete / never-purge design)
- Running a daily refresh on the Mini without putting your live database at risk (copy-only until a future cutover)

### What it is *not*

- Not Gmail/Outlook as the source of truth
- Not a cloud email host or a new MX record
- Not an auto-send bot (no silent sending)
- Not something that stores secrets in git (passwords stay in **macOS Keychain**; docs only name the Keychain **item name**, never the secret)

---

## The two Macs (remember these names)

| Machine | Role today |
|---------|------------|
| **MBP** | Holds the live **Source of Record** database (`mailroom.sqlite`) |
| **Mini** | Runs daily jobs against a **copy** of that database until a future cutover (PR-5). It must not write the live SoR |

If a recipe says Mini and points at `mailroom.sqlite`, that is usually wrong until cutover — Mini should use a **copy** database path.

---

## How mail gets into the library (high level)

1. **Headers / new mail** — pull from iCloud over IMAP (via system `curl`, not random socket hacks).
2. **Bodies** — fetch message text for search (Mini prefers Homebrew `curl` for large body downloads).
3. **Tombstones** — if a message disappears from the server, mark it gone locally (`present_on_server=0`) but **keep** the row. Never “empty trash” or expunge iCloud from these tools.
4. **Classify / bills** — sort into lanes (auth, money, people, …) and track bills in a local table.
5. **Embed** — optional meaning-based index with a **local** embedding model (Ollama). **One writer at a time** per database file (HARD DECK).

A long “rem-legacy” embed backfill may run for a while on the MBP. While it holds the live database lock, do not start a second embed writer on that same file.

---

## How to *use* it (day to day)

### A. Search / ask (most people start here)

On a Mac with the project and a Python venv already set up:

**Mini (copy database until cutover):**

```zsh
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/ask_mail.py 'your question here'
```

**MBP (live SoR):**

```zsh
$HOME/MailArchive/.venv/bin/python scripts/ask_mail.py 'your question here'
```

Useful ideas:

- Default search is **history** (includes mail no longer on the server).
- Add `--live` if you only want mail still present on iCloud right now.
- `--json` for machine-readable output.
- Local UI (when served): `http://127.0.0.1:8743/ui` (generate answers need a local generate server on `127.0.0.1:1234` when configured).

More recipes: [docs/ask_mail.md](ask_mail.md)

### B. Hybrid search only (no chat answer)

```zsh
# Mini example — copy DB
MAILROOM_DB=$HOME/MailArchive/mailroom-copy.sqlite \
  $HOME/MailArchive/.venv/bin/python scripts/semantic_search.py 'invoice'
```

### C. Health check (read-only)

```zsh
$HOME/MailArchive/.venv/bin/python $HOME/MailArchive/scripts/sor_health_pack.py
```

Safe to run on Mini against a copy. See [docs/sor-health.md](sor-health.md).

### D. Daily automation (Mini)

A LaunchAgent can run the daily chain (new mail → bodies → classify/bills → light embed).  
Setup notes: [scripts/README.mailroom-daily.md](../scripts/README.mailroom-daily.md)

---

## Safety rails (please read once)

- **Never physically delete** iCloud mail from these scripts (no EXPUNGE / empty-trash tooling).
- **Auth / 2FA mail** stays out of Junk/Trash; codes are never texted by the assistant.
- **Secrets** live in Keychain. Docs may mention the Keychain **name** `mailroom.imap.app-password` — never commit the password.
- **One embed writer** per SQLite file.
- **GitHub** must stay free of personal data (no real subjects, addresses, phone numbers, or chat handles in commits/tests).

Deeper design (still educational):

- Soft-delete: [docs/MAILROOM.md](MAILROOM.md), [docs/soft-delete.md](soft-delete.md), [docs/tombstone.md](tombstone.md)
- Embeds: [docs/embed-backfill.md](embed-backfill.md)
- Attachments / unified search: **design docs only** for now — [docs/att0-constraints.md](att0-constraints.md), [docs/unified-search-design.md](unified-search-design.md)

---

## Related docs

Operator HARD DECKs and advanced recipes stay in [README.md](../README.md) and [docs/ops-terminal.md](ops-terminal.md). This guide does not change rem-legacy, SoR writers, IMAP, or Keychain.

---

*End. ZERO PII. Rem untouched by this document.*
