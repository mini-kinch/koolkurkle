"""Opt-in imaplib transport for partial BODY.PEEK fetches.

EXAMINE only. The only fetch command is a regex-pinned
``UID FETCH <uid> (BODY.PEEK[<part>]<off.len>)`` partial.
TLS uses ``ssl.create_default_context()`` with hostname checks and
``CERT_REQUIRED``. A test CA file is added with ``load_verify_locations``
and does not turn verification off. The password is an in-process
argument. This module does not put that argument in the process
environment and it does not spawn a process.
"""

from __future__ import annotations

import imaplib
import re
import ssl

_PINNED_FETCH_RE = re.compile(
    r"^UID FETCH [1-9]\d* \(BODY\.PEEK\[[1-9]\d*(?:\.[1-9]\d*)*\]<\d+\.\d+>\)$"
)


def _refuse(message: str) -> None:
    from attachments.fetch_p1 import FetchRefuse

    raise FetchRefuse(message)


def verified_context(ca_file: str | None = None) -> ssl.SSLContext:
    """Default-verify context. ``ca_file`` adds a trust anchor only."""
    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        _refuse("imap tls verification is off")
    if ca_file:
        context.load_verify_locations(cafile=str(ca_file))
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            _refuse("imap tls verification is off")
    return context


def pinned_fetch_command(uid: str, item: str) -> str:
    """Return the wire command, or refuse if it is not a PEEK partial."""
    command = "UID FETCH %s %s" % (uid, item)
    if _PINNED_FETCH_RE.match(command) is None:
        _refuse("part fetch failed")
    return command


class ImaplibPartConn:
    """One ``imaplib.IMAP4_SSL`` session. Readonly EXAMINE, partial FETCH."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        ca_file: str | None = None,
    ) -> None:
        if not host or any(char in str(host) for char in "\r\n\x00"):
            _refuse("imap host is required")
        try:
            seconds = float(timeout)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds <= 0:
            _refuse("imap timeout is required")
        context = verified_context(ca_file)
        self._raw = imaplib.IMAP4_SSL(
            str(host),
            int(port),
            ssl_context=context,
            timeout=seconds,
        )
        self._raw.sock.settimeout(seconds)
        self.socket_timeout = self._raw.sock.gettimeout()

    def login(self, user: str, password: str) -> tuple:
        if any(char in str(user) + str(password) for char in "\r\n\x00"):
            _refuse("imap login failed")
        try:
            return self._raw.login(str(user), str(password))
        finally:
            password = ""

    def logout(self) -> tuple:
        try:
            return self._raw.logout()
        except Exception:
            try:
                self._raw.shutdown()
            except Exception:
                pass
            return "BYE", [b""]

    def select(self, mailbox: str, readonly: bool = False) -> tuple:
        if readonly is not True:
            _refuse("imap select failed")
        return self._raw.select(mailbox, readonly=True)

    def uid(self, cmd: str, uid: str, item: str) -> tuple:
        if str(cmd).upper() != "FETCH":
            _refuse("part fetch failed")
        pinned_fetch_command(str(uid), str(item))
        try:
            typ, data = self._raw.uid("FETCH", str(uid), str(item))
        except Exception:
            _refuse("part fetch failed")
        if typ != "OK":
            _refuse("part fetch failed")
        return typ, data
