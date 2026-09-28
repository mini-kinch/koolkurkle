# ask-mail-serve as a second SoR writer

DESIGN ONLY. This note does not change `scripts/ask_mail.py` or any other code.

Repo copy only; the installed copy may have drifted from main.

Subject: the loopback search server, `scripts/ask_mail.py --serve`, LaunchAgent label `com.mailroom.ask-mail-serve`. The same write functions are also reached from the one-shot CLI and from `--mcp`. A later code change in those functions covers all three. The process a maintenance window stops today is the LaunchAgent.

The writer lock is an advisory `flock` on `$HOME/MailArchive/mailroom.write.lock` (`MAILROOM_WRITE_LOCK`). It is not a SQLite reserved lock. Holding it does not by itself make SQLite return `SQLITE_BUSY`. Search never takes it, so a window that holds it does not, by that fact alone, stop search from opening the SoR and inserting.

## 1. Current state

### 1.1 Process

`main` sends `--serve` to `serve_http` (`scripts/ask_mail.py:1912-1913`, flag at `1810-1813`). `serve_http` binds `ThreadingHTTPServer` (`1461-1509`, import at `47`). Each request runs on its own thread. The server object does not keep a SQLite connection. Every request that touches the database opens a connection and closes it before the handler returns.

The checked-in agent template is `launchd/com.mailroom.ask-mail-serve.plist.template`. Its arguments are `--serve --db __HOME__/MailArchive/mailroom.sqlite --fts-only --no-generate --host 127.0.0.1 --port 8743` (lines 15-27). `RunAtLoad` and `KeepAlive` are both true (lines 32-35). `docs/search-resume-watchdog.md:16-17` says not to add `KeepAlive` on this label because it fights S1. This note does not edit the template or the watchdog doc. `--fts-only` and `--no-generate` do not disable the write paths below: a hits-only ask still inserts `ask_audit`, and `POST /draft_reply` is still registered.

Default database path, when `--db` is absent, is `$MAILROOM_DB` or `$HOME/MailArchive/mailroom.sqlite` (`scripts/ask_mail.py:148-152`). `_cli_config` does not set an `audit` key (`1860-1888`). `_dispatch_ask` and `_dispatch_hybrid` therefore pass `audit=True` (`1430`, `1457`). `ask` itself defaults `audit=True` (`826`). There is no CLI flag that turns audit off.

### 1.2 How a connection is opened

Two open paths.

`connect` (`scripts/ask_mail.py:193-202`) calls `sqlite3.connect(str(db))` with no `timeout`, no `isolation_level`, and no `autocommit`. On CPython that is `timeout=5.0` (a `sqlite3_busy_timeout` of 5000 ms) and legacy transaction control with `isolation_level=""`, which issues `BEGIN` before a DML statement. SQLite treats that `BEGIN` as deferred. The file contains no `BEGIN IMMEDIATE` and no `BEGIN EXCLUSIVE`. `check_same_thread` stays at the module default, true. That is safe only because each thread opens its own connection.

`connect` then calls `apply_reader_pragmas` and swallows any exception (`198-201`). Those pragmas (`scripts/sqlite_pragmas.py:60-75`):

| Order | Statement | Effect on this connection |
|---|---|---|
| 1 | `PRAGMA journal_mode=WAL` | Runs before `busy_timeout` is raised. If the file is already WAL, SQLite reports the current mode. If it is not, this changes the journal mode, which is a write. |
| 2 | `PRAGMA busy_timeout=30000` | Overrides the 5 s connect default. `BUSY_TIMEOUT_MS` is 30000 (`sqlite_pragmas.py:28`). |
| 3 | `PRAGMA foreign_keys=ON` | No foreign keys are declared on `ask_audit` or `drafts`. |
| 4 | `PRAGMA mmap_size=0` | |
| 5 | `PRAGMA temp_store=MEMORY` | |
| 6 | `PRAGMA synchronous=NORMAL` | Reader set. Writer set is `synchronous=FULL` (`68-70`). |

If `apply_reader_pragmas` raises, the connection keeps the 5 s busy timeout and the SQLite defaults (journal mode unchanged, `synchronous` left at the engine default FULL).

