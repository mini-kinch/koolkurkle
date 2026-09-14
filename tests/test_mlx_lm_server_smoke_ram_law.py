#!/usr/bin/env python3
"""KOO-62 mlx_lm.server smoke codes + Mini RAM law + bind 127.0.0.1."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_mail  # noqa: E402

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
DOCS = (
    ROOT / "docs" / "ops-terminal.md",
    ROOT / "docs" / "ask_mail.md",
    ROOT / "docs" / "generate-mlx.md",
    ROOT / "README.md",
)
WRAPPER = ROOT / "scripts" / "mlx-generate-server.sh"


class MlxLmServerSmokeRamLawTests(unittest.TestCase):
    def test_smoke_codes_are_mlx_lm_server(self):
        self.assertEqual(ask_mail.SMOKE_PROCESS, "mlx_lm.server")
        self.assertEqual(ask_mail.GENERATE_PROCESS, "mlx_lm.server")
        self.assertIn("mlx_lm_server_stopped", ask_mail.NEG_SMOKE)
        self.assertIn("mlx_lm_server_unreachable", ask_mail.NEG_SMOKE)
        self.assertEqual(
            ask_mail.NEG_SMOKE["mlx_lm_server_unreachable"]["generate_error"],
            "lm_studio_unreachable",
        )
        self.assertEqual(
            ask_mail.NEG_SMOKE["lm_studio_stopped"]["generate_error"],
            "lm_studio_unreachable",
        )
        self.assertIn("8B embed + 35B generate", ask_mail.MINI_RAM_LAW)

    def test_docs_and_bind_localhost(self):
        for path in DOCS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("mlx_lm.server", text, msg=path.name)
            self.assertIn("127.0.0.1", text, msg=path.name)
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay, msg=path.name)
        ops = DOCS[0].read_text(encoding="utf-8")
        self.assertIn("no co-reside 8B embed + 35B generate", ops)
        self.assertIn("1234", ops)
        self.assertIn("8743", ops)
        wrap = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("--host 127.0.0.1", wrap)
        self.assertIn("--port 1234", wrap)
        self.assertIn("mlx_lm.server", wrap)
        self.assertEqual(ask_mail.DEFAULT_HTTP_HOST, "127.0.0.1")
        self.assertEqual(ask_mail.DEFAULT_HTTP_PORT, 8743)
        self.assertEqual(ask_mail.DEFAULT_LM_STUDIO_URL, "http://127.0.0.1:1234")


if __name__ == "__main__":
    unittest.main()
