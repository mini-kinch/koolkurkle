#!/usr/bin/env python3
"""Search-resume watchdog for com.mailroom.ask-mail-serve.

FAIL-OPEN FOR SEARCH AVAILABILITY, FAIL-CLOSED FOR THE SOR.

Mini-local fallback for an L1 window. S1 takes search out of the gui
domain and writes a deadline file. S2 deletes that file after search is
loaded again. If the operator session dies in between, this process is
what puts search back. It is not KeepAlive on ask-mail-serve.

The exact S1 command list and the AR-R step order are not in this
file. Those come from CoS before install.

CODE AND PLIST TEMPLATE ONLY, NO INSTALL. The LaunchAgent template is
launchd/com.mailroom.search-resume-watchdog.plist.template. Nothing in
this file bootstraps that template.

On each fire the watchdog restores search only when all of these hold:
now is at or past the earliest deadline, ask-mail-serve is not loaded,
and the writer lock is not held. A job that is gone from the gui domain
cannot be started by kickstart alone. The restore is:

  launchctl print gui/$UID/com.mailroom.ask-mail-serve
  launchctl bootstrap gui/$UID $SEARCH_RESUME_PLIST
  launchctl kickstart gui/$UID/com.mailroom.ask-mail-serve

Not loaded means print exits 113, or its output contains
"Could not find service". Any other print result is logged and that
tick is skipped. Exit 0 means the job is loaded. Kickstart is issued
without -k, and only after bootstrap returns 0. If kickstart fails,
one follow-up print: loaded is a success no-op, otherwise the loud
failure. There is at most one bootstrap attempt per tick. No enable.
No print-disabled. The domain is gui/$UID.

The writer lock is observed read-only with lsof and signal 0. This
process does not take the lock (no exclusive probe, including a
non-blocking one), does not open a database, and does not write the
SoR. A detection fault is treated as held and logs
"detection-fault, search left off" on every such tick.

+26 is dropped by drop_early_deadline on the first successful
writer-lock acquire for MAILROOM_SEARCH_RESUME_RUN_ID. +50 stays.

Defaults (override with the env vars):
  SEARCH_RESUME_DEADLINE_FILE  $HOME/MailArchive/state/search_resume_after.epoch
  SEARCH_RESUME_LOG            $HOME/MailArchive/logs/search_resume_watchdog.log
  SEARCH_RESUME_PLIST          $HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist
  MAILROOM_WRITE_LOCK          $HOME/MailArchive/mailroom.write.lock

The wrapper drops +26 only when the caller sets
MAILROOM_SEARCH_RESUME_RUN_ID to the run_id in the deadline file.
That is the only extra input. Unset means do not drop and the
deadline file is not read for a drop. A match drops +26, then
re-reads status. If d26_live is still yes, the child does not run.
A mismatch, or a missing or unreadable file, stops the child.
The wrapper does not invent a run-id.

Commands:
  search_resume_watchdog.py watch
  search_resume_watchdog.py write --run-id <run-id>
  search_resume_watchdog.py arm --run-id <run-id>
  search_resume_watchdog.py status
  search_resume_watchdog.py clear

Docs: docs/search-resume-watchdog.md
Python 3.9+ (macOS /usr/bin/python3).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

LABEL = "com.mailroom.ask-mail-serve"
OFFSET_26 = 26 * 60
OFFSET_50 = 50 * 60
NOT_LOADED_RC = 113
NOT_LOADED_TEXT = "Could not find service"
LOUD = "search still off: writer lock held pid=%s purpose=%s"
DETECTION_FAULT = "detection-fault, search left off"
ALREADY_LOADED = "search already loaded"
MALFORMED = "search resume deadline file malformed"
RUN_ID_ENV = "MAILROOM_SEARCH_RESUME_RUN_ID"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TOKEN_RE = re.compile(r"(?i)\btoken=\S+")
TOKEN_KEYS = frozenset(("token", "lock_token", "writer_token"))
USAGE = (
    "usage: search_resume_watchdog.py watch\n"
    "       search_resume_watchdog.py write --run-id <run-id> [--now <epoch>]\n"
    "       search_resume_watchdog.py arm --run-id <run-id>\n"
    "       search_resume_watchdog.py status\n"
    "       search_resume_watchdog.py clear\n"
)

RunResult = Tuple[int, str, str]
Runner = Callable[[Sequence[str]], RunResult]
# run_id, deadline_26 or None once +26 has been dropped, deadline_50
Deadline = Tuple[str, Optional[int], int]
# free, held (a writer), or fault (detection failed; treat as held)
LockState = Tuple[str, str, str]


class DeadlineRefusal(Exception):
    """drop_early_deadline refused. The deadline file is unchanged."""


class DeadlineMismatch(DeadlineRefusal):
    """The explicit run-id does not match the deadline file."""


def _expand(raw: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(raw)))


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return _expand(raw)


def deadline_path() -> Path:
    return _path_from_env(
        "SEARCH_RESUME_DEADLINE_FILE",
        Path.home() / "MailArchive" / "state" / "search_resume_after.epoch",
    )


def log_path() -> Path:
    return _path_from_env(
        "SEARCH_RESUME_LOG",
        Path.home() / "MailArchive" / "logs" / "search_resume_watchdog.log",
    )


def installed_plist_path() -> Path:
    return _path_from_env(
        "SEARCH_RESUME_PLIST",
        Path.home()
        / "Library"
        / "LaunchAgents"
        / "com.mailroom.ask-mail-serve.plist",
    )


def lock_path() -> Path:
    return _path_from_env(
        "MAILROOM_WRITE_LOCK",
        Path.home() / "MailArchive" / "mailroom.write.lock",
    )


def _uid() -> str:
    raw = os.environ.get("UID", "").strip()
    if raw.isdigit() and int(raw) >= 0:
        return raw
    return str(os.getuid())


def _gui_domain(uid: str) -> str:
    return "gui/%s" % (uid,)


def _gui_target(uid: str) -> str:
    return "gui/%s/%s" % (uid, LABEL)


def _clock() -> int:
    raw = os.environ.get("SEARCH_RESUME_NOW", "").strip()
    if not raw:
        return int(time.time())
    if not raw.isdigit():
        raise ValueError("SEARCH_RESUME_NOW")
    return int(raw)


def _redact(message: str) -> str:
    return TOKEN_RE.sub("token=<redacted>", message)


def _log(path: Path, message: str, loud: bool = False) -> None:
    message = _redact(message).replace("\n", " ").replace("\r", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = "[%s] %s\n" % (stamp, message)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    if loud:
        sys.stderr.write(line)
        sys.stderr.flush()


def _run(argv: Sequence[str]) -> RunResult:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return -1, "", ""
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _not_loaded_signal(rc: int, stdout: str, stderr: str) -> bool:
    """True only for the not-loaded signals. Exit 0 is loaded."""
    if rc == 0:
        return False
    if rc == NOT_LOADED_RC:
        return True
    blob = "%s\n%s" % (stdout or "", stderr or "")
    return NOT_LOADED_TEXT in blob


def render_deadline(run_id: str, now: int) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id")
    if now < 0:
        raise ValueError("now")
    return "run_id=%s\ndeadline_26=%d\ndeadline_50=%d\n" % (
        run_id,
        now + OFFSET_26,
        now + OFFSET_50,
    )


def _atomic_write(path: Path, payload: str) -> None:
    """Temp file in the same directory, fsync, then os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_deadline(path: Path, run_id: str, now: int) -> None:
    payload = render_deadline(run_id, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, payload)


