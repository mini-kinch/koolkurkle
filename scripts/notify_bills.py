#!/usr/bin/env python3
"""Send the daily bills digest via Messages.app. Phone from Keychain only.

Never prints the number. Never includes codes, passwords, or full account numbers.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import mailroom_copy_db

DB = Path.home() / "MailArchive" / "mailroom.sqlite"
KEYCHAIN_SERVICE = "mailroom.notify.phone"
KEYCHAIN_ACCOUNT = "mailroom"
TZ = ZoneInfo("America/Los_Angeles")


def log(msg: str) -> None:
    print(msg, flush=True)


def keychain_phone() -> str:
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            check=False,
            capture_output=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("Keychain phone prompt timed out") from None
    if result.returncode != 0:
        raise SystemExit("Keychain mailroom.notify.phone missing or unreadable")
    phone = (result.stdout or b"").decode("utf-8", "replace").strip()
    return normalize_us_phone(phone)


def normalize_us_phone(raw: str) -> str:
    """Accept +1XXXXXXXXXX, 1XXXXXXXXXX, or XXXXXXXXXX, with spaces/dashes/parens."""
    s = (raw or "").strip()
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s[1:])
    else:
        digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise SystemExit(
        "Keychain phone is not a 10-digit US number (got %d digits). Re-store with: "
        "security add-generic-password -a mailroom -s mailroom.notify.phone -U -w"
        % len(digits)
    )


def send_imessage(phone: str, body: str) -> None:
    if '"' in body or "\\" in body:
        body = body.replace("\\", " ").replace('"', "'")
    if "\n" in body:
        body = body.replace("\n", " | ")
    script = (
        'tell application "Messages"\n'
        "set targetService to 1st service whose service type = iMessage\n"
        f'set targetBuddy to buddy "{phone}" of targetService\n'
        f'send "{body}" to targetBuddy\n'
        "end tell\n"
    )
    result = subprocess.run(
        ["osascript"],
        input=script.encode("utf-8"),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise SystemExit(f"Messages.app send failed: {err or result.returncode}")


def open_bills(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            "SELECT vendor, due_date, account_hint, amount_cents, status "
            "FROM bills WHERE status='open' ORDER BY vendor"
        )
    )


def digest(rows: list[sqlite3.Row]) -> str:
    parts = []
    for row in rows:
        bit = row["vendor"] or "unknown"
        # no account numbers in SMS; invoice tokens only if short and not a long digit string
        hint = row["account_hint"] or ""
        if hint.startswith("invoice ") and len(hint) <= 24:
            bit += f" ({hint})"
        if row["due_date"]:
            bit += f" due {row['due_date']}"
        if row["amount_cents"] is not None:
            bit += f" ${row['amount_cents'] / 100:.2f}"
        parts.append(bit)
    text = "Bills: " + "; ".join(parts)
    if len(text) > 300:
        text = "Bills: " + ", ".join((row["vendor"] or "?") for row in rows)
        text += f" ({len(rows)} open)"
    return text


def ensure_notify_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notify_log (
          ts TEXT NOT NULL,
          message_id TEXT NOT NULL,
          channel TEXT NOT NULL,
          result TEXT,
          UNIQUE(message_id, channel)
        )
        """
    )


def already_sent(conn: sqlite3.Connection, key: str) -> bool:
    ensure_notify_log(conn)
    row = conn.execute(
        "SELECT 1 FROM notify_log WHERE message_id=? AND channel='imessage'",
        (key,),
    ).fetchone()
    return row is not None


def mark_sent(conn: sqlite3.Connection, key: str, result: str) -> None:
    ensure_notify_log(conn)
    conn.execute(
        "INSERT OR REPLACE INTO notify_log(ts, message_id, channel, result) VALUES (?,?,?,?)",
        (datetime.now(TZ).isoformat(timespec="seconds"), key, "imessage", result),
    )
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true")
    p.add_argument("--force", action="store_true", help="send even if today's digest was already logged")
    p.add_argument("--db", default=None, help="Copy sqlite (--db or $MAILROOM_DB).")
    args = p.parse_args()
    global DB
    try:
        DB = mailroom_copy_db.bind_copy_db()
    except mailroom_copy_db.CopyDbRefuse as exc:
        mailroom_copy_db.emit_db_mode("refused")
        raise SystemExit("error: %s" % exc) from exc
    log(f"opened_db={DB.name}")
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if args.test:
        phone = keychain_phone()
        send_imessage(phone, "Mailroom test. You should see this on your iPhone.")
        log("test sent")
        return 0
    if not DB.is_file():
        log(f"missing {DB}")
        return 1
    conn = sqlite3.connect(DB)
    try:
        rows = open_bills(conn)
        if not rows:
            log("quiet: no open bills")
            return 0
        key = f"bills-{today}"
        if already_sent(conn, key) and not args.force:
            log("already sent today")
            return 0
        body = digest(rows)
        phone = keychain_phone()
        send_imessage(phone, body)
        mark_sent(conn, key, "ok")
        log(f"sent {len(rows)} open bills")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
