#!/usr/bin/env python3
"""BODYSTRUCTURE fetch over pinned ``/usr/bin/curl imaps://``.

One curl process per folder. The URL is the base ``imaps://host:port/``
with no mailbox, so curl does not SELECT (SELECT is read-write). The
first transfer is ``EXAMINE "<mailbox>"``. Further transfers are
``UID FETCH <uidset> (BODYSTRUCTURE)``, joined by ``next`` so the login
is reused. ``next`` resets per-transfer options, so every transfer
repeats ``user``, ``connect-timeout``, ``max-time``, and ``cacert``
when a test certificate is injected.

The password is written only to that process's stdin as curl config
(``-K -``, ``user = "..."``). It is not placed in argv, the environment,
or a file. There is no ``-v``, ``--verbose``, or ``--trace``. Stderr is
redacted before it is retained. The binary is the literal
``/usr/bin/curl``: ``CURL_BIN`` is not read, and Homebrew curl is not a
fallback.

A non-zero curl status fails closed after that single process. UIDs
from a failed batch are not retried one at a time.
"""

from __future__ import annotations

import os
import re
import subprocess

try:
    from attachments.bodystructure import ParseError, bodystructure_from_fetch, parse_sexp
except ImportError:  # python3 scripts/attachments/meta_fill.py
    from bodystructure import ParseError, bodystructure_from_fetch, parse_sexp  # type: ignore

try:
    from imap_keychain import KeychainError, read_imap_app_password
except ImportError:  # package import path
    from scripts.imap_keychain import (  # type: ignore
        KeychainError,
        read_imap_app_password,
    )

# Pinned Apple curl. Not read from CURL_BIN. No Homebrew path.
CURL_BIN = "/usr/bin/curl"
_DEFAULT_TIMEOUT_S = 30
UID_BATCH_SIZE = 50
_UIDVALIDITY_RE = re.compile(r"(?m)^\* OK \[UIDVALIDITY (\d+)\]")
_EXAMINE_RE = re.compile(r'^EXAMINE "(?:[^"\\\r\n\x00]|\\.)*"$')
_FETCH_RE = re.compile(
    r"^UID FETCH (\d+(?::\d+)?(?:,\d+(?::\d+)?)*) \(BODYSTRUCTURE\)$"
)
_FORBIDDEN_ARGV = frozenset(
    {
        "-v",
        "--verbose",
        "--trace",
        "--trace-ascii",
        "--trace-time",
        "-k",
        "--insecure",
        "--user",
        "-u",
    }
)
_FORBIDDEN_CONFIG = frozenset(
    {
        "verbose",
        "trace",
        "trace-ascii",
        "trace-time",
        "insecure",
    }
)
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
    if not text or any(ch in text for ch in " \t\r\n\x00/@\\"):
        raise CurlImapError("imap host is required")
    return text


def base_url(host: str, port: int = 993) -> str:
    """``imaps://host:port/`` with no mailbox, so curl does not SELECT."""
    return "imaps://%s:%s/" % (_check_host(host), int(port))


def _check_mailbox(mailbox: str) -> str:
    if mailbox is None or str(mailbox).strip() == "":
        raise ValueError("missing mailbox")
    text = str(mailbox)
    if "\r" in text or "\n" in text or "\x00" in text:
        raise CurlImapError("imap mailbox is invalid")
    return text


def examine_command(mailbox: str) -> str:
    """Read-only EXAMINE of one quoted mailbox. The mailbox is not in the URL."""
    name = _check_mailbox(mailbox)
    command = "EXAMINE " + quote_imap_mailbox(name)
    allow_command(command)
    return command


def _check_uid(uid: str) -> str:
    if uid is None or str(uid).strip() == "":
        raise ValueError("missing uid")
    text = str(uid).strip()
    if not text.isdigit():
        raise ValueError("missing uid")
    return text


