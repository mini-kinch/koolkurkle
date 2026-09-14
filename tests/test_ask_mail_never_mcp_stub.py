#!/usr/bin/env python3
"""Fail-closed Ready gate: ask_mail.py must stay a real retrieve/CLI.

Source/AST only. Does not import ask_mail, open MailArchive, or touch
live sqlite / IMAP / rem-legacy.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASK_MAIL = ROOT / "scripts" / "ask_mail.py"

# Matches scripts/install-mlx-generate.sh refuse_stub (size floor).
MIN_BYTES = 10000
# Real file is ~1800 lines. A stub/placeholder is far smaller.
MIN_LINES = 400

REQUIRED_FUNCS = ("build_parser", "retrieve_hits", "ask", "main")

REQUIRED_SUBSTRINGS = (
    "import argparse",
    "import semantic_search as ss",
    "def build_parser",
    "def retrieve_hits",
    "def ask(",
    "ss.retrieve",
    "--phase",
    "--json",
    "--serve",
    "--mcp",
    "choices=(\"retrieve\", \"generate\")",
)

# Known MCP-push stub marker + phrases that mean the file is not the CLI.
STUB_MARKERS = (
    "LOADED_FROM_MCP_PUSH_ASK_JSON",
    "MCP-only stub",
    "mcp-only stub",
    "MCP stub",
    "tiny placeholder",
    "placeholder only",
    "TODO: implement retrieve",
    "TODO implement retrieve",
)


def _violations(text: str, nbytes: int, nlines: int) -> list[str]:
    """Contract failures for a candidate ask_mail.py source."""
    found: list[str] = []
    if nbytes < MIN_BYTES:
        found.append("too_small_bytes")
    if nlines < MIN_LINES:
        found.append("too_small_lines")
    for needle in REQUIRED_SUBSTRINGS:
        if needle not in text:
            found.append("missing:%s" % needle)
    for marker in STUB_MARKERS:
        if marker in text:
            found.append("stub_marker:%s" % marker)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        found.append("not_parseable")
        return found
    func_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    for name in REQUIRED_FUNCS:
        if name not in func_names:
            found.append("missing_func:%s" % name)
    main = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        ),
        None,
    )
    if main is not None:
        called = {
            n.func.id
            for n in ast.walk(main)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if "build_parser" not in called:
            found.append("main_skips_build_parser")
        if "ask" not in called:
            found.append("main_skips_ask")
        if "serve_mcp" in called and "ask" not in called:
            found.append("mcp_only_main")
    retrieve = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "retrieve_hits"
        ),
        None,
    )
    if retrieve is not None:
        uses_ss_retrieve = False
        for node in ast.walk(retrieve):
            if isinstance(node, ast.Attribute) and node.attr == "retrieve":
                uses_ss_retrieve = True
            if isinstance(node, ast.Name) and node.id == "retrieve":
                uses_ss_retrieve = True
        if not uses_ss_retrieve:
            found.append("retrieve_hits_skips_ss_retrieve")
    return found


class AskMailNeverMcpStubTests(unittest.TestCase):
    def test_current_file_is_real_retrieve_cli(self) -> None:
        self.assertTrue(ASK_MAIL.is_file())
        text = ASK_MAIL.read_text(encoding="utf-8")
        nbytes = ASK_MAIL.stat().st_size
        nlines = text.count("\n") + (0 if text.endswith("\n") else 1)
        self.assertEqual(_violations(text, nbytes, nlines), [])
        self.assertGreater(nbytes, MIN_BYTES)
        self.assertGreater(nlines, MIN_LINES)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("@me.com", text)
        self.assertNotIn("@icloud.com", text)

    def test_mcp_stub_and_placeholder_fail_closed(self) -> None:
        mcp_stub = "LOADED_FROM_MCP_PUSH_ASK_JSON\n"
        self.assertIn("too_small_bytes", _violations(mcp_stub, len(mcp_stub), 1))
        self.assertIn(
            "stub_marker:LOADED_FROM_MCP_PUSH_ASK_JSON",
            _violations(mcp_stub, max(len(mcp_stub), MIN_BYTES), MIN_LINES),
        )

        tiny = "import argparse\ndef main():\n    print('mcp')\n"
        tiny_v = _violations(tiny, len(tiny.encode("utf-8")), tiny.count("\n") + 1)
        self.assertIn("too_small_bytes", tiny_v)
        self.assertIn("missing:def retrieve_hits", tiny_v)

        # MCP-only wrapper: stdio loop, no retrieve CLI / argparse parser.
        mcp_only = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "def serve_mcp():\n"
            "    while True:\n"
            "        line = sys.stdin.readline()\n"
            "        if not line:\n"
            "            return 0\n"
            "        sys.stdout.write(line)\n"
            "def main():\n"
            "    return serve_mcp()\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n"
        )
        padded = mcp_only + ("# pad\n" * 500)
        mcp_v = _violations(padded, len(padded.encode("utf-8")), padded.count("\n") + 1)
        self.assertIn("missing:import argparse", mcp_v)
        self.assertIn("missing:def build_parser", mcp_v)
        self.assertIn("missing:def retrieve_hits", mcp_v)
        self.assertIn("missing:ss.retrieve", mcp_v)
        self.assertIn("missing_func:ask", mcp_v)
        self.assertIn("main_skips_ask", mcp_v)
        self.assertIn("mcp_only_main", mcp_v)


class DocsPointerTests(unittest.TestCase):
    def test_ask_mail_docs_block_ready_on_this_gate(self) -> None:
        docs = (ROOT / "docs" / "ask_mail.md").read_text(encoding="utf-8")
        self.assertIn("HARD DECK", docs)
        self.assertIn("MCP stub", docs)
        self.assertIn("test_ask_mail_never_mcp_stub.py", docs)
        self.assertIn("Ready/merge", docs)
        self.assertNotIn("/Users/", docs)
        self.assertNotIn("@me.com", docs)
        self.assertNotIn("@icloud.com", docs)


if __name__ == "__main__":
    unittest.main()
