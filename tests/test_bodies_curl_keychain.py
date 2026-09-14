#!/usr/bin/env python3
"""KOO-39 Mini brew-curl ≥8.17 + Keychain name. No live IMAP / Keychain."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import imap_fetch_bodies_fts as bodies  # noqa: E402

DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
MAILROOM = ROOT / "docs" / "MAILROOM.md"
PLIST = ROOT / "launchd" / "com.mailroom.daily.plist"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HOMEBREW = "/opt/homebrew/opt/curl/bin/curl"
APPLE = "/usr/bin/curl"
KEYCHAIN = "mailroom.imap.app-password"


class BrewCurlVersionTests(unittest.TestCase):
    def test_parse_and_minimum(self):
        self.assertEqual(bodies.parse_curl_version("curl 8.17.0 (x86_64-apple-darwin)"), (8, 17, 0))
        self.assertEqual(bodies.parse_curl_version("curl 8.7.1"), (8, 7, 1))
        self.assertTrue(bodies.version_at_least((8, 17, 0)))
        self.assertTrue(bodies.version_at_least((8, 18, 0)))
        self.assertFalse(bodies.version_at_least((8, 16, 9)))
        self.assertFalse(bodies.version_at_least(None))

    def test_apple_curl_fail_closed_for_body_peek(self):
        with self.assertRaises(bodies.CurlRefuse) as ctx:
            bodies.refuse_apple_curl_for_body_peek(APPLE)
        msg = str(ctx.exception)
        self.assertIn("fail-closed", msg)
        self.assertIn("BODY.PEEK", msg)
        self.assertIn(HOMEBREW, msg)
        bodies.refuse_apple_curl_for_body_peek(HOMEBREW)

    def test_resolve_prefers_homebrew_and_refuses_apple(self):
        exists = lambda p: str(p) == HOMEBREW
        path = bodies.resolve_bodies_curl(
            curl_bin="",
            version_text="curl 8.17.0 (Homebrew)",
            exists_fn=exists,
        )
        self.assertEqual(path, Path(HOMEBREW))

        with self.assertRaises(bodies.CurlRefuse):
            bodies.resolve_bodies_curl(
                curl_bin=APPLE,
                version_text="curl 8.7.1 (secure transport)",
                exists_fn=lambda _p: True,
            )
        with self.assertRaises(bodies.CurlRefuse):
            bodies.resolve_bodies_curl(
                curl_bin=HOMEBREW,
                version_text="curl 8.16.0 (Homebrew)",
                exists_fn=lambda _p: True,
            )


class KeychainNameContractTests(unittest.TestCase):
    def test_name_only_never_secret(self):
        self.assertEqual(bodies.KEYCHAIN_ITEM_NAME, KEYCHAIN)
        raw = PLIST.read_text(encoding="utf-8")
        self.assertIn(KEYCHAIN, raw)
        self.assertNotIn("IMAP_APP_PASSWORD", raw)
        for path in (DAILY, OPS, README, MAILROOM, ROOT / "scripts" / "imap_fetch_bodies_fts.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIn(KEYCHAIN, text, msg=path.name)
            self.assertIn(HOMEBREW, text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)


if __name__ == "__main__":
    unittest.main()
