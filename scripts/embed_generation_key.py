#!/usr/bin/env python3
"""KOO-58 embed generation key (model/runtime/dim/prefix).

Locked key: model_tag + embed_runtime + native_dim + store_dim +
instruction_prefix. Refuse writes that mismatch embedding_meta.
Aligns with embed_lib.py (4096 native / 1024 store).
"""

from __future__ import annotations

from typing import Any, Mapping

GENERATION_KEY_FIELDS = (
    "model_tag",
    "embed_runtime",
    "native_dim",
    "store_dim",
    "instruction_prefix",
)
DEFAULT_MODEL_TAG = "qwen3-embedding:8b"
DEFAULT_EMBED_RUNTIME = "ollama"
DEFAULT_NATIVE_DIM = 4096
DEFAULT_STORE_DIM = 1024
DEFAULT_INSTRUCTION_PREFIX = (
    "Instruct: Given a mail search query, retrieve the most relevant email.\n"
    "Query: "
)
MLX_EMBED_RUNTIME = "mlx"
_QWEN3_8B_ALIASES = frozenset(
    {
        "qwen3-embedding:8b",
        "qwen3-embedding",
        "qwen3-embedding:latest",
        "qwen3-embedding-8b",
    }
)


class GenerationKeyRefuse(RuntimeError):
    """Fail-closed generation-key mismatch. Never includes secrets."""


def normalize_model_tag(model_tag: str | None) -> str:
    tag = (model_tag or DEFAULT_MODEL_TAG).strip()
    if tag.lower() in _QWEN3_8B_ALIASES:
        return DEFAULT_MODEL_TAG
    return tag


def embed_generation_key(
    *,
    model_tag: str | None = None,
    embed_runtime: str | None = None,
    native_dim: int | None = None,
    store_dim: int | None = None,
    instruction_prefix: str | None = None,
) -> dict[str, Any]:
    return {
        "model_tag": normalize_model_tag(model_tag),
        "embed_runtime": (embed_runtime or DEFAULT_EMBED_RUNTIME).strip(),
        "native_dim": int(native_dim if native_dim is not None else DEFAULT_NATIVE_DIM),
        "store_dim": int(store_dim if store_dim is not None else DEFAULT_STORE_DIM),
        "instruction_prefix": (
            DEFAULT_INSTRUCTION_PREFIX
            if instruction_prefix is None
            else str(instruction_prefix)
        ),
    }


def locked_v1_key() -> dict[str, Any]:
    return embed_generation_key()


def generation_key_tuple(key: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(key[field] for field in GENERATION_KEY_FIELDS)


def keys_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return generation_key_tuple(left) == generation_key_tuple(right)


def refuse_generation_mismatch(
    stored: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    *,
    locked: Mapping[str, Any] | None = None,
) -> None:
    """Refuse writes that mismatch embedding_meta or the locked v1 key."""
    want = locked or locked_v1_key()
    if stored is None:
        if not keys_match(incoming, want):
            raise GenerationKeyRefuse(
                "incoming generation key mismatches locked v1 embedding_meta key"
            )
        return
    if not keys_match(stored, incoming):
        raise GenerationKeyRefuse(
            "refuse write: generation key mismatches embedding_meta"
        )


def meta_row_to_key(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Best-effort map of embedding_meta columns onto the generation key."""
    if row is None:
        return None
    mapping = dict(row)
    if not mapping:
        return None
    model = mapping.get("embed_model") or mapping.get("model_tag") or mapping.get("model")
    runtime = mapping.get("embed_runtime")
    native = mapping.get("native_dim")
    store = mapping.get("store_dim") or mapping.get("embed_dim") or mapping.get("dims")
    prefix = mapping.get("instruction_prefix")
    if model is None and store is None and runtime is None:
        return None
    return embed_generation_key(
        model_tag=str(model) if model is not None else None,
        embed_runtime=str(runtime) if runtime is not None else None,
        native_dim=int(native) if native is not None else None,
        store_dim=int(store) if store is not None else None,
        instruction_prefix=str(prefix) if prefix is not None else None,
    )
