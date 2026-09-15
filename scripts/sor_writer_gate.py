#!/usr/bin/env python3
"""Fail-closed rem-aware SoR writer gate (KOO-85).

Same-sqlite dual-writer HARD DECK: never two-wide writers on one
``.sqlite``. While rem-legacy (or another job) is the sole writer on
live basename ``mailroom.sqlite``, classic SoR writers CONFLICT.

Copy DBs (name contains copy / mailroom-copy / not basename
mailroom.sqlite) are allowed; they still use the normal lock.

This gate does not start rem-legacy, does not restart rem, and does
not require MBP. Prefer ``MAILROOM_DB=copy`` until rem EXIT 0.

  python3 sor_writer_gate.py --db /tmp/mailroom.sqlite
  python3 sor_writer_gate.py --db /tmp/mailroom-copy.sqlite
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path
from typing import Iterable

import with_writer_lock as wwl

SOR_BASENAME = "mailroom.sqlite"
COPY_HINTS = ("copy", "mailroom-copy")
CONFLICT_TOKEN = "CONFLICT"
CONFLICT_EXIT = 2

# Fail-closed rem / rem-legacy process needles (cmdline / lock purpose).
REM_CMDLINE_NEEDLES = (
    "rem-legacy",
    "--reembed-legacy",
    "embed-rem",
    "embed_rem",
)
EMBED_BACKFILL_NEEDLE = "embed_backfill"
REM_LOCK_PURPOSE_NEEDLES = (
    "rem",
    "rem-legacy",
    "embed-rem",
    "embed_rem",
    "reembed-legacy",
)

LOOKAHEAD_NEEDLE = (
    "if rem/writer on live SoR → refuse calendar SoR writers same cycle; "
    "skip/rem-safe **before** the clock"
)

REFUSE_WHILE_REM_ON_LIVE_SOR = (
    "Classic MBP 8pm (`imap_newmail`+`classify`+`notify_bills` → SoR)",
    "IMAP writers on SoR (`imap_newmail`/`tombstone`/`fetch_bodies*`)",
    "Classify/bills SoR writes",
    "Second `embed_backfill` / shard / merge-apply on same sqlite",
    "`embed_merge_shards` into live SoR",
    "`mailroom_copy_db` from live SoR while rem writing",
    "ATT SoR A/D/F (catalog/apply-text/apply-vec); file-only B/C/E OK",
    "Destructive maintenance (purge/EXPUNGE/DELETE messages/JSONL rewrite)",
    "PR-5 cutover / RunAtLoad enable",
    "Post-rem levers (batch bump / Mini MLX / sidecar) against live rem SoR",
)

ALLOW_WHILE_REM = (
    "ask_mail/semantic_search/sor_health read-only",
    "Mini daily copy-only",
    "file-stage on copy",
    "rem-safe docs/tests",
)

COPY_DB_SUGGEST = (
    "Use a copy DB (MAILROOM_DB=copy / mailroom-copy.sqlite) until rem EXIT 0."
)


class SorWriterRefuse(RuntimeError):
    """Rem / lock CONFLICT on live SoR. Never includes secrets."""


def is_live_sor(path: str | Path) -> bool:
    return Path(path).name == SOR_BASENAME


def is_copy_db(path: str | Path) -> bool:
    """True when the basename is clearly a copy (or not live SoR)."""
    name = Path(path).name
    if name == SOR_BASENAME:
        return False
    low = name.lower()
    if any(hint in low for hint in COPY_HINTS):
        return True
    return True


def conflict_message(detail: str) -> str:
    return (
        "%s: rem-legacy or another writer holds live SoR %s (%s). "
        "Refuse calendar SoR writers this cycle. "
        "Skip/rem-safe before the clock. %s"
        % (CONFLICT_TOKEN, SOR_BASENAME, detail, COPY_DB_SUGGEST)
    )


def parse_db_from_cmdline(cmdline: str) -> Path | None:
    parts = (cmdline or "").replace("\0", " ").split()
    for i, part in enumerate(parts):
        if part == "--db" and i + 1 < len(parts):
            return Path(parts[i + 1]).expanduser()
        if part.startswith("--db="):
            return Path(part.split("=", 1)[1]).expanduser()
        if part == "--primary" and i + 1 < len(parts):
            return Path(parts[i + 1]).expanduser()
        if part.startswith("--primary="):
            return Path(part.split("=", 1)[1]).expanduser()
    return None


def same_db_path(left: Path, right: Path) -> bool:
    a = Path(left).expanduser()
    b = Path(right).expanduser()
    try:
        if a.exists() and b.exists():
            return a.samefile(b)
    except OSError:
        pass
    return a.as_posix() == b.as_posix()


def is_rem_cmdline(cmdline: str) -> bool:
    """True for rem-legacy / --reembed-legacy / known rem PID patterns."""
    text = (cmdline or "").replace("\0", " ")
    low = text.lower()
    if any(needle.lower() in low for needle in REM_CMDLINE_NEEDLES):
        return True
    if EMBED_BACKFILL_NEEDLE in text:
        target = parse_db_from_cmdline(text)
        if target is None or is_live_sor(target):
            return True
    return False


def iter_proc_cmdlines(proc_dir: Path | None = None) -> Iterable[tuple[int, str]]:
    root = proc_dir if proc_dir is not None else Path("/proc")
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        yield int(entry.name), raw.replace(b"\0", b" ").decode("utf-8", "replace")


def iter_ps_cmdlines() -> Iterable[tuple[int, str]]:
    import subprocess

    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return
    for line in proc.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(None, 1)
        if not parts or not parts[0].isdigit():
            continue
        yield int(parts[0]), parts[1] if len(parts) > 1 else ""


def iter_cmdlines(
    *,
    proc_dir: Path | None = None,
    cmdlines: Iterable[tuple[int, str]] | None = None,
) -> Iterable[tuple[int, str]]:
    if cmdlines is not None:
        yield from cmdlines
        return
    seen = False
    for item in iter_proc_cmdlines(proc_dir):
        seen = True
        yield item
    if not seen:
        yield from iter_ps_cmdlines()


def rem_process_hits(
    *,
    self_pid: int | None = None,
    cmdlines: Iterable[tuple[int, str]] | None = None,
    proc_dir: Path | None = None,
) -> list[tuple[int, str]]:
    me = os.getpid() if self_pid is None else self_pid
    hits: list[tuple[int, str]] = []
    for pid, line in iter_cmdlines(proc_dir=proc_dir, cmdlines=cmdlines):
        if pid == me:
            continue
        if is_rem_cmdline(line):
            hits.append((pid, line))
    return hits


def lock_purpose_is_rem(purpose: str) -> bool:
    low = (purpose or "").strip().lower()
    return any(needle == low or needle in low for needle in REM_LOCK_PURPOSE_NEEDLES)


def writer_lock_held(
    path: Path | None = None,
    *,
    held: bool | None = None,
) -> tuple[bool, str]:
    """Probe the exclusive writer lock without stealing or rewriting it.

    Returns (held, detail). ``held=`` injects the probe for tests.
    """
    if held is not None:
        return bool(held), "writer lock held (injected)"
    lock = Path(path).expanduser() if path is not None else wwl.default_lock_path()
    if not lock.exists():
        return False, "writer lock absent"
    try:
        fh = lock.open("r+", encoding="utf-8")
    except OSError:
        return False, "writer lock unreadable"
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        info = wwl.read_lock_info(lock)
        fh.close()
        return True, "writer lock held: %s" % info.summary()
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
    return False, "writer lock free"


def refuse_if_sor_writer_conflict(
    db: str | Path,
    *,
    self_pid: int | None = None,
    cmdlines: Iterable[tuple[int, str]] | None = None,
    proc_dir: Path | None = None,
    lock_path: Path | None = None,
    lock_held: bool | None = None,
) -> None:
    """Refuse live SoR writes when rem or a foreign writer lock is present.

    Copy / non-``mailroom.sqlite`` paths are allowed (normal lock still
    applies at the caller). Fail-closed: no env override.
    """
    path = Path(db).expanduser()
    if not is_live_sor(path):
        return
    hits = rem_process_hits(
        self_pid=self_pid, cmdlines=cmdlines, proc_dir=proc_dir
    )
    if hits:
        pid, _line = hits[0]
        raise SorWriterRefuse(conflict_message("rem process pid=%s" % pid))
    held, detail = writer_lock_held(lock_path, held=lock_held)
    if held:
        raise SorWriterRefuse(conflict_message(detail))


def refuse_copy_from_live_sor(
    source: str | Path,
    *,
    self_pid: int | None = None,
    cmdlines: Iterable[tuple[int, str]] | None = None,
    proc_dir: Path | None = None,
    lock_path: Path | None = None,
    lock_held: bool | None = None,
) -> None:
    """Refuse mailroom_copy_db from live SoR while rem/lock is writing."""
    refuse_if_sor_writer_conflict(
        source,
        self_pid=self_pid,
        cmdlines=cmdlines,
        proc_dir=proc_dir,
        lock_path=lock_path,
        lock_held=lock_held,
    )


def intended_db_from_argv(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Path | None:
    """Best-effort --db / $MAILROOM_DB. None means unset."""
    args = sys.argv[1:] if argv is None else argv
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--db":
            if i + 1 >= len(args):
                return None
            return Path(args[i + 1]).expanduser()
        if arg.startswith("--db="):
            return Path(arg.split("=", 1)[1]).expanduser()
        if arg == "--primary":
            if i + 1 >= len(args):
                return None
            return Path(args[i + 1]).expanduser()
        if arg.startswith("--primary="):
            return Path(arg.split("=", 1)[1]).expanduser()
        i += 1
    raw = ""
    if env is not None:
        raw = (env.get("MAILROOM_DB") or "").strip()
    else:
        raw = (os.environ.get("MAILROOM_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return None


def refuse_intended_sor_writer(
    argv: list[str] | None = None,
    *,
    db: str | Path | None = None,
    self_pid: int | None = None,
    cmdlines: Iterable[tuple[int, str]] | None = None,
    proc_dir: Path | None = None,
    lock_path: Path | None = None,
    lock_held: bool | None = None,
) -> None:
    """Gate the explicit --db / MAILROOM_DB / caller path when it is SoR."""
    path = Path(db).expanduser() if db is not None else intended_db_from_argv(argv)
    if path is None:
        return
    refuse_if_sor_writer_conflict(
        path,
        self_pid=self_pid,
        cmdlines=cmdlines,
        proc_dir=proc_dir,
        lock_path=lock_path,
        lock_held=lock_held,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed rem-aware SoR writer gate. "
            "Refuse basename mailroom.sqlite when rem-legacy or the "
            "writer lock is held. Copy DBs are allowed."
        )
    )
    parser.add_argument(
        "--db",
        required=True,
        help="SQLite path to gate (SoR basename mailroom.sqlite is checked).",
    )
    parser.add_argument(
        "--lock-file",
        default=None,
        help="Writer lock path (default: $MAILROOM_WRITE_LOCK or ~/MailArchive/mailroom.write.lock).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lock_path = Path(args.lock_file).expanduser() if args.lock_file else None
    try:
        refuse_if_sor_writer_conflict(args.db, lock_path=lock_path)
    except SorWriterRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return CONFLICT_EXIT
    sys.stdout.write("sor_writer_gate=allow db=%s\n" % Path(args.db).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
