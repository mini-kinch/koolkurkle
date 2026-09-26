#!/usr/bin/env python3
"""Copy-only MAILROOM_DB helper for Mini daily children.

Same allowlist as mailroom_daily.py. Honor --db, then $MAILROOM_DB.
After SoR cutover, explicit mailroom.sqlite is allowed (db_mode=sor)
alongside the two copy basenames. Unset / unknown still refuse.
Hard-fail, not fail-open. No silent default to the SoR name. The SoR
basename is allowed only when explicitly named, and rem-legacy must
be absent.

Rem-gated copy: Mini copy only when rem-legacy is not writing, or
after rem-legacy EXIT 0. No SMB/NFS dual-write. No live MBP→Mini
copy from this helper. The rem-aware SoR writer gate refuses
mailroom.sqlite (CONFLICT) when rem-legacy or the writer lock is
held; prefer MAILROOM_DB=copy.

Daily children (imap_newmail, imap_tombstone, imap_fetch_bodies_fts /
imap_fetch_bodies, classify, notify_bills) resolve the DB through
bind_copy_db() / resolve_from_argv() instead of a hardcoded SoR path
(t.DB or ~/MailArchive/mailroom.sqlite). argv=None means sys.argv[1:]
— otherwise --db on the process command line is ignored and a child
can still open Mini's empty SoR stub. embed_backfill.py already
accepts --db; the orchestrator still passes --db and MAILROOM_DB so
every child opens the same copy as the driver.

  /usr/bin/python3 mailroom_copy_db.py --db /tmp/mailroom-copy.sqlite
  /usr/bin/python3 mailroom_copy_db.py --db /tmp/mailroom.sqlite
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from refuse_destructive import (
    DestructiveRefuse,
    refuse_destructive_cli,
    refuse_imap_purge_cli,
)
from sor_writer_gate import (
    SorWriterRefuse,
    refuse_copy_from_live_sor,
    refuse_intended_sor_writer,
)

COPY_DB_BASENAMES = frozenset(
    {
        "mailroom-copy.sqlite",
        "mailroom-daily-copy.sqlite",
    }
)
SOR_BASENAME = "mailroom.sqlite"
# SoR basename is allowed only when --db / MAILROOM_DB names it.
# Unset and unknown basenames still refuse. Not a silent default.
ALLOWED_DB_BASENAMES = COPY_DB_BASENAMES | {SOR_BASENAME}
ALLOWLIST_HELP = (
    "mailroom-copy.sqlite, mailroom-daily-copy.sqlite, or mailroom.sqlite "
    "(SoR basename allowed only when explicitly named; rem-legacy must be absent)"
)


class CopyDbRefuse(RuntimeError):
    """Hard refuse (db_mode=refused). No silent SoR default."""


def allowed_copy_db(path: Path) -> bool:
    """True for a copy basename or an explicit SoR basename."""
    return path.name in ALLOWED_DB_BASENAMES


def db_mode_for(path: Path) -> str:
    """Return sor for basename mailroom.sqlite, otherwise copy."""
    return "sor" if path.name == SOR_BASENAME else "copy"


def env_db_path() -> Path | None:
    """Return MAILROOM_DB if set. Never silently default to mailroom.sqlite."""
    raw = (os.environ.get("MAILROOM_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return None


def unset_db_message() -> str:
    return (
        "MAILROOM_DB is unset. Set an explicit copy path (basename %s). "
        "The SoR basename is allowed only when explicitly named, and "
        "rem-legacy must be absent. A silent default to mailroom.sqlite "
        "would write the SoR name (empty on Mini, or race rem embed)."
        % ALLOWLIST_HELP
    )


def refuse_copy_db_message(path: Path) -> str:
    return (
        "MAILROOM_DB basename %r is not on the allowlist (%s). "
        "Refusing start. No IMAP/embed. Explicit path required "
        "(copy basenames or mailroom.sqlite; SoR basename allowed only "
        "when explicitly named, and rem-legacy must be absent)."
        % (path.name, ALLOWLIST_HELP)
    )


def resolve_copy_db(cli_db: str | None = None) -> Path:
    """Hard-fail unless basename is on the allowlist (copy or explicit SoR).

    Precedence: --db / cli_db, then $MAILROOM_DB. Unset / unknown refuse.
    SoR basename allowed only when explicitly named; rem-legacy must be absent.
    """
    if cli_db:
        path = Path(str(cli_db)).expanduser()
    else:
        path = env_db_path()
        if path is None:
            raise CopyDbRefuse(unset_db_message())
    if not allowed_copy_db(path):
        raise CopyDbRefuse(refuse_copy_db_message(path))
    return path


def parse_db_cli(argv: list[str] | None) -> str | None:
    """Return --db value from argv, or None. Ignores other flags.

    argv=None reads sys.argv[1:] (the process command line without the
    program name). An explicit empty list means "no CLI flags". Treating
    None like [] would ignore --db on the real command line.
    """
    if argv is None:
        argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--db":
            if i + 1 >= len(argv):
                raise CopyDbRefuse("MAILROOM_DB --db requires a path")
            return argv[i + 1]
        if arg.startswith("--db="):
            return arg.split("=", 1)[1]
        i += 1
    return None


def resolve_from_argv(argv: list[str] | None = None) -> Path:
    """Resolve copy DB from argv --db, else $MAILROOM_DB. Same allowlist.

    argv=None reads sys.argv[1:].
    """
    return resolve_copy_db(parse_db_cli(argv))


def bind_copy_db(argv: list[str] | None = None) -> Path:
    """Resolve the DB, export MAILROOM_DB, emit db_mode=copy|sor.

    Daily children call this before any sqlite open. argv=None means
    sys.argv[1:] so ``bind_copy_db()`` honors a process-level --db.
    Raises CopyDbRefuse for unset / unknown names. An explicit SoR
    basename emits db_mode=sor (rem-legacy must be absent).
    """
    path = resolve_from_argv(argv)
    os.environ["MAILROOM_DB"] = str(path)
    emit_db_mode(db_mode_for(path))
    return path


def child_main(
    argv: list[str] | None = None,
    *,
    name: str = "child",
    cmdlines=None,
    lock_held: bool | None = None,
    lock_path=None,
) -> int:
    """Shared daily-child entry: rem-aware gate, bind DB, fail closed.

    No IMAP, Keychain, or classify work. GitHub contract is SoR bind.
    Mini-local live bodies should call bind_copy_db() the same way.
    Explicit mailroom.sqlite passes when rem-legacy is absent and the
    writer lock is free. Live SoR + rem/lock → CONFLICT (skip/rem-safe
    before the clock).
    """
    del name
    try:
        refuse_destructive_cli(argv)
        refuse_imap_purge_cli(argv)
        refuse_intended_sor_writer(
            argv,
            cmdlines=cmdlines,
            lock_path=lock_path,
            lock_held=lock_held,
        )
        path = bind_copy_db(argv)
    except DestructiveRefuse as exc:
        emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except SorWriterRefuse as exc:
        emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except CopyDbRefuse as exc:
        emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    sys.stdout.write("opened_db=%s\n" % path)
    return 0


def emit_db_mode(mode: str) -> None:
    sys.stderr.write("db_mode=%s\n" % mode)
    sys.stderr.flush()


def child_would_open_sor(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """True if a child would open the SoR basename (or has no copy path).

    Used by negative smoke. A daily child honors --db / MAILROOM_DB via
    the same allowlist. Explicit mailroom.sqlite counts as opening SoR.
    Unset counts as SoR-open / refuse. There is no silent default.
    """
    saved = None
    if env is not None:
        saved = os.environ.get("MAILROOM_DB")
        raw = (env.get("MAILROOM_DB") or "").strip()
        if raw:
            os.environ["MAILROOM_DB"] = raw
        else:
            os.environ.pop("MAILROOM_DB", None)
    try:
        try:
            path = resolve_from_argv(argv)
        except CopyDbRefuse:
            cli = parse_db_cli(argv)
            if cli and Path(cli).expanduser().name == SOR_BASENAME:
                return True
            env_path = env_db_path()
            if env_path is not None and env_path.name == SOR_BASENAME:
                return True
            if cli is None and env_path is None:
                return True
            return False
        return path.name == SOR_BASENAME
    finally:
        if env is not None:
            if saved is None:
                os.environ.pop("MAILROOM_DB", None)
            else:
                os.environ["MAILROOM_DB"] = saved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a Mini daily DB (--db or $MAILROOM_DB). "
            "Allowlist: mailroom-copy.sqlite, mailroom-daily-copy.sqlite, "
            "mailroom.sqlite (SoR basename allowed only when explicitly named; "
            "rem-legacy must be absent). Unset / unknown refused. "
            "No silent default to mailroom.sqlite."
        )
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Copy DB path (overrides $MAILROOM_DB). Same allowlist as the daily driver.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_path",
        help="Print the resolved path on stdout (default).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        refuse_destructive_cli(argv)
        refuse_imap_purge_cli(argv)
    except DestructiveRefuse as exc:
        emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    args = build_parser().parse_args(argv)
    try:
        if args.db:
            refuse_copy_from_live_sor(args.db)
        else:
            # --db is absent: still gate an explicit MAILROOM_DB SoR path.
            # Copy basenames return immediately inside the gate.
            env_path = env_db_path()
            if env_path is not None:
                refuse_copy_from_live_sor(env_path)
        path = resolve_copy_db(args.db)
    except SorWriterRefuse as exc:
        emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except CopyDbRefuse as exc:
        emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    emit_db_mode(db_mode_for(path))
    sys.stdout.write(str(path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
