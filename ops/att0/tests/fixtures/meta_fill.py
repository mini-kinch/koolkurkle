"""Test double for meta_fill. Prints a canary that the runner must drop."""

import os
import sqlite3
import sys
import time


def main(argv):
    if "--db" not in argv:
        sys.stderr.write("error: --db required\n")
        return 2
    db = argv[argv.index("--db") + 1]
    apply = "--apply" in argv
    live = "/dryrun/" not in db and db.endswith("/mailroom.sqlite")
    if apply and live and os.environ.get("ATT0_FAKE_A3_HANG") == "1":
        time.sleep(30)
    if apply and live and os.environ.get("ATT0_FAKE_A3_RC"):
        sys.stderr.write("error: fill failed\n")
        return int(os.environ["ATT0_FAKE_A3_RC"])
    sys.stdout.write("CANARY-SECRET-VALUE\n")
    if os.environ.get("ATT0_FAKE_FALLBACK") == "1":
        sys.stdout.write("warning: falling back to legacy item\n")
    # Same printer as scripts/attachments/meta_fill.py format_report.
    # unscanned_all on the seeded db is 1063, and 992+44+27 = 1063.
    src = os.environ.get("ATT0_REAL_META_FILL", "")
    if not src:
        sys.stderr.write("error: ATT0_REAL_META_FILL unset\n")
        return 2
    import importlib.util
    spec = importlib.util.spec_from_file_location("att0_real_meta_fill", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = {
        "dry_run": not apply,
        "source": "imap",
        "db_basename": "mailroom.sqlite",
        "messages": 0,
        "parts": 0,
        "has_attachments": 0,
        "filenames": 0,
        "bytes_stored": 0,
        "scanned": 1063,
        "stopped": "",
        "capped": 0,
        "errors": 1063,
        "eligible": 1063,
        "skipped": 0,
        "partial": False,
        "partial_banner": "",
        "parts_truncated": 1,
        "uidvalidity_mismatch": 0,
        "curl_failures": (
            ["imap login failed"]
            if apply and (not live) and os.environ.get("ATT0_FAKE_REH_CURL") == "1"
            else []
        ),
        "literal_dropped": 0,
        "literal_truncated": 0,
        "literal_folders": [],
    }
    sys.stdout.write(mod.format_report(report))
    if apply:
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE messages SET has_attachments=1 WHERE present_on_server=1"
        )
        conn.commit()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
