"""Test double for meta_fill. Prints a canary that the runner must drop."""

import os
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
    if (not apply) and os.environ.get("ATT0_FAKE_PROBE_RC"):
        sys.stderr.write("error: probe failed\n")
        return int(os.environ["ATT0_FAKE_PROBE_RC"])
    sys.stdout.write("CANARY-SECRET-VALUE\n")
    if os.environ.get("ATT0_FAKE_FALLBACK") == "1":
        sys.stdout.write("warning: falling back to legacy item\n")
    sys.stdout.write("att0 meta fill\n")
    sys.stdout.write("dry_run=%s\n" % (0 if apply else 1))
    sys.stdout.write("messages=0\n")
    sys.stdout.write("errors=992\n")
    sys.stdout.write("capped=0\n")
    sys.stdout.write("eligible=992\n")
    sys.stdout.write("bytes_stored=0\n")
    sys.stdout.write("uidvalidity_mismatch=0\n")
    sys.stdout.write("curl_failures=[]\n")
    sys.stdout.write("parts_truncated=1\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
