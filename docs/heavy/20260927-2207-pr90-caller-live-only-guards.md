# PR 90 caller live-only guards

Status: PR #90 documented the four raw-basename guards and left those scripts unchanged. Option A is implemented in the follow-up stacked on that branch: the four caller predicates call `sor_writer_gate.is_live_sor`. This note is the PR #90 inventory and is not rewritten. Residual, not fixed here: `is_live_sor()` uses the resolved basename, not the inode, so an APFS hard link with a different name to the SoR inode still looks not-live. This note replaces `docs/heavy/20260927-2159-caller-side-sor-basename-guards.md`.

`SOR_BASENAME` is `mailroom.sqlite`.

## 1. What each guard is

Each guard refuses when the raw basename equals `SOR_BASENAME` and the allow flag is off. The comparison is `Path.name`. None of the four call `sor_writer_gate.is_live_sor()` or read `SOR_FORCE_LIVE_CHECKS`.

1. `scripts/attachments/migrate_att0_schema.py:364`, inside `migrate_database`:

```python
if path.name == SOR_BASENAME and not allow_mailroom_sqlite:
    raise MigrateRefuse(
        "refuse: basename mailroom.sqlite "
        "(pass --allow-mailroom-sqlite to override)"
    )
```

Existing test: `tests/test_att0_attachment_search.py`, `SchemaMigrationTests.test_refuses_mailroom_sqlite_without_flag_and_does_not_open_it`. It uses a real file named `mailroom.sqlite`. Alias directions: no test.

2. `scripts/attachments/migrate_att0_schema.py:434`, inside `main`:

```python
if db_path.name == SOR_BASENAME and not args.allow_mailroom_sqlite:
    sys.stderr.write(
        "error: refuse: basename mailroom.sqlite "
        "(pass --allow-mailroom-sqlite to override)\n"
    )
    return 2
```

Existing test: `SchemaMigrationTests.test_cli_refuse_and_copy_success`. Alias directions: no test.

3. `scripts/attachments/meta_fill.py:931`, inside `fill_metadata`:

```python
if path.name == SOR_BASENAME and not allow_mailroom_sqlite:
    raise FillRefuse(
        "refuse: basename mailroom.sqlite "
        "(pass --allow-mailroom-sqlite to override)"
    )
```

Existing test: `tests/test_att0_meta_fill.py`, `FillTests.test_refuses_mailroom_sqlite_without_creating_it`. Alias directions: no test.

4. `scripts/attachments/meta_fill.py:1284`, inside `main`:

```python
if db_path.name == SOR_BASENAME and not args.allow_mailroom_sqlite:
    sys.stderr.write(
        "error: refuse: basename mailroom.sqlite "
        "(pass --allow-mailroom-sqlite to override)\n"
    )
    return 2
```

Existing test: `CliTests.test_negative_smoke_and_mailroom_refuse`. Alias directions: no test.

On the CLI path the `main` guard runs first and returns 2, so the library guard is not reached for that refuse. When `main` continues, it passes `allow_mailroom_sqlite=bool(args.allow_mailroom_sqlite)` into `migrate_database` (line 446) or `fill_metadata` (line 1311), and the library guard runs the same predicate again. A direct call to `migrate_database` or `fill_metadata` hits only the library guard.

## 2. Callers and entry points

Grep of `migrate_database`, `fill_metadata`, `migrate_att0_schema`, `meta_fill.py`, and `allow-mailroom-sqlite` across the repo.

### Code that reaches the guards

| Guard | Who executes it |
|---|---|
| `migrate_database` (line 364) | `migrate_att0_schema.main` at line 444. `tests/test_att0_attachment_search.py` calls `mig.migrate_database` directly. `tests/test_att0_meta_fill.py` `_seed` calls `mig.migrate_database` at line 197 after building the fixture database. |
| migrate `main` (line 434) | `if __name__ == "__main__"` at line 460. `SchemaMigrationTests.test_cli_refuse_and_copy_success` calls `mig.main`. |
| `fill_metadata` (line 931) | `meta_fill.main` at line 1305. `tests/test_att0_meta_fill.py` calls `meta.fill_metadata` directly. |
| meta-fill `main` (line 1284) | `if __name__ == "__main__"` at line 1333. `CliTests` calls `meta.main`. `FillTests.test_clone_dry_run_needs_no_install` runs `scripts/attachments/meta_fill.py` as a subprocess. |

