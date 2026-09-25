#!/usr/bin/env python3
"""Pin tests for scripts/qwen-chat-up.sh.

Stdlib only. Python 3.9+. Behavioral cases run a COPY of the script under a
temp HOME. Stubs replace launchctl, osascript, killall, curl, lsof, and sleep.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
UP = ROOT / "scripts" / "qwen-chat-up.sh"
WATCHDOG = ROOT / "scripts" / "qwen-mlx-watchdog.sh"
DOC = ROOT / "docs" / "path-a" / "model-pin.md"
BASE_REV = "138ec34"
SNAP = "10c35caafbb80f7dc6a7a432cdd11af10a6d4818"
REPO = "models--mlx-community--Qwen3.8-27B-4bit"
DECOY_REPO = "models--aaa--Qwen-decoy"
MLX_LABEL = "com.mailroom.mlx-lm-server"
STUB_NAMES = ("launchctl", "osascript", "killall", "curl", "lsof", "sleep")
PATH_LINE = "export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH"

# Pre-change scripts/qwen-chat-up.sh at 138ec34. Used when that blob is not
# in the local git history (shallow CI checkouts).
EMBEDDED_BASELINE = r"""#!/bin/bash
# Path A Qwen chat session UP — RAM rules (CoS 2026-09-24)
# 1) Stop Ollama  2) ask_mail --serve --fts-only --no-generate  3) mlx_lm.server :1234 if weights ready
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH
LOG="$HOME/MailArchive/logs/qwen-chat-up.log"
exec >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] qwen-chat-up start"
rm -f "$HOME/qwen-mlx/HOLD"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] cleared watchdog HOLD"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping Ollama"
osascript -e 'quit application "Ollama"' >/dev/null 2>&1 || true
killall ollama 2>/dev/null || true
sleep 2

# Pin / reload ask-mail-serve (FTS-only, no generate)
PLIST="$HOME/Library/LaunchAgents/com.mailroom.ask-mail-serve.plist"
if [ -f "$PLIST" ]; then
  launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$PLIST"
  launchctl kickstart -k gui/$(id -u)/com.mailroom.ask-mail-serve
fi
for i in 1 2 3 4 5 6 7 8; do
  if curl -sf -m 2 http://127.0.0.1:8743/health >/dev/null; then break; fi
  sleep 1
done
curl -sS -m 5 http://127.0.0.1:8743/health || echo "ask_mail health FAIL"
echo

