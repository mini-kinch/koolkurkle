#!/usr/bin/env python3
"""Format ask_mail --json hits into a DATA + QUESTION paste block."""
from __future__ import annotations

import json
import re
import sys


def scrub(s: str) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", s)
    # HTML tags only (no @) so an address like <user@example.com> survives
    s = re.sub(r"(?is)</?[a-zA-Z][^>@]*>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if "{" in s:
        head = s.split("{", 1)[0].strip(" ;,-")
        for mark in ("@media", "@font-face", "@import"):
            i = head.find(mark)
            if i >= 0:
                head = head[:i].strip(" ;,-")
        if len(head) >= 8:
            s = head
    for mark in ("@media", "@font-face", "@import"):
        i = s.find(mark)
        if i >= 0:
            s = s[:i].strip(" ;,-")
    return s.strip()


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    data = json.load(sys.stdin)
    hits = data.get("hits") or []
    print("DATA:")
    if not hits:
        print("(no hits)")
    else:
        for i, h in enumerate(hits, 1):
            date = scrub(str(h.get("date") or h.get("date_utc") or ""))
            frm = scrub(str(h.get("from") or h.get("from_addr") or ""))
            subj = scrub(str(h.get("subject") or ""))
            snip = scrub(str(h.get("snippet") or ""))[:220]
            print("%d. date: %s" % (i, date))
            print("   from: %s" % frm)
            print("   subject: %s" % subj)
            print("   snippet: %s" % snip)
    print()
    print("QUESTION:")
    print(query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
