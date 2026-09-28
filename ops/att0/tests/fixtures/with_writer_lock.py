"""Test double for the writer-lock wrapper. No secrets.

The acquisition path matches with_writer_lock.py: LOCK_EX|LOCK_NB, then
the command, then release. A held lock exits 2 and does not run the command.
"""

import fcntl
import os
import subprocess
import sys

LOCK_TOKEN_ENV = "MAILROOM_WRITER_LOCK_TOKEN"
ALLOWED = ("att0-migrate", "att0 meta fill", "att0-restore", "att0-lock-probe")


def main(argv):
    if "--purpose" not in argv or "--" not in argv:
        sys.stderr.write("error: purpose and command are required\n")
        return 2
    purpose = argv[argv.index("--purpose") + 1]
    if purpose not in ALLOWED:
        sys.stderr.write("error: purpose not allowed\n")
        return 2
    cmd = argv[argv.index("--") + 1 :]
    if not cmd:
        sys.stderr.write("error: command is required after --\n")
        return 2
    if "--lock-file" in argv:
        lock_path = argv[argv.index("--lock-file") + 1]
    else:
        lock_path = os.path.expanduser("~/MailArchive/mailroom.write.lock")
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        sys.stderr.write("error: writer lock held\n")
        return 2
    try:
        return _run(cmd)
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _run(cmd):
    run_id = os.environ.get("MAILROOM_SEARCH_RESUME_RUN_ID", "").strip()
    if run_id:
        path = os.path.expanduser("~/MailArchive/state/search_resume_after.epoch")
        try:
            text = open(path).read().splitlines()
        except Exception as exc:
            sys.stderr.write(
                "error: search resume +26 drop failed; child not started: %s\n" % exc
            )
            return 2
        got = ""
        for line in text:
            if line.startswith("run_id="):
                got = line.split("=", 1)[1]
        if got != run_id:
            sys.stderr.write(
                "error: search resume +26 drop failed; child not started\n"
            )
            return 2
        kept = [line for line in text if not line.startswith("deadline_26=")]
        handle = open(path, "w")
        handle.write("\n".join(kept) + "\n")
        handle.close()
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
