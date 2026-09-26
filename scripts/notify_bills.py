#!/usr/bin/env python3
"""Send the daily bills digest via Messages.app. Phone from Keychain only.

Never prints the number. Never includes codes, passwords, or full account numbers.
A failed read raises KeychainPhoneError and does not try another source.
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
# Absolute path. No PATH lookup and no environment override.
SECURITY_BIN = "/usr/bin/security"
_SECURITY_TIMEOUT_SEC = 45
TZ = ZoneInfo("America/Los_Angeles")


class KeychainPhoneError(SystemExit):
    """Classified Keychain read failure. The message never includes the value."""

    def __init__(self, kind: str, returncode: int | None, detail: str) -> None:
        self.kind = kind
        self.returncode = returncode
        self.detail = detail
        if returncode is None:
            rc_text = "none"
        else:
            rc_text = str(int(returncode))
        SystemExit.__init__(
            self,
            "Keychain %s %s (security rc=%s): %s"
            % (KEYCHAIN_SERVICE, kind, rc_text, detail),
        )


def log(msg: str) -> None:
    print(msg, flush=True)


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data


def classify_security_failure(returncode: int, stderr: str) -> str:
    """Map a ``security`` failure to a class. Does not look at stdout."""
    text = stderr or ""
    lower = text.lower()
    if (
        "-25308" in text
        or "errsecinteractionnotallowed" in lower
        or "user interaction is not allowed" in lower
        or "interaction with the security server is not allowed" in lower
        or "interaction is not allowed" in lower
    ):
        return "interaction_not_allowed"
    if (
        "could not be found" in lower
        or "the item cannot be found" in lower
        or "errsecitemnotfound" in lower
        or "-25300" in text
    ):
        return "item_not_found"
    if (
        "keychain is locked" in lower
        or "errsecauthfailed" in lower
        or "-25293" in text
        or "passphrase you entered is not correct" in lower
        or "authorization and/or authentication failed" in lower
        or "authentication failed" in lower
    ):
        return "keychain_locked"
    if returncode == 44:
        return "item_not_found"
    if returncode == 36:
        return "interaction_not_allowed"
    if returncode == 51:
        return "keychain_locked"
    return "other"


def _redact_secret(text: str, secret: str) -> str:
    """Drop the Keychain value from stderr. Keep short OSStatus text."""
    raw = (text or "").replace("\x00", "")
    secret = (secret or "").strip()
    secret_digits = re.sub(r"\D", "", secret)
    windows: tuple[str, ...] = ()
    if len(secret_digits) >= 4:
        windows = tuple(
            secret_digits[start : start + 4]
            for start in range(0, len(secret_digits) - 3)
        )

    def scrub_token(token: str) -> str:
        if secret and secret in token:
            token = token.replace(secret, "[redacted]")
        digits = re.sub(r"\D", "", token)
        if secret_digits and secret_digits in digits:
            return "[redacted]"
        if len(digits) >= 7:
            return "[redacted]"
        for window in windows:
            if window in digits or window in token:
                return "[redacted]"
        return token

    parts = re.split(r"(\s+)", raw)
    redacted = []
    for part in parts:
        if part == "" or part.isspace():
            redacted.append(part)
        else:
            redacted.append(scrub_token(part))
    collapsed = " ".join("".join(redacted).split())
    if len(collapsed) > 240:
        return collapsed[:240] + "..."
    return collapsed


def _keychain_error(kind: str, returncode: int | None, stderr: str, secret: str) -> None:
    detail = _redact_secret(stderr, secret) or "no stderr"
    raise KeychainPhoneError(kind, returncode, detail) from None


def keychain_phone() -> str:
    try:
        result = subprocess.run(
            [
                SECURITY_BIN,
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_SECURITY_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        _keychain_error("other", None, _as_text(exc.stderr), _as_text(exc.stdout))
    except OSError:
        raise KeychainPhoneError(
            "other", None, "security binary could not be executed"
        ) from None
    secret = _as_text(result.stdout)
    stderr = _as_text(result.stderr)
    if result.returncode != 0:
        kind = classify_security_failure(int(result.returncode), stderr)
        _keychain_error(kind, int(result.returncode), stderr, secret)
    return normalize_us_phone(secret.strip())


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


if __name__ == "__main__":
    sys.exit(main())
