#!/usr/bin/env python3
"""KOO-20 IMAP via curl imaps — never Python sockets ops contract.

Docs/tests contract only. No live IMAP, no curl against IMAP, no
connection to imap.mail.me.com, no MailArchive, no live sqlite, no
Keychain reads, no rem-legacy writer.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")


class ImapViaCurlImapsNeverPythonSocketsTests(unittest.TestCase):
    def test_contract_locks_curl_imaps_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("IMAP live checks via curl imaps (never Python sockets)", raw)
        self.assertIn("IMAP live checks use `/usr/bin/curl imaps://`", raw)
        self.assertIn("Never open a Python socket client", raw)
        self.assertIn("imap.mail.me.com", raw)
        self.assertIn("Errno 9", raw)
        self.assertIn("bad file descriptor", text)
        self.assertIn("Python `socket` / `imaplib`", raw)
        self.assertIn("Do not retry with another Python socket", raw)
        self.assertIn("Fail closed", raw)
        self.assertIn("no `/usr/bin/curl imaps://`, no IMAP live check", text)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, email", text)
        self.assertIn("secret, credential, or app password", text)
        self.assertIn("# Mini — prove Apple curl exists", raw)
        self.assertIn("# MBP — prove Apple curl exists", raw)
        self.assertIn("test -x /usr/bin/curl", raw)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run live IMAP", text)
        self.assertIn("does not connect to `imap.mail.me.com`", text)
        self.assertIn("does not run curl against IMAP", text)
        self.assertIn("does not read Keychain", text)
        self.assertIn("does not", text.lower())
        self.assertIn("rem-legacy", text.lower())
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_gate(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("/usr/bin/curl imaps://", raw)
        self.assertIn("never Python sockets", text)
        self.assertIn("imap.mail.me.com", raw)
        self.assertIn("Errno 9", raw)
        self.assertNotIn("/Users/", raw)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, raw)

    def test_fail_closed_never_python_socket_fallback(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        self.assertIn("Fail closed: no `/usr/bin/curl imaps://`, no IMAP live check", text)
        self.assertIn("Never open a Python socket client", raw)
        self.assertIn("Do not retry with another Python socket", raw)
        self.assertNotIn("try imaplib first", low)
        self.assertNotIn("python socket fallback", low)
        self.assertNotIn("retry with imaplib", low)
        self.assertNotIn("sockets are ok", low)
        self.assertNotIn("assume the tool exists", low)


if __name__ == "__main__":
    unittest.main()
