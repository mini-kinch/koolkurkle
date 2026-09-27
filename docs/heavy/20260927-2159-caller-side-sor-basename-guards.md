# Caller-side SoR basename guards

Status: documented only. `scripts/attachments/migrate_att0_schema.py` and `scripts/attachments/meta_fill.py` are unchanged.

## Problem

ATT-0 migrate and meta-fill each refuse a database whose raw basename is `mailroom.sqlite`, unless `--allow-mailroom-sqlite` is set. The check is `Path.name == SOR_BASENAME`. It does not resolve symlinks.

`sor_writer_gate.is_live_sor()` does resolve the path, then compares the resolved basename to `SOR_BASENAME`. A link is judged by its target name, not by the name of the link. The caller guards and `is_live_sor()` therefore disagree on both symlink directions.

The caller guard is not the same policy as the writer-lock gate. The gate refuses a live path only when a rem process is visible or the writer lock is held. The caller guard refuses the raw basename even when rem is absent and the lock is free. Skipping the caller guard is enough to open the database in that quiet case. The gate does not replace it.

## Evidence

`SOR_BASENAME` is `mailroom.sqlite`.

Four guards:

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

`is_live_sor()` (`scripts/sor_writer_gate.py`) returns true when the resolved basename is `mailroom.sqlite`. `LivePathTests.test_realpath_symlink_alias` locks the two directions for the gate only. It does not call migrate or meta-fill.

Direction 1, fail-open. A symlink whose own name is not `mailroom.sqlite` and whose target is `mailroom.sqlite`. Example from that test: `rehearsal-alias.sqlite` pointing at `mailroom.sqlite`. `path.name` is `rehearsal-alias.sqlite`, so all four guards allow the call through. `is_live_sor()` is true. If rem is absent and the writer lock is free, `refuse_if_sor_writer_conflict()` also allows the call. Migrate or meta-fill can then open the SoR without `--allow-mailroom-sqlite`.

Direction 2, extra refuse. A path whose raw name is `mailroom.sqlite` and whose target is a copy. Example from that test: `nested/mailroom.sqlite` pointing at `mailroom-copy.sqlite`. `path.name` is `mailroom.sqlite`, so all four guards refuse. `is_live_sor()` is false, and the gate allows that path even while the writer lock is held. A regular file that is itself named `mailroom.sqlite` is refused by both the basename guard and `is_live_sor()`, because the resolved basename is still `mailroom.sqlite`. The disagreement on this side is the symlink, not every file that happens to use that name.

## Is this the third live-only check from Heavy §3.1?

No. §3.1, as locked in this branch, is the live branch inside `refuse_if_sor_writer_conflict`. After `live_checks_apply()`, that function calls `rem_process_hits()` once and `writer_lock_held()` once. `InventoryTests.test_refuse_if_live_branch_is_two_checks` asserts that call set. These four guards are not calls inside that function. They sit in the callers, and they run before the gate. Adding them does not create a third check in the gate, and the two-check test still describes the gate.

They are still an uninventoried live-only policy. The static inventory grepped `is_live_sor`, `writer_lock_held`, `rem_process_hits`, and `refuse_if_sor`. That pattern does not match `path.name == SOR_BASENAME`. The fail-open alias is a hole in the caller rule "do not open a live SoR without the flag." It is not the §3.1 blocker.

## Options

A. Switch all four conditions to `sor_writer_gate.is_live_sor(path)` (and `is_live_sor(db_path)` in each `main`). Keep `--allow-mailroom-sqlite` as the bypass of that caller refuse. Leave `refuse_if_sor_writer_conflict()` behind it. Add tests for both symlink directions on `migrate_database`, migrate `main`, `fill_metadata`, and meta-fill `main`.

This makes the caller rule and the gate use one definition of live. Direction 1 starts refusing. Direction 2 stops refusing, which matches `is_live_sor()` and the gate. A regular file named `mailroom.sqlite` still refuses, so the existing basename tests keep passing.

B. Leave the four conditions on the raw basename. The flag text already says "basename mailroom.sqlite". The existing tests match that string. This keeps direction 2 refused and leaves direction 1 fail-open. Nothing in the flag text or the tests justifies writing the SoR through a differently named symlink while the lock is free.

C. Refuse when the raw basename matches or when `is_live_sor()` is true. Direction 1 closes. Direction 2 stays refused, so the caller remains stricter than the gate for a link named `mailroom.sqlite` that points at a copy. The flag would have to bypass both halves or the extra refuse survives the flag and still disagrees with the gate.

## Proposed fix

Option A. The live database is the resolved basename, which is what `is_live_sor()` and the design note already say. The caller hard-refuse should use that predicate so a differently named symlink cannot skip it. Alias tests belong on all four sites. Do not implement that in this change. Do not edit `migrate_att0_schema.py` or `meta_fill.py` here.
