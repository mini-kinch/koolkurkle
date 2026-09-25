#!/usr/bin/env python3
"""Offline negative-smoke tests for the Path A paste pipeline.

Drives scripts/ask_mail_paste.sh through a temporary MAILARCHIVE with a
stub .venv python, a stub ask_mail.py, the real ask_mail_paste_fmt.py
copied in, and a stub sqlite3 on PATH. Formatter edges call the real
script with stdin. No network, no mail database, no model server.

Shell cases skip when zsh is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASTE_SH = ROOT / "scripts" / "ask_mail_paste.sh"
PASTE_FMT = ROOT / "scripts" / "ask_mail_paste_fmt.py"

QUERY_WORDS = ("synthetic", "query")
QUERY = "synthetic query"

ONE_HIT = json.dumps(
    {
        "hits": [
            {
                "date": "2015-01-02",
                "from": "sender@example.com",
                "subject": "synthetic subject",
                "snippet": "synthetic snippet",
            }
        ]
    },
    separators=(",", ":"),
)

SUCCESS_BLOCK = (
    "DATA:\n"
    "1. date: 2015-01-02\n"
    "   from: sender@example.com\n"
    "   subject: synthetic subject\n"
    "   snippet: synthetic snippet\n"
    "\n"
    "QUESTION:\n"
    "synthetic query\n"
)

ZERO_HITS_BLOCK = "DATA:\n(no hits)\n\nQUESTION:\nsynthetic query\n"

# Simulates: sqlite3 DB ".backup 'SNAP'" by creating the snap and the
# sidecars the paste script removes on EXIT. Does not read the db.
SQLITE_STUB = """#!/bin/sh
set -eu
state="${PASTE_SMOKE_STUB_DIR:?}"
db="${1:-}"
sql="${2:-}"
printf '%s\\n' "$db" > "$state/sqlite_db.txt"
printf '%s\\n' "$sql" > "$state/sqlite_sql.txt"
snap=$(printf '%s\\n' "$sql" | sed -n "s/.*'\\([^']*\\)'.*/\\1/p")
if [ -n "$snap" ]; then
  printf '%s\\n' "$snap" > "$state/snap_path.txt"
  : > "$snap"
  : > "${snap}-journal"
  : > "${snap}-wal"
  : > "${snap}-shm"
fi
exit "${PASTE_SMOKE_SQLITE_EXIT:-0}"
"""

ASK_STUB = """#!/usr/bin/env python3
import json
import os
import sys

state = os.environ["PASTE_SMOKE_STUB_DIR"]
with open(os.path.join(state, "ask_argv.json"), "w", encoding="utf-8") as fh:
    json.dump(sys.argv, fh)
raw = os.environ.get("PASTE_SMOKE_ASK_JSON", "{\\"hits\\":[]}")
if not raw.endswith("\\n"):
    raw = raw + "\\n"
