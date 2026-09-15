#!/usr/bin/env python3
"""KOO-55 post-rem same-writer embed batch bump (config + fail-closed).

AFTER rem-legacy EXIT 0 only. First next-run bump is 32, then 64 if
stable. 256 is not the first bump. Forbid mid-job bump. Same
qwen3-embedding:8b / 1024-d store / instruction prefix. Commit per
batch. Shipping this config ≠ starting rem-legacy and ≠ enabling the
bump against a live rem job.
"""

from __future__ import annotations

from pathlib import Path

from sor_writer_gate import refuse_if_sor_writer_conflict

# Rem-legacy stays on 8 until EXIT 0. Do not mid-job hot-swap.
REM_LEGACY_BATCH_SIZE = 8
# Heavy 04 / §5: first bump 32, then 64 if stable — NOT 256 first.
POST_REM_NEXT_RUN_BATCH_SIZE = 32
POST_REM_NEXT_RUN_BATCH_IF_STABLE = 64
POST_REM_BATCH_BAND = (32, 64)
POST_REM_HOLD_256 = 256
POST_REM_MODEL_TAG = "qwen3-embedding:8b"
POST_REM_STORE_DIM = 1024
POST_REM_NATIVE_DIM = 4096
COMMIT_PER_BATCH = True


class PostRemBatchRefuse(RuntimeError):
    """Fail-closed batch-bump contract. Never includes secrets."""


def validate_post_rem_batch_size(
    batch_size: int,
    *,
    mid_job: bool = False,
    rem_exit_0: bool = False,
    human_go: bool = False,
    first_bump_done: bool = False,
    first_bump_stable: bool = False,
) -> int:
    """Refuse mid-job, pre-EXIT, and 256-first bumps.

    Docs PR Ready is not permission to enable this against live rem.
    """
    size = int(batch_size)
    if mid_job:
        raise PostRemBatchRefuse(
            "forbid mid-job batch bump; rem-legacy stays %s until EXIT 0"
            % REM_LEGACY_BATCH_SIZE
        )
    if not rem_exit_0:
        raise PostRemBatchRefuse(
            "post-rem batch bump is AFTER EXIT 0 only; rem-legacy stays %s"
            % REM_LEGACY_BATCH_SIZE
        )
    if not human_go:
        raise PostRemBatchRefuse(
            "post-rem batch bump needs EXIT 0 + human go; "
            "Ready ≠ enable against live rem"
        )
    if size == POST_REM_HOLD_256:
        raise PostRemBatchRefuse(
            "256 is not the first bump; measured holdout only after 32 then 64"
        )
    if size == POST_REM_NEXT_RUN_BATCH_IF_STABLE:
        if not first_bump_done or not first_bump_stable:
            raise PostRemBatchRefuse(
                "first bump is 32; 64 only if that bump is stable"
            )
        return size
    if size == POST_REM_NEXT_RUN_BATCH_SIZE:
        return size
    raise PostRemBatchRefuse(
        "post-EXIT next-run batch is 32 first, then 64 if stable (got %s)"
        % size
    )


def refuse_post_rem_against_live_rem_sor(db: str | Path, **kwargs) -> None:
    """Post-rem levers refuse live rem SoR (batch bump / sidecar / Mini MLX)."""
    refuse_if_sor_writer_conflict(db, **kwargs)


def next_run_default_argv() -> tuple[str, ...]:
    """Documented next-run default AFTER EXIT 0 + human go. Not a live start."""
    return (
        "--batch-size",
        str(POST_REM_NEXT_RUN_BATCH_SIZE),
        "--quote-strip",
        "--lock",
    )