`write_ask_audit` does not call `connect`. It calls `sqlite3.connect(str(db))` itself (`626`) and never applies pragmas. Its busy timeout stays at the 5 s connect default. It does not set `journal_mode` or `synchronous`. The module docstring states the contract: "ask_mail may call apply_reader_pragmas; it does not take the writer lock" (`sqlite_pragmas.py:13`).

Retrieve uses a third open. `semantic_search.retrieve` calls `embed_lib.connect_db` (`scripts/semantic_search.py:1117`), which is `sqlite3.connect(str(path))` plus `sqlite-vec` load and no pragmas (`scripts/embed_lib.py:556-561`). On failure it falls back to the same `sqlite3.connect` (`semantic_search.py:1118-1120`). The connection closes in the `finally` when retrieve owns it (`1319-1321`). Statements on that path are `SELECT` and `PRAGMA table_info`. No `INSERT`, `UPDATE`, `DELETE`, or `CREATE`.

### 1.3 Write paths reached from `--serve`

| HTTP | Function | Table or file | Statements | Transaction | Writer lock | busy_timeout |
|---|---|---|---|---|---|---|
| `GET /ask`, `POST /ask` | `ask` → `write_ask_audit` | `ask_audit` | `CREATE TABLE IF NOT EXISTS`, then `INSERT`, then `commit` | Deferred `BEGIN` around the `INSERT` only. DDL is outside that transaction. | Not taken | 5000 ms (connect default). `PRAGMA busy_timeout` is not executed. |
| `POST /hybrid_search` | `hybrid_search` → `ask` → `write_ask_audit` | `ask_audit` | Same | Same | Not taken | 5000 ms |
| `POST /draft_reply` | `draft_reply` | `drafts`, plus a text file | `CREATE TABLE IF NOT EXISTS`, file write, `INSERT`, `commit` | Deferred `BEGIN` around the `INSERT`. Reader pragmas already applied, including `synchronous=NORMAL`. | Not taken | 30000 ms after pragmas succeed; 5000 ms if they throw |
| `GET /message`, `POST /get_thread`, and the generate-data load inside `ask` | `connect` | none, unless `journal_mode=WAL` changes the mode | `PRAGMA journal_mode=WAL` then reads | Autocommit pragmas, then `SELECT` | Not taken | 5000 ms for the journal-mode pragma (it runs first); 30000 ms after that |

`GET /`, `GET /health`, and `GET /ui` do not open SQLite (`1280-1304`).

#### `ask_audit`

`ensure_ask_audit` (`582-595`):

```sql
CREATE TABLE IF NOT EXISTS ask_audit (
  ts TEXT NOT NULL,
  actor TEXT,
  query TEXT,
  k INTEGER,
  hit_count INTEGER,
  hit_ids TEXT,
  detail TEXT
)
```

No primary key. The same DDL is in `scripts/migrate_pr1_schema.py:122-133`. `CREATE TABLE IF NOT EXISTS` does not alter a table that already exists. On a migrated SoR this statement should not change the schema. It still executes on every audit write. When the table is missing it is a schema write under SQLite's own autocommit. The Python module does not wrap DDL in the deferred `BEGIN`.

`write_ask_audit` (`613-656`) inserts `ts`, `actor` (`MAILROOM_ACTOR` or `ask_mail`, `169-171`), `query`, `k`, `hit_count`, `hit_ids` (comma-joined citation ids), and a JSON `detail` of `model`, `host`, `generate_mode`, `rerank_mode`, and `runtime`. The docstring and the module header say bodies, snippets, and subjects are not stored (`7`, `624`). `tests/test_ask_mail.py` `test_ask_audit_has_no_bodies` covers that on a temp database.

Call sites inside `ask`, all gated only by `audit`:

- hits-only, including `--no-generate` (`865-875`)
- generate requested but the model tag is missing (`891-901`)
- after generate success or fail-open (`952-962`)

The `INSERT` is the DML that starts the deferred transaction. The reserved lock is taken at that `INSERT`, then `conn.commit()` (`652`) releases it. There is no `rollback()` call. On any exception, including `SQLITE_BUSY` / `OperationalError`, the function writes `warning: ask_audit write failed (no bodies stored)` to stderr and returns (`655-656`). The ask result is still returned, and the handler still answers HTTP 200. The row is gone. Nothing is queued.

