"""Fail tests that reach the real Keychain or a non-loopback curl.

``install()`` is called from ``sitecustomize`` when ``PYTHONPATH`` includes
``tests``, which is how ``python -m unittest discover -s tests`` starts.
There is no global off switch. Real ``/usr/bin/curl`` is allowed only inside
``allow_real_curl(target)`` when ``target`` is loopback and every URL on
that call is loopback. ``/usr/bin/security`` is never allowed unpatched.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading

SECURITY_BIN = "/usr/bin/security"
CURL_BIN = "/usr/bin/curl"
_LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1"))
_URL_RE = re.compile(r"(?i)\b(?:imaps?|https?)://(\[[^\]]+\]|[^/:\"'\s]+)")

_INSTALLED = False
_DEPTH = threading.local()
_ALLOW = threading.local()

_ORIG_RUN = subprocess.run
_ORIG_CALL = subprocess.call
_ORIG_CHECK_CALL = subprocess.check_call
_ORIG_CHECK_OUTPUT = subprocess.check_output
_ORIG_POPEN_INIT = subprocess.Popen.__init__


class HermeticBinaryError(BaseException):
    """An unpatched test called /usr/bin/security or /usr/bin/curl."""


def installed() -> bool:
    return bool(_INSTALLED) and getattr(subprocess.run, "_mailroom_hermetic", False)


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "surrogateescape")
    return str(value)


def _program(args) -> str:
    if isinstance(args, (list, tuple)):
        if not args:
            return ""
        return _text(args[0]).strip()
    if isinstance(args, (str, bytes)):
        text = _text(args).strip()
        if not text:
            return ""
        return text.split()[0]
    return ""


def _host_of(target) -> str:
    text = _text(target).strip()
    if "://" in text:
        match = _URL_RE.search(text)
        if not match:
            return ""
        host = match.group(1)
    else:
        host = text.split("/")[0].split("@")[-1]
        if host.startswith("[") and "]" in host:
            host = host[1 : host.index("]")]
        else:
            host = host.split(":")[0]
    return host.strip("[]").lower()


def _allow_stack():
    stack = getattr(_ALLOW, "stack", None)
    if stack is None:
        stack = []
        _ALLOW.stack = stack
    return stack


def _depth() -> int:
    return int(getattr(_DEPTH, "n", 0) or 0)


def _push_depth() -> None:
    _DEPTH.n = _depth() + 1


def _pop_depth() -> None:
    _DEPTH.n = max(0, _depth() - 1)


def _url_hosts(args, data) -> list:
    parts = []
    if isinstance(args, (list, tuple)):
        parts.extend(_text(item) for item in args)
    elif args is not None:
        parts.append(_text(args))
    if isinstance(data, bytes):
        parts.append(data.decode("utf-8", "surrogateescape"))
    elif isinstance(data, str):
        parts.append(data)
    blob = "\n".join(parts)
    hosts = []
    for match in _URL_RE.finditer(blob):
        hosts.append(match.group(1).strip("[]").lower())
    return hosts


def _refuse(args, data) -> None:
    program = _program(args)
    if program == SECURITY_BIN:
        raise HermeticBinaryError(
            "hermetic guard: unpatched /usr/bin/security call"
        )
    if program != CURL_BIN:
        return
    stack = list(_allow_stack())
    hosts = _url_hosts(args, data)
    if not stack:
        raise HermeticBinaryError(
            "hermetic guard: unpatched /usr/bin/curl call"
        )
    if not hosts or any(host not in _LOOPBACK for host in hosts):
        raise HermeticBinaryError(
            "hermetic guard: /usr/bin/curl call is not a loopback target"
        )


class allow_real_curl(contextlib.ContextDecorator):
    """Per-test opt-in for real /usr/bin/curl. ``target`` must be loopback."""

    def __init__(self, target: str) -> None:
        self._host = _host_of(target)
        if self._host not in _LOOPBACK:
            raise HermeticBinaryError(
                "hermetic guard: real curl opt-in requires a loopback target"
            )

    def __enter__(self):
        _allow_stack().append(self._host)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        stack = _allow_stack()
        if stack:
            stack.pop()


def _guarded_run(*args, **kwargs):
    if _depth() == 0:
        cmd = args[0] if args else kwargs.get("args")
        _refuse(cmd, kwargs.get("input"))
    _push_depth()
    try:
        return _ORIG_RUN(*args, **kwargs)
    finally:
        _pop_depth()


def _guarded_call(*args, **kwargs):
    if _depth() == 0:
        cmd = args[0] if args else kwargs.get("args")
        _refuse(cmd, kwargs.get("input"))
    _push_depth()
    try:
        return _ORIG_CALL(*args, **kwargs)
    finally:
        _pop_depth()


def _guarded_check_call(*args, **kwargs):
    if _depth() == 0:
        cmd = args[0] if args else kwargs.get("args")
        _refuse(cmd, kwargs.get("input"))
    _push_depth()
    try:
        return _ORIG_CHECK_CALL(*args, **kwargs)
    finally:
        _pop_depth()


def _guarded_check_output(*args, **kwargs):
    if _depth() == 0:
        cmd = args[0] if args else kwargs.get("args")
        _refuse(cmd, kwargs.get("input"))
    _push_depth()
    try:
        return _ORIG_CHECK_OUTPUT(*args, **kwargs)
    finally:
        _pop_depth()


def _guarded_popen_init(self, args, *pos, **kwargs):
    if _depth() == 0:
        _refuse(args, kwargs.get("input"))
    _push_depth()
    try:
        return _ORIG_POPEN_INIT(self, args, *pos, **kwargs)
    finally:
        _pop_depth()


def _wrap_exec(name: str, file_index: int, argv_mode: str):
    orig = getattr(os, name)

    def wrapped(*args, **kwargs):
        if len(args) > file_index:
            _refuse(args[file_index], None)
        if argv_mode == "list" and len(args) > file_index + 1:
            argv = args[file_index + 1]
            if isinstance(argv, (list, tuple)) and argv:
                _refuse(argv[0], None)
        elif argv_mode == "first" and len(args) > file_index + 1:
            _refuse(args[file_index + 1], None)
        return orig(*args, **kwargs)

    wrapped.__name__ = name
    wrapped._mailroom_hermetic = True
    setattr(os, name, wrapped)


def _install_os_wrappers() -> None:
    for name in ("execl", "execle", "execlp", "execlpe"):
        if hasattr(os, name):
            _wrap_exec(name, 0, "first")
    for name in ("execv", "execve", "execvp", "execvpe"):
        if hasattr(os, name):
            _wrap_exec(name, 0, "list")
    for name in ("spawnl", "spawnle", "spawnlp", "spawnlpe"):
        if hasattr(os, name):
            _wrap_exec(name, 1, "first")
    for name in ("spawnv", "spawnve", "spawnvp", "spawnvpe"):
        if hasattr(os, name):
            _wrap_exec(name, 1, "list")
    for name in ("posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            _wrap_exec(name, 0, "list")


def install() -> None:
    """Install the guard once. Discover loads this from sitecustomize."""
    global _INSTALLED
    if _INSTALLED or getattr(subprocess.run, "_mailroom_hermetic", False):
        _INSTALLED = True
        return
    _guarded_run._mailroom_hermetic = True
    _guarded_call._mailroom_hermetic = True
    _guarded_check_call._mailroom_hermetic = True
    _guarded_check_output._mailroom_hermetic = True
    _guarded_popen_init._mailroom_hermetic = True
    subprocess.run = _guarded_run
    subprocess.call = _guarded_call
    subprocess.check_call = _guarded_check_call
    subprocess.check_output = _guarded_check_output
    subprocess.Popen.__init__ = _guarded_popen_init
    _install_os_wrappers()
    _INSTALLED = True
