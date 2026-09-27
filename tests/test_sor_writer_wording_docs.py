#!/usr/bin/env python3
"""Doc contract: Mini daily job is the sole SoR writer.

Locks the role wording in beginner-guide, rerank, ask_mail,
ops-terminal, the README index, model-runtime-gates, and ATT-0 hard deck 8.
Docs only. No Keychain, no network, no live sqlite.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "beginner-guide.md"
RERANK = ROOT / "docs" / "rerank.md"
ASK = ROOT / "docs" / "ask_mail.md"
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"
GATES = ROOT / "docs" / "model-runtime-gates.md"
ATT0 = ROOT / "docs" / "att0-constraints.md"
DECISION_RECORD = "CRM-log/20260924-1201-mini-only-writer-user.md"

CANONICAL = (
    "The Mini daily job is the sole SoR writer; "
    "the MBP is a non-writer (rollback, read-only)."
)
HISTORICAL_REM_LEGACY_SOLE = (
    "rem-legacy was the sole writer on basename `mailroom.sqlite` until EXIT 0."
)
HISTORICAL_REFUSE_LABEL = (
    "Classic MBP 8pm (`imap_newmail`+`classify`+`notify_bills` → SoR)"
)
PRESENT_REM_SOLE = re.compile(
    r"rem-legacy\b(?:\s+\S+){0,12}\s+sole[\s-]*writer\b",
    re.IGNORECASE,
)
RULE_MARKERS = ("sole writer", "sole sor writer", "only sor writer")
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

    def test_followup_spots_drop_stale_sor_writer_wording(self):
        ops = OPS.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        gates = GATES.read_text(encoding="utf-8")
        self.assertIn(
            "- %s No MBP daily runs against it, and nothing on the MBP writes the SoR."
            % (CANONICAL,),
            ops,
        )
        self.assertNotIn("The MBP SoR is a rollback/read target", ops)
        self.assertNotIn("rollback/read", ops)
        self.assertIn(
            "do not run the MBP 8pm chain while rem-legacy is\n"
            "alive; use Mini/copy until rem EXIT 0. " + CANONICAL,
            ops,
        )
        self.assertNotIn("classic MBP SoR", ops)
        self.assertIn("No live copy from MBP to Mini from this gate.", ops)
        self.assertNotIn("No live MBP→Mini copy", ops)
        self.assertIn(
            "proof, Mini copy-only notes (`$HOME` only), and phase watermarks. %s "
            "Mini retrieve stays on a copy DB until PR-5; "
            "PR-5 cutover / RunAtLoad stays gated:" % (CANONICAL,),
            readme,
        )
        self.assertIn("Mini copy-only notes (`$HOME` only)", readme)
        self.assertNotIn("proof (`$HOME` only)", readme)
        self.assertIn(
            "do not run the MBP 8pm chain while rem-legacy is alive. " + CANONICAL,
            readme,
        )
        self.assertNotIn("classic MBP SoR 8pm", readme)
        self.assertIn("The MBP live matrix is the operator gate.", gates)
        self.assertNotIn("Live MBP", gates)
        for text in (ops, readme, gates):
            hay = text.replace("/Users/<operator>/", "")
            for needle in PRIVACY_NEEDLES:
                self.assertNotIn(needle, hay)
            self.assertNotIn("cutover is done", text.lower())

    def test_att0_hard_deck_8_names_mini_only_writer(self):
        text = ATT0.read_text(encoding="utf-8")
        rule = (
            "%s Mini retrieve stays on a copy DB until PR-5; "
            "PR-5 cutover / RunAtLoad stays gated."
            % (CANONICAL,)
        )
        self.assertIn(
            "8. %s Attachment jobs on Mini use copy DB + local extract dir. `%s`"
            % (rule, DECISION_RECORD),
            text,
        )
        self.assertNotIn("Mini **copy-only until PR-5**", text)
        self.assertNotIn("Mini copy-only until PR-5", text)
        self.assertNotIn(
            "PR-5 cutover stays gated. Attachment jobs on Mini use copy DB",
            text,
        )
        self.assertNotIn("](%s)" % (DECISION_RECORD,), text)
        self.assertIn("7. Bot box never holds SoR, live extracts, or attachment blobs.", text)
        self.assertIn("9. No SMB/NFS sqlite. Stage extract trees locally.", text)
        self.assertFalse((ROOT / "CRM-log" / "20260924-1201-mini-only-writer-user.md").is_file())
        hay = text.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)
        self.assertNotIn("cutover is done", text.lower())

    def test_every_sor_writer_doc_uses_canonical_sentence(self):
        """Docs that state the SoR writer rule use the canonical sentence.

        Current-tense docs must not call rem-legacy the sole writer.
        The Heavy-05 historical sentence and the Classic MBP 8pm refuse
        labels stay allowlisted.
        """
        docs = [README]
        docs.extend(sorted((ROOT / "docs").rglob("*.md")))
        saw_rule = False
        for path in docs:
            text = path.read_text(encoding="utf-8")
            flat = " ".join(text.split()).lower()
            if not any(marker in flat for marker in RULE_MARKERS):
                continue
            saw_rule = True
            rel = str(path.relative_to(ROOT))
            self.assertIn(CANONICAL, text, msg=rel)
            scrubbed = text.replace(CANONICAL, "")
            scrubbed = scrubbed.replace(HISTORICAL_REM_LEGACY_SOLE, "")
            scrubbed = scrubbed.replace(HISTORICAL_REFUSE_LABEL, "")
            match = PRESENT_REM_SOLE.search(" ".join(scrubbed.split()))
            self.assertIsNone(
                match,
                msg="%s current-tense rem-legacy sole writer: %s"
                % (rel, match.group(0) if match else ""),
            )
            self.assertNotIn("is that sole writer", flat, msg=rel)
            self.assertNotIn("MBP today", text, msg=rel)
            self.assertNotIn("PR-5 flips", text, msg=rel)
        self.assertTrue(saw_rule)

        att0 = (ROOT / "docs" / "attachments" / "ATT-0-design.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(HISTORICAL_REM_LEGACY_SOLE, att0)
        self.assertIn(CANONICAL, att0)
        self.assertNotIn("one Mac mini is the sole writer", att0)
        design = (ROOT / "docs" / "unified-search-design.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Mini retrieve stays on a copy DB until PR-5", design)
        self.assertIn("PR-5 cutover / RunAtLoad stays gated", design)
        self.assertIn("MailArchive-mini/", design)
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("filled the older long-body band", guide)
        self.assertIn("held the live-database lock", guide)
        self.assertNotIn("is filling", guide)
        for path in (
            OPS,
            ROOT / "docs" / "MAILROOM.md",
            ROOT / "docs" / "embed-backfill.md",
        ):
            raw = path.read_text(encoding="utf-8")
            self.assertIn(HISTORICAL_REFUSE_LABEL, raw, msg=path.name)
            self.assertIn(
                "may hold the write lock while it runs", raw, msg=path.name
            )
            self.assertIn(
                "do not run the MBP", raw, msg=path.name
            )
            self.assertIn(CANONICAL, raw, msg=path.name)
        readme = README.read_text(encoding="utf-8")
        ops = OPS.read_text(encoding="utf-8")
        for line in readme.splitlines():
            if line.startswith("Mini SoR (sole writer) vs MBP non-writer"):
                self.assertIn(CANONICAL, line)
            if line.startswith("`mailroom.sqlite` (") and "Recipes that use" in line:
                self.assertIn(CANONICAL, line)
                self.assertIn("Mini is the only SoR writer via the daily job only", line)
            if line.startswith("# Mini — hybrid retrieve (copy DB until PR-5"):
                self.assertIn(CANONICAL, line)
                self.assertIn("copy DB until PR-5", line)
                self.assertIn("Mini is the only SoR writer via the daily job only", line)
        ops_index = next(
            line
            for line in ops.splitlines()
            if line.startswith("Mini SoR (sole writer) vs MBP non-writer")
        )
        self.assertIn(CANONICAL, ops_index)
        self.assertIn("No MBP writers against SoR", ops_index)
        self.assertNotIn("rollback/read", ops)
        hay = "\n".join(
            path.read_text(encoding="utf-8") for path in docs
        ).replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)


if __name__ == "__main__":
    unittest.main()
