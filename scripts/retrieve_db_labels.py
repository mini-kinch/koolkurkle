#!/usr/bin/env python3
"""KOO-59 Mini retrieve labels: db_mode=copy + copy_age.

Mini ask_mail / semantic_search must label copy DBs. Never imply
live/SoR on a copy. Docs/tests/fail-closed only. No live copy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COPY_DB_BASENAMES = frozenset(
    {
        "mailroom-copy.sqlite",
        "mailroom-daily-copy.sqlite",
    }
)
SOR_BASENAME = "mailroom.sqlite"
FORBIDDEN_COPY_IMPLY = frozenset({"live", "sor", "source-of-record", "source of record"})


class RetrieveLabelRefuse(RuntimeError):
    """Fail-closed retrieve labeling. Never includes secrets."""


def is_copy_basename(path: str | Path) -> bool:
    return Path(path).name in COPY_DB_BASENAMES


def copy_age_seconds(path: str | Path, *, now: datetime | None = None) -> int | None:
    """Age of the copy file mtime in seconds. None if the file is missing."""
    target = Path(path)
    if not target.is_file():
        return None
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
    return max(0, int((clock - mtime).total_seconds()))


def retrieve_db_labels(
    path: str | Path | None,
    *,
    now: datetime | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """Return db_mode + copy_age. Copy never implies live/SoR."""
    if path is None:
        return {"db_mode": "unset", "copy_age": None}
    target = Path(path)
    if is_copy_basename(target):
        labels = {
            "db_mode": "copy",
            "copy_age": copy_age_seconds(target, now=now),
        }
        refuse_copy_implies_live_or_sor(labels)
        return labels
    if target.name == SOR_BASENAME:
        # MBP SoR recipes only. Mini retrieve recipes must not use this name.
        if host and str(host).strip().lower() == "mini":
            raise RetrieveLabelRefuse(
                "Mini retrieve must use a copy DB; never imply live/SoR"
            )
        return {"db_mode": "sor", "copy_age": None}
    return {"db_mode": "other", "copy_age": None}


def refuse_copy_implies_live_or_sor(labels: dict[str, Any]) -> None:
    mode = str(labels.get("db_mode") or "").strip().lower()
    if mode != "copy":
        return
    for key, value in labels.items():
        text = str(value).strip().lower()
        if text in FORBIDDEN_COPY_IMPLY:
            raise RetrieveLabelRefuse(
                "copy retrieve must not imply live/SoR (%s=%s)" % (key, value)
            )
    if mode in FORBIDDEN_COPY_IMPLY:
        raise RetrieveLabelRefuse("copy retrieve must not imply live/SoR")