def clear_deadline(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def parse_deadline(text: str) -> Optional[Deadline]:
    fields = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return None
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in fields:
            return None
        fields[key] = value
    run_id = fields.get("run_id", "")
    if not RUN_ID_RE.fullmatch(run_id):
        return None
    if "deadline_50" not in fields:
        return None
    try:
        deadline_50 = int(fields["deadline_50"])
    except (TypeError, ValueError):
        return None
    if deadline_50 < 0:
        return None
    if "deadline_26" not in fields:
        return run_id, None, deadline_50
    try:
        deadline_26 = int(fields["deadline_26"])
    except (TypeError, ValueError):
        return None
    if deadline_26 < 0:
        return None
    return run_id, deadline_26, deadline_50


def format_status(parsed: Deadline) -> str:
    """Fixed key=value lines. No token, no other text on those lines."""
    run_id, deadline_26, deadline_50 = parsed
    if deadline_26 is None:
        shown = "none"
        live = "no"
    else:
        shown = str(deadline_26)
        live = "yes" if deadline_26 < deadline_50 else "no"
    return (
        "run_id=%s\n"
        "deadline_26=%s\n"
        "deadline_50=%d\n"
        "d26_live=%s\n" % (run_id, shown, deadline_50, live)
    )


def earliest_deadline(parsed: Deadline) -> int:
    """The soonest instant restore may start. After +26 is dropped, +50."""
    _run_id, deadline_26, deadline_50 = parsed
    if deadline_26 is None:
        return deadline_50
    return min(deadline_26, deadline_50)


def load_deadline(path: Path) -> Tuple[str, Optional[Deadline]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "malformed", None
    parsed = parse_deadline(text)
    if parsed is None:
        return "malformed", None
    return "ok", parsed


def drop_early_deadline(run_id: str, path: Optional[Path] = None) -> None:
    """Drop +26 so the earliest restore is +50.

    Atomic rewrite in the same directory. Idempotent when +26 is already
    gone. Refuses a missing or unparseable file and leaves it untouched.
    A mismatched run-id raises DeadlineMismatch and leaves the file
    untouched. Does not take the writer lock. Does not read a run-id
    from the file unless the caller passed one.
    """
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise DeadlineMismatch("run-id mismatch")
    target = deadline_path() if path is None else path
    if not target.is_file():
        raise DeadlineRefusal("deadline file missing")
    status, parsed = load_deadline(target)
    if status == "absent":
        raise DeadlineRefusal("deadline file missing")
    if status != "ok" or parsed is None:
        raise DeadlineRefusal("deadline file refused")
    file_run, deadline_26, deadline_50 = parsed
    if file_run != run_id:
        raise DeadlineMismatch("run-id mismatch")
    if deadline_26 is None:
        return
    _atomic_write(
        target,
        "run_id=%s\ndeadline_50=%d\n" % (file_run, deadline_50),
    )


def _parse_lock_text(text: str) -> Tuple[Optional[int], str, bool]:
    """Return pid, purpose, and whether a usable pid was present.

    A token field is discarded and is not returned to the caller.
    """
    pid: Optional[int] = None
    purpose = ""
    saw_pid = False
    well = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in TOKEN_KEYS:
            continue
        if key == "pid":
            saw_pid = True
            if value.isdigit() and int(value) > 0:
                pid = int(value)
                well = True
            else:
                pid = None
                well = False
        elif key == "purpose":
            purpose = value.replace("\n", " ").replace("\r", " ")
    if not saw_pid:
        return None, purpose, False
    return pid, purpose, well and pid is not None


def _pid_is_live(pid: int) -> Optional[bool]:
    """True live, False dead, None ambiguous. Signal 0 does not take a lock."""
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _classify_lsof(rc: int, stdout: str, stderr: str, path_exists: bool) -> str:
    """Return 'clear', 'held', or 'error'.

    lsof exits 1 with empty output when no process has the path open.
    That is clear, not an error. Any other failure is ambiguous.
    """
    out = stdout.strip()
    err = stderr.strip().lower()
    if rc == 0:
        if not out:
            return "clear"
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if lines and all(line.isdigit() and int(line) > 0 for line in lines):
            return "held"
        return "error"
    if rc == 1 and not out:
        if not err:
            return "clear"
        missing = (
            "no such file" in err
            or "cannot stat" in err
            or "can't stat" in err
            or "status error" in err
        )
        if missing and not path_exists:
            return "clear"
        return "error"
    return "error"


def _first_pid(stdout: str) -> str:
    for line in stdout.splitlines():
        line = line.strip()
        if line.isdigit() and int(line) > 0:
            return line
    return "?"


def _lsof_state(
    path: Path,
    runner: Runner,
    pid_text: str,
    purpose_text: str,
    path_exists: bool,
) -> LockState:
    try:
        rc, out, err = runner(["lsof", "-t", str(path)])
    except Exception:
        return "fault", pid_text, purpose_text
    if not isinstance(rc, int):
        return "fault", pid_text, purpose_text
    if rc < 0:
        return "fault", pid_text, purpose_text
    state = _classify_lsof(rc, out or "", err or "", path_exists)
    if state == "error":
        return "fault", pid_text, purpose_text
    if state == "held":
        if pid_text == "?":
            pid_text = _first_pid(out or "")
        return "held", pid_text, purpose_text
    return "free", pid_text, purpose_text


def inspect_lock(path: Path, runner: Runner) -> LockState:
    """Read-only holder check. Returns (state, pid_for_log, purpose_for_log).

    state is 'free', 'held', or 'fault'. A fault is held for the restore
    decision and is logged separately. Token fields are never returned.
    A live recorded pid is held without requiring lsof. lsof distinguishes
    a dead pid, and a missing file, from a real holder.
    """
    pid_text = "?"
    purpose_text = "?"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return _lsof_state(path, runner, pid_text, purpose_text, False)
    except OSError:
        return "fault", pid_text, purpose_text
    file_pid, purpose, well = _parse_lock_text(text)
    if purpose:
        purpose_text = purpose
    if file_pid is not None:
        pid_text = str(file_pid)
    if not well or file_pid is None:
        return "fault", pid_text, purpose_text
    live = _pid_is_live(file_pid)
    if live is None:
        return "fault", pid_text, purpose_text
    if live:
        return "held", pid_text, purpose_text
    return _lsof_state(path, runner, pid_text, purpose_text, True)


def watch_once(runner: Optional[Runner] = None, now: Optional[int] = None) -> int:
    """One watchdog pass. Always returns 0 so launchd keeps the interval."""
    run = runner or _run
    destination = log_path()
    try:
        clock = _clock() if now is None else int(now)
    except (TypeError, ValueError):
        _log(destination, "search resume clock override malformed")
        return 0
    if clock < 0:
        _log(destination, "search resume clock override malformed")
        return 0
    status, parsed = load_deadline(deadline_path())
    if status == "absent":
        return 0
    if status != "ok" or parsed is None:
        _log(destination, MALFORMED)
        return 0
    # +26 is the uncommitted checkpoint. After it is dropped, +50 is earliest.
    if clock < earliest_deadline(parsed):
        return 0
    uid = _uid()
    target = _gui_target(uid)
    print_rc, print_out, print_err = run(["launchctl", "print", target])
    if print_rc == 0:
        return 0
    if not _not_loaded_signal(print_rc, print_out, print_err):
        _log(
            destination,
            "search resume launchctl print skipped rc=%s" % print_rc,
        )
        return 0
    state, pid_text, purpose_text = inspect_lock(lock_path(), run)
    if state == "fault":
        # Loud on every such tick. Repeated faults do not start search.
        _log(destination, DETECTION_FAULT, loud=True)
        return 0
    if state == "held":
        _log(destination, LOUD % (pid_text, purpose_text), loud=True)
        return 0
    plist = str(installed_plist_path())
    # At most one bootstrap per tick. Never -k. Never enable.
    # Never print-disabled. Never a second attempt.
    boot_rc, _boot_out, _boot_err = run(
        ["launchctl", "bootstrap", _gui_domain(uid), plist]
    )
    if boot_rc != 0:
        # S2 may have loaded the job between the first print and bootstrap.
        follow_rc, _follow_out, _follow_err = run(
            ["launchctl", "print", target]
        )
        if follow_rc == 0:
            _log(destination, ALREADY_LOADED)
            return 0
        _log(destination, "search restore failed bootstrap rc=%s" % boot_rc)
        return 0
    kick_rc, _kick_out, _kick_err = run(["launchctl", "kickstart", target])
    if kick_rc != 0:
        # Not-found or any other failure: one follow-up print. No -k.
        follow_rc, _follow_out, _follow_err = run(["launchctl", "print", target])
        if follow_rc == 0:
            _log(destination, ALREADY_LOADED)
            return 0
        _log(destination, "search restore failed kickstart rc=%s" % kick_rc)
        return 0
    _log(destination, "search restored")
    return 0


def _take_run_id(args: Sequence[str], allow_now: bool) -> Tuple[int, str, str]:
    """Return (status, run_id, now_raw). Status 0 is ok; 2 is usage."""
    run_id = ""
    now_raw = ""
    index = 0
    items = list(args)
    while index < len(items):
        item = items[index]
        if item == "--run-id" and index + 1 < len(items):
            run_id = items[index + 1]
            index += 2
            continue
        if allow_now and item == "--now" and index + 1 < len(items):
            now_raw = items[index + 1]
            index += 2
            continue
        return 2, "", ""
    if not run_id:
        return 2, "", ""
    return 0, run_id, now_raw


def _cmd_write(args: Sequence[str]) -> int:
    status, run_id, now_raw = _take_run_id(args, True)
    if status != 0:
        sys.stderr.write(USAGE)
        return 2
    if now_raw:
        if not str(now_raw).isdigit():
            sys.stderr.write("now must be an epoch\n")
            return 2
        clock = int(now_raw)
    else:
        clock = int(time.time())
    try:
        write_deadline(deadline_path(), run_id, clock)
    except ValueError:
        sys.stderr.write("run-id must be a short id\n")
        return 2
    return 0


def _cmd_arm(args: Sequence[str]) -> int:
    status, run_id, _now_raw = _take_run_id(args, False)
    if status != 0:
        sys.stderr.write(USAGE)
        return 2
    try:
        drop_early_deadline(run_id)
    except DeadlineRefusal as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    except OSError:
        sys.stderr.write("error: deadline rewrite failed\n")
        return 1
    return 0


def _cmd_clear() -> int:
    clear_deadline(deadline_path())
    return 0


def _cmd_status() -> int:
    """Print deadline checkpoints. Does not write, lock, or open sqlite."""
    status, parsed = load_deadline(deadline_path())
    if status == "absent":
        sys.stdout.write("status=missing\n")
        return 1
    if status != "ok" or parsed is None:
        sys.stdout.write("status=unparseable\n")
        return 1
    sys.stdout.write(format_status(parsed))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "watch":
        if len(args) > 1:
            sys.stderr.write(USAGE)
            return 2
        return watch_once()
    if args[0] == "write":
        return _cmd_write(args[1:])
    if args[0] == "arm":
        return _cmd_arm(args[1:])
    if args[0] == "status":
        if len(args) != 1:
            sys.stderr.write(USAGE)
            return 2
        return _cmd_status()
    if args[0] == "clear":
        if len(args) != 1:
            sys.stderr.write(USAGE)
            return 2
        return _cmd_clear()
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
