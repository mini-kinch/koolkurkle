#!/usr/bin/env python3
"""Canaries for the suite-wide /usr/bin/security and /usr/bin/curl guard.

These tests do not call ``hermetic_binaries.install()``. Discover loads the
guard from ``tests/sitecustomize.py`` because ``PYTHONPATH=tests``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

import hermetic_binaries

ROOT = Path(__file__).resolve().parents[1]


class HermeticBinaryTests(unittest.TestCase):
    def test_canary_unpatched_security_raises(self):
        """An unpatched /usr/bin/security call fails the test immediately."""
        with self.assertRaises(hermetic_binaries.HermeticBinaryError) as ctx:
            subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    "mailroom.not-a-real-item",
                    "-a",
                    "user@example.com",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        self.assertIn("unpatched /usr/bin/security", str(ctx.exception))
        print("hermetic_guard=active", flush=True)

    def test_canary_unpatched_curl_non_loopback_raises(self):
        """An unpatched curl call to a non-loopback URL fails immediately."""
        with self.assertRaises(hermetic_binaries.HermeticBinaryError) as ctx:
            subprocess.run(
                [
                    "/usr/bin/curl",
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    "1",
                    "--max-time",
                    "2",
                    "https://imap.example.invalid/",
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        self.assertIn("unpatched /usr/bin/curl", str(ctx.exception))
        print("hermetic_guard=active", flush=True)

    def test_security_stays_blocked_inside_curl_opt_in(self):
        with hermetic_binaries.allow_real_curl("127.0.0.1"):
            with self.assertRaises(hermetic_binaries.HermeticBinaryError) as ctx:
                subprocess.run(
                    [
                        "/usr/bin/security",
                        "find-generic-password",
                        "-s",
                        "mailroom.not-a-real-item",
                        "-w",
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
        self.assertIn("unpatched /usr/bin/security", str(ctx.exception))

    def test_real_curl_opt_in_rejects_a_non_loopback_target(self):
        with self.assertRaises(hermetic_binaries.HermeticBinaryError) as ctx:
            hermetic_binaries.allow_real_curl("imap.example.invalid")
        self.assertIn("loopback", str(ctx.exception))

    def test_loopback_opt_in_still_rejects_a_non_loopback_url(self):
        with hermetic_binaries.allow_real_curl("127.0.0.1"):
            with self.assertRaises(hermetic_binaries.HermeticBinaryError) as ctx:
                subprocess.run(
                    [
                        "/usr/bin/curl",
                        "--connect-timeout",
                        "1",
                        "--max-time",
                        "2",
                        "https://imap.example.invalid/",
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
        self.assertIn("not a loopback target", str(ctx.exception))

    def test_discover_loads_the_guard(self):
        """Prove the guard is active under discover with PYTHONPATH=tests.

        The child is the same command, scoped to the canaries so this test
        does not re-enter itself. The canaries do not install the guard.
        """
        if os.environ.get("MAILROOM_HERMETIC_DISCOVER_CHILD") == "1":
            self.skipTest("discover proof does not re-enter")
        env = os.environ.copy()
        env["PYTHONPATH"] = "tests"
        env["MAILROOM_HERMETIC_DISCOVER_CHILD"] = "1"
        cmd = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_hermetic_binaries.py",
            "-k",
            "test_canary_unpatched",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("hermetic_guard=active", proc.stdout)
        self.assertIn("OK", proc.stderr)
        self.assertTrue(hermetic_binaries.installed())


if __name__ == "__main__":
    unittest.main()