HF_ROOT="$HOME/.cache/huggingface/hub"
MOD=$(ls -d "$HF_ROOT"/models--*Qwen* 2>/dev/null | head -1 || true)
INC=1; SAFE=0; SNAP=""
if [ -n "${MOD:-}" ]; then
  INC=$(find "$MOD" -name '*.incomplete' 2>/dev/null | wc -l | tr -d ' ')
  SAFE=$(find "$MOD" -name '*.safetensors' 2>/dev/null | wc -l | tr -d ' ')
  SNAP=$(ls -d "$MOD"/snapshots/* 2>/dev/null | head -1 || true)
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] HF incomplete=$INC safetensors=$SAFE snap=${SNAP:-none}"

if [ "${INC}" = "0" ] && [ "${SAFE}" -ge 1 ] && [ -n "${SNAP:-}" ]; then
  SPLIST="$HOME/Library/LaunchAgents/com.mailroom.mlx-lm-server.plist"
  cat > "$SPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mailroom.mlx-lm-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/qwen-mlx/bin/python</string>
    <string>-m</string>
    <string>mlx_lm.server</string>
    <string>--model</string>
    <string>$SNAP</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>1234</string>
    <string>--chat-template-args</string>
    <string>{"enable_thinking":false}</string>
    <string>--prompt-cache-size</string>
    <string>1</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/MailArchive/logs/mlx_lm_server_1234.log</string>
  <key>StandardErrorPath</key><string>$HOME/MailArchive/logs/mlx_lm_server_1234.log</string>
</dict>
</plist>
EOF
  launchctl bootout gui/$(id -u) "$SPLIST" 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) "$SPLIST"
  launchctl kickstart -k gui/$(id -u)/com.mailroom.mlx-lm-server
  for i in $(seq 1 36); do
    if lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] mlx_lm.server listening :1234"
      curl -sS -m 30 http://127.0.0.1:1234/v1/models | head -c 400; echo
      break
    fi
    sleep 5
  done
  if ! lsof -nP -iTCP:1234 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN :1234 not up yet — check mlx_lm_server_1234.log"
  fi
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] model not ready — ask_mail FTS-only serve up; mlx_lm.server deferred (HF watcher will start)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] qwen-chat-up done"
echo "Product path: ask_mail /ask (FTS-only) + mlx_lm.server :1234. No /ui."
"""


def _git_baseline() -> Optional[str]:
    proc = subprocess.run(
        ["git", "show", "%s:scripts/qwen-chat-up.sh" % BASE_REV],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.startswith("#!/bin/bash"):
        return None
    return proc.stdout


def _baseline() -> str:
    git_text = _git_baseline()
    if git_text is not None:
        return git_text
    return EMBEDDED_BASELINE


def _marker(lines: list[str], pred, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    raise AssertionError("marker not found")


def stable_text(text: str) -> str:
    """Lines outside the model-selection block and the not-ready branch.

    The selection block is the HF_ROOT assignment through the line before the
    HF incomplete/safetensors/snap log. The not-ready branch is the body
    between else and fi. Everything else, including that log line, stays.
    """
    lines = text.splitlines(keepends=True)
    hf = _marker(lines, lambda line: line.startswith("HF_ROOT="))
    echo = _marker(lines, lambda line: "HF incomplete=" in line)
    else_i = _marker(lines, lambda line: line.startswith("else"), echo)
    fi_i = _marker(lines, lambda line: line.startswith("fi"), else_i + 1)
    if not (hf < echo < else_i < fi_i):
        raise AssertionError("pin markers out of order")
    kept = lines[:hf] + lines[echo : else_i + 1] + lines[fi_i:]
    return "".join(kept)


def _selection_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    hf = _marker(lines, lambda line: line.startswith("HF_ROOT="))
    echo = _marker(lines, lambda line: "HF incomplete=" in line)
    return "".join(lines[hf:echo])


def _else_body(text: str) -> str:
    lines = text.splitlines(keepends=True)
    echo = _marker(lines, lambda line: "HF incomplete=" in line)
    else_i = _marker(lines, lambda line: line.startswith("else"), echo)
    fi_i = _marker(lines, lambda line: line.startswith("fi"), else_i + 1)
    return "".join(lines[else_i + 1 : fi_i])


def _pin_dir(home: Path) -> Path:
    return home / ".cache" / "huggingface" / "hub" / REPO / "snapshots" / SNAP


def _decoy_dir(home: Path) -> Path:
    return home / ".cache" / "huggingface" / "hub" / DECOY_REPO / "snapshots" / "x"


class QwenChatUpPinStaticTests(unittest.TestCase):
    def test_lines_outside_pin_block_match_baseline(self) -> None:
        new = UP.read_text()
        git_text = _git_baseline()
        if git_text is not None:
            self.assertEqual(EMBEDDED_BASELINE, git_text)
            baseline = git_text
        else:
            baseline = EMBEDDED_BASELINE
        self.assertEqual(stable_text(baseline), stable_text(new))
        stable = stable_text(new)
        self.assertIn('rm -f "$HOME/qwen-mlx/HOLD"', stable)
        self.assertIn("cleared watchdog HOLD", stable)
        self.assertIn('{"enable_thinking":false}', stable)
        self.assertIn("--prompt-cache-size", stable)
        self.assertIn(
            "    <string>--prompt-cache-size</string>\n    <string>1</string>\n",
            stable,
        )
        self.assertIn('ls -d "$HF_ROOT"/models--*Qwen*', _selection_block(baseline))
        self.assertNotIn("ls -d", _selection_block(new))
        self.assertIn("PIN=", _selection_block(new))
        self.assertIn("HF watcher will start", _else_body(baseline))
        self.assertIn("exit 3", _else_body(new))
        self.assertNotIn("HF watcher will start", new)

    def test_no_glob_and_pin_matches_watchdog(self) -> None:
        text = UP.read_text()
        self.assertNotIn("models--*", text)
        self.assertNotIn("ls -d", text)
        self.assertNotIn("snapshots/*", text)
        wd = WATCHDOG.read_text()
        wd_match = re.search(
            r'^WD_MODEL_PATH="\$\{WD_MODEL_PATH:-(.*)\}"',
            wd,
            re.M,
        )
        self.assertIsNotNone(wd_match)
        hf_match = re.search(r'^HF_ROOT="([^"]*)"', text, re.M)
        pin_match = re.search(r'^PIN="\$HF_ROOT/([^"]*)"', text, re.M)
        self.assertIsNotNone(hf_match)
        self.assertIsNotNone(pin_match)
        pin = hf_match.group(1) + "/" + pin_match.group(1)
        self.assertEqual(pin, wd_match.group(1))
        self.assertTrue(pin.endswith("/" + REPO + "/snapshots/" + SNAP))

    def test_model_pin_doc_disables_stage_without_removing_plist(self) -> None:
        text = DOC.read_text()
        disable_lines = [
            line for line in text.splitlines() if "launchctl disable gui/" in line
        ]
        self.assertTrue(disable_lines)
        self.assertTrue(any("hf-qwen-stage" in line for line in disable_lines))
        self.assertNotIn("/Users/", text)
        for line in text.splitlines():
            if "hf-qwen-stage" in line:
                self.assertIsNone(re.search(r"\brm\b", line), line)
        self.assertIsNone(re.search(r"\brm\b[^\n]{0,200}hf-qwen-stage", text))
        self.assertIsNone(re.search(r"hf-qwen-stage[^\n]{0,200}\brm\b", text))


class QwenChatUpPinBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.stub_bin = self.tmp / "stubbin"
        self.stub_log = self.tmp / "stub-argv.log"
        self.real_agents = Path.home() / "Library" / "LaunchAgents"
        self._agents_before = self._agents_snapshot()
        self._write_stubs()
        self.copy = self._write_copy()
        (self.home / "MailArchive" / "logs").mkdir(parents=True)
        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)
        (self.home / "qwen-mlx").mkdir(parents=True)

    def tearDown(self) -> None:
        self.assertEqual(self._agents_snapshot(), self._agents_before)
        real_plist = self.real_agents / (MLX_LABEL + ".plist")
        self.assertFalse(real_plist.exists())
        self._tmp.cleanup()

    def _agents_snapshot(self) -> tuple[str, ...]:
        if not self.real_agents.is_dir():
            return ()
        return tuple(sorted(p.name for p in self.real_agents.iterdir()))

    def _write_stubs(self) -> None:
        self.stub_bin.mkdir()
        log = str(self.stub_log)
        if "'" in log:
            raise AssertionError("stub log path must not contain a single quote")
        body = """#!/bin/sh
resolved=$0
case "$resolved" in
  /*) ;;
  *) resolved=$(command -v "$0" 2>/dev/null || printf '%s' "$0") ;;
esac
{
  printf '%s' "$resolved"
  for a in "$@"; do
    printf '\\t%s' "$a"
  done
  printf '\\n'
} >> '@@LOG@@'
exit 0
""".replace("@@LOG@@", log)
        for name in STUB_NAMES:
            path = self.stub_bin / name
            path.write_text(body)
            path.chmod(0o755)

    def _write_copy(self) -> Path:
        text = UP.read_text()
        self.assertEqual(text.count(PATH_LINE), 1)
        stub = str(self.stub_bin)
        rewritten = "export PATH=%s:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH" % stub
        text = text.replace(PATH_LINE, rewritten, 1)
        self.assertIn(rewritten, text)
        copy = self.tmp / "qwen-chat-up.sh"
        copy.write_text(text)
        copy.chmod(0o755)
        return copy

    def _run(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PATH"] = "%s:%s" % (self.stub_bin, env.get("PATH", ""))
        start = time.monotonic()
        proc = subprocess.run(
            ["bash", str(self.copy)],
            cwd=str(self.tmp),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 15, "script ran long enough to be real sleep/launchctl")
        return proc

    def _stub_lines(self) -> list[str]:
        if not self.stub_log.is_file():
            return []
        return [line for line in self.stub_log.read_text().splitlines() if line]

    def _assert_stubs_invoked(self, required: tuple[str, ...]) -> list[str]:
        lines = self._stub_lines()
        self.assertTrue(lines, "no stub invocations recorded")
        seen = set()
        for line in lines:
            resolved = line.split("\t", 1)[0]
            self.assertTrue(
                resolved.startswith(str(self.stub_bin) + os.sep),
                resolved,
            )
            name = Path(resolved).name
            self.assertIn(name, STUB_NAMES)
            self.assertTrue((self.stub_bin / name).is_file())
            seen.add(name)
        for name in required:
            self.assertIn(name, seen, name)
        return lines

    def _log(self) -> str:
        return (self.home / "MailArchive" / "logs" / "qwen-chat-up.log").read_text()

    def _plist(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / (MLX_LABEL + ".plist")

    def _write_safetensors(self, directory: Path) -> None:
        directory.mkdir(parents=True)
        (directory / "model.safetensors").write_bytes(b"not-weights")

    def test_decoy_does_not_win(self) -> None:
        self._write_safetensors(_decoy_dir(self.home))
        pin = _pin_dir(self.home)
        self._write_safetensors(pin)
        proc = self._run()
        log = self._log()
        self.assertEqual(proc.returncode, 0, log + proc.stderr)
        lines = self._assert_stubs_invoked(STUB_NAMES)
        self.assertTrue(self._plist().is_file())
        data = plistlib.loads(self._plist().read_bytes())
        args = data["ProgramArguments"]
        model = args[args.index("--model") + 1]
        self.assertEqual(model, str(pin))
        self.assertNotIn(DECOY_REPO, model)
        self.assertNotIn(DECOY_REPO, "\n".join(args))
        self.assertEqual(
            args[args.index("--chat-template-args") + 1],
            '{"enable_thinking":false}',
        )
        self.assertEqual(args[args.index("--prompt-cache-size") + 1], "1")
        joined = "\n".join(lines)
        self.assertIn(MLX_LABEL, joined)
        self.assertIn("127.0.0.1:8743", joined)
        self.assertIn("127.0.0.1:1234", joined)
        self.assertIn(str(pin), log)
        self.assertNotIn(DECOY_REPO, log)

    def test_missing_pin_fails_closed(self) -> None:
        self._write_safetensors(_decoy_dir(self.home))
        pin = _pin_dir(self.home)
        self.assertFalse(pin.exists())
        proc = self._run()
        log = self._log()
        self.assertEqual(proc.returncode, 3, log + proc.stderr)
        lines = self._assert_stubs_invoked(("osascript", "killall", "curl", "sleep"))
        self.assertFalse(self._plist().exists())
        self.assertFalse(any(MLX_LABEL in line for line in lines))
        self.assertFalse(
            any(Path(line.split("\t", 1)[0]).name == "launchctl" for line in lines)
        )
        fail_lines = [line for line in log.splitlines() if "fail-closed:" in line]
        self.assertEqual(len(fail_lines), 1, log)
        self.assertIn(str(pin), fail_lines[0])
        self.assertIn("missing dir", fail_lines[0])
        self.assertNotIn(DECOY_REPO, log)
        self.assertNotIn("qwen-chat-up done", log)

    def test_incomplete_under_repo_fails_closed(self) -> None:
        pin = _pin_dir(self.home)
        self._write_safetensors(pin)
        repo = pin.parent.parent
        blob = repo / "blobs"
        blob.mkdir(parents=True)
        (blob / "weights.incomplete").write_bytes(b"partial")
        proc = self._run()
        log = self._log()
        self.assertEqual(proc.returncode, 3, log + proc.stderr)
        lines = self._assert_stubs_invoked(("osascript", "killall", "curl", "sleep"))
        self.assertFalse(self._plist().exists())
        self.assertFalse(any(MLX_LABEL in line for line in lines))
        self.assertFalse(
            any(Path(line.split("\t", 1)[0]).name == "launchctl" for line in lines)
        )
        fail_lines = [line for line in log.splitlines() if "fail-closed:" in line]
        self.assertEqual(len(fail_lines), 1, log)
        self.assertIn(str(pin), fail_lines[0])
        self.assertIn("incomplete files", fail_lines[0])


if __name__ == "__main__":
    unittest.main()
