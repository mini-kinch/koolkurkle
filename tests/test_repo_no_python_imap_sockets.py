#!/usr/bin/env python3
"""Repo guard: scripts/ must not open a Python IMAP socket.

Scanned origin/main (9c8a454 and dd2f47c) scripts/**/*.py. The only
import of imaplib, IMAP4_SSL, or Python ssl connection to port 993 was
scripts/attachments/meta_fill.py, and that client was removed from
scripts/. scripts/path_a_bench.py calls socket.create_connection for the
local Ollama port only. It does not name imap.mail.me.com or port 993,
so it is not an IMAP hit and is not allowlisted.

ALLOWLIST is an explicit exact-match tuple of repo-relative paths.
No globs and no directory allow. The only entry is
scripts/attachments/imaplib_part.py (read-only EXAMINE plus BODY.PEEK
partials). A missing allowlisted file is not a failure. Every other
file under scripts/ still fails.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Exact-match allowlist. No globs. No directory allow.
# One path only: scripts/attachments/imaplib_part.py
# (read-only EXAMINE plus BODY.PEEK partials). The file is not required
# to exist; a missing allowlisted path is not a failure. Every other
# path under scripts/ still fails.
ALLOWLIST: tuple[str, ...] = (
    "scripts/attachments/imaplib_part.py",
)

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


def collect_imap_socket_hits(scripts_dir: Path, root: Path) -> list[str]:
    """Scan ``scripts_dir`` for Python IMAP sockets outside ALLOWLIST.

    Comparison is exact (``rel in ALLOWLIST``). No glob and no directory
    prefix. A path that is not on disk is not a hit, including a missing
    allowlisted file.
    """
    hits = []
    if not scripts_dir.is_dir():
        return hits
    for path in sorted(scripts_dir.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST:
            continue
        reasons = imap_socket_violations(path.read_text(encoding="utf-8"))
        if reasons:
            hits.append("%s: %s" % (rel, ", ".join(reasons)))
    return hits


class RepoNoPythonImapSocketsTests(unittest.TestCase):
    def test_scripts_tree_has_no_python_imap_socket(self):
        self.assertEqual(collect_imap_socket_hits(SCRIPTS, ROOT), [])

    def test_allowlist_has_exactly_one_entry(self):
        self.assertEqual(
            ALLOWLIST,
            ("scripts/attachments/imaplib_part.py",),
        )
        self.assertEqual(len(ALLOWLIST), 1)
        entry = ALLOWLIST[0]
        self.assertEqual(entry, "scripts/attachments/imaplib_part.py")
        self.assertTrue(entry.startswith("scripts/"))
        self.assertTrue(entry.endswith(".py"))
        self.assertFalse(entry.endswith("/"))
        self.assertFalse(any(ch in entry for ch in "*?[]"))
        self.assertNotIn("scripts/attachments", ALLOWLIST)
        self.assertNotIn("scripts/attachments/", ALLOWLIST)
        self.assertNotIn("scripts/attachments/*.py", ALLOWLIST)

    def test_missing_allowlisted_file_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "attachments"
            scripts.mkdir(parents=True)
            (scripts / "clean.py").write_text("print('curl only')\n", encoding="utf-8")
            self.assertFalse((root / "scripts/attachments/imaplib_part.py").exists())
            self.assertEqual(collect_imap_socket_hits(root / "scripts", root), [])

    def test_allowlisted_path_passes(self):
        body = (
            "import imaplib\n"
            "imaplib.IMAP4_SSL('imap.mail.me.com', 993)\n"
        )
        self.assertTrue(imap_socket_violations(body))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowed = root / "scripts" / "attachments" / "imaplib_part.py"
            allowed.parent.mkdir(parents=True)
            allowed.write_text(body, encoding="utf-8")
            self.assertEqual(collect_imap_socket_hits(root / "scripts", root), [])

    def test_sibling_importing_imaplib_still_fails(self):
        body = "import imaplib\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowed = root / "scripts" / "attachments" / "imaplib_part.py"
            sibling = root / "scripts" / "attachments" / "other.py"
            allowed.parent.mkdir(parents=True)
            allowed.write_text(body, encoding="utf-8")
            sibling.write_text(body, encoding="utf-8")
            hits = collect_imap_socket_hits(root / "scripts", root)
            self.assertEqual(
                hits,
                ["scripts/attachments/other.py: imports imaplib"],
            )

    def test_renamed_or_copied_file_still_fails(self):
        body = "import imaplib\n"
        renamed = "scripts/attachments/imaplib_fetch.py"
        copied = "scripts/attachments/imaplib_part_copy.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in (
                "scripts/attachments/imaplib_part.py",
                renamed,
                copied,
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
            hits = collect_imap_socket_hits(root / "scripts", root)
            self.assertEqual(
                hits,
                [
                    "%s: imports imaplib" % renamed,
                    "%s: imports imaplib" % copied,
                ],
            )

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
