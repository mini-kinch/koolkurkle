#!/usr/bin/env python3
"""BODYSTRUCTURE fetch over pinned ``/usr/bin/curl imaps://``.

One curl process per folder. The URL is the base ``imaps://host:port/``
with no mailbox, so curl does not SELECT (SELECT is read-write). The
first transfer is ``EXAMINE "<mailbox>"``. Further transfers are
``UID FETCH <uidset> (BODYSTRUCTURE)``, joined by ``next`` so the login
is reused. ``--fail-early`` stops the process on the first transfer
error so a failed login does not open a second connection. ``next``
resets per-transfer options, so every transfer
repeats ``user``, ``connect-timeout``, ``max-time``, ``write-out``,
and ``cacert`` when a test certificate is injected.

The password is written only to that process's stdin as curl config
(``-K -``, ``user = "..."``). It is not placed in argv, the environment,
or a file. There is no ``-v``, ``--verbose``, or ``--trace``. Stderr is
redacted before it is retained. The binary is the literal
``/usr/bin/curl``: ``CURL_BIN`` is not read, and Homebrew curl is not a
fallback.

Stdout is captured as bytes so a CRLF before a ``{n}`` literal stays
a CRLF. There is no ``--dump-header``. UIDVALIDITY is the first
untagged ``* OK [UIDVALIDITY n]`` in the EXAMINE reply, before any
FETCH line. ``write-out`` records ``num_connects``; more than one new
connection fails closed. Curl status 7, 21, 28, 60, and 67 become a
classified message with no stderr and no secret. A failed batch is
not retried one UID at a time.
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
_FETCH_LINE_RE = re.compile(r"(?im)^\* \d+ FETCH\b")
_FETCH_START_RE = re.compile(r"(?m)^\* \d+ FETCH\b")
_BRACE_RE = re.compile(r"\{\d+\}")
_CONNECT_RE = re.compile(r"^CURL_NUM_CONNECTS:(\d+)$")
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

    def __init__(self, message: str, rc: int | None = None) -> None:
        super().__init__(message)
        self.rc = rc


class LiteralFetchError(ValueError):
    """One UID's structure failed. Other UIDs in the folder still parse."""

    def __init__(self, uid: str, reason: str) -> None:
        self.uid = str(uid)
        self.reason = str(reason)
        super().__init__(self.reason)


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
    argv = [CURL_BIN, "--silent", "--show-error", "--fail-early", "-K", "-"]
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
        _config_line("connect-timeout", seconds),
        _config_line("max-time", seconds),
        _config_line("write-out", "CURL_NUM_CONNECTS:%{num_connects}\n"),
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
    """Drop an exact consecutive duplicate of an untagged ``*`` line.

    A captured curl transcript can repeat those lines. Collapsing them
    leaves a ``{n}`` literal in place. Production does not set
    ``dump-header``.
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
    """First untagged UIDVALIDITY in the EXAMINE reply, before FETCH.

    A later ``* OK [UIDVALIDITY n]`` after ``* N FETCH`` is not used.
    Tagged ``[READ-ONLY]`` text is ignored.
    """
    cleaned = undouble_untagged(text or "")
    fetch_at = _FETCH_LINE_RE.search(cleaned)
    prefix = cleaned[: fetch_at.start()] if fetch_at else cleaned
    found = _UIDVALIDITY_RE.search(prefix)
    if not found:
        raise CurlImapError("imap uidvalidity missing")
    return int(found.group(1))


def strip_connect_markers(text: str) -> str:
    """Remove per-transfer connect counts. A second connect fails closed."""
    counts = []
    kept = []
    for line in (text or "").splitlines(keepends=True):
        match = _CONNECT_RE.match(line.rstrip("\r\n"))
        if match:
            counts.append(int(match.group(1)))
            continue
        kept.append(line)
    if counts and sum(counts) != 1:
        raise CurlImapError("imap curl opened a second connection; not retrying")
    return "".join(kept)


def _unwrap_structure(raw: str) -> str:
    """Return the sexp text. A ``{n}`` literal's bytes are that sexp."""
    try:
        node = parse_sexp(raw)
    except ParseError:
        return raw
    if isinstance(node, str):
        return node
    return raw


def _literal_reason(segment: str, exc: BaseException) -> str:
    """Classify one structure. A short remainder is truncated; a brace that
    ate the following protocol is dropped.
    """
    message = str(exc)
    if (
        "literal short" in message
        or "literal newline" in message
        or "literal length" in message
    ):
        return "literal_truncated"
    if _BRACE_RE.search(segment):
        return "literal_dropped"
    return "parse_error"


