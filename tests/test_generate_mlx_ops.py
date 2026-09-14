#!/usr/bin/env python3
"""KOO-19 generate process = mlx_lm.server :1234, not LM Studio.

Docs/tests contract only. No live SoR DB, no MailArchive, no
Keychain reads, no rem-legacy writer, no live machine SSH.
Does not start generate. Does not hit :1234 or :8743.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
GENERATE = ROOT / "docs" / "generate-mlx.md"
WRAPPER = ROOT / "scripts" / "mlx-generate-server.sh"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Generate process (mlx_lm.server, not LM Studio)"
CANONICAL_PY = "~/MailArchive/venv-mlx/bin/python"
UI_POINTER = "http://127.0.0.1:8743/ui"


def _scrub_home_placeholder(text: str) -> str:
    return text.replace("/Users/<operator>/", "").replace("/Users/<operator>", "")


class GenerateMlxOpsContractTests(unittest.TestCase):
    def test_contract_locks_generate_mlx_lm_server_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("**`mlx_lm.server`**", raw)
        self.assertIn("127.0.0.1:1234", raw)
        self.assertIn("POST /v1/chat/completions", text)
        self.assertIn("Canonical python is **venv-mlx**", raw)
        self.assertIn(CANONICAL_PY, raw)
        self.assertIn("placeholder path only", text)
        self.assertIn("This is not LM Studio", raw)
        self.assertIn("Do not open LM Studio.app", raw)
        self.assertIn("lms server start", raw)
        self.assertIn("Ollama is embed-only", raw)
        self.assertIn(UI_POINTER, raw)
        self.assertIn("No secrets", raw)
        self.assertIn("generate-mlx.md", raw)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not start generate", text)
        self.assertIn("does not", text.lower())
        self.assertIn("Keychain", raw)
        self.assertIn("rem-legacy", text.lower())
        self.assertIn(":1234", raw)
        self.assertIn(":8743", raw)
        hay = _scrub_home_placeholder(raw)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("generate process", text)
        self.assertIn("mlx_lm.server", raw)
        self.assertIn("127.0.0.1:1234", raw)
        self.assertIn("venv-mlx", raw)
        self.assertIn(CANONICAL_PY, raw)
        self.assertIn("not LM Studio", text)
        self.assertIn(UI_POINTER, raw)
        self.assertIn("generate-mlx.md", raw)
        hay = _scrub_home_placeholder(raw)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_generate_mlx_doc_locks_venv_mlx_and_not_lm_studio(self):
        raw = GENERATE.read_text(encoding="utf-8")
        self.assertIn("mlx_lm.server", raw)
        self.assertIn("127.0.0.1:1234", raw)
        self.assertIn("venv-mlx", raw)
        self.assertIn(CANONICAL_PY, raw)
        self.assertIn("Not LM Studio", raw)
        self.assertIn("Do not open LM Studio.app", raw)
        self.assertIn("lms server start", raw)
        self.assertIn("ops-terminal.md", raw)
        self.assertIn(UI_POINTER, raw)
        hay = _scrub_home_placeholder(raw)
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_wrapper_default_matches_canonical_python(self):
        raw = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("mlx_lm.server", raw)
        self.assertIn("venv-mlx/bin/python", raw)
        self.assertIn("127.0.0.1", raw)
        self.assertIn("--port 1234", raw)
        self.assertNotIn("lms server start", raw)
        self.assertNotIn("/Users/", raw)

    def test_fail_closed_generate_is_not_lm_studio(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("This is not LM Studio", raw)
        self.assertIn("mlx_lm.server", raw)
        self.assertIn("venv-mlx", raw)
        self.assertNotIn("Generate process is LM Studio", text)
        self.assertNotIn("canonical python is LM Studio", text.lower())
        self.assertNotIn("open LM Studio.app as the generate process", text.lower())
        self.assertNotIn("lms server start as the generate process", text.lower())
        self.assertNotIn("this document starts generate", text.lower())
        self.assertNotIn("this gate starts generate", text.lower())


if __name__ == "__main__":
    unittest.main()
