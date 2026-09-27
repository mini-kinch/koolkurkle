#!/usr/bin/env python3
"""Read the IMAP app password from macOS Keychain.

Same service names as ``scripts/run_mailroom_daily.sh``. The lookup is
``security find-generic-password -s <item> -a <account> -w``. The item
is fixed to ``mailroom.imap.app-password``. One fallback to
``mailroom.icloud.app-password`` only when that default name misses,
and that fallback uses the same ``-a`` account. The account is the
runtime IMAP user the caller already uses for LOGIN. It is not
hardcoded. An empty account fails closed before ``security`` runs.
The binary is pinned to ``/usr/bin/security``.

Does not read ``IMAP_APP_PASSWORD``, ``MAILROOM_IMAP_PASSWORD``, or any
other environment variable. Never logs the secret. Tests inject
``runner``, ``binary``, ``item``, and ``account`` arguments.
"""

from __future__ import annotations

import subprocess
import sys

KEYCHAIN_DEFAULT = "mailroom.imap.app-password"
KEYCHAIN_LEGACY = "mailroom.icloud.app-password"
_SECURITY_BIN = "/usr/bin/security"


class KeychainError(RuntimeError):
    """Keychain read failed. The message never includes the secret."""


def security_argv(binary: str, service: str, account: str) -> list:
    """Pinned lookup. ``account`` is the runtime IMAP user."""
    return [
        binary,
        "find-generic-password",
        "-s",
        service,
        "-a",
        account,
        "-w",
    ]


def _run_security(binary: str, service: str, account: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            security_argv(binary, service, account),
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


def _read_service(binary: str, service: str, account: str, runner) -> str | None:
    rc, out = runner(binary, service, account)
    if rc != 0 or out == "":
        return None
    return out


def read_imap_app_password(
    runner=None,
    binary: str | None = None,
    item: str | None = None,
    account: str | None = None,
) -> str:
    """Return the Keychain app password for the runtime IMAP user.

    Production always uses ``/usr/bin/security`` and
    ``mailroom.imap.app-password``. ``account`` is that IMAP user.
    An empty account does not call ``security``. ``runner``, ``binary``,
    and ``item`` are test injections. Environment variables are ignored.
    """
    login = "" if account is None else str(account).strip()
    if not login:
        raise KeychainError("imap user is required")
    binary_path = _SECURITY_BIN if binary is None else binary
    service = KEYCHAIN_DEFAULT if item is None else item
    call = _run_security if runner is None else runner
    password = _read_service(str(binary_path), str(service), login, call)
    if password is None and str(service) == KEYCHAIN_DEFAULT:
        password = _read_service(str(binary_path), KEYCHAIN_LEGACY, login, call)
        if password is not None:
            sys.stderr.write(
                "warning: Keychain service %s missing or empty; "
                "falling back to %s\n" % (KEYCHAIN_DEFAULT, KEYCHAIN_LEGACY)
            )
    if password is None:
        raise KeychainError("imap keychain password is missing")
    return password
