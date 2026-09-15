#!/usr/bin/env python3
"""Dry / read-only PR-5 preflight (KOO-86). Docs/verify only.

Never enables PR-5, never sets RunAtLoad, never launchctl
enable/bootstrap/load/kickstart, never flips SoR host, never writes
SoR, never opens live IMAP, never restarts rem.

Installed LaunchAgent: plist absent or RunAtLoad disabled is the
HOLD-safe pair. The checked-in template is not an enable.

#40 gate: future SoR writes CONFLICT when rem is live or flock is
held. A stale dead-PID lock file with no flock is not a false
CONFLICT.

  python3 pr5_preflight.py --installed-plist /tmp/missing.plist \\
      --copy-db /tmp/mailroom-copy.sqlite --lock-file /tmp/lock
"""

from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path
from typing import Callable, Iterable

import sor_writer_gate as gate

ENABLE_VERDICT = "NON-GO"
CONFLICT_TOKEN = gate.CONFLICT_TOKEN
SOR_BASENAME = gate.SOR_BASENAME
COPY_HINTS = gate.COPY_HINTS
KEYCHAIN_NAME_ONLY = "mailroom.imap.app-password"
BODY_PEEK_CURL = "/opt/homebrew/opt/curl/bin/curl"
AUTH_HARD_GATE = "lane=auth"

# launchctl verbs this helper must never invoke.
FORBIDDEN_LAUNCHCTL = (
    "launchctl enable",
    "launchctl bootstrap",
    "launchctl load",
    "launchctl kickstart",
)

REQUIRED_POST_REM_GATES = (
    "rem_exit",
    "gate_40_live",
    "catchup_done",
    "single_writer_hard_deck",
    "flock_free",
)

STANDING_CITE = (
    "rem-legacy EXIT completed (17223/17223)",
    "#40 fail-closed sor_writer_gate live on operator MBP",
    "Mailroom catch-up Done after rem EXIT",
    "HOLD still: PR-5 enable / RunAtLoad / SoR host flip / ATT implement / money / external send",
)

ORDER_NEEDLE = "EXIT → gate ALLOW → catch-up → later copy+integrity → then checklist"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class PreflightRefuse(RuntimeError):
    """Fail-closed preflight. Never includes secrets."""


def inspect_plist(path: str | Path | None) -> dict:
    """Read-only plist inspect. Absent path ⇒ state=absent."""
    if path is None:
        return {"state": "absent", "run_at_load": False, "mailroom_db": None}
    target = Path(path).expanduser()
    if not target.exists():
        return {"state": "absent", "run_at_load": False, "mailroom_db": None}
    data = plistlib.loads(target.read_bytes())
    run = bool(data.get("RunAtLoad"))
    env = data.get("EnvironmentVariables") or {}
    db = env.get("MAILROOM_DB")
    return {
        "state": "enabled_run_at_load" if run else "disabled",
        "run_at_load": run,
        "mailroom_db": db,
    }


def installed_not_enabled(info: dict) -> bool:
    """True when installed agent is absent or RunAtLoad disabled."""
    return info.get("state") in ("absent", "disabled")


def path_exists(
    path: str | Path,
    *,
    exists_fn: Callable[[Path], bool] | None = None,
) -> bool:
    target = Path(path).expanduser()
    if exists_fn is not None:
        return bool(exists_fn(target))
    return target.exists()


def is_copy_basename(path: str | Path) -> bool:
    name = Path(path).name
    if name == SOR_BASENAME:
        return False
    low = name.lower()
    return any(hint in low for hint in COPY_HINTS) or name != SOR_BASENAME


def repo_template_copy_only(plist_path: str | Path) -> str:
    """Fail-closed: checked-in daily template must stay copy-only."""
    info = inspect_plist(plist_path)
    raw = info.get("mailroom_db") or ""
    if not raw:
        raise PreflightRefuse("repo template MAILROOM_DB missing")
    if Path(raw).name == SOR_BASENAME:
        raise PreflightRefuse(
            "repo template MAILROOM_DB is SoR stub — Mini promote is NON-GO"
        )
    if not is_copy_basename(raw):
        raise PreflightRefuse("repo template MAILROOM_DB is not a copy basename")
    return raw


def post_rem_gates_pass(evidence: dict | None) -> tuple[bool, list[str]]:
    """Fail-closed: missing or false evidence is not a pass."""
    data = evidence or {}
    missing = [key for key in REQUIRED_POST_REM_GATES if not data.get(key)]
    return (not missing, missing)


def flock_is_free(
    lock_path: str | Path | None,
    *,
    lock_held: bool | None = None,
) -> bool:
    """Probe exclusive flock without stealing or rewriting it."""
    held, _detail = gate.writer_lock_held(
        Path(lock_path) if lock_path is not None else None,
        held=lock_held,
    )
    return not held


