#!/usr/bin/env python3
"""Read the IMAP app password from macOS Keychain.

Same service names and ``security find-generic-password -s <item> -w``
read as ``scripts/run_mailroom_daily.sh`` (the wrapper that loads
Keychain before the IMAP child scripts). The item is fixed to
``mailroom.imap.app-password``. One fallback to
``mailroom.icloud.app-password`` only when that default name misses.
The binary is pinned to ``/usr/bin/security``.

Does not read ``IMAP_APP_PASSWORD``, ``MAILROOM_IMAP_PASSWORD``, or any
other environment variable. Never logs the secret. Tests inject
``runner``, ``binary``, and ``item`` arguments.
"""

from __future__ import annotations

import subprocess
import sys

KEYCHAIN_DEFAULT = "mailroom.imap.app-password"
KEYCHAIN_LEGACY = "mailroom.icloud.app-password"
_SECURITY_BIN = "/usr/bin/security"


class KeychainError(RuntimeError):
    """Keychain read failed. The message never includes the secret."""


def _run_security(binary: str, service: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [binary, "find-generic-password", "-s", service, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 1, ""
    out = proc.stdout or ""
    if out.endswith("\r\n"):
        out = out[:-2]
    elif out.endswith("\n"):
        out = out[:-1]
    return int(proc.returncode), out


def _read_service(binary: str, service: str, runner) -> str | None:
    rc, out = runner(binary, service)
    if rc != 0 or out == "":
        return None
    return out


def read_imap_app_password(runner=None, binary: str | None = None, item: str | None = None) -> str:
    """Return the Keychain app password.

    Production always uses ``/usr/bin/security`` and
    ``mailroom.imap.app-password``. ``runner``, ``binary``, and ``item``
    are test injections. Environment variables are ignored.
    """
    binary_path = _SECURITY_BIN if binary is None else binary
    service = KEYCHAIN_DEFAULT if item is None else item
    call = _run_security if runner is None else runner
    password = _read_service(str(binary_path), str(service), call)
    if password is None and str(service) == KEYCHAIN_DEFAULT:
        password = _read_service(str(binary_path), KEYCHAIN_LEGACY, call)
        if password is not None:
            sys.stderr.write(
                "warning: Keychain service %s missing or empty; "
                "falling back to %s\n" % (KEYCHAIN_DEFAULT, KEYCHAIN_LEGACY)
            )
    if password is None:
        raise KeychainError("imap keychain password is missing")
    return password
