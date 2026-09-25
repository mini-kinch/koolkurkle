"""Test-side shims. scripts/ask_mail.py is frozen and must not be edited."""

from __future__ import annotations

import sys
import unittest

_ASK_MAIL_PY39_SKIP = (
    "scripts/ask_mail.py needs Python 3.10+ (PEP 604 alias at import); "
    "ask_mail.py is frozen; run these under python3.10+"
)


def import_ask_mail():
    """Import ask_mail.

    On Python < 3.10 the frozen module raises TypeError while evaluating a
    PEP 604 alias. That case is a skip. Any other import error propagates.
    """
    try:
        import ask_mail
    except TypeError:
        if sys.version_info < (3, 10):
            raise unittest.SkipTest(_ASK_MAIL_PY39_SKIP)
        raise
    return ask_mail
