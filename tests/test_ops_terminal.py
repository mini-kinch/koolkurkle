#!/usr/bin/env python3
"""Contract tests for human Terminal / Mac ops docs. No network, no secrets."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
DAILY = ROOT / "scripts" / "README.mailroom-daily.md"
README = ROOT / "README.md"
RERANK = ROOT / "docs" / "rerank.md"
SLIM = ROOT / "macos-slim" / "README.md"
HEALTH = ROOT / "docs" / "sor-health.md"
LOCK = ROOT / "docs" / "pr0" / "with_writer_lock_DESIGN.md"
GATES = ROOT / "docs" / "model-runtime-gates.md"
ASK = ROOT / "docs" / "ask_mail.md"
EMBED = ROOT / "docs" / "embed-backfill.md"
TOMBSTONE = ROOT / "docs" / "tombstone.md"
GENERATE = ROOT / "docs" / "generate-mlx.md"


class OpsTerminalDocTests(unittest.TestCase):
    def test_covers_required_topics(self):
        text = OPS.read_text(encoding="utf-8")
        self.assertIn("One machine per card", text)
        self.assertIn("Terminal AR format", text)
        self.assertIn("Title equals body", text)
        self.assertIn("One command fence per copy button", text)
        self.assertIn("SWITCH TO Mini/MBP before machine-specific Terminal AR", text)
        self.assertIn("Sent-from-machine", text)
        self.assertIn("Verify tool exists before Terminal AR", text)
        self.assertIn("Do not invent tool paths", text)
        self.assertIn("IMAP live checks via curl imaps", text)
        self.assertIn("/usr/bin/curl imaps://", text)
        self.assertIn("imap.mail.me.com", text)
        self.assertIn("Errno 9", text)
        self.assertIn("never Python sockets", text)
        self.assertIn("Auth/2FA mail never Junk or Trash", text)
        self.assertIn("destination hygiene folder is Auth", text)
        self.assertIn("must not be classified into Junk or Trash", text)
        self.assertIn("fail closed for classify/rules", text)
        self.assertIn("CoS HOLD Mac writers", text)
        self.assertIn("Developer owns Mac ops", text)
        self.assertIn("CoS does not run Mac writer/recovery ops", text)
        self.assertIn("CoS orders Developer, collects status, issues user ARs only", text)
        self.assertIn("Developer owns Mac process ownership and installs", text)
        self.assertIn("Discuss ≠ authorize", text)
        self.assertIn("how questions are not authorization", text)
        self.assertIn("do it / approved / implement", text)
        self.assertIn("standing authorized process", text)
        self.assertIn("CoS Desk discussion/troubleshooting", text)
        self.assertIn("do not act until explicit", text)
        self.assertIn("After Action required: zero chatter until Done", text)
        self.assertIn("silence until Done/Blocked/explicit reply", text)
        self.assertIn("exceptions only STOP / hello / wake-up", text)
        self.assertIn("Do not stack chatter or routine status on an open AR", text)
        self.assertIn("Zero further user-facing messages", text)
        self.assertIn("No Terminal AR for facts Shell can read", text)
        self.assertIn("do not issue Terminal AR for facts agent Shell can read", text)
        self.assertIn("Terminal AR only for GUI / Little Snitch / sudo / secrets", text)
        self.assertIn("secrets in a real Terminal", text)
        self.assertIn("Embed model (local Qwen3-Embedding-8B via Ollama)", text)
        self.assertIn("embed model is local Qwen3-Embedding-8B via Ollama", text)
        self.assertIn("Not cloud embed", text)
        self.assertIn("do not start an embed job", text)
        self.assertIn("Do not fall through to cloud embed", text)
        self.assertIn("does not start embed jobs", text)
        self.assertIn("does not start Ollama", text)
        self.assertIn("one Action-required card at a time", text)
        self.assertIn("TO DO", text)
        self.assertIn("first check whether the card's instructions", text)
        self.assertIn("main...branch", text)
        self.assertIn("only **Merge** remains", text)
        self.assertIn("named chat attachment", text)
        self.assertIn("download link", text)
        self.assertIn("official paste", text)
        self.assertIn("Build is **staging**", text)
        self.assertIn("mini-kinch/koolkurkle", text)
        self.assertIn("https://github.com/mini-kinch/koolkurkle", text)
        self.assertIn("same cycle", text)
        self.assertIn("MBP", text)
        self.assertIn("Mini", text)
        self.assertIn("one command per fence", text)
        self.assertIn("security add-generic-password -a \"$USER\" -s mailroom.imap.app-password -w", text)
        self.assertIn("wc -c", text)
        self.assertIn("mailroom.imap.app-password", text)
        self.assertIn("mailroom.icloud.app-password", text)
        self.assertIn("16–19", text)
        self.assertIn("appleid.apple.com", text)
        self.assertIn("Login denied", text)
        self.assertIn("ZERO personal info on GitHub", text)
        self.assertIn("EXAMPLE_USER_LOCAL", text)
        self.assertIn("example.invalid", text)
        self.assertIn("user@example.com", text)
        self.assertIn("<operator>@example.com", text)
        self.assertIn("$HOME", text)
        self.assertIn("__HOME__", text)
        self.assertIn("USERNAME", text)
        self.assertIn("Conversation comment", text)
        self.assertIn("title pencil", text)
        self.assertIn("Little Snitch", text)
        self.assertIn("registry.ollama.ai:443", text)
        self.assertIn("bad file descriptor", text)
        self.assertIn("curl≠gh dial bad-file-descriptor", text)
        self.assertIn("api.github.com", text)
        self.assertIn("/opt/homebrew/bin/gh", text)
        self.assertIn("do not re-auth blindly", text)
        self.assertIn("gh api rate_limit", text)
        self.assertIn("dengcao/Qwen3-Reranker-0.6B:Q8_0", text)
        self.assertIn(":F16", text)
        self.assertNotIn("dengcao/Qwen3-Reranker-0.6B &&", text)
        self.assertNotIn("ollama pull dengcao", text)
        self.assertNotIn("ollama cp dengcao", text)
        self.assertIn("Early-error traps", text)
        self.assertIn("Interface proof", text)
        self.assertIn("fail-open-only", text)
        self.assertIn("model-runtime-gates.md", text)
        self.assertIn("ask_mail.py --probe", text)
        self.assertIn("qwen3-embedding:8b", text)
        self.assertIn("$HOME/Desktop/Heavy-Bot/to-bot", text)
        self.assertIn("/workspace", text)
        self.assertIn("before box read", text)
        self.assertIn("ollama stop qwen3-embedding:8b", text)
        self.assertIn("--phase retrieve", text)
        self.assertIn("copy-only", text)
        self.assertIn("MBP SoR vs Mini copy-only", text)
        self.assertIn("live Source of Record", text)
        self.assertIn("No Mini writers against SoR", text)
        self.assertIn("gated on rem-legacy EXIT 0", text)
        self.assertIn("mailroom-copy.sqlite", text)
        self.assertIn("same copy", text)
        self.assertIn("mailroom_copy_db.py", text)
        self.assertIn("bind_copy_db", text)
        self.assertIn("launchctl start com.mailroom.daily", text)
        self.assertIn("embed-backfill.md", text)
        self.assertIn("HARD DECK", text)
        self.assertIn("tombstone.md", text)
        self.assertIn("never-purge", text)
        self.assertIn("same-file 2-wide", text)
        self.assertIn("embed_merge_shards.py", text)
        self.assertIn("Generate process (mlx_lm.server, not LM Studio)", text)
        self.assertIn("venv-mlx", text)
        self.assertIn("http://127.0.0.1:8743/ui", text)
        self.assertIn("does not start generate", text)
        self.assertNotIn("/Users/", text.replace("/Users/<operator>/", ""))
        self.assertNotIn("-----BEGIN", text)
        self.assertNotIn("ak_live", text)
        self.assertNotIn("kirkbacon", text)
        self.assertNotIn("@me.com", text)
        self.assertNotIn("@icloud.com", text)

    def test_old_github_owner_leftovers_are_parked(self):
        old = "9zjf9jpv7z-glitch"  # parked/historical GitHub owner — not live SoR
        skip_dirs = {".git", "__pycache__", ".venv"}
        leftovers = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if old in line:
                    leftovers.append((path.relative_to(ROOT), lineno, line.strip()))
        for rel, lineno, line in leftovers:
            low = line.lower()
            self.assertTrue(
                "parked" in low or "historical" in low,
                msg="%s:%s names %s without parked/historical: %s"
                % (rel, lineno, old, line),
            )

    def test_readme_names_live_github_sor(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("mini-kinch/koolkurkle", text)
        self.assertNotIn("9zjf9jpv7z-glitch", text)  # parked/historical; not live SoR

    def test_linked_from_existing_docs(self):
        for path in (
            README,
            DAILY,
            RERANK,
            SLIM,
            HEALTH,
            LOCK,
            GATES,
            ASK,
            EMBED,
            TOMBSTONE,
            GENERATE,
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("ops-terminal.md", text, msg=path.name)

    def test_daily_readme_keeps_legacy_fallback(self):
        text = DAILY.read_text(encoding="utf-8")
        self.assertIn("mailroom.icloud.app-password", text)
        self.assertIn("read fallback", text)
        self.assertIn("keep the legacy item until IMAP", text)
        self.assertIn("# MBP — create IMAP Keychain item", text)
        self.assertIn("# Mini — create IMAP Keychain item", text)
        self.assertNotIn("EXAMPLE_USER_LOCAL", text)
        self.assertNotIn("@example.invalid", text)
        self.assertNotIn("/Users/USERNAME", text)


if __name__ == "__main__":
    unittest.main()
