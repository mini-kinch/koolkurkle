#!/usr/bin/env python3
"""Phone normalization and classified Keychain errors. No live Keychain or osascript."""

from __future__ import annotations

import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
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
        self.assertEqual(argv[0], "/usr/bin/security")
        self.assertIs(argv[0], notify_bills.SECURITY_BIN)
        self.assertNotIn("osascript", argv)
        self.send.assert_not_called()


_CANARY = _RAW_10
_CANARY_DIGITS = "8005550100"


def _assert_value_hidden(testcase, blob):
    _assert_hides_number(testcase, _CANARY, blob)
    testcase.assertNotIn(_CANARY_DIGITS, blob)
    testcase.assertNotIn(_OK, blob)
    testcase.assertNotIn(_RAW_PLUS, blob)


class KeychainSecurityBinTests(_NoSendMixin):
    """Fake ``security`` via SECURITY_BIN. Never calls /usr/bin/security."""

    def _fake(self, directory, returncode, stdout, stderr):
        path = Path(directory) / "security"
        marker = Path(directory) / "argv.txt"
        script = (
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "pathlib.Path(%r).write_text('\\n'.join(sys.argv), encoding='utf-8')\n"
            "sys.stdout.write(%r)\n"
            "sys.stderr.write(%r)\n"
            "raise SystemExit(%d)\n"
        ) % (str(marker), stdout, stderr, int(returncode))
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path, marker

    def _run(self, returncode, stdout, stderr, decoy_on_path=False):
        """Run keychain_phone against a fake binary.

        ``MAILROOM_SECURITY_BIN`` points at a decoy that would return the
        canary. ``PATH`` defaults to the fake's directory so main's
        ``security`` lookup still executes this fake. New code uses the
        patched absolute ``SECURITY_BIN`` and ignores both. ``decoy_on_path``
        puts the decoy first to prove the absolute path wins.
        """
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake, marker = self._fake(tmp, returncode, stdout, stderr)
            decoy_dir = Path(tmp) / "decoy"
            decoy_dir.mkdir()
            decoy, _decoy_marker = self._fake(
                decoy_dir, 0, _CANARY + "\n", "decoy-should-not-run\n"
            )
            first = decoy_dir if decoy_on_path else Path(tmp)
            env = {
                "MAILROOM_SECURITY_BIN": str(decoy),
                "PATH": str(first) + os.pathsep + os.environ.get("PATH", ""),
            }
            with patch.dict(os.environ, env, clear=False):
                # New code: absolute SECURITY_BIN, PATH ignored.
                # main's keychain_phone has no SECURITY_BIN; PATH finds this fake
                # and the generic SystemExit drops the class.
                bin_patch = (
                    patch.object(notify_bills, "SECURITY_BIN", str(fake))
                    if hasattr(notify_bills, "SECURITY_BIN")
                    else nullcontext()
                )
                with bin_patch:
                    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                        try:
                            value = notify_bills.keychain_phone()
                        except SystemExit as exc:
                            caught = exc
                            value = None
                        else:
                            caught = None
            argv = marker.read_text(encoding="utf-8") if marker.exists() else ""
        captured = stdout_buf.getvalue() + stderr_buf.getvalue()
        return value, caught, argv, captured

    def _assert_quiet(self, caught, captured):
        blob = captured
        if caught is not None:
            blob += str(caught)
            blob += repr(caught)
        _assert_value_hidden(self, blob)

    def test_exit_0_returns_normalized_phone_and_does_not_log_it(self):
        value, caught, argv, captured = self._run(0, _CANARY + "\n", "")
        self.assertIsNone(caught)
        self.assertEqual(value, _OK)
        self.assertNotIn(_CANARY, captured)
        self.assertNotIn(_OK, captured)
        self.assertNotIn(_CANARY_DIGITS, captured)
        self.assertTrue(argv.splitlines()[0].endswith("/security"))
        self.assertNotEqual(os.path.basename(argv.splitlines()[0]), argv.splitlines()[0])
        self.assertIn("find-generic-password", argv)
        self.assertIn("mailroom.notify.phone", argv)
        self.assertNotIn(_CANARY, argv)
        self.assertNotIn(_CANARY_DIGITS, argv)

    def test_not_found_keeps_error_class_old_code_drops_it(self):
        """main's keychain_phone raises SystemExit with no kind.

        This fails on that function: the generic message drops the class.
        It passes once the failure is KeychainPhoneError kind item_not_found.
        """
        stderr = (
            "security: SecKeychainSearchCopyNext: "
            "The specified item could not be found in the keychain.\n"
        )
        value, caught, argv, captured = self._run(44, _CANARY + "\n", stderr)
        self.assertIsNone(value)
        self.assertIsInstance(caught, SystemExit)
        message = str(caught)
        self._assert_quiet(caught, captured)
        kind = getattr(caught, "kind", None)
        if kind != "item_not_found":
            self.fail(
                "error class lost (no item_not_found on SystemExit): %s" % message
            )
        self.assertNotIn("missing or unreadable", message)
        self.assertEqual(kind, "item_not_found")
        self.assertEqual(caught.returncode, 44)
        self.assertIn("security rc=44", message)
        self.assertIn("could not be found", message)
        self.assertTrue(Path(argv.splitlines()[0]).is_absolute())
        self.assertNotIn("decoy-should-not-run", message)

    def test_path_and_env_cannot_select_the_binary(self):
        stderr = "The specified item could not be found in the keychain.\n"
        value, caught, argv, captured = self._run(
            44, _CANARY + "\n", stderr, decoy_on_path=True
        )
        self.assertIsNone(value)
        self.assertEqual(caught.kind, "item_not_found")
        self.assertTrue(Path(argv.splitlines()[0]).is_absolute())
        self.assertNotIn("decoy-should-not-run", str(caught))
        self._assert_quiet(caught, captured)

    def test_exit_36_is_interaction_not_allowed(self):
        value, caught, _argv, captured = self._run(
            36,
            _OK + "\n",
            "security: SecKeychainItemCopyContent: User interaction is not allowed.\n",
        )
        self.assertIsNone(value)
        self.assertEqual(caught.kind, "interaction_not_allowed")
        self.assertEqual(caught.returncode, 36)
        self.assertIn("security rc=36", str(caught))
        self.assertIn("User interaction is not allowed", str(caught))
        self._assert_quiet(caught, captured)

    def test_exit_51_is_keychain_locked(self):
        value, caught, _argv, captured = self._run(
            51,
            _RAW_PLUS + "\n",
            "security: The user name or passphrase you entered is not correct.\n",
        )
        self.assertIsNone(value)
        self.assertEqual(caught.kind, "keychain_locked")
        self.assertEqual(caught.returncode, 51)
        self.assertIn("security rc=51", str(caught))
        self._assert_quiet(caught, captured)

    def test_errsec_interaction_text_is_classified_even_when_rc_is_other(self):
        stderr = "errSecInteractionNotAllowed (-25308)\n" + _CANARY + "\n"
        value, caught, _argv, captured = self._run(1, _CANARY_DIGITS + "\n", stderr)
        self.assertIsNone(value)
        self.assertEqual(caught.kind, "interaction_not_allowed")
        self.assertEqual(caught.returncode, 1)
        self.assertIn("-25308", str(caught))
        self.assertNotIn(_CANARY, str(caught))
        self._assert_quiet(caught, captured)

    def test_unknown_exit_is_other_and_redacts_stderr_value(self):
        value, caught, _argv, captured = self._run(
            99,
            _CANARY + "\n",
            "boom " + _CANARY + " tail\n",
        )
        self.assertIsNone(value)
        self.assertEqual(caught.kind, "other")
        self.assertEqual(caught.returncode, 99)
        self.assertIn("[redacted]", str(caught))
        self.assertNotIn("missing or unreadable", str(caught))
        self._assert_quiet(caught, captured)

    def test_missing_binary_is_other_without_path_lookup(self):
        missing = "/tmp/mailroom-no-such-security"
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch.dict(
            os.environ,
            {"MAILROOM_SECURITY_BIN": "/tmp/mailroom-env-security"},
            clear=False,
        ):
            with patch.object(notify_bills, "SECURITY_BIN", missing):
                with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                    with self.assertRaises(notify_bills.KeychainPhoneError) as caught:
                        notify_bills.keychain_phone()
        self.assertEqual(caught.exception.kind, "other")
        self.assertIsNone(caught.exception.returncode)
        self.assertIn("could not be executed", str(caught.exception))
        self.assertNotIn(_CANARY, stdout_buf.getvalue() + stderr_buf.getvalue())
        source = Path(notify_bills.__file__).read_text(encoding="utf-8")
        self.assertNotIn("MAILROOM_SECURITY_BIN", source)
        self.assertNotIn("os.environ", source)
        self.assertIn('SECURITY_BIN = "/usr/bin/security"', source)


if __name__ == "__main__":
    unittest.main()