No other production module imports either script. `tests/imap_bodystructure_double.py` imports `FillRefuse` only. `scripts/imap_curl.py` mentions `meta_fill.py` in an `ImportError` comment and does not call it. No shell script and no plist invokes either script. Neither script calls `with_writer_lock`.

The in-process calls that set `allow_mailroom_sqlite=True` are `SchemaMigrationTests.test_flag_still_uses_writer_gate` and `FillTests.test_flag_still_uses_the_writer_gate`. Those skip the library guard and then hit `refuse_if_sor_writer_conflict`. No test passes the CLI flag `--allow-mailroom-sqlite`. `test_att0_attachment_search.py` only asserts that the help text contains that string.

### Docs and runbook steps

`docs/ops-terminal.md` does not name either script. `docs/MAILROOM.md` does not either. No runbook card shells out to them.

Documented commands, all without `--allow-mailroom-sqlite`:

- `scripts/attachments/migrate_att0_schema.py` lines 32–33:

```
python3 scripts/attachments/migrate_att0_schema.py --db /tmp/mailroom-copy.sqlite
python3 scripts/attachments/migrate_att0_schema.py --db /tmp/mailroom.sqlite
```

The copy basename does not enter the guard body. The second command's raw basename is `mailroom.sqlite`, so migrate `main` returns 2 before `migrate_database`.

- `docs/attachments/ATT-0-design.md` (the dry-run from a clone), also without the flag:

```
PYTHONPATH=scripts:scripts/attachments python3 scripts/attachments/meta_fill.py --dry-run --db /tmp/mailroom-copy.sqlite --source jsonl --jsonl /tmp/archive.jsonl --max-messages 0 --timeout 0 --max-record-bytes 64MB
```

That basename does not enter the guard body. The same design note says a basename of `mailroom.sqlite` is refused unless the flag is passed, and that the writer gate still runs after the flag. It does not show a command that passes the flag. The same paragraph says applying the migration to the system of record is out of scope for that packet.

`FillTests.test_clone_dry_run_needs_no_install` repeats that dry-run shape against a file named `mailroom-copy.sqlite` and does not pass the flag.

### A2 and A3

The labels A2 and A3 appear in one place:

```python
# att0-migrate: A2 wrapper purpose for scripts/attachments/migrate_att0_schema.py.
# att0 meta fill: A3 job name in scripts/attachments/meta_fill.py.
```

Those lines are `scripts/sor_writer_gate.py:66` and `:67`, next to `WRITER_PURPOSE_ALLOWLIST`. There is no A2 card and no A3 card under `docs/`. The string `att0-migrate` does not occur in `migrate_att0_schema.py`. The string `att0 meta fill` occurs in `meta_fill.py` as the report banner inside `format_report` (line 1139), which runs only after `fill_metadata` has passed the guard. Nothing in the repo wraps either script with `with_writer_lock.py --purpose att0-migrate` or `--purpose "att0 meta fill"`.

## 3. Why they disagree with realpath `is_live_sor()`

`is_live_sor()` (`scripts/sor_writer_gate.py`) resolves the path, then compares the resolved basename to `SOR_BASENAME`. A link is judged by its target name. The four guards compare the name of the path they were given, before any resolve.

`LivePathTests.test_realpath_symlink_alias` locks both directions for the gate only. It does not call migrate or meta-fill.

Direction 1, fail-open. A symlink whose own name is not `mailroom.sqlite` and whose target is `mailroom.sqlite`. The gate test uses `rehearsal-alias.sqlite` pointing at `mailroom.sqlite`. `path.name` is `rehearsal-alias.sqlite`, so all four guards allow the call through. `is_live_sor()` is true. If rem is absent and the writer lock is free, `refuse_if_sor_writer_conflict()` also allows the call. Migrate or meta-fill can then open the SoR without `--allow-mailroom-sqlite`.

Direction 2, extra refuse. A path whose raw name is `mailroom.sqlite` and whose target is a copy. The gate test uses `nested/mailroom.sqlite` pointing at `mailroom-copy.sqlite`. `path.name` is `mailroom.sqlite`, so all four guards refuse. `is_live_sor()` is false, and the gate allows that path even while the writer lock is held. A regular file that is itself named `mailroom.sqlite` is refused by both the basename guard and `is_live_sor()`, because the resolved basename is still `mailroom.sqlite`. The disagreement on this side is the symlink.

The caller guard is a harder refuse than the gate. The gate refuses a live path only when a rem process is visible or the writer lock is held. The caller guard refuses the raw basename even when rem is absent and the lock is free. Skipping the caller guard is enough to open the database in that quiet case.