`ask` closes the generate-data connection (`906-913`) before `generate_answer` (`917-928`). The generate HTTP wait does not hold a SQLite fd. The audit connection is a later, separate open. Retrieve has already closed its connection. A single `/ask` holds one SQLite connection at a time. Concurrent requests hold one each.

#### `drafts`

`ensure_drafts` (`598-610`) matches `docs/pr0/mailroom_schema.sql:52`:

```sql
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  text TEXT NOT NULL,
  path TEXT NOT NULL,
  status TEXT DEFAULT 'pending'
)
```

`draft_reply` (`1152-1199`), from `POST /draft_reply` (`1367-1374`):

1. `connect(path)` on the SoR (reader pragmas, including `journal_mode=WAL` and `synchronous=NORMAL`).
2. `load_mail_data` (`SELECT`). Missing id raises `AskMailError` before any write.
3. `ensure_drafts`.
4. `folder.mkdir` and `dest.write_text` under `MAILROOM_DRAFTS` or `$HOME/MailArchive/drafts` (`182-186`, `1174-1180`). This file write happens before the `INSERT`.
5. `INSERT INTO drafts(...) VALUES (..., 'pending')` and `conn.commit()` (`1182-1187`).

`send=true` raises `AskMailError` before a connection (`1161-1162`). The handler turns `AskMailError` into HTTP 400 JSON (`1385-1395`). `sqlite3.OperationalError` is not an `AskMailError`. It leaves `do_POST`, and `ThreadingHTTPServer` logs it from the request thread. The client does not get that JSON error object. A busy draft can sit inside `busy_timeout` for 30 s on the request thread, then fail that way.

If the `INSERT` or `commit` fails after `write_text`, the text file remains and the row does not. `conn.close()` in the `finally` (`1198-1199`) rolls back an uncommitted deferred transaction.

#### Read paths that can still write

`get_message` (`1096`, close at `1118`), `get_thread` (`987`, close at `1052-1053`), and the generate-data block (`906-913`) call `connect`. Their SQL after the pragmas is `SELECT`. The write risk on these paths is `PRAGMA journal_mode=WAL` when the file is not already WAL, and the shared lock they take while the fd is open.

### 1.4 Writer lock, as the code stands

`scripts/with_writer_lock.py` is the sole-writer wrapper. `acquire_writer_lock` (`200-234`) opens the lock file and takes `fcntl.LOCK_EX | fcntl.LOCK_NB`. If that raises `BlockingIOError`, it refuses. Age over 4 hours is also a refuse, and it does not steal (`49`, `220-225`). On success it overwrites the file with pid, hostname, purpose, timestamp, and `writer_token` (`181-197`). `run_with_lock` (`280-316`) refuses when `$HOME/MailArchive/ACTION_REQUIRED` exists (`289-293`), may drop the search-resume `+26` deadline, puts the token in the child environment, and holds the flock until the child exits. The module says ask_mail does not use this (`13`, `329`).

`docs/pr0/with_writer_lock_DESIGN.md:9` and the table at line 18 say the same thing, and they already list the short `ask_audit` / draft `INSERT` as a writer that does not take the lock. `docs/MAILROOM.md:69` repeats it. `docs/MAILROOM.md:135` lists ask_mail with the read-only allows. The code inserts anyway. Those two sentences are both in the repo: the allow-list calls search read-only, and the lock design names the inserts and says they skip the lock.

Search does not read `ACTION_REQUIRED`, does not call `acquire_writer_lock`, and does not call `sor_writer_gate.writer_lock_held`. The gate's probe (`scripts/sor_writer_gate.py:532-562`) takes `LOCK_EX | LOCK_NB` and unlocks if it won. That probe is the wrong primitive for a request path: a free lock is briefly acquired.

The watchdog's probe is the read-only one. `inspect_lock` (`scripts/search_resume_watchdog.py:472-479`) reads the lock text and uses `lsof` plus signal 0. It does not take the flock, including a non-blocking exclusive probe, and it does not open the database (`35-39`). A detection fault is treated as held.

### 1.5 WAL assumptions in this repo

Writers are documented to set WAL and `synchronous=FULL`. Readers are documented to set WAL and `synchronous=NORMAL` (`sqlite_pragmas.py:7-11`). The SoR file is assumed to already be WAL because some earlier writer applied that pragma. The audit connection does not apply it; it writes in whatever mode the file is already in. The draft connection and every `connect()` read do apply it, so a non-WAL file would be switched to WAL by search.

