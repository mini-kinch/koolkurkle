#!/usr/bin/env python3
"""Compare two fingerprints from ``att0_fp.py``.

Exit 0 when the fingerprint is unchanged, 1 when it changed, and 2 on
a usage error. The last stdout line is ``SOR_FINGERPRINT=IDENTICAL`` or
``SOR_FINGERPRINT=CHANGED``. Earlier lines name the tables and fields
that differ (``DIFF ...``). Notes are not differences.

``--logical`` ignores file bytes, mtime, and stat (``main_sha256``,
``stat_main``, ``stat_wal``, ``stat_shm``) and compares only table
counts and hashes. It still refuses two fingerprints whose exclusion
sets differ, and it still reports added or removed tables. Use it when
the files are different snapshots of the same rows, such as a backup
copy beside the original.

A table present on only one side is ``added`` or ``removed``.
``--allow-added-table NAME`` (repeatable) expects NAME on the after
side only. ATT-0 tables from ``scripts/attachments/schema.sql`` are the
intended use. An allowed add is a note. A hash change on a table that
exists on both sides is still a difference. A removed table is still a
difference.

Fingerprints made with different exclusion sets are not compared
(exit 2).

``skipped_virtual_tables`` is recorded by ``att0_fp.py`` and ignored
here. A missing list, an empty list, and a different list are not
differences, in either mode. Shadow tables are ordinary tables, so
their counts and hashes still compare.

Without ``--logical``, file stats are compared too. An ``-shm`` change
is a note (a read-only reader updates read marks). A ``-wal`` that
appears with size 0 is a note. A new non-empty ``-wal`` is a
difference. If ``-wal`` already existed, its size and mtime must match.
``att0_fp.py`` does not create those sidecars; the notes still apply
when a fingerprint recorded them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCALAR_FIELDS = (
    "journal_mode",
    "db_basename",
    "imap_live_total",
    "imap_live_null_folder",
    "imap_live_folders_n",
    "imap_live_not_on_server",
)


class FpdiffRefuse(Exception):
    """Usage refuse. Exit 2. The message has no filesystem path."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _load(path: Path) -> dict:
    if not path.exists():
        raise FpdiffRefuse("refuse: fingerprint is missing (%s)" % path.name)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise FpdiffRefuse(
            "refuse: cannot read fingerprint (%s)" % path.name
        ) from None
    if not isinstance(doc, dict):
        raise FpdiffRefuse("refuse: fingerprint is not an object (%s)" % path.name)
    return doc


def _exclusion_set(doc: dict, label: str):
    raw = doc.get("exclusions")
    if not isinstance(raw, dict):
        raise FpdiffRefuse("refuse: fingerprint has no exclusions (%s)" % label)
    tables = raw.get("tables")
    columns = raw.get("columns")
    if not isinstance(tables, list) or not isinstance(columns, list):
        raise FpdiffRefuse("refuse: fingerprint exclusions are invalid (%s)" % label)
    if not all(isinstance(item, str) for item in tables + columns):
        raise FpdiffRefuse("refuse: fingerprint exclusions are invalid (%s)" % label)
    return tuple(sorted(tables)), tuple(sorted(columns))


def _tables(doc: dict, label: str) -> dict:
    raw = doc.get("tables")
    if not isinstance(raw, dict):
        raise FpdiffRefuse("refuse: fingerprint has no tables (%s)" % label)
    cleaned = {}
    for name, info in raw.items():
        if not isinstance(name, str) or not isinstance(info, dict):
            raise FpdiffRefuse("refuse: fingerprint tables are invalid (%s)" % label)
        count = info.get("count")
        digest = info.get("sha256")
        if isinstance(count, bool) or not isinstance(count, int):
            raise FpdiffRefuse("refuse: fingerprint tables are invalid (%s)" % label)
        if not isinstance(digest, str):
            raise FpdiffRefuse("refuse: fingerprint tables are invalid (%s)" % label)
        cleaned[name] = {"count": count, "sha256": digest}
    return cleaned


