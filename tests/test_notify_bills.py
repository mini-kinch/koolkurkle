#!/usr/bin/env python3
"""Phone normalization for the bills digest. No network, Keychain, or osascript."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notify_bills  # noqa: E402

# Fictitious 555-01xx only (800-555-0100).
_OK = "+18005550100"
_RAW_10 = "800-555-0100"
_RAW_11 = "1-800-555-0100"
_RAW_PLUS = "+1 (800) 555-0100"
_RAW_9 = "+ (800) 555-010"
_RAW_12 = "800555010000"
_RAW_11_OTHER = "+2 (800) 555-0100"


def _expected_refuse(count):
    return (
        "Keychain phone is not a 10-digit US number (got %d digits). Re-store with: "
        "security add-generic-password -a mailroom -s mailroom.notify.phone -U -w"
        % count
    )


def _stripped_digits(raw):
    text = (raw or "").strip()
    if text.startswith("+"):
        text = text[1:]
    return re.sub(r"\D", "", text)


def _assert_hides_number(testcase, raw, message):
    digits = _stripped_digits(raw)
    testcase.assertNotIn(raw, message)
    testcase.assertNotIn(digits, message)
    if len(digits) >= 4:
        for start in range(0, len(digits) - 3):
            testcase.assertNotIn(digits[start : start + 4], message)


class _NoSendMixin(unittest.TestCase):
    def setUp(self):
        self.send_patcher = patch("notify_bills.send_imessage")
        self.send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    def tearDown(self):
        self.send.assert_not_called()


class NormalizeUsPhoneTests(_NoSendMixin):
    def setUp(self):
        super().setUp()
        self.run_patcher = patch(
            "notify_bills.subprocess.run",
            side_effect=AssertionError("subprocess.run"),
        )
        self.run = self.run_patcher.start()
        self.addCleanup(self.run_patcher.stop)

    def test_accepts_10_digit_11_digit_and_plus_one(self):
        samples = (
            "8005550100",
            _RAW_10,
            "(800) 555-0100",
            "18005550100",
            _RAW_11,
            _RAW_PLUS,
            "+1 800-555-0100",
        )
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertEqual(notify_bills.normalize_us_phone(raw), _OK)
        self.run.assert_not_called()

    def test_rejects_short_long_and_non_us_without_leaking_digits(self):
        samples = (
            (_RAW_9, 9),
            (_RAW_12, 12),
            (_RAW_11_OTHER, 11),
        )
        for raw, count in samples:
            with self.subTest(raw=raw):
                with self.assertRaises(SystemExit) as caught:
                    notify_bills.normalize_us_phone(raw)
                message = str(caught.exception)
                self.assertEqual(message, _expected_refuse(count))
                self.assertIn("got %d digits" % count, message)
                _assert_hides_number(self, raw, message)
        self.run.assert_not_called()


class KeychainPhoneTests(_NoSendMixin):
    def test_bad_keychain_value_raises_without_input_digits(self):
        raw = _RAW_9
        completed = subprocess.CompletedProcess(
            args=["security"],
            returncode=0,
            stdout=(raw + "\n").encode("utf-8"),
            stderr=b"",
        )
        with patch("notify_bills.subprocess.run", return_value=completed) as run:
            with self.assertRaises(SystemExit) as caught:
                notify_bills.keychain_phone()
        message = str(caught.exception)
        self.assertEqual(message, _expected_refuse(9))
        self.assertIn("got 9 digits", message)
        _assert_hides_number(self, raw, message)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "security")
        self.assertNotIn("osascript", argv)
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
