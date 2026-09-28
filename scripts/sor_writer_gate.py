#!/usr/bin/env python3
"""Fail-closed rem-aware SoR writer gate (KOO-85).

Same-sqlite dual-writer HARD DECK: never two-wide writers on one
``.sqlite``. While rem-legacy (or another job) is the sole writer on
live basename ``mailroom.sqlite``, classic SoR writers CONFLICT.

Copy DBs (name contains copy / mailroom-copy / not basename
mailroom.sqlite) are allowed; they still use the normal lock.

This gate does not start rem-legacy, does not restart rem, and does
not require MBP. Prefer ``MAILROOM_DB=copy`` until rem EXIT 0.

``is_live_sor`` compares realpaths (symlinks resolved). Live checks
are ``rem_process_hits`` and ``writer_lock_held``. When the wrapper
has passed ``MAILROOM_WRITER_LOCK_TOKEN``, a held lock is allowed only
if that token matches the lock file's ``writer_token`` field, the
recorded pid is this process or a live ancestor, and the purpose is
allowlisted. A present token with a free lock is a conflict. No token
keeps the previous probe.
``SOR_FORCE_LIVE_CHECKS=1`` can turn the live checks on for a path
that is not the live SoR. Nothing turns them off on the live path.

  python3 sor_writer_gate.py --db /tmp/mailroom.sqlite
  python3 sor_writer_gate.py --db /tmp/mailroom-copy.sqlite
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import fcntl
import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Callable, Iterable

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

# Lock-file purposes a wrapped child may recognize. Exact match.
# att0-migrate: A2 wrapper purpose for scripts/attachments/migrate_att0_schema.py.
# att0-restore: AR-R step 5. Exact string. Not an alias of att0-migrate.
# att0 meta fill: A3 job name in scripts/attachments/meta_fill.py.
# embed_batch: embed_backfill.py and embed_lib.py default.
# embed_backfill: docs/pr0/with_writer_lock_DESIGN.md example.
# pr1_schema: migrate_pr1_schema.py default.
# sidecar_apply: embed_sidecar_apply.py DEFAULT_PURPOSE.
# post_exit_catchup: docs/post-exit-catchup.md.
# rem*: REM_LOCK_PURPOSE_NEEDLES.
WRITER_PURPOSE_ALLOWLIST = frozenset(
    (
        "att0-migrate",
        "att0-restore",
        "att0 meta fill",
        "embed_batch",
        "embed_backfill",
        "pr1_schema",
        "sidecar_apply",
        "post_exit_catchup",
        "rem",
        "rem-legacy",
        "embed-rem",
        "embed_rem",
        "reembed-legacy",
    )
)

FORCE_LIVE_CHECKS_ENV = "SOR_FORCE_LIVE_CHECKS"
MAX_ANCESTOR_DEPTH = 32
PROC_PIDTBSDINFO = 3
_DARWIN_MAXCOMLEN = 16


class AncestorWalkError(Exception):
    """Parent-pid walk failed. ``code`` is a short token, never a secret."""

    def __init__(self, code: str) -> None:
        self.code = code
        Exception.__init__(self, code)


class ProcBsdInfo(ctypes.Structure):
    """Darwin ``proc_bsdinfo``. ``pbi_ppid`` is at byte 16; size is 136.

    Field order matches the public ``bsd/sys/proc_info.h`` layout.
    """

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * _DARWIN_MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * _DARWIN_MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


LOOKAHEAD_NEEDLE = (
    "if rem/writer on live SoR → refuse calendar SoR writers same cycle; "
    "skip/rem-safe **before** the clock"
)

REFUSE_WHILE_REM_ON_LIVE_SOR = (
    "MBP 8pm (`imap_newmail`+`classify`+`notify_bills`); the MBP is a non-writer (rollback, read-only)",
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


def _resolved_path(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    try:
        return raw.resolve()
    except (OSError, RuntimeError):
        return raw


def is_live_sor(path: str | Path) -> bool:
    """True when the resolved path's basename is the live SoR.

    Symlinks are resolved first. A link named ``mailroom.sqlite`` that
    points at a copy is not live. A link with another name that points
    at ``mailroom.sqlite`` is live.
    """
    return _resolved_path(path).name == SOR_BASENAME


def live_checks_apply(path: str | Path) -> bool:
    """Live checks on the real path, or forced on elsewhere.

    ``SOR_FORCE_LIVE_CHECKS=1`` can only turn checks on. A live path
    stays checked for every other value, including ``0``.
    """
    if is_live_sor(path):
        return True
    return os.environ.get(FORCE_LIVE_CHECKS_ENV) == "1"


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


def _libproc_proc_pidinfo(
    pid: int,
    flavor: int,
    arg: int,
    info: ProcBsdInfo,
    size: int,
) -> int:
    """Darwin ``proc_pidinfo`` syscall. Tests pass a replacement instead."""
    names = []
    found = ctypes.util.find_library("System")
    if found:
        names.append(found)
    names.append("/usr/lib/libSystem.B.dylib")
    lib = None
    for name in names:
        try:
            lib = ctypes.CDLL(name, use_errno=True)
            break
        except OSError:
            lib = None
    if lib is None:
        raise AncestorWalkError("syscall")
    fn = lib.proc_pidinfo
    fn.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    fn.restype = ctypes.c_int
    try:
        written = fn(
            int(pid),
            int(flavor),
            ctypes.c_uint64(int(arg)),
            ctypes.byref(info),
            int(size),
        )
    except OSError as exc:
        raise AncestorWalkError("syscall") from exc
    if int(written) <= 0 and ctypes.get_errno() == errno.ESRCH:
        raise AncestorWalkError("no-such-process")
    return int(written)


def darwin_ppid(
    pid: int,
    *,
    proc_pidinfo: Callable[..., int] | None = None,
) -> int:
    """Parent pid via libproc ``proc_pidinfo``. Fail-closed on any error."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise AncestorWalkError("bad-pid")
    info = ProcBsdInfo()
    size = ctypes.sizeof(info)
    call = proc_pidinfo if proc_pidinfo is not None else _libproc_proc_pidinfo
    try:
        written = call(int(pid), PROC_PIDTBSDINFO, 0, info, size)
    except AncestorWalkError:
        raise
    except Exception as exc:
        raise AncestorWalkError("syscall") from exc
    if isinstance(written, bool) or not isinstance(written, int):
        raise AncestorWalkError("syscall")
    if written <= 0 or written != size:
        raise AncestorWalkError("syscall")
    if int(info.pbi_pid) != int(pid):
        raise AncestorWalkError("pid-mismatch")
    parent = int(info.pbi_ppid)
    if parent < 0:
        raise AncestorWalkError("negative-ppid")
    return parent