def compress_uid_set(uids) -> str:
    """IMAP uid-set. Consecutive numbers become ``start:end``."""
    nums = sorted({int(_check_uid(uid)) for uid in uids})
    if not nums:
        raise ValueError("missing uid")
    parts = []
    start = prev = nums[0]
    for number in nums[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append("%s:%s" % (start, prev) if prev != start else str(start))
        start = prev = number
    parts.append("%s:%s" % (start, prev) if prev != start else str(start))
    return ",".join(parts)


def batch_uid_sets(uids, size: int = UID_BATCH_SIZE) -> list:
    """Split UIDs into bounded sets. One set is one UID FETCH, not one curl."""
    nums = sorted({int(_check_uid(uid)) for uid in uids})
    width = int(size)
    if width <= 0:
        width = UID_BATCH_SIZE
    sets = []
    for index in range(0, len(nums), width):
        sets.append(compress_uid_set(nums[index : index + width]))
    return sets


def fetch_command(uidset: str) -> str:
    """Allowlisted ``UID FETCH <uidset> (BODYSTRUCTURE)``."""
    command = "UID FETCH %s (BODYSTRUCTURE)" % str(uidset).strip()
    allow_command(command)
    return command


def allow_command(command: str) -> None:
    """Permit only EXAMINE and UID FETCH (BODYSTRUCTURE)."""
    text = str(command or "")
    if _EXAMINE_RE.match(text) or _FETCH_RE.match(text):
        return
    raise CurlImapError("imap command refused")


def curl_argv() -> list:
    """Pinned argv. Password and commands stay in the ``-K -`` config."""
    argv = [CURL_BIN, "--silent", "--show-error", "-K", "-"]
    guard_curl_argv(argv)
    return argv


def guard_curl_argv(argv) -> None:
    """Refuse any curl invocation that is not pinned stdin config.

    Mirrors the tombstone rule that the secret never rides in argv:
    binary is ``/usr/bin/curl``, config is ``-K -``, and verbose, trace,
    insecure, ``--user``, and Homebrew paths are rejected.
    """
    args = [str(item) for item in list(argv or [])]
    if not args or args[0] != CURL_BIN:
        raise CurlImapError("imap curl binary is not /usr/bin/curl")
    for arg in args:
        if "homebrew" in arg.lower() or arg.startswith("/opt/"):
            raise CurlImapError("imap curl binary is not /usr/bin/curl")
        if arg in _FORBIDDEN_ARGV:
            raise CurlImapError("imap curl argv refused")
    if "-K" not in args:
        raise CurlImapError("imap curl config must be stdin")
    k_at = args.index("-K")
    if k_at + 1 >= len(args) or args[k_at + 1] != "-":
        raise CurlImapError("imap curl config must be stdin")


def assert_secret_not_in_argv(argv, password: str) -> None:
    if not password:
        return
    for arg in argv:
        if password in str(arg):
            raise CurlImapError("imap curl argv refused")


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


def _config_line(name: str, value: str) -> str:
    return '%s = "%s"\n' % (name, _curl_config_escape(value))


def transfer_block(
    *,
    user: str,
    password: str,
    command: str,
    url: str,
    timeout_s: float,
    cacert: str | None,
) -> str:
    """One transfer. ``next`` clears these, so each block carries them."""
    allow_command(command)
    seconds = _seconds(timeout_s)
    lines = [
        "silent\n",
        "show-error\n",
        'dump-header = "-"\n',
        _config_line("connect-timeout", seconds),
        _config_line("max-time", seconds),
    ]
    if cacert:
        lines.append(_config_line("cacert", str(cacert)))
    lines.append(_config_line("user", "%s:%s" % (user, password)))
    lines.append(_config_line("request", command))
    lines.append(_config_line("url", url))
    return "".join(lines)


def curl_config(blocks: list) -> str:
    """Join transfer blocks with ``next``. At least one block is required."""
    if not blocks:
        raise CurlImapError("imap command refused")
    text = "\nnext\n".join(block.rstrip("\n") for block in blocks) + "\n"
    guard_curl_config(text)
    return text


def config_blocks(config_text: str) -> list:
    """Split a config on a line that is exactly ``next``."""
    blocks = []
    current = []
    for line in (config_text or "").splitlines():
        if line.strip() == "next":
            blocks.append("\n".join(current))
            current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [block for block in blocks if block.strip()]


def guard_curl_config(config_text: str) -> None:
    """Reject verbose, trace, and insecure options in the stdin config."""
    for line in (config_text or "").splitlines():
        name = line.split("=", 1)[0].strip().lower()
        if name in _FORBIDDEN_CONFIG:
            raise CurlImapError("imap curl argv refused")


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


def redact_stderr(stderr: str, password: str, user: str = "") -> str:
    """Strip the secret before any stderr is retained. Never logs the raw text."""
    text = stderr or ""
    if user and password:
        text = text.replace("%s:%s" % (user, password), "[redacted]")
    if password:
        text = text.replace(password, "[redacted]")
    return text


def undouble_untagged(text: str) -> str:
    """Drop a curl dump-header copy of an identical untagged line.

    ``--dump-header -`` writes each ``*`` line twice and then the literal
    bytes once. Collapsing exact consecutive ``*`` duplicates leaves the
    ``{n}`` literal in place for the BODYSTRUCTURE parser.
    """
    lines = (text or "").splitlines(keepends=True)
    out = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            index + 1 < len(lines)
            and lines[index + 1] == line
            and line.startswith("*")
        ):
            out.append(line)
            index += 2
            continue
        out.append(line)
        index += 1
    return "".join(out)


def uidvalidity_from_curl_output(text: str) -> int:
    """Untagged ``* OK [UIDVALIDITY n]`` only. Tagged READ-ONLY is ignored."""
    found = _UIDVALIDITY_RE.search(undouble_untagged(text or ""))
    if not found:
        raise CurlImapError("imap uidvalidity missing")
    return int(found.group(1))


def _unwrap_structure(raw: str) -> str:
    """Return the sexp text. A ``{n}`` literal's bytes are that sexp."""
    try:
        node = parse_sexp(raw)
    except ParseError:
        return raw
    if isinstance(node, str):
        return node
    return raw