def parse_fetch_structures(text: str):
    """Return ``(ok_by_uid, reason_by_uid)``.

    Each FETCH line is parsed alone. A dropped or truncated literal records
    that UID and leaves every other UID in the buffer parseable.
    """
    cleaned = undouble_untagged(text or "")
    starts = [match.start() for match in _FETCH_START_RE.finditer(cleaned)]
    if not starts and "BODYSTRUCTURE" in cleaned.upper():
        starts = [0]
    found = {}
    issues = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(cleaned)
        segment = cleaned[start:end]
        uids = re.findall(r"UID\s+(\d+)", segment, flags=re.IGNORECASE)
        uid = uids[-1] if uids else ""
        try:
            structure = bodystructure_from_fetch(segment)
        except ParseError as exc:
            if uid:
                issues[uid] = _literal_reason(segment, exc)
            continue
        if not uid:
            continue
        found[uid] = _unwrap_structure(structure)
    return found, issues


def structures_by_uid(text: str) -> dict:
    """Map UID to BODYSTRUCTURE text. Failures are omitted, not raised."""
    found, _issues = parse_fetch_structures(text)
    return found


def _raise_for_status(rc: int, stderr: str, password: str, user: str) -> str:
    """One failure, no retry. Stderr is redacted and is not part of the message."""
    redacted = redact_stderr(stderr, password, user)
    low = redacted.lower()
    if "errno 9" in low or "bad file descriptor" in low:
        raise CurlImapError("imap curl failed closed (errno 9); not retrying", int(rc))
    if rc == 67:
        raise CurlImapError(
            "imap curl authentication failed (rc 67); not retrying", int(rc)
        )
    if rc == 21:
        raise CurlImapError("imap curl failed (rc 21 no or bad); not retrying", int(rc))
    if rc == 7:
        raise CurlImapError("imap curl failed (rc 7 connect); not retrying", int(rc))
    if rc == 60:
        raise CurlImapError(
            "imap curl failed (rc 60 certificate); not retrying", int(rc)
        )
    if rc == 28:
        raise CurlImapError("imap curl failed (rc 28 timeout); not retrying", int(rc))
    if rc != 0:
        raise CurlImapError("imap curl failed (rc %s); not retrying" % int(rc), int(rc))
    return redacted


def run_subprocess(argv, config_text, env, timeout):
    """Run pinned curl once. Config is stdin bytes, never a file.

    Stdout stays bytes until it is decoded with ``surrogateescape``, so
    a CRLF in front of a ``{n}`` literal is not turned into LF.
    """
    guard_curl_argv(argv)
    guard_curl_config(config_text)
    try:
        proc = subprocess.run(
            list(argv),
            input=config_text.encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CurlImapError("imap curl failed (rc 28 timeout); not retrying", 28) from None
    except OSError as exc:
        if getattr(exc, "errno", None) == 9:
            raise CurlImapError(
                "imap curl failed closed (errno 9); not retrying", 9
            ) from None
        raise CurlImapError("imap curl failed closed") from None
    stdout = (proc.stdout or b"").decode("utf-8", "surrogateescape")
    stderr = (proc.stderr or b"").decode("utf-8", "replace")
    return int(proc.returncode), stdout, stderr


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
        self._issues: dict = {}
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
        return strip_connect_markers(out)

    def select(self, mailbox: str, readonly: bool = True) -> None:
        """EXAMINE only. Readonly. A curl failure is not retried."""
        if readonly is not True:
            raise CurlImapError("imap select must be readonly")
        name = _check_mailbox(mailbox)
        text = self._invoke([examine_command(name)])
        self.mailbox = name
        self.uidvalidity = uidvalidity_from_curl_output(text)
        self._structures = {}
        self._issues = {}

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
        if tokens:
            found, issues = parse_fetch_structures(text)
        else:
            found, issues = {}, {}
        self._structures = found
        self._issues = issues

    def fetch_bodystructure(self, uid: str) -> str:
        """Return a structure from the folder batch. Does not start curl."""
        if self.mailbox is None:
            raise CurlImapError("imap mailbox is not selected")
        token = _check_uid(uid)
        if token in self._issues:
            raise LiteralFetchError(token, self._issues[token])
        if token not in self._structures:
            raise ValueError("imap uid missing from batch")
        return self._structures[token]
