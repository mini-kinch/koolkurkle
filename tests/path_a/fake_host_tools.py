#!/usr/bin/env python3
"""PATH stand-in for launchctl, curl, kill, ps, and lsof.

Test double only. Exits unless FAKE_HOST_TOOLS=1. Does not bind a port
and does not open a network connection.
"""
from __future__ import print_function

import os
import sys


def _root():
    return os.environ["FAKE_ROOT"]


def _path(name):
    return os.path.join(_root(), name)


def _read(name, default=""):
    path = _path(name)
    if not os.path.exists(path):
        return default
    with open(path, "r") as handle:
        return handle.read().strip()


def _write(name, value):
    with open(_path(name), "w") as handle:
        handle.write(str(value))


def _trace(cmd, args):
    path = os.environ["FAKE_TRACE"]
    line = cmd + "\t" + "\t".join(args) + "\n"
    with open(path, "a") as handle:
        handle.write(line)


def _scenario():
    return os.environ.get("FAKE_SCENARIO", "launchd")


def _append_log(line):
    path = os.environ["DRILL_LOG"]
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "a") as handle:
        handle.write(line + "\n")


def _append_restart():
    _append_log(
        "[2026-09-25 12:00:00] restart kickstart -k gui/1/com.mailroom.mlx-lm-server rc=0"
    )


def _append_fail(n):
    _append_log("[2026-09-25 12:00:00] fail consecutive=%d" % n)


def _hold_exists():
    path = os.environ.get("DRILL_HOLD", "")
    return bool(path) and os.path.exists(path)


def _write_hold(text):
    path = os.environ["DRILL_HOLD"]
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w") as handle:
        handle.write(text)


def _bump_pid():
    raw = _read("pid", "0")
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n <= 0:
        n = 4100
    _write("pid", str(n + 1))


def _emit(code):
    sys.stdout.write("%s\n" % code)
    return 0


def _maybe_mutate():
    if os.environ.get("FAKE_MUTATE_ASK") != "1":
        return
    path = os.environ.get("FAKE_ASK", "")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write("\n# mutated\n")


def _curl_port_kill(phase):
    if phase != "down":
        return _emit("200")
    n = int(_read("down_curls", "0") or "0") + 1
    _write("down_curls", str(n))
    if n >= 2:
        _append_restart()
        _write("phase", "up")
        _bump_pid()
        return _emit("200")
    return _emit("000")


def _curl_hold(phase):
    if _hold_exists():
        return _emit("000")
    if phase == "down":
        _append_restart()
        _write("phase", "up")
        _bump_pid()
        return _emit("200")
    return _emit("200")


def _curl_stop(phase):
    if phase != "stopped":
        return _emit("200")
    n = int(_read("stop_curls", "0") or "0") + 1
    _write("stop_curls", str(n))
    if n == 1:
        _append_fail(1)
        return _emit("000")
    _append_fail(2)
    _append_restart()
    _write("phase", "up")
    _bump_pid()
    return _emit("200")


def _curl_down_only(phase):
    if phase == "up":
        return _emit("200")
    return _emit("000")


def do_curl(_args):
    scenario = _scenario()
    phase = _read("phase", "up")
    if scenario in ("port-kill", "mutate-ask"):
        return _curl_port_kill(phase)
    if scenario == "port-kill-hold":
        return _curl_hold(phase)
    if scenario == "stop-cont":
        return _curl_stop(phase)
    if scenario in ("hold-violated", "no-recovery", "loop3"):
        return _curl_down_only(phase)
    if phase == "up":
        return _emit("200")
    return _emit("000")


def _kill_signal(args):
    sig = ""
    for arg in args:
        if arg.startswith("-"):
            sig = arg
    return sig


def _kill_loop3():
    n = int(_read("restarts_done", "0") or "0")
    if n < 3:
        _append_restart()
        _write("restarts_done", str(n + 1))
        _write("phase", "up")
        _bump_pid()
        return 0
    _write_hold("loop guard: HOLD written\n")
    _write("phase", "down")
    return 0


def do_kill(args):
    scenario = _scenario()
    sig = _kill_signal(args)
    if sig == "-CONT":
        return 0
    _maybe_mutate()
    if sig == "-STOP":
        _write("phase", "stopped")
        _write("stop_curls", "0")
        return 0
    if scenario == "loop3":
        return _kill_loop3()
    if scenario == "hold-violated":
        _append_restart()
        _write("phase", "down")
        return 0
    _write("phase", "down")
    _write("down_curls", "0")
    return 0


def do_ps():
    pid = _read("pid", "")
    if not pid:
        return 1
    sys.stdout.write("%s mlx_lm.server\n" % pid)
    return 0


def do_lsof():
    pid = _read("pid", "")
    if not pid:
        return 1
    sys.stdout.write("%s\n" % pid)
    return 0


def do_launchctl(args):
    if not args:
        return 2
    sub = args[0]
    if sub == "print":
        if os.path.exists(_path("loaded")):
            sys.stdout.write("state = running\n")
            return 0
        return 1
    if sub == "bootout":
        if os.environ.get("FAKE_IGNORE_BOOTOUT") != "1":
            loaded = _path("loaded")
            if os.path.exists(loaded):
                os.remove(loaded)
        return 0
    if sub == "bootstrap":
        _write("loaded", "1")
        return 0
    if sub == "kickstart":
        _write("phase", "up")
        if not _read("pid", ""):
            _write("pid", "4200")
        return 0
    if sub in ("submit", "remove"):
        return 0
    return 0


def main():
    if os.environ.get("FAKE_HOST_TOOLS") != "1":
        sys.stderr.write("test double only\n")
        return 2
    cmd = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    _trace(cmd, args)
    if cmd == "launchctl":
        return do_launchctl(args)
    if cmd == "curl":
        return do_curl(args)
    if cmd == "kill":
        return do_kill(args)
    if cmd == "ps":
        return do_ps()
    if cmd == "lsof":
        return do_lsof()
    sys.stderr.write("unknown tool\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