def linux_ppid(pid: int) -> int:
    """Parent pid from ``/proc/<pid>/status``. Fail-closed on a bad read."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise AncestorWalkError("bad-pid")
    status = Path("/proc") / str(pid) / "status"
    try:
        text = status.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AncestorWalkError("no-such-process") from exc
    except OSError as exc:
        raise AncestorWalkError("unreadable") from exc
    for line in text.splitlines():
        if line.startswith("PPid:"):
            raw = line.split(":", 1)[1].strip()
            try:
                parent = int(raw)
            except ValueError as exc:
                raise AncestorWalkError("bad-ppid") from exc
            if parent < 0:
                raise AncestorWalkError("negative-ppid")
            return parent
    raise AncestorWalkError("bad-ppid")


def ppid_of(pid: int) -> int:
    if sys.platform == "darwin":
        return darwin_ppid(pid)
    if sys.platform.startswith("linux"):
        return linux_ppid(pid)
    raise AncestorWalkError("unsupported")


def ancestor_pids(
    pid: int,
    *,
    max_depth: int = MAX_ANCESTOR_DEPTH,
    ppid_fn: Callable[[int], int] | None = None,
) -> list[int]:
    """Parent chain, not including ``pid``. Max depth 32. Fail-closed.

    Stops at pid 0 or after including pid 1. A missing start pid raises
    ``no-such-process``. Any later error, cycle, or depth overflow raises
    a different code so callers can refuse.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise AncestorWalkError("bad-pid")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth <= 0:
        raise AncestorWalkError("bad-pid")
    fn = ppid_fn if ppid_fn is not None else ppid_of
    ancestors: list[int] = []
    seen = {pid}
    current = pid
    while len(ancestors) < max_depth:
        try:
            parent = fn(current)
        except AncestorWalkError as exc:
            if exc.code == "no-such-process" and not ancestors:
                raise
            if exc.code == "no-such-process":
                raise AncestorWalkError("walk-error") from exc
            raise
        except Exception as exc:
            raise AncestorWalkError("ppid-failed") from exc
        if isinstance(parent, bool) or not isinstance(parent, int):
            raise AncestorWalkError("bad-ppid")
        if parent < 0:
            raise AncestorWalkError("negative-ppid")
        if parent == 0:
            return ancestors
        if parent in seen:
            raise AncestorWalkError("cycle")
        ancestors.append(parent)
        if parent == 1:
            return ancestors
        seen.add(parent)
        current = parent
    raise AncestorWalkError("max-depth")