def probe_stale_dead_pid_lock(
    lock_path: str | Path,
    db: str | Path,
    *,
    cmdlines: Iterable[tuple[int, str]] | None = None,
) -> str:
    """Leftover lock file + dead PID metadata + no flock ≠ CONFLICT."""
    gate.refuse_if_sor_writer_conflict(
        db,
        cmdlines=() if cmdlines is None else cmdlines,
        lock_path=Path(lock_path),
    )
    return "stale_dead_pid_lock_not_conflict"


def probe_live_rem_refuses(
    db: str | Path,
    *,
    cmdlines: Iterable[tuple[int, str]],
    lock_held: bool = False,
) -> None:
    """Live rem on future SoR writes must CONFLICT."""
    gate.refuse_if_sor_writer_conflict(
        db, cmdlines=cmdlines, lock_held=lock_held
    )


def read_only_integrity_needles(text: str) -> dict[str, bool]:
    """Doc/label needles only — no live PRAGMA / SoR write."""
    return {
        "integrity_pack": "Integrity pack" in text or "integrity_check" in text,
        "copy_freshness": "Copy freshness" in text or "copy_age" in text,
        "embed_key": "Quote-strip generation match" in text
        or "embed generation key" in text,
    }


def hard_deck_survives(text: str) -> bool:
    low = text.lower()
    return "HARD DECK" in text and "one writer" in low


def refuse_enable_flag(enable: bool) -> None:
    if enable:
        raise PreflightRefuse(
            "NON-GO: this helper does not enable PR-5 / RunAtLoad / SoR host flip"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry PR-5 preflight. Always NON-GO. "
            "Does not enable cutover or RunAtLoad."
        )
    )
    parser.add_argument(
        "--installed-plist",
        default=None,
        help="Installed LaunchAgent path (absent or RunAtLoad false is HOLD-safe).",
    )
    parser.add_argument(
        "--repo-plist",
        default=None,
        help="Checked-in template to confirm copy-only MAILROOM_DB.",
    )
    parser.add_argument(
        "--copy-db",
        default=None,
        help="Copy DB path to existence-check (never written).",
    )
    parser.add_argument(
        "--lock-file",
        default=None,
        help="Writer lock path to probe (no steal).",
    )
    parser.add_argument(
        "--sor-db",
        default=None,
        help="SoR basename to gate-probe (never written).",
    )
    parser.add_argument(
        "--cite-standing",
        action="store_true",
        help="Print standing citations. Still NON-GO.",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="Refused. This helper never enables.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        refuse_enable_flag(bool(args.enable))
    except PreflightRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2

    installed = inspect_plist(args.installed_plist)
    not_enabled = installed_not_enabled(installed)
    copy_ok = True
    if args.copy_db:
        copy_ok = path_exists(args.copy_db) and is_copy_basename(args.copy_db)
    flock_free = True
    if args.lock_file is not None:
        flock_free = flock_is_free(args.lock_file)

    if args.repo_plist:
        try:
            repo_template_copy_only(args.repo_plist)
        except PreflightRefuse as exc:
            sys.stderr.write("error: %s\n" % exc)
            return 2

    if args.cite_standing:
        for line in STANDING_CITE:
            sys.stdout.write("cite: %s\n" % line)

    sys.stdout.write("enable_verdict=%s\n" % ENABLE_VERDICT)
    sys.stdout.write("installed_plist_state=%s\n" % installed["state"])
    sys.stdout.write("installed_not_enabled=%s\n" % str(not_enabled).lower())
    sys.stdout.write("copy_path_ok=%s\n" % str(copy_ok).lower())
    sys.stdout.write("flock_free=%s\n" % str(flock_free).lower())
    sys.stdout.write("order=%s\n" % ORDER_NEEDLE)
    sys.stdout.write("keychain_name_only=%s\n" % KEYCHAIN_NAME_ONLY)
    sys.stdout.write("auth_hard_gate=%s\n" % AUTH_HARD_GATE)
    sys.stdout.write("body_peek_curl=%s\n" % BODY_PEEK_CURL)
    if args.sor_db:
        sys.stdout.write("sor_basename=%s\n" % Path(args.sor_db).name)
        sys.stdout.write("sor_written=false\n")
    # Fail-closed on installed enable or missing copy when asked.
    if args.installed_plist is not None and not not_enabled:
        sys.stderr.write(
            "error: installed LaunchAgent RunAtLoad is true — HOLD / NON-GO\n"
        )
        return 2
    if args.copy_db and not copy_ok:
        sys.stderr.write("error: copy DB path missing or not a copy basename\n")
        return 2
    if args.lock_file is not None and not flock_free:
        sys.stderr.write("error: writer flock held — flock free gate failed\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
