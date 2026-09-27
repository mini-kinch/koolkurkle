#!/usr/bin/env python3
"""Test-only imaplib double for BODYSTRUCTURE fetches.

Production ``scripts/attachments/meta_fill.py`` does not import this
module. It does not import imaplib. Live fills use ``/usr/bin/curl``.
"""

from __future__ import annotations

import imaplib
import ssl
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from attachments.bodystructure import bodystructure_from_fetch  # noqa: E402
from attachments.meta_fill import FillRefuse  # noqa: E402
from imap_keychain import KeychainError, read_imap_app_password  # noqa: E402

_BODYSTRUCTURE_ITEM = "(BODYSTRUCTURE)"
_IMAP_SSL_PORT = 993


def quote_imap_mailbox(name: str) -> str:
    """IMAP atom quoting. Spaces, quotes, and backslashes stay one mailbox."""
    escaped = str(name).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _uidvalidity_from_conn(conn: Any) -> int:
    """Read UIDVALIDITY after EXAMINE or SELECT has returned.

    ``IMAP4.response`` pops the untagged value collected during that
    command. Real imaplib returns ``('UIDVALIDITY', [b'42'])``, or
    ``('UIDVALIDITY', [None])`` when the server did not send one.
    Call this once, immediately after ``select``, before anything else
    calls ``response('UIDVALIDITY')``.
    """
    responder = getattr(conn, "response", None)
    if responder is None:
        raise RuntimeError("imap uidvalidity missing")
    typ, data = responder("UIDVALIDITY")
    if typ != "UIDVALIDITY" or not data or data[0] in (None, b"", ""):
        raise RuntimeError("imap uidvalidity missing")
    raw = data[0]
    if isinstance(raw, bytes):
        text = raw.decode("ascii", "replace").strip()
    else:
        text = str(raw).strip()
    if not text.isdigit():
        raise RuntimeError("imap uidvalidity missing")
    return int(text)


class ImapBodystructureClient:
    """Test-only UID FETCH double over imaplib.IMAP4_SSL port 993.

    Not constructed by the CLI or by ``fill_metadata``. The password
    comes from Keychain via ``password_fn`` (default
    ``read_imap_app_password``). This class does not read a password
    argument or a password environment variable, and it does not put the
    password in fetch results. Plain IMAP is never constructed.
    Call ``select`` for each folder before fetching that folder's UIDs.
    """

    def __init__(
        self,
        host: str,
        user: str | None,
        *,
        timeout: float = 30,
        imap_factory: Any = None,
        password_fn: Callable[[], str] | None = None,
    ) -> None:
        if not host:
            raise FillRefuse("imap host is required")
        self.host = host
        self.user = user or ""
        self.port = _IMAP_SSL_PORT
        self.timeout = timeout
        self._factory = imaplib.IMAP4_SSL if imap_factory is None else imap_factory
        if self._factory is imaplib.IMAP4:
            raise FillRefuse("plain IMAP is refused")
        self._password_fn = password_fn
        self._conn: Any = None
        self.mailbox: str | None = None
        self.uidvalidity: int | None = None

    def __enter__(self) -> "ImapBodystructureClient":
        fn = self._password_fn or read_imap_app_password
        try:
            password = fn()
        except KeychainError:
            raise FillRefuse("imap keychain password is missing") from None
        try:
            context = ssl.create_default_context()
            self._conn = self._factory(
                self.host, 993, timeout=self.timeout, ssl_context=context
            )
            self._conn.login(self.user, password)
        except FillRefuse:
            self._close()
            raise
        except Exception:
            self._close()
            raise
        finally:
            password = ""
        return self

    def select(self, mailbox: str, readonly: bool = True) -> None:
        """EXAMINE one folder. Readonly. The mailbox argument is quoted."""
        if readonly is not True:
            raise FillRefuse("imap select must be readonly")
        if mailbox is None or str(mailbox).strip() == "":
            raise ValueError("missing mailbox")
        if self._conn is None:
            raise RuntimeError("imap select failed")
        quoted = quote_imap_mailbox(str(mailbox))
        typ, _data = self._conn.select(quoted, readonly=True)
        if typ != "OK":
            raise RuntimeError("imap select failed")
        self.mailbox = str(mailbox)
        self.uidvalidity = _uidvalidity_from_conn(self._conn)

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close()

    def _close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.logout()
        except Exception:
            return

    def fetch_bodystructure(self, uid: str) -> str:
        if uid is None or str(uid).strip() == "":
            raise ValueError("missing uid")
        typ, data = self._conn.uid("FETCH", str(uid), _BODYSTRUCTURE_ITEM)
        if typ != "OK":
            raise ValueError("bodystructure fetch failed")
        return bodystructure_from_fetch(data)