def _excluded_pids(
    me: int,
    *,
    ancestors: Iterable[int] | None,
) -> set[int]:
    """Self plus the walked ancestor chain. Never an env pid by itself.

    ``MAILROOM_WRITER_LOCK_PID`` is omitted only when that walk lists it.
    A failed walk excludes only self, so a rem-like process stays a hit.
    """
    exclude = set()
    if isinstance(me, int) and not isinstance(me, bool) and me > 0:
        exclude.add(me)
    if ancestors is None:
        try:
            chain = ancestor_pids(me)
        except AncestorWalkError:
            chain = []
    else:
        chain = list(ancestors)
    for pid in chain:
        if isinstance(pid, bool) or not isinstance(pid, int):
            raise AncestorWalkError("bad-ppid")
        if pid > 0:
            exclude.add(pid)
    return exclude


def rem_process_hits(
    *,
    self_pid: int | None = None,
    cmdlines: Iterable[tuple[int, str]] | None = None,
    proc_dir: Path | None = None,
    ancestors: Iterable[int] | None = None,
) -> list[tuple[int, str]]:
    """Rem cmdline hits. Excludes self and the walked ancestor chain.

    The env wrapper pid is not trusted on its own. A failed walk excludes
    only self.
    """
    me = os.getpid() if self_pid is None else self_pid
    exclude = _excluded_pids(me, ancestors=ancestors)
    hits: list[tuple[int, str]] = []
    for pid, line in iter_cmdlines(proc_dir=proc_dir, cmdlines=cmdlines):
        if pid in exclude:
            continue
        if is_rem_cmdline(line):
            hits.append((pid, line))
    return hits


def lock_purpose_is_rem(purpose: str) -> bool:
    low = (purpose or "").strip().lower()
    return any(needle == low or needle in low for needle in REM_LOCK_PURPOSE_NEEDLES)


def _probe_writer_lock(
    path: Path | None = None,
    *,
    held: bool | None = None,
) -> tuple[bool, str]:
    """Today's flock probe. Drops the probe fd. Never keeps that lock.

    ``LOCK_UN`` runs only on the probe fd after this probe acquired it.
    A ``BlockingIOError`` means someone else holds the lock; that fd is
    closed without ``LOCK_UN``.
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


def _identity_token() -> str | None:
    raw = os.environ.get(wwl.LOCK_TOKEN_ENV)
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return text


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time token compare. Hashes first so length is not a leak."""
    try:
        a = hashlib.sha256(left.encode("utf-8")).digest()
        b = hashlib.sha256(right.encode("utf-8")).digest()
    except Exception:
        return False
    return hmac.compare_digest(a, b)


def pid_is_live(pid: int) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.EPERM:
            return True
        return False
    return True


def _identity_decision(lock: Path, *, child_pid: int | None = None) -> tuple[bool, str]:
    """Held-lock identity. True only when every check matches.

    Reasons are fixed strings. They never include the token.
    """
    token = _identity_token()
    info = wwl.read_lock_info(lock)
    if token is None or not tokens_equal(info.writer_token or "", token):
        return False, "token mismatch"
    if info.pid is None or not pid_is_live(info.pid):
        return False, "pid not live"
    me = os.getpid() if child_pid is None else child_pid
    if info.pid != me:
        try:
            chain = ancestor_pids(me)
        except AncestorWalkError:
            return False, "ancestor walk error"
        if info.pid not in chain:
            return False, "pid not ancestor"
    if (info.purpose or "") not in WRITER_PURPOSE_ALLOWLIST:
        return False, "purpose not allowed"
    return True, "match"


def writer_lock_held(
    path: Path | None = None,
    *,
    held: bool | None = None,
) -> tuple[bool, str]:
    """Probe the exclusive writer lock, then apply the identity matrix.

    With no identity token in the environment the return is the probe:
    ``(held, detail)``, byte-for-byte the previous behavior, including
    the injected ``held=`` short circuit.

    With a token, the boolean is the gate decision (True means refuse):

    - probe free: refuse (the wrapper is gone; do not write unlocked)
    - probe held and identity matches: allow
    - probe held and any miss or walk error: refuse
    """
    probed_held, probed_detail = _probe_writer_lock(path, held=held)
    if _identity_token() is None:
        return probed_held, probed_detail
    if not probed_held:
        return True, "writer lock identity refused: lock not held"
    lock = Path(path).expanduser() if path is not None else wwl.default_lock_path()
    matched, why = _identity_decision(lock)
    if matched:
        return False, "writer lock held by wrapper"
    return True, "writer lock identity refused: %s" % why


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
    applies at the caller) unless ``SOR_FORCE_LIVE_CHECKS=1``. That flag
    cannot turn checks off on a live path.
    """
    path = Path(db).expanduser()
    if not live_checks_apply(path):
        return
    try:
        hits = rem_process_hits(
            self_pid=self_pid, cmdlines=cmdlines, proc_dir=proc_dir
        )
    except AncestorWalkError:
        raise SorWriterRefuse(conflict_message("ancestor walk failed")) from None
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
