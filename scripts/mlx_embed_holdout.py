#!/usr/bin/env python3
"""KOO-56 Mini MLX embedder holdout (fail-closed, fixtures only).

Required before any MLX cutover: holdout of N frozen message_ids +
cosine-agreement threshold. Fail-closed if miss. Same family+dim+
prefix OR a new model_version / SoR generation. No mid-index switch.
Rem untouched. This module never starts an embedder.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_HOLD_N = 32
DEFAULT_COSINE_THRESHOLD = 0.97
SAME_FAMILY_MODEL_TAG = "qwen3-embedding:8b"
SAME_FAMILY_STORE_DIM = 1024
SAME_FAMILY_NATIVE_DIM = 4096


class MlxHoldoutRefuse(RuntimeError):
    """Fail-closed MLX holdout. Never includes secrets."""


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise MlxHoldoutRefuse(
            "holdout dim mismatch: %s vs %s" % (len(left), len(right))
        )
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        fa = float(a)
        fb = float(b)
        dot += fa * fb
        left_norm += fa * fa
        right_norm += fb * fb
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise MlxHoldoutRefuse("holdout cosine refused on a zero vector")
    return dot / math.sqrt(left_norm * right_norm)


def load_frozen_ids(path: str | Path) -> list[str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        ids = raw.get("message_ids") or raw.get("ids") or []
    else:
        ids = raw
    out = [str(item) for item in ids if str(item).strip()]
    if not out:
        raise MlxHoldoutRefuse("holdout fixture has no frozen message_ids")
    return out


def evaluate_holdout(
    frozen_ids: Sequence[str],
    pairs: Mapping[str, Mapping[str, Sequence[float]]] | Iterable[Mapping[str, Any]],
    *,
    threshold: float = DEFAULT_COSINE_THRESHOLD,
    n_required: int | None = None,
    same_family: bool = True,
    model_tag: str = SAME_FAMILY_MODEL_TAG,
    store_dim: int = SAME_FAMILY_STORE_DIM,
    new_model_version: bool = False,
) -> dict[str, Any]:
    """Fail-closed holdout. Missing id or cosine below threshold refuses."""
    want_n = int(n_required if n_required is not None else DEFAULT_HOLD_N)
    ids = [str(mid) for mid in frozen_ids]
    if len(ids) < want_n:
        raise MlxHoldoutRefuse(
            "holdout requires N=%s frozen message_ids (got %s)" % (want_n, len(ids))
        )
    if same_family and not new_model_version:
        if model_tag != SAME_FAMILY_MODEL_TAG:
            raise MlxHoldoutRefuse(
                "same-family MLX path must keep %s or declare a new model_version"
                % SAME_FAMILY_MODEL_TAG
            )
        if int(store_dim) != SAME_FAMILY_STORE_DIM:
            raise MlxHoldoutRefuse(
                "same-family MLX path must keep store_dim=%s or a new SoR generation"
                % SAME_FAMILY_STORE_DIM
            )
    by_id: dict[str, Mapping[str, Any]]
    if isinstance(pairs, Mapping) and pairs and "ollama" not in next(iter(pairs.values()), {}):
        # already id -> {ollama, mlx}
        sample = next(iter(pairs.values()))
        if isinstance(sample, Mapping) and ("ollama" in sample or "mlx" in sample):
            by_id = {str(k): dict(v) for k, v in pairs.items()}
        else:
            by_id = {}
    else:
        by_id = {str(k): dict(v) for k, v in dict(pairs).items()}
    scores: dict[str, float] = {}
    for mid in ids[:want_n]:
        row = by_id.get(mid)
        if row is None:
            raise MlxHoldoutRefuse("holdout miss: frozen message_id missing %s" % mid)
        left = row.get("ollama")
        right = row.get("mlx")
        if left is None or right is None:
            raise MlxHoldoutRefuse("holdout miss: frozen message_id missing %s" % mid)
        score = cosine(list(left), list(right))
        if score < float(threshold):
            raise MlxHoldoutRefuse(
                "holdout fail-closed: cosine %s < threshold %s for %s"
                % (score, threshold, mid)
            )
        scores[mid] = score
    return {
        "ok": True,
        "n": want_n,
        "threshold": float(threshold),
        "min_cosine": min(scores.values()) if scores else None,
        "same_family": bool(same_family) and not new_model_version,
        "new_model_version": bool(new_model_version),
    }
