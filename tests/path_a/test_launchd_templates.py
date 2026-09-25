#!/usr/bin/env python3
"""Checked-in LaunchAgent templates for mlx_lm.server and ask_mail --serve.

Stdlib only. Python 3.9+. Substitutes __HOME__ then parses with plistlib.
Does not install, bootstrap, or talk to the network.
"""
from __future__ import annotations

import plistlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP = ROOT / "scripts" / "qwen-chat-up.sh"
WATCHDOG = ROOT / "launchd" / "com.mailroom.qwen-watchdog.plist.template"
MLX = ROOT / "launchd" / "com.mailroom.mlx-lm-server.plist.template"
ASK = ROOT / "launchd" / "com.mailroom.ask-mail-serve.plist.template"
DOC = ROOT / "docs" / "path-a" / "mini-install.md"
PLACEHOLDER = "__HOME__"
FAKE_HOME = "/tmp/example-home"
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Path segments these templates are allowed to use, plus the derived PIN.
ALLOWED_SEGMENTS = {
    ".cache",
    ".venv",
    "MailArchive",
    "ask_mail.py",
    "ask_mail_serve.log",
    "bin",
    "huggingface",
    "hub",
    "logs",
    "mailroom.sqlite",
    "mlx_lm_server_1234.log",
    "python",
    "qwen-mlx",
    "scripts",
    "snapshots",
}


def pin_suffix(text):
    """Relative model path from scripts/qwen-chat-up.sh ($HOME/ stripped)."""
    hf_match = re.search(r'^HF_ROOT="(\$HOME/[^"]*)"', text, re.M)
    pin_match = re.search(r'^PIN="\$HF_ROOT/([^"]*)"', text, re.M)
    if hf_match is None or pin_match is None:
        raise AssertionError("could not derive PIN from qwen-chat-up.sh")
    hf_rel = hf_match.group(1)
    if not hf_rel.startswith("$HOME/"):
        raise AssertionError(hf_rel)
    suffix = hf_rel[len("$HOME/") :] + "/" + pin_match.group(1)
    if suffix.startswith("/") or ".." in suffix.split("/"):
        raise AssertionError(suffix)
    return suffix


def load_template(path):
    raw = path.read_text(encoding="utf-8")
    rendered = raw.replace(PLACEHOLDER, FAKE_HOME)
    data = plistlib.loads(rendered.encode("utf-8"))
    return raw, data


def string_values(value):
    found = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(string_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(string_values(item))
    return found


def plist_strings(raw):
    values = []
    for line in raw.splitlines():
        stripped = line.strip()
        prefix = "<string>"
        suffix = "</string>"
        if stripped.startswith(prefix) and stripped.endswith(suffix):
            values.append(stripped[len(prefix) : -len(suffix)])
    return values


class LaunchdTemplateTests(unittest.TestCase):
    def setUp(self):
        self.pin = pin_suffix(UP.read_text(encoding="utf-8"))
        self.mlx_raw, self.mlx = load_template(MLX)
        self.ask_raw, self.ask = load_template(ASK)

    def test_placeholder_matches_watchdog_template(self):
        watchdog = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn(PLACEHOLDER + "/", watchdog)
        self.assertNotIn("$HOME/", watchdog)
        for raw in (self.mlx_raw, self.ask_raw):
            self.assertIn(PLACEHOLDER + "/", raw)
            self.assertNotIn("$HOME/", raw)

    def test_mlx_label_and_program_arguments(self):
        self.assertEqual(self.mlx["Label"], "com.mailroom.mlx-lm-server")
        self.assertIs(self.mlx["KeepAlive"], True)
        self.assertIs(self.mlx["RunAtLoad"], True)
        model = FAKE_HOME + "/" + self.pin
        expected = [
            FAKE_HOME + "/qwen-mlx/bin/python",
            "-m",
            "mlx_lm.server",
            "--model",
            model,
            "--host",
            "127.0.0.1",
            "--port",
            "1234",
            "--chat-template-args",
            '{"enable_thinking":false}',
            "--prompt-cache-size",
            "1",
        ]
        self.assertEqual(self.mlx["ProgramArguments"], expected)
        args = self.mlx["ProgramArguments"]
        self.assertEqual(args[args.index("--prompt-cache-size") + 1], "1")
        self.assertEqual(
            args[args.index("--model") + 1].rsplit("/", 1)[-1],
            self.pin.rsplit("/", 1)[-1],
        )
        log = FAKE_HOME + "/MailArchive/logs/mlx_lm_server_1234.log"
        self.assertEqual(self.mlx["StandardOutPath"], log)
        self.assertEqual(self.mlx["StandardErrorPath"], log)

    def test_ask_label_and_program_arguments(self):
        self.assertEqual(self.ask["Label"], "com.mailroom.ask-mail-serve")
        self.assertIs(self.ask["KeepAlive"], True)
        self.assertIs(self.ask["RunAtLoad"], True)
        self.assertEqual(
            self.ask["EnvironmentVariables"]["PATH"],
            "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        )
        self.assertEqual(
            self.ask["WorkingDirectory"],
            FAKE_HOME + "/MailArchive/scripts",
        )
        expected = [
            FAKE_HOME + "/MailArchive/.venv/bin/python",
            FAKE_HOME + "/MailArchive/scripts/ask_mail.py",
            "--serve",
            "--db",
            FAKE_HOME + "/MailArchive/mailroom.sqlite",
            "--fts-only",
            "--no-generate",
            "--host",
            "127.0.0.1",
            "--port",
            "8743",
        ]
        self.assertEqual(self.ask["ProgramArguments"], expected)
        args = self.ask["ProgramArguments"]
        self.assertIn("--fts-only", args)
        self.assertIn("--no-generate", args)
        log = FAKE_HOME + "/MailArchive/logs/ask_mail_serve.log"
        self.assertEqual(self.ask["StandardOutPath"], log)
        self.assertEqual(self.ask["StandardErrorPath"], log)

    def test_loopback_hosts_and_ports(self):
        pairs = ((self.mlx, "1234"), (self.ask, "8743"))
        for data, port in pairs:
            args = data["ProgramArguments"]
            self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")
            self.assertEqual(args[args.index("--port") + 1], port)
            self.assertNotIn("0.0.0.0", args)
            for text in string_values(data):
                for ip in IPV4_RE.findall(text):
                    self.assertEqual(ip, "127.0.0.1", text)

    def test_no_personal_info(self):
        allowed = set(ALLOWED_SEGMENTS)
        allowed.update(self.pin.split("/"))
        for raw in (self.mlx_raw, self.ask_raw):
            self.assertNotIn("/Users/", raw)
            self.assertNotIn("@", raw)
            self.assertIsNone(EMAIL_RE.search(raw))
            for value in plist_strings(raw):
                if not value.startswith(PLACEHOLDER + "/"):
                    continue
                for segment in value[len(PLACEHOLDER) + 1 :].split("/"):
                    self.assertIn(segment, allowed, value)

    def test_doc_pointer(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("com.mailroom.mlx-lm-server.plist.template", text)
        self.assertIn("com.mailroom.ask-mail-serve.plist.template", text)
        self.assertIn(PLACEHOLDER, text)
        self.assertIn("sed", text)
        self.assertIn("~/Library/LaunchAgents", text)
        self.assertIn("bootstrap", text)
        self.assertIn("This change does not do that.", text)
        self.assertNotIn("/Users/", text)


if __name__ == "__main__":
    unittest.main()
