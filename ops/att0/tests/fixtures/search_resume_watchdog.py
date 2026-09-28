"""Test double for the search-resume watchdog. No secrets."""

import os
import sys
import time


def _path():
    return os.path.expanduser("~/MailArchive/state/search_resume_after.epoch")


def _read():
    path = _path()
    if not os.path.exists(path):
        return None
    data = {}
    for line in open(path).read().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    return data


def status():
    data = _read()
    if data is None:
        sys.stdout.write("status=missing\n")
        return 1
    deadline_26 = data.get("deadline_26")
    live = "yes" if deadline_26 and deadline_26 != "none" else "no"
    shown = deadline_26 if deadline_26 else "none"
    sys.stdout.write("run_id=%s\n" % data.get("run_id", ""))
    sys.stdout.write("deadline_26=%s\n" % shown)
    sys.stdout.write("deadline_50=%s\n" % data.get("deadline_50", ""))
    sys.stdout.write("d26_live=%s\n" % live)
    return 0


def write(run_id):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = int(time.time())
    handle = open(path, "w")
    handle.write(
        "run_id=%s\ndeadline_26=%d\ndeadline_50=%d\n" % (run_id, now + 1560, now + 3000)
    )
    handle.close()
    return 0


def clear():
    path = _path()
    if os.path.exists(path):
        os.remove(path)
    return 0


def main(argv):
    if not argv:
        return 2
    cmd = argv[0]
    if cmd == "status":
        return status()
    if cmd == "clear":
        return clear()
    if cmd == "write" and "--run-id" in argv:
        return write(argv[argv.index("--run-id") + 1])
    if cmd == "arm" and "--run-id" in argv:
        return write(argv[argv.index("--run-id") + 1])
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
