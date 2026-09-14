#!/usr/bin/env python3
"""KOO-33 Factory docs: one combined PR per batch (or stacked).

Docs/tests contract only. No live Mac writers. No live classify.
No live IMAP. No live SoR DB. No MailArchive. No Keychain reads.
No rem-legacy writer. No live machine SSH.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "docs" / "ops-terminal.md"
README = ROOT / "README.md"

PRIVACY_NEEDLES = ("/Users/", "@me.com", "@icloud.com")
HEADING = "## Factory docs batches (one combined PR per batch or stacked)"
ALLOWED_FORBID = (
    "forbid parallel prs that all edit the same shared docs files"
)
ALLOWED_FAIL_CLOSED = (
    "if a factory docs batch would open parallel prs that all edit "
    "the same shared docs files, do not open them"
)
ALLOWED_DO_NOT_OPEN = "do not open parallel same-file docs prs"


class FactoryDocsOneCombinedPrPerBatchOpsTests(unittest.TestCase):
    def test_contract_locks_one_combined_pr_per_batch_language(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        self.assertIn(HEADING.lstrip("# ").strip(), raw)
        self.assertIn("factory docs batches = one combined PR per batch", text)
        self.assertIn("OR stacked branches", raw)
        self.assertIn("forbid parallel PRs", text)
        self.assertIn("same shared docs files", text)
        self.assertIn("docs/ops-terminal.md + README", text)
        self.assertIn("Do not open parallel same-file docs PRs", raw)
        self.assertIn("one combined PR", text)
        self.assertIn("stacked branches", text)
        self.assertIn(
            "Fail closed: if a factory docs batch would open parallel PRs",
            raw,
        )
        self.assertIn("do not open them", text)
        self.assertIn("Use one combined PR per batch, or stacked branches", raw)
        self.assertIn("MBP and Mini only", text)
        self.assertIn("Never a login, home path, or email", text)
        self.assertIn("docs/tests only", text)
        self.assertIn("does not run live Mac writers", text)
        self.assertIn("does not run live classify", text)
        self.assertIn("does not run live IMAP", text)
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
        self.assertIn("Factory docs batches", raw)
        self.assertIn("one combined PR per batch", text)
        self.assertIn("stacked branches", text)
        self.assertIn("forbid parallel PRs", text)
        self.assertIn("same shared docs files", text)
        self.assertIn("docs/ops-terminal.md + README", text)
        hay = raw.replace("/Users/<operator>/", "")
        for needle in PRIVACY_NEEDLES:
            self.assertNotIn(needle, hay)

    def test_fail_closed_no_parallel_same_file_docs_prs(self):
        raw = OPS.read_text(encoding="utf-8")
        text = " ".join(raw.split())
        low = text.lower()
        scrubbed = low
        for allowed in (
            ALLOWED_FORBID,
            ALLOWED_FAIL_CLOSED,
            ALLOWED_DO_NOT_OPEN,
        ):
            scrubbed = scrubbed.replace(allowed, "")
        self.assertIn(
            "Fail closed: if a factory docs batch would open parallel PRs",
            raw,
        )
        self.assertIn("same shared docs files", text)
        self.assertIn("do not open them", text)
        self.assertIn("Use one combined PR per batch, or stacked branches", raw)
        self.assertIn("Do not open parallel same-file docs PRs", raw)
        self.assertNotIn("open parallel same-file docs prs is ok", scrubbed)
        self.assertNotIn(
            "parallel prs that all edit the same shared docs files are ok",
            scrubbed,
        )
        self.assertNotIn("skip one combined pr per batch", low)
        self.assertNotIn("parallel same-file docs prs by default", low)
        self.assertNotIn("this gate runs live mac writers", low)
        self.assertNotIn("this document starts mac writers", low)
        self.assertNotIn("this gate starts mac writers", low)


if __name__ == "__main__":
    unittest.main()
