#!/usr/bin/env python3
"""Doc contract: Mini daily job is the sole SoR writer.

Locks the role wording in beginner-guide, rerank, and ask_mail.
Docs only. No Keychain, no network, no live sqlite.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "beginner-guide.md"
RERANK = ROOT / "docs" / "rerank.md"
ASK = ROOT / "docs" / "ask_mail.md"

CANONICAL = (
    "The Mini daily job is the sole SoR writer; "
    "the MBP is a non-writer (rollback, read-only)."
)
MBP_LABEL = "MBP non-writer (rollback, read-only)"
PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")

RERANK_COMMENTS = (
    "hybrid retrieve (CrossEncoder when extra is installed; else fail-open)",
    "hybrid retrieve JSON (rerank_mode on stderr)",
    "hybrid retrieve (horse)",
    "force rerank_mode=none",
)
ASK_COMMENTS = (
    "phase 1: retrieve + rerank (embed resident; CrossEncoder in-process)",
    "phase 2: generate (mlx_lm.server). --fts-only avoids reloading embed 8b",
    "ask (mlx_lm.server when MAILROOM_GENERATE_MODEL is set)",
    "HTTP loopback (8743; 8744 if bound). GET /ui is the thin same-origin UI.",
    "2. UI + HTTP ask. Open http://127.0.0.1:8743/ui",
    "3. MCP stdio (four tools; separate process from --serve)",
)
RERANK_COMMANDS = (
    "scripts/semantic_search.py 'SDGE bill'",
    "scripts/semantic_search.py --json --k 20 'Caddell'",
    "scripts/semantic_search.py 'horse'",
    "scripts/semantic_search.py --no-rerank 'SDGE bill'",
)
ASK_COMMANDS = (
    "scripts/ask_mail.py --phase retrieve --json 'SDGE bill'",
    "scripts/ask_mail.py --phase generate --fts-only --json 'SDGE bill'",
    "scripts/ask_mail.py --json 'SDGE bill'",
    "scripts/ask_mail.py --serve",
    "scripts/ask_mail.py --mcp",
)


def _role_rows(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    start = lines.index("| Machine | Role today |")
    window = lines[start : start + 6]
    mbp = next(line for line in window if line.startswith("| **MBP** |"))
    mini = next(line for line in window if line.startswith("| **Mini** |"))
    return mbp, mini


class SorWriterWordingDocsTests(unittest.TestCase):
    def test_beginner_guide_role_rows(self):
        text = GUIDE.read_text(encoding="utf-8")
        mbp, mini = _role_rows(text)
        self.assertIn(CANONICAL, mini)
        self.assertIn("Non-writer (rollback, read-only)", mbp)
        self.assertIn("PR-5 cutover stays gated", mbp)
        self.assertIn("rem-legacy", mbp)
        self.assertIn("Until PR-5", mini)
        self.assertIn("**copy**", mini)
        self.assertIn("cutover stays gated", mini)
        self.assertNotIn("Holds the live **Source of Record**", mbp)
        self.assertNotIn("Holds the live **Source of Record**", text)
        self.assertNotIn("Must not write the live SoR", mini)
        self.assertNotIn("Must not write the live SoR", text)
        self.assertNotIn("empty stub", mbp)
        self.assertNotIn("empty stub", mini)
        self.assertNotIn("empty stub", text)
        self.assertNotIn("MBP (live SoR)", text)
        self.assertNotIn("copy-only until a future cutover", text)
        self.assertIn(
            "Daily refresh on the Mini. " + CANONICAL,
            text,
        )
        self.assertIn(
            "**MBP (non-writer, rollback, read-only).** " + CANONICAL,
            text,
        )
        self.assertIn(
            "A Mini retrieve recipe must point at a **copy** path, not `mailroom.sqlite`",
            text,
        )
        self.assertIn("wrong until PR-5 cutover", text)
        self.assertNotIn("host", mbp.lower())
        self.assertNotIn("host", mini.lower())
        self.assertNotIn("cutover is done", text.lower())
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_rerank_drops_mbp_sor_only(self):
        text = RERANK.read_text(encoding="utf-8")
        self.assertIn(CANONICAL, text)
        self.assertNotIn("MBP-SoR-only", text)
        self.assertNotIn("empty stub", text)
        self.assertIn(
            "# Mini, hybrid retrieve on a copy DB until PR-5. Mini daily job = sole SoR writer; MBP = non-writer.",
            text,
        )
        self.assertNotIn("copy DB until PR-5; " + CANONICAL, text)
        for comment in RERANK_COMMENTS:
            self.assertIn("# %s — %s" % (MBP_LABEL, comment), text)
        for command in RERANK_COMMANDS:
            self.assertIn(command, text)
        self.assertIn("MAILROOM_DB=$HOME/MailArchive/mailroom.sqlite", text)
        self.assertNotIn("cutover is done", text.lower())
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_ask_mail_drops_mbp_sor_only(self):
        text = ASK.read_text(encoding="utf-8")
        self.assertIn(CANONICAL, text)
        self.assertNotIn("MBP-SoR-only", text)
        self.assertNotIn("empty stub", text)
        self.assertNotIn("Live MBP", text)
        self.assertIn("The MBP live matrix is the operator gate.", text)
        self.assertNotIn("MBP probe matrix", text)
        self.assertIn(
            "# Mini, phase 1: retrieve only on a copy DB until PR-5. Mini daily job = sole SoR writer; MBP = non-writer.",
            text,
        )
        self.assertIn(
            "# Copy DB until PR-5. Mini daily job = sole SoR writer; MBP = non-writer.",
            text,
        )
        self.assertNotIn("copy DB until PR-5; " + CANONICAL, text)
        self.assertIn(
            "%s SoR: `$MAILROOM_DB` or `$HOME/MailArchive/mailroom.sqlite`"
            % (CANONICAL,),
            text,
        )
        self.assertNotIn("read-only).):", text)
        for comment in ASK_COMMENTS:
            self.assertIn("# %s — %s" % (MBP_LABEL, comment), text)
        for command in ASK_COMMANDS:
            self.assertIn(command, text)
        self.assertNotIn("cutover is done", text.lower())
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)


if __name__ == "__main__":
    unittest.main()