In WAL mode a deferred insert and a concurrent reader do not block each other for the whole transaction. They still meet on a checkpoint, on `BEGIN EXCLUSIVE`, and on the backup API. In a rollback journal, the audit `INSERT` blocks readers for the transaction. Search does not run `wal_checkpoint`.

Sibling files, when the mode is WAL, are `mailroom.sqlite-wal` and `mailroom.sqlite-shm`, opened by path.

### 1.6 What a window does today

S1's `launchctl` verbs are `print`, `print-disabled`, and `bootout` of `com.mailroom.ask-mail-serve` (`docs/heavy/20260927-2227-pr89-heavy-followup-S1-and-AR-R.md:21-35`). After bootout the same list runs `lsof` on the SoR, the `-wal`, and the `-shm` (line 35). AR-R step 5 is the restore write: `with_writer_lock.py --purpose att0-restore` around a sqlite3 `.backup` then `mv` (`:51`). Step 8 loads search again only after that child has exited and the lock is free (`:55`). S1 and AR-R are described there as not armed. This note does not redesign them.

The reason search is booted out is the pair of facts in §1: it writes the same file the window replaces, and it does not look at the lock.

## 2. Hazards if the window runs while search is up

### 2.1 Lost audit rows

`write_ask_audit` treats every failure as a warning. A window that holds a SQLite reserved or exclusive lock long enough to exceed 5 s produces `SQLITE_BUSY`, the warning line, HTTP 200, and no row. A window that only holds the flock, and has not taken a SQLite lock, does not trip this path: the insert commits into the live file, and the later `mv` discards that inode. Either way the row is not in the file search reads after the window.

Draft rows fail louder (the request errors) and still do not land in the replaced file. The text file under the drafts directory can outlive the row, because it is written first.

### 2.2 `SQLITE_BUSY`

The flock is invisible to SQLite. `SQLITE_BUSY` happens when the window's child actually holds a SQLite lock (exclusive transaction, checkpoint, or backup) or when two search threads write at once and one waits.

Audit waits up to 5 s, then drops the row and returns the answer. Drafts wait up to 30 s on the handler thread, then the error escapes `do_POST`. Readers that call `connect` can busy-fail on `PRAGMA journal_mode=WAL` inside that first 5 s, before `busy_timeout=30000` is set. `connect` swallows that exception and continues with the weaker defaults, so a later `SELECT` busy-fails at 5 s rather than 30 s. `get_message` turns `AskMailError` into a fail-open JSON body; a raw `OperationalError` from a later `SELECT` does not.

A rollback-journal SoR makes this worse: the audit insert blocks readers, and readers block the insert, for the whole deferred transaction.

### 2.3 Writes that land in a file about to be replaced

`acquire_writer_lock` succeeds before the child runs (`with_writer_lock.py:296-313`). Between that acquire and the `mv`, and during `.backup`, search still inserts into the path the child is about to replace. Those commits are durable in that inode (audit leaves `synchronous` at the engine default FULL; drafts have set `NORMAL`). The `mv` puts a different inode at the path. The commits are not in the new file.

A queued replay with the same bug is worse than a drop. A buffer flushed through a connection that was opened before the `mv` writes the old inode. A buffer flushed after the `mv` but before the window's `quick_check` writes the new file while the window still thinks it is the only writer.

`ask_audit` has no primary key, so a later replay cannot tell a committed row from a duplicate.

### 2.4 An open fd on a renamed file

`mv` onto an existing path changes the directory entry. An fd opened before the `mv` still refers to the old inode. Search does not keep a connection for the life of the process, but it does keep one for the life of a request:

- retrieve, including rerank, until `semantic_search.py:1321`
- `get_message` / `get_thread` until their `finally`
- `draft_reply` until `1199`
- `write_ask_audit` until `654`

`ThreadingHTTPServer` means several of those can overlap. Generate does not hold one (`ask` closes at `913` before the HTTP generate call).

