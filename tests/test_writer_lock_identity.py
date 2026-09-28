#!/usr/bin/env python3
"""Writer-lock identity handoff. Temp DBs only. No live SoR. No token prints."""

from __future__ import annotations

import ast
import ctypes
import errno
import inspect
import io
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sor_writer_gate as gate  # noqa: E402
import with_writer_lock as wwl  # noqa: E402

_CLEARED = (
    wwl.LOCK_TOKEN_ENV,
    wwl.LOCK_PID_ENV,
    wwl.LOCK_PURPOSE_ENV,
    gate.FORCE_LIVE_CHECKS_ENV,
)

_CHILD_ACCEPT = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["MAILROOM_SCRIPTS"])
import sor_writer_gate as gate
result = Path(os.environ["MAILROOM_RESULT"])
ready = Path(os.environ["MAILROOM_READY"])
release = Path(os.environ["MAILROOM_RELEASE"])
try:
    gate.refuse_if_sor_writer_conflict(
        os.environ["MAILROOM_TEST_DB"],
        cmdlines=(),
        lock_path=os.environ["MAILROOM_TEST_LOCK"],
    )
except gate.SorWriterRefuse:
    result.write_text("REFUSED")
    sys.stdout.write("REFUSED\\n")
    raise SystemExit(2)
result.write_text("SELF_OK")
ready.write_text("ready")
deadline = time.time() + 20
while not release.exists():
    if time.time() > deadline:
        raise SystemExit(3)
    time.sleep(0.05)
sys.stdout.write("SELF_OK\\n")
"""

_CHILD_SIBLING = """
import os, sys
sys.path.insert(0, os.environ["MAILROOM_SCRIPTS"])
import sor_writer_gate as gate
try:
    gate.refuse_if_sor_writer_conflict(
        os.environ["MAILROOM_TEST_DB"],
        cmdlines=(),
        lock_path=os.environ["MAILROOM_TEST_LOCK"],
    )
except gate.SorWriterRefuse as exc:
    text = str(exc)
    if "pid not ancestor" in text:
        sys.stdout.write("REFUSED pid not ancestor\\n")
    elif "token mismatch" in text:
        sys.stdout.write("REFUSED token mismatch\\n")
    else:
        sys.stdout.write("REFUSED\\n")
    raise SystemExit(2)