def structures_by_uid(text: str) -> dict:
    """Map UID to BODYSTRUCTURE text, including a ``{n}`` literal."""
    cleaned = undouble_untagged(text or "")
    found = {}
    upper = cleaned.upper()
    start = 0
    marker = "BODYSTRUCTURE"
    while True:
        idx = upper.find(marker, start)
        if idx < 0:
            break
        window = cleaned[max(0, idx - 120) : idx]
        uids = re.findall(r"UID\s+(\d+)", window, flags=re.IGNORECASE)
        try:
            structure = bodystructure_from_fetch(cleaned[idx:])
        except ParseError:
            raise
        consumed = len(structure)
        if not uids:
            after = cleaned[idx : idx + len(marker) + consumed + 40]
            uids = re.findall(r"UID\s+(\d+)", after, flags=re.IGNORECASE)
        if uids:
            found[uids[-1]] = _unwrap_structure(structure)
        start = idx + len(marker)
        # Step past this structure so a nested copy is not a second hit.
        rest = cleaned[idx + len(marker) :]
        at = rest.find(structure)
        if at >= 0:
            start = idx + len(marker) + at + consumed
    return found


def _raise_for_status(rc: int, stderr: str, password: str, user: str) -> str:
    """One failure, no retry. The returned stderr is redacted and not raised raw."""
    redacted = redact_stderr(stderr, password, user)
    low = redacted.lower()
    if "errno 9" in low or "bad file descriptor" in low:
        raise CurlImapError("imap curl failed closed (errno 9); not retrying")
    if rc == 67:
        raise CurlImapError("imap curl authentication failed; not retrying")
    if rc == 21:
        raise CurlImapError("imap curl failed (rc 21 no or bad); not retrying")
    if rc == 7:
        raise CurlImapError("imap curl failed (rc 7 connect); not retrying")
    if rc == 60:
        raise CurlImapError("imap curl failed (rc 60 certificate); not retrying")
    if rc != 0:
        raise CurlImapError("imap curl failed (rc %s); not retrying" % int(rc))
    return redacted


def run_subprocess(argv, config_text, env, timeout):
    """Run pinned curl once. Config is stdin, never a file."""
    guard_curl_argv(argv)
    guard_curl_config(config_text)
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


class CurlImapsClient:
    """One curl per folder: EXAMINE, then batched UID FETCH, joined by ``next``."""

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
        batch_size: int = UID_BATCH_SIZE,
    ) -> None:
        self.host = _check_host(host)
        self.user = user or ""
        self.timeout = timeout
        self.port = int(port)
        self._password_fn = password_fn
        self._cacert = cacert
        self._runner = run_subprocess if runner is None else runner
        self._batch_size = int(batch_size) if batch_size else UID_BATCH_SIZE
        self._password = ""
        self.mailbox: str | None = None
        self.uidvalidity: int | None = None
        self._structures: dict = {}
        self.last_stderr = ""

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

    def _invoke(self, commands: list) -> str:
        if not commands:
            raise CurlImapError("imap command refused")
        for command in commands:
            allow_command(command)
        url = base_url(self.host, self.port)
        blocks = [
            transfer_block(
                user=self.user,
                password=self._password,
                command=command,
                url=url,
                timeout_s=self.timeout,
                cacert=self._cacert,
            )
            for command in commands
        ]
        config = curl_config(blocks)
        argv = curl_argv()
        assert_secret_not_in_argv(argv, self._password)
        env = curl_child_env(self._password)
        per = float(self.timeout) if self.timeout and self.timeout > 0 else float(_DEFAULT_TIMEOUT_S)
        timeout = per * len(commands) + 5.0
        rc, out, err = self._runner(argv, config, env, timeout)
        self.last_stderr = _raise_for_status(int(rc), err, self._password, self.user)
        return out

    def select(self, mailbox: str, readonly: bool = True) -> None:
        """EXAMINE only. Readonly. A curl failure is not retried."""
        if readonly is not True:
            raise CurlImapError("imap select must be readonly")
        name = _check_mailbox(mailbox)
        text = self._invoke([examine_command(name)])
        self.mailbox = name
        self.uidvalidity = uidvalidity_from_curl_output(text)
        self._structures = {}

    def open_folder(self, mailbox: str, uids) -> None:
        """One process: EXAMINE, then batched UID FETCH. No per-UID retry."""
        name = _check_mailbox(mailbox)
        commands = [examine_command(name)]
        tokens = []
        for uid in uids:
            tokens.append(_check_uid(uid))
        if tokens:
            for uidset in batch_uid_sets(tokens, self._batch_size):
                commands.append(fetch_command(uidset))
        text = self._invoke(commands)
        self.mailbox = name
        self.uidvalidity = uidvalidity_from_curl_output(text)
        self._structures = structures_by_uid(text) if tokens else {}

    def fetch_bodystructure(self, uid: str) -> str:
        """Return a structure from the folder batch. Does not start curl."""
        if self.mailbox is None:
            raise CurlImapError("imap mailbox is not selected")
        token = _check_uid(uid)
        if token not in self._structures:
            raise ValueError("imap uid missing from batch")
        return self._structures[token]
