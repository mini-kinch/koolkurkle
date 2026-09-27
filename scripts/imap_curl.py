#!/usr/bin/env python3
"""BODYSTRUCTURE fetch over ``/usr/bin/curl imaps://``.

Production transport for ``meta_fill``. One curl process per command.
The password is written only to that process's stdin as a curl config
(``user = "..."``). It is not placed in argv, the environment, or a file.

Curl URL-decodes the mailbox path and then quotes spaces, quotes, and
backslashes itself (``imap_atom``). The URL therefore percent-encodes the
raw mailbox. The ``-X`` command is ``EXAMINE`` plus that same mailbox
already quoted, because a custom request with a mailbox in the URL is
preceded by curl's own SELECT of the URL path. EXAMINE is the read-only
command. ``UID FETCH <uid> (BODYSTRUCTURE)`` does not set the seen flag.

``--cacert`` is added only when a caller passes ``cacert`` (tests against
a throwaway certificate). Production does not. TLS verification stays on:
no ``-k`` and no ``--insecure``. The binary is ``/usr/bin/curl`` and is
not read from ``CURL_BIN`` or any other environment variable.

A non-zero curl status, an authentication failure, or an Errno 9 / bad
file descriptor failure raises ``CurlImapError`` after a single attempt.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse

try:
    from attachments.bodystructure import ParseError, bodystructure_from_fetch
except ImportError:  # python3 scripts/attachments/meta_fill.py
    from bodystructure import ParseError, bodystructure_from_fetch  # type: ignore

try:
    from imap_keychain import KeychainError, read_imap_app_password
except ImportError:  # package import path
    from scripts.imap_keychain import (  # type: ignore
        KeychainError,
        read_imap_app_password,
    )

CURL_BIN = "/usr/bin/curl"
_DEFAULT_TIMEOUT_S = 30
_UIDVALIDITY_RE = re.compile(r"\[UIDVALIDITY\s+(\d+)\]", re.IGNORECASE)
_BLOCKED_ENV = frozenset(
    {
        "CURL_BIN",
        "IMAP_APP_PASSWORD",
        "MAILROOM_IMAP_PASSWORD",
        "MAILROOM_IMAP_APP_PASSWORD",
    }
)


class CurlImapError(RuntimeError):
    """Curl IMAP failed closed. The message never includes a secret."""


def quote_imap_mailbox(name: str) -> str:
    """IMAP atom quoting. Spaces, quotes, and backslashes stay one mailbox."""
    escaped = str(name).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _seconds(timeout_s: float) -> str:
    value = float(timeout_s)
    if value <= 0:
        value = float(_DEFAULT_TIMEOUT_S)
    if value.is_integer():
        return str(int(value))
    return str(value)


def _check_host(host: str) -> str:
    text = str(host or "")
    if not text or any(ch in text for ch in " \t\r\n/@\\"):
        raise CurlImapError("imap host is required")
    return text


def folder_url(host: str, mailbox: str, port: int = 993) -> str:
    """``imaps://`` URL. The path is the raw mailbox, percent-encoded.

    Curl quotes the decoded path when it SELECTs. Pre-quoting the path
    would send a doubled atom (``"\\"Deleted Messages\\""``) and the
    folder would not match.
    """
    encoded = urllib.parse.quote(str(mailbox), safe="")
    return "imaps://%s:%s/%s" % (_check_host(host), int(port), encoded)


def examine_command(mailbox: str) -> str:
    """Read-only EXAMINE of one quoted mailbox."""
    return "EXAMINE " + quote_imap_mailbox(mailbox)


def fetch_command(uid: str) -> str:
    """UID FETCH of BODYSTRUCTURE only. This item does not set \\Seen."""
    return "UID FETCH %s (BODYSTRUCTURE)" % uid


def curl_argv(url: str, command: str, timeout_s: float, cacert: str | None = None) -> list:
    """Pinned curl argv. ``cacert`` is a test injection and is otherwise absent."""
    argv = [
        CURL_BIN,
        "--silent",
        "--show-error",
        "--max-time",
        _seconds(timeout_s),
        "--connect-timeout",
        _seconds(timeout_s),
        "--dump-header",
        "-",
        "--config",
        "-",
        "-X",
        command,
        url,
    ]
    if cacert:
        argv = argv[:-1] + ["--cacert", str(cacert), argv[-1]]
    return argv


def _curl_config_escape(value: str) -> str:
    """Escape one curl config double-quoted value. Backslash first."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\v", "\\v")
    )


def curl_user_config(user: str, password: str) -> str:
    """One ``user = "name:password"`` line for ``curl --config -``."""
    token = "%s:%s" % (user, password)
    return 'user = "%s"\n' % _curl_config_escape(token)