SQLite also has the `-wal` and `-shm` fds, opened by the sibling names. Replacing the main file and leaving those names in place attaches a new connection to the previous WAL. Replacing the sibling names while an old connection still has them open leaves that connection writing an unlinked WAL, and leaves the new file with a new WAL. That is how a restore that only `mv`s the main database corrupts or silently splits the file. S1's post-bootout `lsof` of all three names exists because of this (`docs/heavy/20260927-2227-pr89-heavy-followup-S1-and-AR-R.md:35`).

Pausing inserts does not close a retrieve fd. A read pause, or an equivalent drain until no request is inside those sections, is part of making the `mv` safe. The process can stay loaded the whole time.

### 2.5 Schema write from the "read" helper

`PRAGMA journal_mode=WAL` on every `connect()` is a mode change when the file is not WAL. During a restore the file at the path may briefly be a newly moved database. Search can flip that file to WAL, or busy-fail the request, without any insert.

## 3. Options

All three keep the LaunchAgent loaded. None of them call `run_with_lock` from search: that wrapper overwrites purpose and token, drops `+26` when `MAILROOM_SEARCH_RESUME_RUN_ID` is set, and holds the flock for a child process. Search must not be given `MAILROOM_WRITER_LOCK_TOKEN`.

### (a) Pause SoR writes while the window is on, keep serving reads

A marker file, plus a read-only look at the writer lock. While either says the window is on, `/ask`, `/hybrid_search`, `/message`, `/get_thread`, `/health`, and `/ui` still run. `write_ask_audit` does not open the SoR. `draft_reply` fails before `mkdir`, `write_text`, and `INSERT`.

Proposed marker: `$HOME/MailArchive/state/ask-mail-writes.off` (override `MAILROOM_ASK_WRITES_OFF`). The window creates it before `acquire_writer_lock`, the same order S1 uses for the deadline file before bootout. Search also treats the lock as held when `inspect_lock` says `held` or `fault`, so a crashed window that left the flock held still pauses writes. Fault means pause writes, matching the watchdog's fail-closed rule for the SoR. Retrieve stays up in that state.

Do not queue audit rows in the search process. A queue flushed at the wrong time is hazard 2.3. Skip the insert and put a label on the JSON (`audit` skipped, reason `writer_lock` or `marker`). The stderr warning stays. `draft_reply` raises `AskMailError` so the existing handler returns 400 and no orphan text file.

For the `mv` only, a second phase on the same marker (or a second file `ask-mail-reads.off`) makes new requests return a small JSON 503 and waits until in-flight handlers have left `retrieve`, `connect`, `draft_reply`, and `write_ask_audit`. Then the child `mv`s. Then the marker is removed. The process was loaded the entire time. `/health` reports `writes` (`on` or `paused`), `reads` (`on` or `paused`), and `sqlite_requests` so the window can wait. Health must not open the SoR to answer that.

Pros:

- Search stays loaded. S1 does not have to bootout, so `KeepAlive` and the watchdog restore path are not in the critical path of the window.
- No search insert into the inode that is about to be replaced, and no 5 s or 30 s busy wait on the request thread during an exclusive SQLite lock.
- The read pause is bounded to the rename, not to the whole lock hold. Rem can hold the flock for the life of the process (`docs/rem-window-freeze.md`); reads continue for that whole hold.
- The probe does not take `LOCK_EX`, so it cannot steal the window's flock or overwrite purpose and token.

Cons:

- Audit rows during the window are dropped. That matches today's exception path, and it is a product choice if those rows matter.
- Drafts fail for the window. A user with the UI open gets an error on `POST /draft_reply`.
- Polling the lock file alone misses the gap between "window is about to acquire" and "flock is held". The marker has to be written first or that gap remains.
- In-flight requests that entered SQLite before the pause can still commit once. The window waits on `sqlite_requests=0`, not on the flag flip.
- `connect()` still runs `journal_mode=WAL` unless that pragma is removed from the read path in the same change. The pause has to cover `connect`, not only the two insert functions.
- A 503 during the rename is a short search outage. It is not a bootout, and it is still an outage.

Changes:

- `scripts/ask_mail.py`: new helper next to `connect` (name proposal `sor_writes_paused` / `sor_reads_paused`). Call it at the start of `write_ask_audit` and `draft_reply`, and at the start of `connect` for the read phase. Extend the `/health` body in `AskHandler.do_GET`. Add the `audit` label in `_base_response` or in `ask` when the skip happens. Do not call `acquire_writer_lock` or `run_with_lock`.
- `scripts/search_resume_watchdog.py`: reuse `inspect_lock` (or a shared read-only function extracted beside it). Do not call `sor_writer_gate._probe_writer_lock`.
- `scripts/sqlite_pragmas.py`: `apply_reader_pragmas` should stop issuing `PRAGMA journal_mode=WAL` on the search read path, or `connect` should stop calling the full reader set and only set `busy_timeout`. Mode changes belong on the writer that holds the flock.
- `tests/test_ask_mail.py`: the cases in the test strategy below. Temp files only.
- Not in the first code change: `launchd/com.mailroom.ask-mail-serve.plist.template`, S1, AR-R, `scripts/with_writer_lock.py`.

Test strategy, temp directories only, never the live SoR path:

- Marker present: `ask` returns hits, `ask_audit` count stays 0, the JSON says audit was skipped, no connection executes `INSERT`.
- Lock held by another process (`LOCK_EX` in a subprocess): same skip, including when the marker is absent.
- Unreadable lock path: writes pause, retrieve still returns.
- `draft_reply` with the marker: `AskMailError`, no text file, no `drafts` row.
- `journal_mode` is not executed while paused.
- A handler already inside a fake `retrieve` is allowed to finish; a handler that starts after the read pause gets 503; when the pause clears, the next `connect` opens the post-rename inode (compare `st_ino`).
- `/health` does not create `mailroom.sqlite` when the file is missing.
- Existing `test_ask_audit_has_no_bodies` still passes with the gate off.

Rollout:

1. Ship the gate dark. Proposed env `MAILROOM_ASK_WRITE_GATE`. Unset means today's behavior, so a repo merge does not change a running service.
2. Turn the env on for the LaunchAgent in an operator step. This note does not edit the plist template.
3. Prove on a copy database: lock held, audit skipped, drafts refused, `GET /ask` still 200, `lsof` empty after the read pause.
4. Only then change the window to write the marker, skip bootout, drain, `mv`, clear the marker. Until that proof, S1 still bootouts. The watchdog stays as it is. Do not add or remove `KeepAlive` as part of this work.

### (b) Sidecar database, merged later under the lock

Search stops inserting into `mailroom.sqlite`. `write_ask_audit` and the `drafts` insert open a different file, proposed `$HOME/MailArchive/mailroom-ask.sqlite` (`MAILROOM_ASK_DB`). The name must not be the live basename `mailroom.sqlite`, so the existing live-SoR basename checks do not treat it as the SoR. `ensure_ask_audit` and `ensure_drafts` run against the sidecar only.

Reads of `messages` stay on the SoR. `draft_reply` still `SELECT`s the SoR to prove the `message_id` exists, then inserts into the sidecar. The text file stays under the drafts directory.

A merge into the SoR, if anything still expects these tables there, is a later child of `with_writer_lock.py`, after the window has released the lock, against the post-`mv` file. `scripts/embed_sidecar_apply.py` is the pattern (missing-only apply under `acquire_writer_lock`). `ask_audit` cannot be missing-only until it has a key. Add a primary key or a unique token in the sidecar schema before any merge exists. Running the merge twice without that key duplicates rows. Do not merge inside the restore child.

The paste path is the precedent for keeping audit off the live file: `scripts/ask_mail_paste.sh:3` takes a `.backup` and does not write `ask_audit` on the SoR.

Pros:

- A window can replace the SoR and search's rows are still in the sidecar. No drop, and no insert into the inode being replaced.
- Search no longer takes a SQLite write lock on the SoR, so it does not cause `SQLITE_BUSY` on the window's child and the window's exclusive lock does not drop audit rows.
- Matches the read-only allow line for ask_mail on the SoR (`docs/MAILROOM.md:135`) in a way option (a) does not: search never writes that file.
- Drafts survive the window without a queue inside the process.

Cons:

- Does not fix hazard 2.4. Retrieve still holds an fd on the SoR across the `mv`. A read pause from option (a) is still required for the rename.
- `connect()` on the read path can still change `journal_mode` on the SoR.
- A second database to include in backups. The backup set documented for the SoR does not include this file.
- `ask_audit` in the SoR and `ask_audit` in the sidecar diverge until a merge. Tools that read the SoR table will not see window-time rows.
- Merge is a new SoR writer. It has to take the flock, refuse `ACTION_REQUIRED`, and refuse a live basename unless the operator passed the existing allow flag. A bad merge is a second-writer incident of the kind the lock was added to prevent.
- `synchronous=NORMAL` on a sidecar opened via `connect` is the wrong durability for an audit log. The sidecar open should use the writer pragma set, on the sidecar only.