## 4. What a 3.5L rehearsal can and cannot exercise

3.5L here means a rehearsal on a backup copy with `SOR_FORCE_LIVE_CHECKS=1`.

`live_checks_apply()` is true when `is_live_sor()` is true, and also when the path is not live and the environment value is exactly `1`. The flag can turn the gate's rem scan and lock probe on for a copy. No value turns those checks off on a live path. The four guards do not read that variable. Their condition is the raw basename and the allow flag.

What 3.5L can exercise:

- A backup whose raw basename is not `mailroom.sqlite` (the copy names already used in the docs, `mailroom-copy.sqlite` and `mailroom-daily-copy.sqlite`) does not enter the guard body. The call reaches `refuse_if_sor_writer_conflict()`. With `SOR_FORCE_LIVE_CHECKS=1`, `live_checks_apply()` is true on that copy, so the rehearsal runs `rem_process_hits()` once and `writer_lock_held()` once. That is the gate on a copy. A held lock or a visible rem process then refuses. A quiet lock with no rem process allows the copy through, because the flag turns the checks on and the checks themselves found nothing to refuse.

What 3.5L cannot exercise:

- It cannot change the four guards. They ignore `SOR_FORCE_LIVE_CHECKS`, so the rehearsal cannot turn a basename refuse on or off.
- A backup that is not named `mailroom.sqlite` never takes the refuse branch. The rehearsal does not print `refuse: basename mailroom.sqlite` and does not pass `--allow-mailroom-sqlite`. The guards stay quiet for the whole run.
- A backup that is named `mailroom.sqlite` (same basename in another directory, or a link with that name) is refused by the guards before the gate, with the force flag set or unset, unless `--allow-mailroom-sqlite` is also passed. Passing the flag skips the guards, so the rehearsal observes the gate after the bypass and still does not observe the guard decision. For a regular file of that name, `is_live_sor()` is already true, so the force flag adds nothing. For a link of that name whose target is a copy, `is_live_sor()` is false; the force flag would turn the gate on only after the allow flag has already skipped the guard.
- Direction 1 is a differently named symlink to `mailroom.sqlite`, which a basename-changed backup copy does not create. On that symlink the guards allow the call, and `is_live_sor()` is already true, so the force flag is not what decides the gate. 3.5L on a copy basename leaves that fail-open untested.

## 5. Options

A. Switch all four conditions to `sor_writer_gate.is_live_sor(path)` (and `is_live_sor(db_path)` in each `main`). Keep `--allow-mailroom-sqlite` as the bypass of that caller refuse. Leave `refuse_if_sor_writer_conflict()` behind it. Add tests for both symlink directions on `migrate_database`, migrate `main`, `fill_metadata`, and meta-fill `main`.

The caller rule and the gate then use one definition of live. Direction 1 starts refusing. Direction 2 stops refusing, which matches `is_live_sor()` and the gate. A regular file named `mailroom.sqlite` still refuses, so the existing basename tests keep passing.

B. Keep the four conditions on the raw basename, and list them in the inventory. The design-doc table and the PR body already do that listing. The flag text already says "basename mailroom.sqlite". The existing tests match that string. Direction 2 stays refused. Direction 1 stays fail-open. A 3.5L rehearsal does not show that hole, because the guards ignore the force flag and a copy basename never enters them.

C. Refuse when the raw basename matches or when `is_live_sor()` is true. Direction 1 closes. Direction 2 stays refused, so the caller remains stricter than the gate for a link named `mailroom.sqlite` that points at a copy. The flag has to bypass both halves, or the extra refuse survives the flag and still disagrees with the gate. The guards still ignore `SOR_FORCE_LIVE_CHECKS`, so 3.5L still cannot exercise them.

## 6. Recommendation

Option A, in a later change. The live database is the resolved basename, which is what `is_live_sor()` already says. The caller hard-refuse should use that predicate so a differently named symlink cannot skip it. Alias tests belong on all four sites, because the current tests use a real file named `mailroom.sqlite` and 3.5L on a backup copy cannot reach the disagreeing symlink cases.

Listing the four guards in the inventory (option B) is the record for this change. It makes the miss visible. It leaves the fail-open in place. Option C closes that hole and keeps a second disagreement the gate does not have.

Do not implement option A in this change. Do not edit `migrate_att0_schema.py` or `meta_fill.py` here.