def _allow_set(names) -> set[str]:
    allowed = set()
    for name in names or ():
        text = name.strip()
        if not text or "\x00" in text:
            raise FpdiffRefuse("refuse: invalid allow-added-table")
        allowed.add(text.casefold())
    return allowed


def compare_fingerprints(before: dict, after: dict, logical=False, allow_added=None):
    """Return ``(exit_code, report_text)``."""
    if _exclusion_set(before, "before") != _exclusion_set(after, "after"):
        raise FpdiffRefuse("refuse: exclusion sets differ")
    before_tables = _tables(before, "before")
    after_tables = _tables(after, "after")
    # skipped_virtual_tables is informational. Both modes ignore it.
    allowed = _allow_set(allow_added)
    diffs = []
    notes = []
    names = sorted(set(before_tables) | set(after_tables), key=str.lower)
    for name in names:
        left = before_tables.get(name)
        right = after_tables.get(name)
        if left is None:
            if name.casefold() in allowed:
                notes.append("note added_table %s allowed" % name)
            else:
                diffs.append("DIFF table %s added" % name)
            continue
        if right is None:
            diffs.append("DIFF table %s removed" % name)
            continue
        if left["count"] != right["count"]:
            diffs.append("DIFF table %s count" % name)
        if left["sha256"] != right["sha256"]:
            diffs.append("DIFF table %s sha256" % name)
    if not logical:
        for field in _SCALAR_FIELDS:
            if before.get(field) != after.get(field):
                diffs.append("DIFF field %s" % field)
        if before.get("stat_main") != after.get("stat_main"):
            diffs.append("DIFF stat_main")
        before_wal = before.get("stat_wal")
        after_wal = after.get("stat_wal")
        if before_wal is not None and before_wal != after_wal:
            diffs.append("DIFF stat_wal")
        if before_wal is None and after_wal is not None:
            size = after_wal.get("size") if isinstance(after_wal, dict) else None
            if size == 0:
                notes.append("note wal_created_empty_by_ro_reader")
            else:
                diffs.append("DIFF stat_wal_created_nonempty")
        if before.get("stat_shm") != after.get("stat_shm"):
            notes.append("note shm_changed(reader read-marks; not proof of a write)")
        if before.get("main_sha256") == after.get("main_sha256"):
            notes.append("note main_sha256_identical")
        elif "DIFF stat_main" not in diffs:
            diffs.append("DIFF main_sha256")
    status = "IDENTICAL" if not diffs else "CHANGED"
    lines = list(diffs) + list(notes)
    lines.append("SOR_FINGERPRINT=%s" % status)
    text = "\n".join(lines) + "\n"
    return (0 if not diffs else 1), text


def compare_paths(before, after, logical=False, allow_added=None):
    return compare_fingerprints(
        _load(Path(before)),
        _load(Path(after)),
        logical=logical,
        allow_added=allow_added,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two att0_fp JSON fingerprints. "
            "Exit 0 identical, 1 changed, 2 usage error. "
            "--logical compares only table counts and hashes "
            "and ignores file bytes, mtime, and stat."
        )
    )
    parser.add_argument("before", help="Fingerprint JSON from before")
    parser.add_argument("after", help="Fingerprint JSON from after")
    parser.add_argument(
        "--logical",
        action="store_true",
        help=(
            "Ignore main_sha256 and stat_main/stat_wal/stat_shm. "
            "Compare table counts and hashes only."
        ),
    )
    parser.add_argument(
        "--allow-added-table",
        action="append",
        default=None,
        metavar="NAME",
        help="Expect NAME to appear only on the after side",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    try:
        code, text = compare_paths(
            args.before,
            args.after,
            logical=args.logical,
            allow_added=args.allow_added_table,
        )
    except FpdiffRefuse as exc:
        sys.stderr.write("%s\n" % exc)
        return exc.code
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
