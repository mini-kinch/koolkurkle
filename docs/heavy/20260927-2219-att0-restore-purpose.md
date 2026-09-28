# Heavy narrative — `att0-restore` is not a gate purpose

Date: 2026-09-27 22:19 UTC
From: implementation pass on PR #89
Status: reported, not patched. `scripts/sor_writer_gate.py` was not edited.

## Problem

The restore acquire uses purpose `att0-restore`. That acquire must drop `+26` for the overwritten run-id `att0-L1-<STAMP>-R`. Mailroom asked whether PR #90's wrapper and gate accept `att0-restore`.

The wrapper accepts it. The gate does not.

## Evidence

Base: `origin/cursor/l1-writer-lock-identity-16b2` at `fe7c076db762b440f0ffc7c524c254223b671434`.

Wrapper (`scripts/with_writer_lock.py` on that base): `acquire_writer_lock` requires a non-empty purpose and stores it. There is no purpose allowlist. A call with purpose `att0-restore` acquires, writes that purpose, and can run the child.

Gate (`scripts/sor_writer_gate.py` on that base): `WRITER_PURPOSE_ALLOWLIST` is an exact-match frozenset. Its members are `att0-migrate`, `att0 meta fill`, `embed_batch`, `embed_backfill`, `pr1_schema`, `sidecar_apply`, `post_exit_catchup`, `rem`, `rem-legacy`, `embed-rem`, `embed_rem`, and `reembed-legacy`. `att0-restore` is not in the set.

`_identity_decision` returns `purpose not allowed` when the lock purpose is outside that set. With the wrapper's token in the environment, `writer_lock_held` then refuses the child (`writer lock identity refused: purpose not allowed`). A control with purpose `att0-migrate`, same pid and same token, returns `match`.

The drop hook does not consult the allowlist. It only compares `MAILROOM_SEARCH_RESUME_RUN_ID` to the deadline file. So the `+26` drop for the `-R` run-id can succeed while the child's later SoR open is still refused.

## Options considered

1. Add `att0-restore` to `WRITER_PURPOSE_ALLOWLIST`. Not done. The brief says not to edit `sor_writer_gate.py` or any allowlist.
2. Change the restore purpose to an allowlisted string such as `att0-migrate`. Not done. The brief names the purpose `att0-restore`. Substituting a different purpose would hide the gap.
3. Leave the wrapper drop as specified and report the gate refusal. This is the option taken.

## Proposed fix

Mailroom adds the exact string `att0-restore` to `WRITER_PURPOSE_ALLOWLIST` in `scripts/sor_writer_gate.py` on PR #90 (or a follow-up that #89 rebases onto). Do not alias it to `att0-migrate`. Until that string is in the set, a wrapped restore child that presents the lock token is refused, even after `+26` has been dropped.

No other file in this change should grow a purpose allowlist.