def curl_child_env(password: str) -> dict:
    """Child environment. Drops curl-bin overrides and the password."""
    env = {}
    for key, value in os.environ.items():
        if key in _BLOCKED_ENV:
            continue
        if password and value == password:
            continue
        env[key] = value
    return env


def uidvalidity_from_curl_output(text: str) -> int:
    """Last ``[UIDVALIDITY n]`` in curl's IMAP response (the EXAMINE)."""
    found = _UIDVALIDITY_RE.findall(text or "")
    if not found:
        raise CurlImapError("imap uidvalidity missing")
    return int(found[-1])


def _raise_for_status(rc: int, stderr: str) -> None:
    """One failure, no retry. Stderr is not copied (it may echo a secret)."""
    err = stderr or ""
    low = err.lower()
    if "errno 9" in low or "bad file descriptor" in low:
        raise CurlImapError("imap curl failed closed (errno 9); not retrying")
    if rc == 67 or "login denied" in low or "authentication failed" in low:
        raise CurlImapError("imap curl authentication failed")
    if rc != 0:
        raise CurlImapError("imap curl failed (rc %s)" % int(rc))


def run_subprocess(argv, config_text, env, timeout):
    """Run pinned curl once. Config is stdin, never a file."""
    try:
        proc = subprocess.run(
            list(argv),
            input=config_text,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CurlImapError("imap curl failed closed (timeout)") from None
    except OSError as exc:
        if getattr(exc, "errno", None) == 9:
            raise CurlImapError("imap curl failed closed (errno 9); not retrying") from None
        raise CurlImapError("imap curl failed closed") from None
    return int(proc.returncode), proc.stdout or "", proc.stderr or ""


def _check_mailbox(mailbox: str) -> str:
    if mailbox is None or str(mailbox).strip() == "":
        raise ValueError("missing mailbox")
    text = str(mailbox)
    if "\r" in text or "\n" in text:
        raise CurlImapError("imap mailbox is invalid")
    return text


def _check_uid(uid: str) -> str:
    if uid is None or str(uid).strip() == "":
        raise ValueError("missing uid")
    text = str(uid).strip()
    if not text.isdigit():
        raise ValueError("missing uid")
    return text


class CurlImapsClient:
    """EXAMINE a folder, then UID FETCH ``(BODYSTRUCTURE)``, via curl.

    ``password_fn`` defaults to Keychain (``read_imap_app_password``).
    ``cacert`` and ``runner`` are test injections. ``runner`` defaults to
    ``run_subprocess`` and is called once per command.
    """

    def __init__(
        self,
        host: str,
        user: str | None,
        *,
        timeout: float = 30,
        password_fn=None,
        port: int = 993,
        cacert: str | None = None,
        runner=None,
    ) -> None:
        self.host = _check_host(host)
        self.user = user or ""
        self.timeout = timeout
        self.port = int(port)
        self._password_fn = password_fn
        self._cacert = cacert
        self._runner = run_subprocess if runner is None else runner
        self._password = ""
        self._proc_timeout = (float(timeout) if timeout and timeout > 0 else float(_DEFAULT_TIMEOUT_S)) + 5.0
        self.mailbox: str | None = None
        self.uidvalidity: int | None = None

    def __enter__(self) -> "CurlImapsClient":
        fn = self._password_fn or read_imap_app_password
        try:
            password = fn()
        except KeychainError:
            raise CurlImapError("imap keychain password is missing") from None
        if not password:
            raise CurlImapError("imap keychain password is missing")
        self._password = password
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._password = ""

    def _invoke(self, url: str, command: str) -> str:
        argv = curl_argv(url, command, self.timeout, cacert=self._cacert)
        config = curl_user_config(self.user, self._password)
        env = curl_child_env(self._password)
        rc, out, err = self._runner(argv, config, env, self._proc_timeout)
        _raise_for_status(int(rc), err)
        return out

    def select(self, mailbox: str, readonly: bool = True) -> None:
        """EXAMINE one folder. Readonly. A curl failure is not retried."""
        if readonly is not True:
            raise CurlImapError("imap select must be readonly")
        name = _check_mailbox(mailbox)
        url = folder_url(self.host, name, self.port)
        text = self._invoke(url, examine_command(name))
        self.mailbox = name
        self.uidvalidity = uidvalidity_from_curl_output(text)

    def fetch_bodystructure(self, uid: str) -> str:
        """One UID FETCH. A curl failure is not retried and not parsed."""
        if self.mailbox is None:
            raise CurlImapError("imap mailbox is not selected")
        token = _check_uid(uid)
        url = folder_url(self.host, self.mailbox, self.port)
        text = self._invoke(url, fetch_command(token))
        try:
            return bodystructure_from_fetch(text)
        except ParseError:
            raise
