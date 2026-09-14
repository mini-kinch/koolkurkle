#!/usr/bin/env python3
"""KOO-26 embed model = local Qwen3-Embedding-8B via Ollama.

Docs/tests contract only. No live embed start. No live Mac writers.
No live SoR DB. No MailArchive. No Keychain reads. No rem-legacy
writer. No live machine SSH. Does not start Ollama.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Embed model (local Qwen3-Embedding-8B via Ollama)"
ALLOWED_NO_CLOUD_API = "do not use a cloud embed api"


class EmbedModelQwen3OllamaOpsTests(unittest.TestCase):
    def test_contract_locks_local_qwen3_ollama_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("embed model is local Qwen3-Embedding-8B via Ollama", text)
        self.assertIn("Not cloud embed", raw)
        self.assertIn("qwen3-embedding:8b", raw)
        self.assertIn("local Ollama only", text)
        self.assertIn("Do not use a cloud embed API", raw)
        self.assertIn(
            "Fail closed: if local Ollama Qwen3-Embedding-8B is not the embed path",
            raw,
        )
        self.assertIn("do not start an embed job", text)
        self.assertIn("Do not fall through to cloud embed", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not start embed jobs", text)
        self.assertIn("does not start Ollama", text)
        self.assertIn("does not run live Mac writers", text)
        self.assertIn("does not open MailArchive or live sqlite", text)
        self.assertIn("does not write embed/SoR data", text)
        self.assertIn("does not read Keychain", text)
        self.assertIn("does not SSH a live machine", text)
        self.assertIn("does not change rem-legacy", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_operators_can_find_the_contract(self):
        raw = README.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn("ops-terminal.md", raw)
        self.assertIn("Embed model (local Qwen3-Embedding-8B via Ollama", raw)
        self.assertIn("not cloud embed", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_cloud_embed_and_no_live_start(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low.replace(ALLOWED_NO_CLOUD_API, "")
        self.assertIn(
            "Fail closed: if local Ollama Qwen3-Embedding-8B is not the embed path",
            raw,
        )
        self.assertIn("do not start an embed job", text)
        self.assertIn("Do not fall through to cloud embed", raw)
        self.assertIn("Not cloud embed", raw)
        self.assertIn("does not start embed jobs", text)
        self.assertIn("does not start Ollama", text)
        self.assertNotIn("cloud embed is allowed", low)
        self.assertNotIn("use a cloud embed api", scrubbed)
        self.assertNotIn("fall through to cloud embed is ok", low)
        self.assertNotIn("openai embed is the default", low)
        self.assertNotIn("this gate starts embed jobs", low)
        self.assertNotIn("this document starts ollama", low)
        self.assertNotIn("this gate starts ollama", low)
        self.assertNotIn("this gate starts rem-legacy", low)
        self.assertNotIn("this document starts embed jobs", low)


if __name__ == "__main__":
    unittest.main()
