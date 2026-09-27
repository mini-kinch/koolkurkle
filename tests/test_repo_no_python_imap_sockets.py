#!/usr/bin/env python3
"""Repo guard: scripts/ must not open a Python IMAP socket.

Scanned origin/main (9c8a454 and dd2f47c) scripts/**/*.py. The only
import of imaplib, IMAP4_SSL, or Python ssl connection to port 993 was
scripts/attachments/meta_fill.py. This branch removes that client from
scripts/. scripts/path_a_bench.py calls socket.create_connection for the
local Ollama port only. It does not name imap.mail.me.com or port 993,
so it is not an IMAP hit and is not allowlisted.

ALLOWLIST is empty on purpose. Add a file here only when main already
contains an IMAP-socket hit this PR must not edit. Name the file in the
comment above the constant.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# No pre-existing IMAP-socket hits outside meta_fill.py. Leave this empty.
ALLOWLIST: tuple[str, ...] = ()

_IMPORT_IMAPLIB = re.compile(r"(?m)^\s*(?:import\s+imaplib\b|from\s+imaplib\b)")
_IMAP4 = re.compile(r"\bimaplib\s*\.\s*IMAP4\b|\bIMAP4_SSL\b")
_PY_NET = re.compile(
    r"\b(?:socket\s*\.\s*(?:create_connection|socket)|"
    r"ssl\s*\.\s*(?:wrap_socket|create_default_context|SSLContext))\b"
)
_PORT_993_CALL = re.compile(
    r"(?:create_connection|wrap_socket|IMAP4_SSL|IMAP4|create_default_context)"
    r"\s*\([^)]*\b993\b",
    re.S,
)
_SSL_CONTEXT_993 = re.compile(r"\b993\b\s*,[^)\n]{0,120}ssl_context")


def imap_socket_violations(text: str) -> list[str]:
    """Return why ``text`` is a production Python IMAP socket."""
    reasons = []
    if _IMPORT_IMAPLIB.search(text):
        reasons.append("imports imaplib")
    if _IMAP4.search(text):
        reasons.append("imaplib IMAP4 or IMAP4_SSL")
    if "imap.mail.me.com" in text and _PY_NET.search(text):
        reasons.append("python socket or ssl to imap.mail.me.com")
    if _PORT_993_CALL.search(text):
        reasons.append("python connection call to port 993")
    if _SSL_CONTEXT_993.search(text):
        reasons.append("port 993 passed with ssl_context")
    return reasons


class RepoNoPythonImapSocketsTests(unittest.TestCase):
    def test_scripts_tree_has_no_python_imap_socket(self):
        hits = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            reasons = imap_socket_violations(path.read_text(encoding="utf-8"))
            if reasons:
                hits.append("%s: %s" % (rel, ", ".join(reasons)))
        self.assertEqual(hits, [])
        for rel in ALLOWLIST:
            self.assertTrue((ROOT / rel).is_file(), rel)
            self.assertTrue(rel.startswith("scripts/"), rel)

    def test_scanner_flags_imaplib_and_ignores_curl_urls(self):
        self.assertIn("imports imaplib", imap_socket_violations("import imaplib\n"))
        self.assertIn(
            "imports imaplib",
            imap_socket_violations("from imaplib import IMAP4_SSL\n"),
        )
        sample = (
            "import imaplib\n"
            "ctx = ssl.create_default_context()\n"
            "imaplib.IMAP4_SSL('imap.mail.me.com', 993, ssl_context=ctx)\n"
        )
        flagged = imap_socket_violations(sample)
        self.assertIn("imports imaplib", flagged)
        self.assertIn("imaplib IMAP4 or IMAP4_SSL", flagged)
        self.assertIn("python socket or ssl to imap.mail.me.com", flagged)
        self.assertIn("python connection call to port 993", flagged)
        curl_only = (
            "port = 993\n"
            "url = 'imaps://imap.example.invalid:993/INBOX'\n"
            "argv = ['/usr/bin/curl', url]\n"
        )
        self.assertEqual(imap_socket_violations(curl_only), [])
        ollama = "sock = socket.create_connection((host, 11434), timeout=1.0)\n"
        self.assertEqual(imap_socket_violations(ollama), [])


if __name__ == "__main__":
    unittest.main()