Changes:

- `scripts/ask_mail.py`: `default_ask_db_path`; `write_ask_audit` opens that path; `draft_reply` reads the SoR and inserts the sidecar. `ask` keeps reading the SoR for hits and message bodies.
- New apply script, or a narrow mode beside `embed_sidecar_apply.py`, that inserts sidecar rows under `acquire_writer_lock`. Not `run_with_lock` from inside the server.
- `scripts/migrate_pr1_schema.py` and `docs/pr0/mailroom_schema.sql`: a key on sidecar `ask_audit` before merge. Leave the SoR table's existing rows alone.
- `tests/test_ask_mail.py`: after `ask`, the SoR `ask_audit` count is 0 and the sidecar count is 1; the SoR inode is unchanged.
- Backup and health docs in a follow-up, not this note's code.

Test strategy:

- Temp SoR plus temp sidecar. `/ask` does not change the SoR file's inode or its `ask_audit` count.
- `draft_reply` writes the text file and a sidecar row, and writes no `drafts` row in the SoR.
- Sidecar open failure: audit warns and the ask still returns; drafts return `AskMailError` and do not leave the text file if the insert did not commit. Write the file after the insert, or delete it if the insert fails. Today's order is the orphan bug.
- Merge test on two temp files under a temp lock file: second run inserts zero new rows (needs the key).
- Rename test from option (a) still fails if a retrieve fd is open. That is the expected gap, recorded in the test name so nobody treats the sidecar as a full answer.

Rollout:

1. Ship sidecar writes while S1 still bootouts. Old SoR rows stay where they are. No backfill.
2. Add the sidecar path to the backup set before relying on it.
3. Add the key, then the merge, and run the merge only when the lock is free and the window is finished.
4. The `mv` still requires the read drain from option (a). Sidecar first does not by itself let the window skip bootout.

### (c) Take the same writer lock briefly, else skip audit

Around the `INSERT` only, search tries to acquire `mailroom.write.lock` with a short wait (proposal: well under a second). On success it inserts, commits, and unlocks. On timeout it skips audit the way `write_ask_audit` already skips on exception, and `draft_reply` raises `AskMailError`.

This cannot be `acquire_writer_lock` as it stands. That function is non-blocking, refuses for the whole hold with no wait loop, overwrites purpose and token, and is wrapped by `run_with_lock`'s `+26` drop. A new function would have to flock without writing the payload, unlock in a `finally`, and never call `_drop_search_resume_plus_26`. The SQLite `busy_timeout` on that connection has to be shorter than the flock hold, or a busy insert keeps the flock while the window's `LOCK_NB` fails.

Pros:

- Closes the check-then-write gap for inserts. A commit cannot overlap a holder that already has the flock, and a holder cannot start the `mv` during the insert if its acquire is non-blocking.
- No second database. No marker protocol.
- During a long rem hold, search never gets the flock, so every audit degrades immediately instead of waiting 5 s on `SQLITE_BUSY`.

Cons:

- Search becomes a lock holder. The window's `LOCK_NB` then fails with "writer lock held" for the length of the insert. Today's acquire does not retry. A window that currently expects the lock to be free whenever search is merely "up" will refuse.
- If the implementation uses the existing payload write, the file's purpose and token become search's for that interval. The watchdog and the operator read that purpose. A `+26` drop must not run.
- A blocking flock with a long timeout stalls `/ask` for as long as rem holds the lock (process lifetime). The timeout has to be short, which means audit is dropped for the whole window anyway. That is option (a)'s audit behavior plus lock contention.
- Does not pause readers, so hazard 2.4 remains. Taking the flock on the read path too would make `/ask` fail for the entire rem hold, which is a bootout by another name.
- Two search threads can stack: one holds the flock, the other times out and drops a row that SQLite itself would have serialized.

Changes:

- `scripts/with_writer_lock.py`: a new `try_acquire_writer_lock` that does not rewrite the payload, does not drop `+26`, and always unlocks. `acquire_writer_lock` stays the window API.
- `scripts/ask_mail.py`: `write_ask_audit` and the insert section of `draft_reply` call it. `connect` does not.
- `tests/test_with_writer_lock.py` and `tests/test_ask_mail.py`.

Test strategy:

- Subprocess holds `LOCK_EX`. `write_ask_audit` returns inside the timeout budget, row count unchanged, lock file purpose unchanged.
- Search holds the short flock. A second `LOCK_NB` gets `BlockingIOError` and, after search's `finally`, succeeds. The purpose text the window wrote is still there.
- `_drop_search_resume_plus_26` is not called on the search path (assert the deadline file is untouched).
- A temp SoR in WAL mode: the insert that wins the flock commits; a forced `OperationalError` inside the hold still unlocks.
- Document that an open retrieve fd during `mv` is out of scope for this option and still fails an inode test.

Rollout:

1. Land the non-destructive try-lock and the skip path dark, behind the same env as option (a).
2. Do not enable it against a window whose acquire is non-blocking unless that window retries when the holder purpose is the search purpose.
3. Enabling it does not remove the need for a read drain around `mv`.

## 4. Recommendation

Do option (a). Keep search loaded, pause SoR writes for the whole lock hold, and pause new SQLite opens only for the `mv` drain.

Skip audit while paused, and label the skip on the response. Do not queue those rows in process memory. Fail `draft_reply` with `AskMailError` before the text file and before the `INSERT`. Observe the lock the way `inspect_lock` does, and require the window to create the marker before `acquire_writer_lock`. Stop issuing `PRAGMA journal_mode=WAL` from search's read connection so a reader cannot change the file the window is replacing.

Reject option (c) as the mechanism. The lock file's purpose and token are the window's identity. A request path that acquires that flock, even briefly, makes the window's non-blocking acquire fail and can rewrite that identity. Degrading to no-audit when the lock is busy is the right outcome, and option (a) does it without becoming a holder.

Leave option (b) as a follow-up, and only if dropped `ask_audit` rows during a window are not acceptable. The sidecar is how those rows survive a replace. It is not how the `mv` becomes safe: retrieve fds still have to drain. Do not build the merge until `ask_audit` has a key, and do not run the merge inside the restore child.

Ship the gate dark (`MAILROOM_ASK_WRITE_GATE` unset means today's code). Prove it on a copy database. Only after that proof does a window skip bootout. This note does not change S1, AR-R, the watchdog, or the plist template.

## 5. Open questions

1. Are `ask_audit` rows during a window allowed to disappear? The current `except` path already drops them on any error and only writes a stderr line.
2. Should a paused `draft_reply` be a hard error (recommended), or a sidecar insert from option (b)?
3. Is the marker file required, or is polling `inspect_lock` enough? Polling misses the interval after the window has decided to start and before its `LOCK_NB` succeeds.
4. How long may the read pause last? Retrieve holds the fd through rerank (`semantic_search.py:1301-1321`). Generate does not hold it (`ask_mail.py:906-928`). A 503 for that drain is the remaining outage. What bound does the window use before it aborts the `mv`?
5. The gate belongs in `write_ask_audit`, `draft_reply`, and `connect`, which the CLI and `--mcp` share. Is enabling it for those one-shot processes in the same change acceptable, given the LaunchAgent is the only long-lived holder of fds?
6. Should `ACTION_REQUIRED` pause SoR writes the way `run_with_lock` refuses them (`with_writer_lock.py:289-293`), while retrieve stays up?
7. Confirm on a migrated file that `CREATE TABLE IF NOT EXISTS` is a pure no-op when `ask_audit` and `drafts` already exist, under the SQLite build the venv actually ships. The repo's DDL matches `migrate_pr1_schema.py` and `docs/pr0/mailroom_schema.sql`. A drifted installed binary is outside this checkout.
8. The plist template sets `KeepAlive` true (`launchd/com.mailroom.ask-mail-serve.plist.template:34-35`). The watchdog doc says not to add `KeepAlive` (`docs/search-resume-watchdog.md:16-17`). Which one is installed? Skipping bootout does not depend on the answer. This note does not edit either file.
9. Line numbers above are this repo at the commit that adds this file. The installed `ask_mail.py` may differ. Re-read the installed file before an operator relies on a citation.