sys.stdout.write("SELF_OK\\n")
"""


def _assert_secret_absent(test, secret, *blobs):
    for blob in blobs:
        if secret and blob and secret in blob:
            test.fail("lock token appeared in stdout, stderr, or log output")


def _scrub(text, secret):
    if not text or not secret:
        return text or ""
    return text.replace(secret, "<redacted>")


def _replace_field(raw, key, value):
    prefix = key + "="
    lines = []
    found = False
    for line in raw.splitlines():
        if line.startswith(prefix):
            lines.append(prefix + value)
            found = True
        else:
            lines.append(line)
    if not found:
        lines.append(prefix + value)
    return "\n".join(lines) + "\n"


class _Capture(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


class IdentityCase(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for key in _CLEARED:
            self._saved[key] = os.environ.get(key)
            os.environ.pop(key, None)
        self._logs = _Capture()
        logging.getLogger().addHandler(self._logs)

    def tearDown(self):
        logging.getLogger().removeHandler(self._logs)
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _secret_blobs(self, *extra):
        return ("".join(self._logs.lines),) + tuple(extra)

    def _dead_pid(self):
        pid = 1 << 30
        while gate.pid_is_live(pid):
            pid += 1
            if pid > (1 << 30) + 20:
                self.fail("could not find a dead pid")
        return pid


class MatrixTests(IdentityCase):
    def test_no_identity_env_matches_probe_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            self.assertEqual(
                gate.writer_lock_held(lock, held=True),
                (True, "writer lock held (injected)"),
            )
            self.assertEqual(
                gate.writer_lock_held(lock, held=False),
                (False, "writer lock held (injected)"),
            )
            self.assertEqual(
                gate.writer_lock_held(lock),
                gate._probe_writer_lock(lock),
            )
            self.assertEqual(gate.writer_lock_held(lock), (False, "writer lock absent"))
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                self.assertEqual(
                    gate.writer_lock_held(lock),
                    gate._probe_writer_lock(lock),
                )
                probed = gate._probe_writer_lock(lock)
                self.assertTrue(probed[0])
                self.assertTrue(probed[1].startswith("writer lock held: "))
                self.assertIn("purpose=rem-legacy", probed[1])
            finally:
                wwl.release_writer_lock(held)
            self.assertEqual(
                gate.writer_lock_held(lock),
                (False, "writer lock free"),
            )

    def test_unwrapped_caller_behaves_as_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            gate.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            token = wwl.read_lock_info(lock).writer_token
            try:
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    try:
                        gate.refuse_if_sor_writer_conflict(
                            db, cmdlines=(), lock_path=lock
                        )
                    except gate.SorWriterRefuse as exc:
                        message = str(exc)
                    else:
                        self.fail("unwrapped held lock was allowed")
                self.assertIn("writer lock held:", message)
                self.assertNotIn("identity", message)
                self.assertIn(gate.CONFLICT_TOKEN, message)
                _assert_secret_absent(
                    self, token, message, out.getvalue(), err.getvalue(), *self._secret_blobs()
                )
            finally:
                wwl.release_writer_lock(held)

    def test_no_lock_at_all_behaves_as_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "mailroom.sqlite"
            gate.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)
            self.assertEqual(gate.writer_lock_held(lock), (False, "writer lock absent"))

    def test_forged_mismatched_and_missing_token_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "rehearsal.sqlite"
            db.write_text("")
            held = wwl.acquire_writer_lock(lock, "att0-migrate")
            token = wwl.read_lock_info(lock).writer_token
            if not token:
                self.fail("lock file has no token")
            forged = secrets.token_urlsafe(16)
            if forged == token:
                forged = forged + "x"
            try:
                os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                os.environ[wwl.LOCK_TOKEN_ENV] = forged
                os.environ[wwl.LOCK_PID_ENV] = str(os.getpid())
                os.environ[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
                message = self._expect_refuse(db, lock)
                self.assertIn("token mismatch", message)
                _assert_secret_absent(self, token, message, *self._secret_blobs())
                _assert_secret_absent(self, forged, message, *self._secret_blobs())

                os.environ.pop(wwl.LOCK_TOKEN_ENV, None)
                message = self._expect_refuse(db, lock)
                self.assertIn("writer lock held:", message)
                self.assertNotIn("identity", message)
                _assert_secret_absent(self, token, message, *self._secret_blobs())

                os.environ[wwl.LOCK_TOKEN_ENV] = ""
                message = self._expect_refuse(db, lock)
                self.assertIn("writer lock held:", message)
                self.assertNotIn("identity", message)
            finally:
                wwl.release_writer_lock(held)

    def test_legacy_token_key_refuses_without_dual_read(self):
        known = "legacy-token-key-secret-4c2e"
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "rehearsal.sqlite"
            db.write_text("")
            held = wwl.acquire_writer_lock(lock, "att0-migrate")
            try:
                legacy = (
                    "pid=%d\n"
                    "hostname=test-host\n"
                    "purpose=att0-migrate\n"
                    "acquired_at=%s\n"
                    "token=%s\n"
                    % (os.getpid(), wwl.utcnow().isoformat(), known)
                )
                keys = [
                    line.split("=", 1)[0]
                    for line in legacy.splitlines()
                    if "=" in line
                ]
                if "token" not in keys or "writer_token" in keys:
                    self.fail("legacy fixture is not an old token= lock")
                lock.write_text(legacy, encoding="utf-8")
                info = wwl.read_lock_info(lock)
                if info.writer_token:
                    self.fail("old token= key was read as writer_token")
                os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                os.environ[wwl.LOCK_TOKEN_ENV] = known
                os.environ[wwl.LOCK_PID_ENV] = str(os.getpid())
                os.environ[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
                message = self._expect_refuse(db, lock)
                self.assertIn("token mismatch", message)
                _assert_secret_absent(self, known, message, *self._secret_blobs())
            finally:
                wwl.release_writer_lock(held)

    def test_env_present_lock_free_refuses_and_probe_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "rehearsal.sqlite"
            token = secrets.token_urlsafe(16)
            lock.write_text(
                wwl.format_lock_payload(
                    "att0-migrate",
                    wwl.utcnow(),
                    os.getpid(),
                    "test-host",
                    token=token,
                ),
                encoding="utf-8",
            )
            os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
            os.environ[wwl.LOCK_TOKEN_ENV] = token
            os.environ[wwl.LOCK_PID_ENV] = str(os.getpid())
            os.environ[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
            message = self._expect_refuse(db, lock)
            self.assertIn("lock not held", message)
            _assert_secret_absent(self, token, message, *self._secret_blobs())
            again = wwl.acquire_writer_lock(lock, "att0-migrate")
            wwl.release_writer_lock(again)

    def test_stale_dead_pid_refuses_while_flock_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "rehearsal.sqlite"
            held = wwl.acquire_writer_lock(lock, "att0-migrate")
            token = wwl.read_lock_info(lock).writer_token
            dead = self._dead_pid()
            try:
                lock.write_text(
                    wwl.format_lock_payload(
                        "att0-migrate",
                        wwl.utcnow(),
                        dead,
                        "test-host",
                        token=token,
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(wwl.WriterLockError):
                    wwl.acquire_writer_lock(lock, "second")
                os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                os.environ[wwl.LOCK_TOKEN_ENV] = token
                os.environ[wwl.LOCK_PID_ENV] = str(dead)
                os.environ[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
                message = self._expect_refuse(db, lock)
                self.assertIn("pid not live", message)
                _assert_secret_absent(self, token, message, *self._secret_blobs())
            finally:
                wwl.release_writer_lock(held)

    def test_identity_allows_when_recorded_pid_is_self(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "rehearsal.sqlite"
            held = wwl.acquire_writer_lock(lock, "att0 meta fill")
            token = wwl.read_lock_info(lock).writer_token
            try:
                os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                os.environ[wwl.LOCK_TOKEN_ENV] = token
                os.environ[wwl.LOCK_PID_ENV] = str(os.getpid())
                os.environ[wwl.LOCK_PURPOSE_ENV] = "att0 meta fill"
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    gate.refuse_if_sor_writer_conflict(
                        db, cmdlines=(), lock_path=lock
                    )
                _assert_secret_absent(
                    self, token, out.getvalue(), err.getvalue(), *self._secret_blobs()
                )
                self.assertIn("att0 meta fill", gate.WRITER_PURPOSE_ALLOWLIST)
            finally:
                wwl.release_writer_lock(held)

    def test_purpose_not_allowlisted_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            db = Path(tmp) / "rehearsal.sqlite"
            held = wwl.acquire_writer_lock(lock, "not-a-writer")
            token = wwl.read_lock_info(lock).writer_token
            try:
                os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                os.environ[wwl.LOCK_TOKEN_ENV] = token
                message = self._expect_refuse(db, lock)
                self.assertIn("purpose not allowed", message)
                _assert_secret_absent(self, token, message, *self._secret_blobs())
            finally:
                wwl.release_writer_lock(held)

    def test_summary_and_busy_error_omit_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "att0-migrate")
            info = wwl.read_lock_info(lock)
            token = info.writer_token
            try:
                if "writer_token=" not in info.raw:
                    self.fail("lock file is missing the writer_token field")
                _assert_secret_absent(
                    self, token, info.summary(), str(info), repr(info)
                )
                try:
                    wwl.acquire_writer_lock(lock, "second")
                except wwl.WriterLockError as exc:
                    message = str(exc)
                else:
                    self.fail("second holder acquired the lock")
                self.assertIn("purpose=att0-migrate", message)
                _assert_secret_absent(self, token, message, *self._secret_blobs())
            finally:
                wwl.release_writer_lock(held)

    def test_lock_describers_never_include_writer_token(self):
        known = "Wt9kQ-known-lock-secret-7f3a"
        seen = []

        def check(label, *blobs):
            for blob in blobs:
                if blob and known in blob:
                    self.fail("writer token appeared in %s" % label)
            seen.append(label)

        def expect_reason(label, detail, phrase):
            check(label, detail)
            if phrase not in detail:
                self.fail("%s missing %s (%s)" % (label, phrase, _scrub(detail, known)))

        def cli_stderr(purpose):
            env = os.environ.copy()
            for key in (wwl.LOCK_TOKEN_ENV, wwl.LOCK_PID_ENV, wwl.LOCK_PURPOSE_ENV):
                env.pop(key, None)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    purpose,
                    "--lock-file",
                    str(lock),
                    "--action-required-file",
                    str(action),
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            return proc.returncode, proc.stdout, proc.stderr

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            db = root / "rehearsal.sqlite"
            db.write_text("")
            action = root / "ACTION_REQUIRED"
            held = wwl.acquire_writer_lock(lock, "att0-migrate")
            sleeper = None
            try:
                payload = wwl.format_lock_payload(
                    "att0-migrate",
                    wwl.utcnow(),
                    os.getpid(),
                    "test-host",
                    token=known,
                )
                keys = [
                    line.split("=", 1)[0]
                    for line in payload.splitlines()
                    if "=" in line
                ]
                if "writer_token" not in keys or "token" in keys:
                    self.fail("lock file field is not writer_token")
                if ("writer_token=" + known) not in payload:
                    self.fail("lock file is missing the writer_token field")
                lock.write_text(payload, encoding="utf-8")
                info = wwl.read_lock_info(lock)
                if info.writer_token != known:
                    self.fail("parsed writer_token did not match the lock file")
                if "purpose=att0-migrate" not in info.summary():
                    self.fail("summary omitted the holder purpose")
                check("summary", info.summary(), str(info), repr(info))

                try:
                    wwl.acquire_writer_lock(lock, "second")
                except wwl.WriterLockError as exc:
                    expect_reason("busy lock", str(exc), "writer lock held")
                else:
                    self.fail("second holder acquired the lock")
                rc, out, err = cli_stderr("second")
                check("wrapper cli subprocess busy", out, err)
                if rc != 2 or "writer lock held" not in err or "no steal" in err:
                    self.fail("busy wrapper cli did not refuse")

                lock.write_text(
                    _replace_field(payload, "acquired_at", "2020-01-01T00:00:00+00:00"),
                    encoding="utf-8",
                )
                try:
                    wwl.acquire_writer_lock(lock, "second")
                except wwl.WriterLockError as exc:
                    expect_reason("stale lock", str(exc), "no steal")
                else:
                    self.fail("stale holder was stolen")
                rc, out, err = cli_stderr("second")
                check("wrapper cli subprocess stale", out, err)
                if rc != 2 or "no steal" not in err:
                    self.fail("stale wrapper cli did not refuse")

                lock.write_text(payload, encoding="utf-8")
                probed_held, probed_detail = gate._probe_writer_lock(lock)
                if not probed_held:
                    self.fail("probe did not see the held lock")
                expect_reason("probe", probed_detail, "purpose=att0-migrate")
                _held, detail = gate.writer_lock_held(lock)
                expect_reason("writer_lock_held probe", detail, "purpose=att0-migrate")
                _held, detail = gate.writer_lock_held(lock, held=True)
                expect_reason("injected held", detail, "writer lock held (injected)")
                _held, detail = gate.writer_lock_held(lock, held=False)
                expect_reason("injected free", detail, "writer lock held (injected)")

                os.environ[wwl.LOCK_TOKEN_ENV] = known + "-other"
                os.environ[wwl.LOCK_PID_ENV] = str(os.getpid())
                os.environ[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
                _held, detail = gate.writer_lock_held(lock)
                expect_reason("token mismatch", detail, "token mismatch")
                conflict = gate.conflict_message(detail)
                check("gate conflict detail", detail, conflict)
                if "token mismatch" not in conflict:
                    self.fail("gate conflict detail omitted the refusal reason")
                os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                expect_reason(
                    "token mismatch refuse",
                    self._expect_refuse(db, lock),
                    "token mismatch",
                )

                os.environ[wwl.LOCK_TOKEN_ENV] = known
                lock.write_text(
                    _replace_field(payload, "pid", str(self._dead_pid())),
                    encoding="utf-8",
                )
                _held, detail = gate.writer_lock_held(lock)
                expect_reason("pid not live", detail, "pid not live")

                lock.write_text(
                    _replace_field(payload, "purpose", "not-a-writer"),
                    encoding="utf-8",
                )
                _held, detail = gate.writer_lock_held(lock)
                expect_reason("purpose not allowed", detail, "purpose not allowed")

                sleeper = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                lock.write_text(
                    _replace_field(payload, "pid", str(sleeper.pid)),
                    encoding="utf-8",
                )
                _held, detail = gate.writer_lock_held(lock)
                expect_reason("pid not ancestor", detail, "pid not ancestor")
                with patch(
                    "sor_writer_gate.ancestor_pids",
                    side_effect=gate.AncestorWalkError("unreadable"),
                ):
                    _held, detail = gate.writer_lock_held(lock)
                expect_reason("ancestor walk error", detail, "ancestor walk error")

                lock.write_text(payload, encoding="utf-8")
                _held, detail = gate.writer_lock_held(lock)
                expect_reason("identity match", detail, "writer lock held by wrapper")
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    try:
                        gate.refuse_if_sor_writer_conflict(
                            db, cmdlines=(), lock_path=lock
                        )
                    except gate.SorWriterRefuse as exc:
                        text = str(exc)
                        check("identity allow", text)
                        self.fail("matching identity was refused")
                check("identity allow", out.getvalue(), err.getvalue())

                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = gate.main(["--db", str(db), "--lock-file", str(lock)])
                check("gate cli allow", out.getvalue(), err.getvalue())
                if rc != 0:
                    self.fail("gate cli refused a matching writer")

                os.environ[wwl.LOCK_TOKEN_ENV] = known + "-other"
                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = gate.main(["--db", str(db), "--lock-file", str(lock)])
                check("gate cli refuse", out.getvalue(), err.getvalue())
                if rc != gate.CONFLICT_EXIT:
                    self.fail("gate cli allowed a mismatched token")

                _held, detail = gate.writer_lock_held(lock, held=False)
                expect_reason("lock not held", detail, "lock not held")

                out = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = wwl.main(
                        [
                            "--purpose",
                            "second",
                            "--lock-file",
                            str(lock),
                            "--action-required-file",
                            str(action),
                            "--",
                            sys.executable,
                            "-c",
                            "pass",
                        ]
                    )
                check("wrapper cli", out.getvalue(), err.getvalue())
                if rc != 2:
                    self.fail("busy wrapper cli did not refuse")
                check("logs", *self._secret_blobs())
            finally:
                if sleeper is not None:
                    try:
                        sleeper.kill()
                    except OSError:
                        pass
                    sleeper.wait()
                wwl.release_writer_lock(held)
        if len(seen) < 12:
            self.fail("describer coverage was incomplete")

    def _expect_refuse(self, db, lock):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                gate.refuse_if_sor_writer_conflict(db, cmdlines=(), lock_path=lock)
            except gate.SorWriterRefuse as exc:
                message = str(exc)
            else:
                self.fail("gate allowed a write that should refuse")
        _assert_secret_absent(
            self, os.environ.get(wwl.LOCK_TOKEN_ENV), out.getvalue(), err.getvalue()
        )
        return message


class WrapperProcessTests(IdentityCase):
    def _paths(self, tmp):
        root = Path(tmp)
        db = root / "rehearsal.sqlite"
        db.write_text("")
        return {
            "lock": root / "mailroom.write.lock",
            "action": root / "ACTION_REQUIRED",
            "db": db,
            "ready": root / "ready",
            "release": root / "release",
            "result": root / "result",
        }

    def _wrapper_env(self, paths):
        env = os.environ.copy()
        for key in (wwl.LOCK_TOKEN_ENV, wwl.LOCK_PID_ENV, wwl.LOCK_PURPOSE_ENV):
            env.pop(key, None)
        env["PYTHONPATH"] = str(SCRIPTS)
        env["MAILROOM_SCRIPTS"] = str(SCRIPTS)
        env["MAILROOM_TEST_DB"] = str(paths["db"])
        env["MAILROOM_TEST_LOCK"] = str(paths["lock"])
        env["MAILROOM_READY"] = str(paths["ready"])
        env["MAILROOM_RELEASE"] = str(paths["release"])
        env["MAILROOM_RESULT"] = str(paths["result"])
        env[gate.FORCE_LIVE_CHECKS_ENV] = "1"
        return env

    def _wrapper_cmd(self, paths):
        return [
            sys.executable,
            str(SCRIPTS / "with_writer_lock.py"),
            "--purpose",
            "att0-migrate",
            "--lock-file",
            str(paths["lock"]),
            "--action-required-file",
            str(paths["action"]),
            "--",
            sys.executable,
            "-c",
            _CHILD_ACCEPT,
        ]

    def test_wrapper_child_accepts_own_lock_and_token_is_unprinted(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            proc = subprocess.Popen(
                self._wrapper_cmd(paths),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._wrapper_env(paths),
            )
            token = ""
            try:
                deadline = time.time() + 15
                while not paths["ready"].exists():
                    if proc.poll() is not None:
                        out, err = proc.communicate()
                        token = wwl.read_lock_info(paths["lock"]).writer_token
                        _assert_secret_absent(self, token, out, err, paths["result"].read_text() if paths["result"].exists() else "")
                        self.fail("wrapper child exited before the gate allowed it")
                    if time.time() > deadline:
                        self.fail("wrapper child did not become ready")
                    time.sleep(0.05)
                token = wwl.read_lock_info(paths["lock"]).writer_token
                if not token:
                    self.fail("wrapper lock file has no token")
                self.assertIsNone(os.environ.get(wwl.LOCK_TOKEN_ENV))
                paths["release"].write_text("go")
                out, err = proc.communicate(timeout=15)
            finally:
                if proc.poll() is None:
                    paths["release"].write_text("go")
                    try:
                        leftover_out, leftover_err = proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        leftover_out, leftover_err = proc.communicate()
                    token = token or wwl.read_lock_info(paths["lock"]).writer_token
                    _assert_secret_absent(self, token, leftover_out, leftover_err)
            result = paths["result"].read_text() if paths["result"].exists() else ""
            _assert_secret_absent(self, token, out, err, result, *self._secret_blobs())
            self.assertEqual(proc.returncode, 0, _scrub(out + err, token))
            self.assertEqual(result, "SELF_OK")
            self.assertIn("SELF_OK", out)
            self.assertNotIn(wwl.LOCK_TOKEN_ENV, os.environ)

    def test_foreign_sibling_wrapper_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            proc = subprocess.Popen(
                self._wrapper_cmd(paths),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._wrapper_env(paths),
            )
            token = ""
            sibling_out = ""
            sibling_err = ""
            try:
                deadline = time.time() + 15
                while not paths["ready"].exists():
                    if proc.poll() is not None:
                        self.fail("holder exited before the sibling could run")
                    if time.time() > deadline:
                        self.fail("holder did not become ready")
                    time.sleep(0.05)
                token = wwl.read_lock_info(paths["lock"]).writer_token
                if not token:
                    self.fail("holder lock file has no token")
                sibling_env = self._wrapper_env(paths)
                sibling_env[wwl.LOCK_TOKEN_ENV] = token
                sibling_env[wwl.LOCK_PID_ENV] = str(wwl.read_lock_info(paths["lock"]).pid)
                sibling_env[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
                sibling = subprocess.run(
                    [sys.executable, "-c", _CHILD_SIBLING],
                    capture_output=True,
                    text=True,
                    env=sibling_env,
                    check=False,
                )
                sibling_out = sibling.stdout
                sibling_err = sibling.stderr
                _assert_secret_absent(self, token, sibling_out, sibling_err)
                self.assertEqual(sibling.returncode, 2, _scrub(sibling_out + sibling_err, token))
                self.assertIn("REFUSED", sibling_out)
                self.assertIn("pid not ancestor", sibling_out)
            finally:
                paths["release"].write_text("go")
                try:
                    out, err = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, err = proc.communicate()
            _assert_secret_absent(
                self, token, out, err, sibling_out, sibling_err, *self._secret_blobs()
            )
            self.assertEqual(proc.returncode, 0, _scrub((out or "") + (err or ""), token))


class RemExclusionTests(IdentityCase):
    def test_rem_process_hits_ignores_self_and_ancestors(self):
        me = 400
        ancestors = [50, 40, 1]
        cmdlines = (
            (me, "python3 rem-legacy --db /tmp/mailroom.sqlite"),
            (50, "embed_backfill.py --reembed-legacy"),
            (40, "embed-rem"),
            (1, "python3 embed_rem --db /tmp/mailroom.sqlite"),
            (77, "python3 rem-legacy --db /tmp/mailroom.sqlite"),
        )
        hits = gate.rem_process_hits(
            self_pid=me, cmdlines=cmdlines, ancestors=ancestors
        )
        self.assertEqual([pid for pid, _line in hits], [77])

    def test_rem_process_hits_env_pid_is_a_hit_unless_ancestor(self):
        os.environ[wwl.LOCK_PID_ENV] = "77"
        self.assertIsNone(os.environ.get(wwl.LOCK_TOKEN_ENV))
        hits = gate.rem_process_hits(
            self_pid=400,
            cmdlines=(
                (77, "python3 rem-legacy --db /tmp/mailroom.sqlite"),
                (88, "python3 embed-rem"),
            ),
            ancestors=[],
        )
        self.assertEqual([pid for pid, _line in hits], [77, 88])
        hits = gate.rem_process_hits(
            self_pid=400,
            cmdlines=(
                (77, "python3 rem-legacy --db /tmp/mailroom.sqlite"),
                (88, "python3 embed-rem"),
            ),
            ancestors=[77, 1],
        )
        self.assertEqual([pid for pid, _line in hits], [88])

    def test_rem_process_hits_catches_foreign_writer_on_real_walk(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("linux /proc walk")
        chain = gate.ancestor_pids(os.getpid())
        foreign = 2100000003
        self.assertNotIn(foreign, chain)
        cmdlines = [(os.getpid(), "python3 rem-legacy --db /tmp/mailroom.sqlite")]
        parent = os.getppid()
        if parent > 0:
            cmdlines.append((parent, "embed_backfill.py --reembed-legacy"))
        for pid in chain:
            cmdlines.append((pid, "embed-rem"))
        cmdlines.append((foreign, "python3 rem-legacy --db /tmp/mailroom.sqlite"))
        hits = gate.rem_process_hits(cmdlines=cmdlines)
        self.assertEqual([pid for pid, _line in hits], [foreign])

    def test_ancestor_walk_error_excludes_only_self(self):
        db = Path("/tmp/mailroom.sqlite")

        def boom(_pid):
            raise gate.AncestorWalkError("unreadable")

        cmdlines = (
            (400, "python3 rem-legacy --db /tmp/mailroom.sqlite"),
            (77, "python3 embed-rem"),
        )
        with patch("sor_writer_gate.ancestor_pids", side_effect=boom):
            os.environ[wwl.LOCK_PID_ENV] = "77"
            hits = gate.rem_process_hits(self_pid=400, cmdlines=cmdlines)
            self.assertEqual([pid for pid, _line in hits], [77])
            with self.assertRaises(gate.SorWriterRefuse) as ctx:
                gate.refuse_if_sor_writer_conflict(
                    db,
                    self_pid=400,
                    cmdlines=cmdlines,
                    lock_held=False,
                )
        message = str(ctx.exception)
        self.assertIn("rem process pid=77", message)
        self.assertNotIn("ancestor walk failed", message)
        self.assertIn(gate.CONFLICT_TOKEN, message)


class LivePathTests(IdentityCase):
    def test_realpath_symlink_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "mailroom.sqlite"
            live.write_text("")
            alias = root / "rehearsal-alias.sqlite"
            alias.symlink_to(live)
            self.assertNotEqual(os.path.realpath(alias), os.path.realpath(root / "missing"))
            self.assertEqual(os.path.realpath(alias), os.path.realpath(live))
            self.assertNotEqual(str(alias), str(live))
            self.assertTrue(gate.is_live_sor(alias))
            self.assertTrue(gate.is_live_sor(live))
            copy = root / "mailroom-copy.sqlite"
            copy.write_text("")
            linked = root / "nested"
            linked.mkdir()
            raw_live = linked / "mailroom.sqlite"
            raw_live.symlink_to(copy)
            self.assertEqual(raw_live.name, gate.SOR_BASENAME)
            self.assertEqual(os.path.realpath(raw_live), os.path.realpath(copy))
            self.assertFalse(gate.is_live_sor(raw_live))
            self.assertFalse(gate.is_live_sor(copy))

            lock = root / "mailroom.write.lock"
            held = wwl.acquire_writer_lock(lock, "rem-legacy")
            try:
                with self.assertRaises(gate.SorWriterRefuse):
                    gate.refuse_if_sor_writer_conflict(
                        alias, cmdlines=(), lock_path=lock
                    )
                gate.refuse_if_sor_writer_conflict(
                    raw_live, cmdlines=(), lock_path=lock
                )
            finally:
                wwl.release_writer_lock(held)

    def test_force_flag_turns_checks_on_and_cannot_turn_them_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "mailroom.sqlite"
            copy = root / "rehearsal.sqlite"
            lock = root / "mailroom.write.lock"
            rem = ((77, "python3 rem-legacy --db /tmp/mailroom.sqlite"),)
            gate.refuse_if_sor_writer_conflict(
                copy, cmdlines=rem, lock_held=True
            )
            os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "0"
            gate.refuse_if_sor_writer_conflict(
                copy, cmdlines=rem, lock_held=True
            )
            os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "false"
            gate.refuse_if_sor_writer_conflict(
                copy, cmdlines=rem, lock_held=True
            )
            os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
            with self.assertRaises(gate.SorWriterRefuse):
                gate.refuse_if_sor_writer_conflict(
                    copy, cmdlines=rem, lock_held=False
                )
            os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "0"
            with self.assertRaises(gate.SorWriterRefuse):
                gate.refuse_if_sor_writer_conflict(
                    live, cmdlines=rem, lock_held=False
                )
            os.environ["SOR_DISABLE_LIVE_CHECKS"] = "1"
            try:
                with self.assertRaises(gate.SorWriterRefuse):
                    gate.refuse_if_sor_writer_conflict(
                        live, cmdlines=rem, lock_held=False
                    )
            finally:
                os.environ.pop("SOR_DISABLE_LIVE_CHECKS", None)
            self.assertTrue(gate.live_checks_apply(live))
            os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
            self.assertTrue(gate.live_checks_apply(live))
            os.environ.pop(gate.FORCE_LIVE_CHECKS_ENV, None)
            self.assertFalse(gate.live_checks_apply(copy))


class AncestorWalkTests(IdentityCase):
    def test_linux_ancestor_walk_reaches_parent(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("linux /proc walk")
        chain = gate.ancestor_pids(os.getpid())
        parent = os.getppid()
        self.assertLessEqual(len(chain), gate.MAX_ANCESTOR_DEPTH)
        if parent > 0:
            self.assertEqual(chain[0], parent)
        self.assertEqual(gate.ppid_of(os.getpid()), gate.linux_ppid(os.getpid()))
        missing = gate.AncestorWalkError
        with self.assertRaises(missing) as ctx:
            gate.linux_ppid(self._dead_pid())
        self.assertEqual(ctx.exception.code, "no-such-process")

    def test_darwin_ancestor_walk_mocked_syscall(self):
        self.assertEqual(ctypes.sizeof(gate.ProcBsdInfo), 136)
        self.assertEqual(gate.ProcBsdInfo.pbi_ppid.offset, 16)
        parents = {}
        for pid in range(1, 40):
            parents[pid] = pid - 1

        def fake(pid, flavor, arg, info, size):
            self.assertEqual(flavor, gate.PROC_PIDTBSDINFO)
            self.assertEqual(arg, 0)
            self.assertEqual(size, 136)
            if pid not in parents:
                return 0
            info.pbi_pid = pid
            info.pbi_ppid = parents[pid]
            return size

        def ppid(pid):
            return gate.darwin_ppid(pid, proc_pidinfo=fake)

        chain = gate.ancestor_pids(33, ppid_fn=ppid)
        self.assertEqual(len(chain), 32)
        self.assertEqual(chain[0], 32)
        self.assertEqual(chain[-1], 1)

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.ancestor_pids(34, ppid_fn=ppid)
        self.assertEqual(ctx.exception.code, "max-depth")

        def cycle_fake(pid, flavor, arg, info, size):
            info.pbi_pid = pid
            info.pbi_ppid = 11 if pid == 10 else 10
            return size

        def cycle_ppid(pid):
            return gate.darwin_ppid(pid, proc_pidinfo=cycle_fake)

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.ancestor_pids(10, ppid_fn=cycle_ppid)
        self.assertEqual(ctx.exception.code, "cycle")

        def short(_pid, _flavor, _arg, _info, _size):
            return 0

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.darwin_ppid(7, proc_pidinfo=short)
        self.assertEqual(ctx.exception.code, "syscall")

        def mismatch(pid, _flavor, _arg, info, size):
            info.pbi_pid = pid + 1
            info.pbi_ppid = 1
            return size

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.darwin_ppid(7, proc_pidinfo=mismatch)
        self.assertEqual(ctx.exception.code, "pid-mismatch")

        def broken(_pid, _flavor, _arg, _info, _size):
            raise OSError("syscall failed")

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.darwin_ppid(7, proc_pidinfo=broken)
        self.assertEqual(ctx.exception.code, "syscall")

        def gone_later(pid):
            if pid == 10:
                return 9
            raise gate.AncestorWalkError("no-such-process")

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.ancestor_pids(10, ppid_fn=gone_later)
        self.assertEqual(ctx.exception.code, "walk-error")

        def gone_start(_pid):
            raise gate.AncestorWalkError("no-such-process")

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.ancestor_pids(10, ppid_fn=gone_start)
        self.assertEqual(ctx.exception.code, "no-such-process")

        def negative(_pid):
            return -1

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.ancestor_pids(10, ppid_fn=negative)
        self.assertEqual(ctx.exception.code, "negative-ppid")


class DarwinEpermAncestorTests(IdentityCase):
    """PROC_PIDTBSDINFO EPERM above the holder. The syscall is injected.

    Same-uid hops return a real parent. The different-uid hop returns 0
    with errno EPERM, which darwin_ppid turns into syscall. The walk must
    query that pid. A stub that never fails is not this test.
    """

    def _tree(self, holder, child):
        shell = 2_100_000_002
        foreign = 2_100_000_003
        if len({child, holder, shell, foreign}) != 4:
            self.fail("pid collision in the simulated tree")
        parents = {child: holder, holder: shell, shell: foreign}
        queried = []

        def fake(pid, flavor, arg, info, size):
            queried.append(pid)
            self.assertEqual(flavor, gate.PROC_PIDTBSDINFO)
            self.assertEqual(arg, 0)
            self.assertEqual(size, ctypes.sizeof(gate.ProcBsdInfo))
            if pid == foreign:
                ctypes.set_errno(errno.EPERM)
                return 0
            if pid not in parents:
                ctypes.set_errno(errno.ESRCH)
                return 0
            info.pbi_pid = pid
            info.pbi_ppid = parents[pid]
            return size

        def ppid(pid):
            return gate.darwin_ppid(pid, proc_pidinfo=fake)

        return child, foreign, queried, ppid

    def test_eperm_above_holder_matches_and_foreign_callers_refuse(self):
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        foreign_holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            holder = sleeper.pid
            child, foreign_pid, queried, ppid = self._tree(holder, os.getpid())
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lock = root / "mailroom.write.lock"
                db = root / "mailroom.sqlite"
                db.write_bytes(b"")
                held = wwl.acquire_writer_lock(lock, "att0-migrate")
                token = held.info.writer_token
                try:
                    payload = lock.read_text(encoding="utf-8")
                    lock.write_text(
                        _replace_field(payload, "pid", str(holder)),
                        encoding="utf-8",
                    )
                    os.environ[wwl.LOCK_TOKEN_ENV] = token
                    os.environ[wwl.LOCK_PURPOSE_ENV] = "att0-migrate"
                    os.environ[gate.FORCE_LIVE_CHECKS_ENV] = "1"
                    with patch("sor_writer_gate.ppid_of", ppid):
                        matched, why = gate._identity_decision(lock, child_pid=child)
                        self.assertTrue(matched, why)
                        self.assertEqual(why, "match")
                        self.assertIn(foreign_pid, queried)
                        self.assertIn(holder, queried)
                        gate.refuse_if_sor_writer_conflict(
                            db,
                            cmdlines=(),
                            lock_path=lock,
                            lock_held=True,
                        )
                        before = len(queried)
                        forged = token + "x"
                        os.environ[wwl.LOCK_TOKEN_ENV] = forged
                        matched, why = gate._identity_decision(lock, child_pid=child)
                        self.assertFalse(matched)
                        self.assertEqual(why, "token mismatch")
                        self.assertEqual(len(queried), before)
                        os.environ[wwl.LOCK_TOKEN_ENV] = token
                        lock.write_text(
                            _replace_field(
                                lock.read_text(encoding="utf-8"),
                                "pid",
                                str(foreign_holder.pid),
                            ),
                            encoding="utf-8",
                        )
                        matched, why = gate._identity_decision(lock, child_pid=child)
                        self.assertFalse(matched)
                        self.assertEqual(why, "pid not ancestor")
                        self.assertIn(foreign_pid, queried)
                        os.environ.pop(wwl.LOCK_TOKEN_ENV, None)
                        calls_before = len(queried)
                        refused, detail = gate.writer_lock_held(lock, held=True)
                        self.assertTrue(refused)
                        self.assertNotIn("ancestor walk error", detail)
                        self.assertIn("writer lock held", detail)
                        self.assertEqual(len(queried), calls_before)
                        with self.assertRaises(gate.SorWriterRefuse) as ctx:
                            gate.refuse_if_sor_writer_conflict(
                                db,
                                cmdlines=(),
                                lock_path=lock,
                                lock_held=True,
                            )
                        message = str(ctx.exception)
                        self.assertIn("writer lock held", message)
                        self.assertNotIn("ancestor walk error", message)
                        self.assertNotIn(token, message)
                        self.assertNotIn(forged, message)
                finally:
                    wwl.release_writer_lock(held)
        finally:
            for proc in (sleeper, foreign_holder):
                if proc.poll() is None:
                    proc.kill()
                proc.wait()

    def test_eperm_on_the_first_hop_still_refuses(self):
        def fake(pid, flavor, arg, info, size):
            self.assertEqual(flavor, gate.PROC_PIDTBSDINFO)
            ctypes.set_errno(errno.EPERM)
            return 0

        def ppid(pid):
            return gate.darwin_ppid(pid, proc_pidinfo=fake)

        with self.assertRaises(gate.AncestorWalkError) as ctx:
            gate.ancestor_pids(2_100_000_010, ppid_fn=ppid)
        self.assertEqual(ctx.exception.code, "syscall")


class InventoryTests(IdentityCase):
    def test_refuse_if_live_branch_is_two_checks(self):
        source = inspect.getsource(gate.refuse_if_sor_writer_conflict)
        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                calls.append(func.id)
            elif isinstance(func, ast.Attribute):
                calls.append(func.attr)
        self.assertEqual(
            set(calls),
            {
                "Path",
                "expanduser",
                "live_checks_apply",
                "rem_process_hits",
                "conflict_message",
                "SorWriterRefuse",
                "writer_lock_held",
            },
        )
        self.assertEqual(calls.count("rem_process_hits"), 1)
        self.assertEqual(calls.count("writer_lock_held"), 1)
        self.assertNotIn("is_live_sor", calls)

    def test_purpose_allowlist_matches_repo_strings(self):
        meta = (SCRIPTS / "attachments" / "meta_fill.py").read_text(encoding="utf-8")
        backfill = (SCRIPTS / "embed_backfill.py").read_text(encoding="utf-8")
        embed_lib = (SCRIPTS / "embed_lib.py").read_text(encoding="utf-8")
        pr1 = (SCRIPTS / "migrate_pr1_schema.py").read_text(encoding="utf-8")
        sidecar = (SCRIPTS / "embed_sidecar_apply.py").read_text(encoding="utf-8")
        design = (ROOT / "docs" / "pr0" / "with_writer_lock_DESIGN.md").read_text(
            encoding="utf-8"
        )
        catchup = (ROOT / "docs" / "post-exit-catchup.md").read_text(encoding="utf-8")
        self.assertIn('"att0 meta fill"', meta)
        self.assertIn('default="embed_batch"', backfill)
        self.assertIn('lock_purpose: str = "embed_batch"', embed_lib)
        self.assertIn("pr1_schema", pr1)
        self.assertIn('DEFAULT_PURPOSE = "sidecar_apply"', sidecar)
        self.assertIn("embed_backfill", design)
        self.assertIn("post_exit_catchup", catchup)
        self.assertEqual(
            gate.WRITER_PURPOSE_ALLOWLIST,
            frozenset(
                (
                    "att0-migrate",
                    "att0-restore",
                    "att0 meta fill",
                    "embed_batch",
                    "embed_backfill",
                    "pr1_schema",
                    "sidecar_apply",
                    "post_exit_catchup",
                    "rem",
                    "rem-legacy",
                    "embed-rem",
                    "embed_rem",
                    "reembed-legacy",
                )
            ),
        )


class TokenOutputTests(IdentityCase):
    def test_cli_stdout_stderr_and_logs_omit_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "mailroom.write.lock"
            action = root / "ACTION_REQUIRED"
            marker = root / "marker"
            env = os.environ.copy()
            for key in (wwl.LOCK_TOKEN_ENV, wwl.LOCK_PID_ENV, wwl.LOCK_PURPOSE_ENV):
                env.pop(key, None)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "with_writer_lock.py"),
                    "--purpose",
                    "att0-migrate",
                    "--lock-file",
                    str(lock),
                    "--action-required-file",
                    str(action),
                    "--",
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path(%r).write_text('ok')" % str(marker),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            token = wwl.read_lock_info(lock).writer_token
            if not token:
                self.fail("cli lock file has no token")
            _assert_secret_absent(
                self,
                token,
                proc.stdout,
                proc.stderr,
                *self._secret_blobs(),
            )
            self.assertEqual(proc.returncode, 0, _scrub(proc.stderr, token))
            self.assertEqual(marker.read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