sys.stdout.write(raw)
raise SystemExit(int(os.environ.get("PASTE_SMOKE_ASK_EXIT", "0")))
"""


def _write_exe(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _python_stub():
    # Absolute interpreter so the stub does not recurse through PATH.
    return "#!/bin/sh\nexec %s \"$@\"\n" % shlex.quote(sys.executable)


def _run_fmt(raw, query=QUERY):
    return subprocess.run(
        [sys.executable, str(PASTE_FMT), query],
        input=raw,
        capture_output=True,
        encoding="utf-8",
    )


def _assert_fmt_error(test, proc):
    """Exit 2, empty stdout, exactly one stderr line starting with error:."""
    err = proc.stderr or ""
    out = proc.stdout or ""
    test.assertEqual(proc.returncode, 2)
    test.assertEqual(out, "")
    test.assertNotIn("Traceback", err)
    test.assertNotIn("Traceback", out)
    test.assertTrue(err.endswith("\n"), err)
    lines = err.splitlines()
    test.assertEqual(len(lines), 1, err)
    test.assertTrue(lines[0].startswith("error:"), err)


class _Layout:
    """Temporary MAILARCHIVE plus the sqlite3 stub directory."""

    def __init__(self, root):
        self.root = root
        self.archive = root / "archive"
        self.state = root / "state"
        self.tmpdir = root / "tmpdir"
        self.bindir = root / "bin"
        self.home = root / "home"
        for path in (
            self.archive,
            self.state,
            self.tmpdir,
            self.bindir,
            self.home,
        ):
            path.mkdir(parents=True)
        self.scripts = self.archive / "scripts"
        self.scripts.mkdir()
        (self.archive / ".venv" / "bin").mkdir(parents=True)
        self.python = self.archive / ".venv" / "bin" / "python"
        self.ask = self.scripts / "ask_mail.py"
        self.fmt = self.scripts / "ask_mail_paste_fmt.py"
        self.sor = self.archive / "mailroom.sqlite"
        _write_exe(self.bindir / "sqlite3", SQLITE_STUB)

    def base_env(self):
        # Fresh map on purpose: a parent ASK_MAIL_PASTE_K must not leak in.
        return {
            "PATH": os.pathsep.join([str(self.bindir), "/usr/bin", "/bin"]),
            "HOME": str(self.home),
            "MAILARCHIVE": str(self.archive),
            "TMPDIR": str(self.tmpdir),
            "PASTE_SMOKE_STUB_DIR": str(self.state),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }

    def populate(
        self,
        python=True,
        ask=True,
        fmt=True,
        sor=True,
        ask_json="",
        ask_exit="",
        sqlite_exit="",
    ):
        if python:
            _write_exe(self.python, _python_stub())
        if ask:
            self.ask.write_text(ASK_STUB, encoding="utf-8")
        if fmt:
            self.fmt.write_text(
                PASTE_FMT.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        if sor:
            self.sor.write_bytes(b"")
        env = self.base_env()
        if ask_json:
            env["PASTE_SMOKE_ASK_JSON"] = ask_json
        if ask_exit:
            env["PASTE_SMOKE_ASK_EXIT"] = ask_exit
        if sqlite_exit:
            env["PASTE_SMOKE_SQLITE_EXIT"] = sqlite_exit
        return env


class PasteShellNegativeTests(unittest.TestCase):
    """ask_mail_paste.sh usage, missing files, snapshot cleanup, default k."""

    def setUp(self):
        zsh = shutil.which("zsh")
        if not zsh:
            self.skipTest("zsh is not installed")
        self.zsh = zsh
        self._td = tempfile.TemporaryDirectory(prefix="paste-neg-")
        self.addCleanup(self._td.cleanup)
        self.layout = _Layout(Path(self._td.name))

    def _run(self, args, env):
        return subprocess.run(
            [self.zsh, str(PASTE_SH)] + list(args),
            capture_output=True,
            encoding="utf-8",
            env=env,
            cwd=str(self.layout.home),
        )

    def _assert_missing(self, proc, path):
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "error: missing %s\n" % path)
        self.assertFalse((self.layout.state / "sqlite_db.txt").is_file())
        self.assertEqual(list(self.layout.tmpdir.glob("mailroom-paste-*")), [])

    def _snap_path(self):
        recorded = self.layout.state / "snap_path.txt"
        self.assertTrue(recorded.is_file(), "sqlite3 stub did not record a snap")
        snap = recorded.read_text(encoding="utf-8").strip()
        self.assertIn("mailroom-paste-", os.path.basename(snap))
        self.assertTrue(snap.endswith(".sqlite"))
        return snap

    def _assert_snap_removed(self):
        snap = self._snap_path()
        self.assertFalse(os.path.exists(snap))
        self.assertFalse(os.path.exists(snap + "-journal"))
        self.assertFalse(os.path.exists(snap + "-wal"))
        self.assertFalse(os.path.exists(snap + "-shm"))
        self.assertEqual(list(self.layout.tmpdir.glob("mailroom-paste-*")), [])
        sql = (self.layout.state / "sqlite_sql.txt").read_text(encoding="utf-8")
        self.assertEqual(sql.strip(), ".backup '%s'" % snap)
        db = (self.layout.state / "sqlite_db.txt").read_text(encoding="utf-8")
        self.assertEqual(db.strip(), str(self.layout.sor))
        return snap

    def _assert_ask_argv(self, snap, k="20"):
        argv = json.loads(
            (self.layout.state / "ask_argv.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            argv,
            [
                str(self.layout.ask),
                "--db",
                snap,
                "--fts-only",
                "--no-generate",
                "--no-rerank",
                "--json",
                "--k",
                k,
                "--",
                QUERY,
            ],
        )

    def test_no_query_args_exits_2_with_usage(self):
        env = self.layout.populate()
        proc = self._run((), env)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(
            proc.stderr,
            "usage: ask_mail_paste.sh <query words...>\n",
        )
        self.assertFalse((self.layout.state / "sqlite_db.txt").is_file())
        self.assertFalse((self.layout.state / "ask_argv.json").is_file())
        self.assertEqual(list(self.layout.tmpdir.glob("mailroom-paste-*")), [])

    def test_missing_python_exits_2(self):
        env = self.layout.populate(python=False)
        proc = self._run(QUERY_WORDS, env)
        self._assert_missing(proc, self.layout.python)

    def test_missing_ask_mail_exits_2(self):
        env = self.layout.populate(ask=False)
        proc = self._run(QUERY_WORDS, env)
        self._assert_missing(proc, self.layout.ask)

    def test_missing_fmt_exits_2(self):
        env = self.layout.populate(fmt=False)
        proc = self._run(QUERY_WORDS, env)
        self._assert_missing(proc, self.layout.fmt)

    def test_missing_sor_exits_2(self):
        env = self.layout.populate(sor=False)
        proc = self._run(QUERY_WORDS, env)
        self._assert_missing(proc, self.layout.sor)

    def test_snapshot_removed_on_success(self):
        env = self.layout.populate(ask_json=ONE_HIT)
        self.assertEqual(
            self.layout.fmt.read_text(encoding="utf-8"),
            PASTE_FMT.read_text(encoding="utf-8"),
        )
        proc = self._run(QUERY_WORDS, env)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.stdout, SUCCESS_BLOCK)
        snap = self._assert_snap_removed()
        self._assert_ask_argv(snap, k="20")

    def test_snapshot_removed_on_ask_failure(self):
        env = self.layout.populate(ask_json=ONE_HIT, ask_exit="1")
        proc = self._run(QUERY_WORDS, env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr, "")
        # ask_mail exits 1 after writing JSON; the real formatter still runs.
        self.assertEqual(proc.stdout, SUCCESS_BLOCK)
        snap = self._assert_snap_removed()
        self._assert_ask_argv(snap, k="20")

    def test_default_k_is_20_and_flags_always_passed(self):
        env = self.layout.populate(ask_json=ONE_HIT)
        self.assertNotIn("ASK_MAIL_PASTE_K", env)
        proc = self._run(QUERY_WORDS, env)
        self.assertEqual(proc.returncode, 0)
        snap = self._assert_snap_removed()
        self._assert_ask_argv(snap, k="20")


class PasteFormatterNegativeTests(unittest.TestCase):
    """ask_mail_paste_fmt.py stdin edges. No zsh, no MAILARCHIVE."""

    maxDiff = None

    def test_fmt_zero_hits_prints_data_block(self):
        samples = ("{}", '{"hits":[]}', '{"hits":null}')
        for raw in samples:
            with self.subTest(raw=raw):
                proc = _run_fmt(raw)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stderr, "")
                self.assertEqual(proc.stdout, ZERO_HITS_BLOCK)
                self.assertNotIn("Traceback", proc.stdout)

    def test_fmt_missing_and_odd_fields(self):
        # No subject and no date on hit 1. Hit 2 has a non-ASCII subject,
        # a snippet longer than the 220-character cap, and a very long
        # body field the formatter does not read.
        long_snippet = "S" * 400
        long_body = "B" * 5000
        payload = {
            "hits": [
                {
                    "from": "sender@example.com",
                    "snippet": "short synthetic",
                },
                {
                    "date": "2015-03-04",
                    "from": "sender@example.com",
                    "subject": "caf\u00e9 \u6771\u4eac",
                    "snippet": long_snippet,
                    "body": long_body,
                },
            ]
        }
        proc = _run_fmt(json.dumps(payload, ensure_ascii=False))
        capped = long_snippet[:220]
        expected = (
            "DATA:\n"
            "1. date: \n"
            "   from: sender@example.com\n"
            "   subject: \n"
            "   snippet: short synthetic\n"
            "2. date: 2015-03-04\n"
            "   from: sender@example.com\n"
            "   subject: caf\u00e9 \u6771\u4eac\n"
            "   snippet: %s\n"
            "\n"
            "QUESTION:\n"
            "synthetic query\n"
        ) % capped
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.stdout, expected)
        self.assertEqual(len(capped), 220)
        self.assertNotIn("B", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_fmt_empty_stdin_no_traceback(self):
        """Empty or whitespace-only stdin exits 2 with one error line."""
        for raw in ("", " ", "\n", "\t\r\n ", "   \n\t  "):
            with self.subTest(raw=repr(raw)):
                _assert_fmt_error(self, _run_fmt(raw))

    def test_fmt_malformed_json_no_traceback(self):
        """Malformed JSON exits 2 with one error line and no traceback."""
        _assert_fmt_error(self, _run_fmt('{"hits":'))

    def test_fmt_non_object_json_exits_2(self):
        """A parsed list or number is not an object; same clean error."""
        for raw in ("[]", "[1, 2]", "3", "0", '"hello"', "null", "true", "false"):
            with self.subTest(raw=raw):
                _assert_fmt_error(self, _run_fmt(raw))

    def test_fmt_valid_stdout_byte_identical(self):
        """Valid objects keep the pre-fix stdout bytes. Exit 0, empty stderr.

        Fixtures are the original formatter's stdout on three synthetic
        inputs: zero hits, three normal hits, and hits with a missing
        subject or date plus non-ASCII text.
        """
        cases = (
            (
                "zero_hits",
                '{"hits":[]}',
                "DATA:\n(no hits)\n\nQUESTION:\nsynthetic query\n",
            ),
            (
                "normal_hits",
                json.dumps(
                    {
                        "hits": [
                            {
                                "date": "2015-01-02",
                                "from": "sender1@example.com",
                                "subject": "synthetic alpha",
                                "snippet": "first synthetic snippet",
                            },
                            {
                                "date": "2016-06-07",
                                "from": "sender2@example.com",
                                "subject": "synthetic beta",
                                "snippet": "second synthetic snippet",
                            },
                            {
                                "date": "2017-08-09",
                                "from": "sender3@example.com",
                                "subject": "synthetic gamma",
                                "snippet": "third synthetic snippet",
                            },
                        ]
                    }
                ),
                (
                    "DATA:\n"
                    "1. date: 2015-01-02\n"
                    "   from: sender1@example.com\n"
                    "   subject: synthetic alpha\n"
                    "   snippet: first synthetic snippet\n"
                    "2. date: 2016-06-07\n"
                    "   from: sender2@example.com\n"
                    "   subject: synthetic beta\n"
                    "   snippet: second synthetic snippet\n"
                    "3. date: 2017-08-09\n"
                    "   from: sender3@example.com\n"
                    "   subject: synthetic gamma\n"
                    "   snippet: third synthetic snippet\n"
                    "\n"
                    "QUESTION:\n"
                    "synthetic query\n"
                ),
            ),
            (
                "odd_nonascii",
                json.dumps(
                    {
                        "hits": [
                            {
                                "from": "sender1@example.com",
                                "snippet": "missing date and subject",
                            },
                            {
                                "date": "2015-03-04",
                                "from": "sender2@example.com",
                                "subject": "caf\u00e9 \u6771\u4eac",
                                "snippet": "non-ascii synthetic \u00e9",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                (
                    "DATA:\n"
                    "1. date: \n"
                    "   from: sender1@example.com\n"
                    "   subject: \n"
                    "   snippet: missing date and subject\n"
                    "2. date: 2015-03-04\n"
                    "   from: sender2@example.com\n"
                    "   subject: caf\u00e9 \u6771\u4eac\n"
                    "   snippet: non-ascii synthetic \u00e9\n"
                    "\n"
                    "QUESTION:\n"
                    "synthetic query\n"
                ),
            ),
        )
        for name, raw, expected in cases:
            with self.subTest(name=name):
                proc = _run_fmt(raw)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stderr, "")
                self.assertNotIn("Traceback", proc.stdout)
                self.assertEqual(proc.stdout.encode("utf-8"), expected.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
