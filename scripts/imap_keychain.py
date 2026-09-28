#!/usr/bin/env python3
"""Read the IMAP app password from macOS Keychain.

The item name is not compiled into this file. Production reads
``MAILROOM_KEYCHAIN_ITEM`` or the first non-comment line of the file
named by ``MAILROOM_KEYCHAIN_CONFIG``. There is no legacy fallback.
The binary is pinned to ``/usr/bin/security``. The read has a deadline
(``MAILROOM_KEYCHAIN_TIMEOUT_S``, default 15 seconds). A timeout kills
the child and raises ``KeychainTimeout``.

Does not read ``IMAP_APP_PASSWORD`` or ``MAILROOM_IMAP_PASSWORD``.
Never logs the secret. Tests inject ``runner``, ``binary``, ``item``,
and ``timeout_s``.
"""

from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

_SECURITY_BIN = "/usr/bin/security"
KEYCHAIN_TIMEOUT_S = 15.0
_ITEM_ENV = "MAILROOM_KEYCHAIN_ITEM"
_CONFIG_ENV = "MAILROOM_KEYCHAIN_CONFIG"
_TIMEOUT_ENV = "MAILROOM_KEYCHAIN_TIMEOUT_S"


class KeychainError(RuntimeError):
    """Keychain read failed. The message never includes the secret."""


class KeychainTimeout(RuntimeError):
    """The Keychain read exceeded its deadline. Not a missing item."""


def _accepts_timeout(fn) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    params = list(sig.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params):
        return True
    return "timeout" in sig.parameters


def _timeout_seconds(timeout_s: float | None, env: dict) -> float:
    if timeout_s is not None:
        try:
            value = float(timeout_s)
        except (TypeError, ValueError):
            return KEYCHAIN_TIMEOUT_S
        return value if value > 0 else KEYCHAIN_TIMEOUT_S
    raw = str(env.get(_TIMEOUT_ENV) or "").strip()
    if not raw:
        return KEYCHAIN_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return KEYCHAIN_TIMEOUT_S
    return value if value > 0 else KEYCHAIN_TIMEOUT_S


def resolve_keychain_item(item: str | None = None, env: dict | None = None) -> str:
    """Return the pinned item name. Never a compiled-in default."""
    if item is not None and str(item).strip():
        return str(item).strip()
    environ = os.environ if env is None else env
    named = str(environ.get(_ITEM_ENV) or "").strip()
    if named:
        return named
    config = str(environ.get(_CONFIG_ENV) or "").strip()
    if config:
        try:
            text = Path(config).read_text(encoding="utf-8")
        except OSError:
            raise KeychainError("imap keychain item is not pinned") from None
        for raw in text.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                return line
    raise KeychainError("imap keychain item is not pinned")


def _run_security(binary: str, service: str, timeout_s: float | None = None) -> tuple[int, str]:
    seconds = _timeout_seconds(timeout_s, os.environ)
    argv = [binary, "find-generic-password", "-s", service, "-w"]
    kwargs = {"check": False, "capture_output": True, "text": True}
    if _accepts_timeout(subprocess.run):
        kwargs["timeout"] = seconds
    try:
        proc = subprocess.run(argv, **kwargs)
    except subprocess.TimeoutExpired:
        raise KeychainTimeout("imap keychain read timed out") from None
    except OSError:
        return 1, ""
    out = proc.stdout or ""
    if out.endswith("\r\n"):
        out = out[:-2]
    elif out.endswith("\n"):
        out = out[:-1]
    return int(proc.returncode), out


def _read_service(binary: str, service: str, runner, timeout_s: float | None) -> str | None:
    try:
        rc, out = runner(binary, service, timeout_s)
    except TypeError:
        rc, out = runner(binary, service)
    if rc != 0 or out == "":
        return None
    return out


def read_imap_app_password(
    runner=None,
    binary: str | None = None,
    item: str | None = None,
    timeout_s: float | None = None,
    env: dict | None = None,
) -> str:
    """Return the Keychain app password for the pinned item.

    A missing pin or a missing item raises ``KeychainError``. A read that
    exceeds the deadline raises ``KeychainTimeout``. No fallback item.
    """
    environ = os.environ if env is None else env
    binary_path = _SECURITY_BIN if binary is None else binary
    service = resolve_keychain_item(item, environ)
    seconds = _timeout_seconds(timeout_s, environ)
    if runner is None:
        password = _read_service(str(binary_path), service, _run_security, seconds)
    else:
        password = _read_service(str(binary_path), service, runner, seconds)
    if password is None:
        raise KeychainError("imap keychain password is missing")
    return password

