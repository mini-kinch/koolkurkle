"""ATT-1 part-byte session over stdlib imaplib.

EXAMINE and a pinned ``UID FETCH (BODY.PEEK[part]<offset.count>)`` only.
This module does not call curl and it is not a fallback for a failed
curl ``-X`` read. The metadata fill does not import it. ``fetch_p1``
keeps the curl client until the transport switch.

TLS is ``ssl.create_default_context`` with hostname checks and
``CERT_REQUIRED``. ``SSLKEYLOGFILE`` is cleared unless
``MAILROOM_SSLKEYLOGFILE_ALLOW=1``. An extra CA is loaded only when the
caller passes ``ca_file``. The password is an in-process argument and
is never logged. ``OSError`` errno 9 becomes ``FetchRefuse`` before any
part bytes are stored.
"""

from __future__ import annotations

import imaplib
import os
import re
import ssl
import time

_PINNED_FETCH_RE = re.compile(
    r"^UID FETCH [1-9]\d* \(BODY\.PEEK\[[1-9]\d*(?:\.[1-9]\d*)*\]<\d+\.\d+>\)$"
)
_NIL_ATOM_RE = re.compile(
    br"BODY(?:\.PEEK)?\[[0-9.]+\](?:<\d+(?:\.\d+)?>)?\s+NIL\b"
)
_QUOTED_RE = re.compile(br'BODY(?:\.PEEK)?\[[0-9.]+\](?:<\d+>)?\s+"((?:\\.|[^"\\])*)"')
_ALLOW_KEYLOG = "MAILROOM_SSLKEYLOGFILE_ALLOW"
_KEYLOG = "SSLKEYLOGFILE"


def _refuse(message: str):
    from attachments.fetch_p1 import FetchRefuse

    raise FetchRefuse(message)


class TransferDeadline:
    """Wall-clock budget. Each socket read is capped by the time left."""

    def __init__(self, seconds: float) -> None:
        try:
            budget = float(seconds)
        except (TypeError, ValueError):
            budget = 0.0
        if budget <= 0:
            _refuse("part fetch failed")
        self.deadline = time.monotonic() + budget

    def remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            _refuse("part fetch failed")
        return left

    def arm(self, conn) -> None:
        sock = getattr(conn, "sock", None)
        if sock is None:
            return
        sock.settimeout(self.remaining())


def verified_context(ca_file: str | None = None, env: dict | None = None) -> ssl.SSLContext:
    """Default-verify context. ``ca_file`` is the only extra trust anchor."""
    environ = os.environ if env is None else env
    allow = str(environ.get(_ALLOW_KEYLOG) or "") == "1"
    keylog = str(environ.get(_KEYLOG) or "")
    saved = os.environ.get(_KEYLOG)
    try:
        if allow and keylog:
            os.environ[_KEYLOG] = keylog
        else:
            os.environ.pop(_KEYLOG, None)
        context = ssl.create_default_context()
    finally:
        if saved is None:
            os.environ.pop(_KEYLOG, None)
        else:
            os.environ[_KEYLOG] = saved
    if not allow and getattr(context, "keylog_filename", None):
        context.keylog_filename = None
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        _refuse("imap tls verification is off")
    if ca_file:
        context.load_verify_locations(cafile=str(ca_file))
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            _refuse("imap tls verification is off")
    return context


def _unescape_quoted(raw: bytes) -> bytes:
    out = bytearray()
    escaped = False
    for byte in raw:
        if escaped:
            out.append(byte)
            escaped = False
            continue
        if byte == 0x5C:
            escaped = True
            continue
        out.append(byte)
    return bytes(out)


def interpret_peek(data, offset: int) -> bytes:
    """Apply R4 to one FETCH payload.

    A literal, including bytes that are ``NIL`` or empty, is the body.
    The NIL atom is the FETCH item, not those bytes. Empty or NIL at
    offset 0 is an error. A later empty body is a clean end.
    """
    try:
        start = int(offset)
    except (TypeError, ValueError):
        _refuse("part fetch failed")
    if start < 0:
        _refuse("part fetch failed")
    literals: list[bytes] = []
    atoms: list[bytes] = []
    for item in data or []:
        if item is None:
            continue
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            literals.append(bytes(item[1]))
            continue
        if isinstance(item, (bytes, bytearray)):
            atoms.append(bytes(item))
    if literals:
        blob = b"".join(literals)
        if start == 0 and blob == b"":
            _refuse("empty part fetch")
        return blob
    text = b" ".join(atoms)
    quoted = _QUOTED_RE.search(text)
    if quoted:
        blob = _unescape_quoted(quoted.group(1))
        if start == 0 and blob == b"":
            _refuse("empty part fetch")
        return blob
    if start == 0:
        _refuse("empty part fetch")
    if _NIL_ATOM_RE.search(text) or not text:
        return b""
    return b""


def _default_opener(host: str, port: int, *, ssl_context, timeout: float):
    return imaplib.IMAP4_SSL(host, int(port), ssl_context=ssl_context, timeout=timeout)


def _from_os(exc: OSError) -> None:
    if getattr(exc, "errno", None) == 9:
        _refuse("part fetch failed")
    _refuse("part fetch failed")


class ImaplibPartConn:
    """One ``imaplib.IMAP4_SSL`` session. Readonly EXAMINE, partial FETCH."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        ca_file: str | None = None,
        opener=None,
        env: dict | None = None,
    ) -> None:
        if not host or any(char in str(host) for char in "\r\n\x00"):
            _refuse("imap host is required")
        self._deadline = TransferDeadline(timeout)
        context = verified_context(ca_file, env)
        dial = _default_opener if opener is None else opener
        try:
            self._raw = dial(
                str(host),
                int(port),
                ssl_context=context,
                timeout=self._deadline.remaining(),
            )
        except OSError as exc:
            _from_os(exc)
        self._arm()

    def _arm(self) -> None:
        self._deadline.arm(self._raw)

    def _call(self, fn, message: str):
        self._arm()
        try:
            return fn()
        except OSError as exc:
            _from_os(exc)
        except Exception:
            _refuse(message)

    def login(self, user: str, password: str) -> tuple:
        if any(char in str(user) + str(password) for char in "\r\n\x00"):
            _refuse("imap login failed")
        try:
            return self._call(
                lambda: self._raw.login(str(user), str(password)),
                "imap login failed",
            )
        finally:
            password = ""

    def logout(self) -> tuple:
        try:
            return self._call(self._raw.logout, "part fetch failed")
        except Exception:
            try:
                self._raw.shutdown()
            except Exception:
                pass
            return "BYE", [b""]

    def select(self, mailbox: str, readonly: bool = False) -> tuple:
        if readonly is not True:
            _refuse("imap select failed")
        if any(char in str(mailbox) for char in "\r\n\x00"):
            _refuse("missing mailbox")
        return self._call(
            lambda: self._raw.select(str(mailbox), readonly=True),
            "imap select failed",
        )

    def uid(self, cmd: str, uid: str, item: str) -> tuple:
        if str(cmd).upper() != "FETCH":
            _refuse("part fetch failed")
        command = "UID FETCH %s %s" % (uid, item)
        if _PINNED_FETCH_RE.match(command) is None:
            _refuse("part fetch failed")
        return self._call(
            lambda: self._raw.uid("FETCH", str(uid), str(item)),
            "part fetch failed",
        )
