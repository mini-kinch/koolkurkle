#!/usr/bin/env python3
"""Rules-first header classifier for imap-live rows.

Copy-only bind goes through mailroom_copy_db.child_main, which calls
mailroom_copy_db.bind_copy_db(). CopyDbRefuse leads to
emit_db_mode("refused"). The log line is opened_db=<basename> so a
home directory is not printed. Unset / mailroom.sqlite refuse (fail
closed). No silent SoR default.

Default (no --folder): source='imap-live' AND lane IS NULL in every
folder, one pass. --folder X narrows to that folder. --all requires
an explicit --folder. Any --source other than imap-live is refused
(the jsonl archive must not be reclassified).

Personal lists load from $MAILROOM_CLASSIFY_RULES, or from
classify_rules.local.json next to the DB. Missing file: empty lists
and one line classify_rules=none. A malformed file refuses before
any write. Sent Messages and Sent never keep urgent=1.

No IMAP. No Keychain. No network.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from email.header import decode_header, make_header
from pathlib import Path

from mailroom_copy_db import bind_copy_db, child_main
from refuse_destructive import (
    DestructiveRefuse,
    refuse_destructive_cli,
    refuse_imap_purge_cli,
)
import mailroom_copy_db

__all__ = ["bind_copy_db", "main", "classify", "load_rules"]

ALLOWED_SOURCE = "imap-live"
GENERIC_FOLDERS = frozenset(
    (
        "INBOX",
        "Junk",
        "Newsletters",
        "Sent Messages",
        "Sent",
        "Deleted Messages",
        "Drafts",
        "Archive",
    )
)
SENT_FOLDERS = frozenset(("Sent Messages", "Sent"))
RULE_KEYS = (
    "property_keep",
    "lawsuit_extra",
    "family_addrs",
    "lawsuit_domains",
    "money_addrs",
    "marketing_domains",
    "pcn_keywords",
)
_RULE_KEY_SET = frozenset(RULE_KEYS)
_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")

AUTH_RE = re.compile(
    r"(?i)("
    r"\bverification code\b|\bone[-\s]?time (?:code|password|passcode)\b|\bOTP\b|\b2FA\b|"
    r"\bsecurity code\b|\bpassword reset\b|\bverify your (?:email|account|identity)\b|"
    r"\bApple ID\b|\bApple Account was used to sign in\b|\bsign[-\s]?in (?:code|alert|request)\b|"
    r"\blogin alert\b|\bnew sign[-\s]?in\b"
    r")"
)
MONEY_SUBJ_RE = re.compile(
    r"(?i)(statement ready|e-?statement|payment due|invoice|tax document|"
    r"1099|W-2|payroll|monthly statement|energy bill|your (?:latest )?energy bill|"
    r"credit card statement|explanation of benefits|\bEOB\b|\belectronic MSN\b|shop card|"
    r"\byour bill is ready\b|\boutstanding invoices\b)"
)
BULK_SUBJ_RE = re.compile(
    r"(?i)(unsubscribe|newsletter|promo|% off|deal of|open for bidding|don't miss|"
    r"weekly warehouse|wildfire season|happy anniversary|privacy notice|entertainment|"
    r"15% off|member only|ends tomorrow|labor day savings|job-list essentials|"
    r"substack\.com|the hidden secrets)"
)
OPS_SUBJ_RE = re.compile(
    r"(?i)(tracking|shipped|delivered|itinerary|boarding pass|reservation|"
    r"appointment|safety recall|reduce your use)"
)
_GENERIC_LAWSUIT_RE = re.compile(r"(?i)lawsuit|written discovery")
_PCN_PREFIX_RE = re.compile(r"^pcn\s*-")
_RECALL_RE = re.compile(r"(?i)safety recall")


class RulesRefuse(RuntimeError):
    """Rules file failed validation. Message never includes list values."""


class _AnyPattern(object):
    """search() is true when any compiled fragment matches."""

    def __init__(self, patterns):
        self.patterns = tuple(patterns)

    def search(self, text):
        if not text:
            return None
        for cre in self.patterns:
            found = cre.search(text)
            if found:
                return found
        return None


def _generic_lawsuit():
    return _AnyPattern((_GENERIC_LAWSUIT_RE,))


class Rules(object):
    """Compiled personal lists. Empty when the local file is missing."""

    def __init__(self):
        self.property_res = ()
        self.lawsuit_re = _generic_lawsuit()
        self.family_addrs = frozenset()
        self.lawsuit_domains = frozenset()
        self.money_addrs = frozenset()
        self.marketing_domains = frozenset()
        self.pcn_keywords = ()
        self.entry_count = 0


def decode_mime(value):
    if not value:
        return ""
    raw = value.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def email_addr(from_addr):
    m = re.search(r"<([^>]+)>", from_addr or "")
    return (m.group(1) if m else (from_addr or "")).strip().lower()


def _domain(addr):
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].strip().lower()


def _slug(text):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip()).strip("_").lower()
    return slug or "keyword"


def _string_list(data, key):
    if key not in data:
        return []
    value = data[key]
    if not isinstance(value, list):
        raise RulesRefuse("classify_rules: %s must be a list of strings" % key)
    out = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise RulesRefuse(
                "classify_rules: %s[%s] must be a non-empty string" % (key, index)
            )
        out.append(item)
    return out


def _compile_one(pattern, key, index):
    try:
        return re.compile(pattern)
    except re.error:
        raise RulesRefuse(
            "classify_rules: %s[%s] is not a valid regex" % (key, index)
        )


def _compile_lawsuit(extras):
    patterns = [_GENERIC_LAWSUIT_RE]
    for index, item in enumerate(extras):
        patterns.append(_compile_one(item, "lawsuit_extra", index))
    return _AnyPattern(patterns)


def parse_rules(data):
    """Validate a decoded JSON object. Raises RulesRefuse. Returns (Rules, count)."""
    if not isinstance(data, dict):
        raise RulesRefuse("classify_rules: expected a JSON object")
    for key in data:
        if key not in _RULE_KEY_SET:
            shown = key if isinstance(key, str) and _KEY_RE.match(key) else "invalid"
            raise RulesRefuse("classify_rules: unknown key %s" % shown)
    lists = {key: _string_list(data, key) for key in RULE_KEYS}
    rules = Rules()
    rules.property_res = tuple(
        _compile_one(item, "property_keep", index)
        for index, item in enumerate(lists["property_keep"])
    )
    rules.lawsuit_re = _compile_lawsuit(lists["lawsuit_extra"])
    rules.family_addrs = frozenset(item.strip().lower() for item in lists["family_addrs"])
    rules.lawsuit_domains = frozenset(
        item.strip().lower().rstrip(".") for item in lists["lawsuit_domains"]
    )
    rules.money_addrs = frozenset(item.strip().lower() for item in lists["money_addrs"])
    rules.marketing_domains = frozenset(
        item.strip().lower().rstrip(".") for item in lists["marketing_domains"]
    )
    rules.pcn_keywords = tuple(
        (item.strip().lower(), "pcn_%s" % _slug(item)) for item in lists["pcn_keywords"]
    )
    count = sum(len(lists[key]) for key in RULE_KEYS)
    rules.entry_count = count
    return rules, count


def rules_path_for(db):
    raw = (os.environ.get("MAILROOM_CLASSIFY_RULES") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(db).parent / "classify_rules.local.json"


def load_rules(path):
    """Return (rules, status, count). status is 'none' or 'loaded'.

    A missing file is status 'none' with empty lists. A present but
    invalid file raises RulesRefuse and must not be applied.
    """
    path = Path(path)
    if not path.exists():
        rules = Rules()
        return rules, "none", 0
    if not path.is_file():
        raise RulesRefuse("classify_rules: not a file")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise RulesRefuse("classify_rules: unreadable")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RulesRefuse("classify_rules: invalid JSON")
    rules, count = parse_rules(data)
    return rules, "loaded", count


def _property_match(rules, subj, from_addr):
    for cre in rules.property_res:
        if cre.search(subj) or cre.search(from_addr or ""):
            return True
    return False


def _pcn_rule(rules, subj):
    if not subj or not _PCN_PREFIX_RE.match(subj):
        return None
    low = subj.lower()
    for needle, name in rules.pcn_keywords:
        if needle in low:
            return name
    return None


def classify(from_addr, subject, folder=None, rules=None):
    """Return lane, junk, urgent, rule. Rule ORDER is the contract."""
    if rules is None:
        rules = Rules()
    addr = email_addr(from_addr)
    subj = subject or ""
    blob = "%s %s" % (from_addr, subj)
    folder = folder or ""
    domain = _domain(addr)
    if folder == "Newsletters":
        return "bulk", 1, 0, "folder_newsletters"
    if folder == "Junk" and not MONEY_SUBJ_RE.search(subj) and not AUTH_RE.search(blob):
        if OPS_SUBJ_RE.search(subj):
            return "ops", 0, 1 if _RECALL_RE.search(subj) else 0, "junk_ops"
    if AUTH_RE.search(blob):
        return "auth", 0, 0, "p10_auth"
    if domain and domain in rules.marketing_domains:
        return "bulk", 1, 0, "marketing_domain"
    if _property_match(rules, subj, from_addr):
        urgent = 1 if rules.lawsuit_re.search(subj) else 0
        return "people", 0, urgent, "property_keep"
    if addr and addr in rules.family_addrs:
        return "people", 0, 0, "family"
    if rules.lawsuit_re.search(subj) or (domain and domain in rules.lawsuit_domains):
        return "people", 0, 1, "lawsuit_people"
    if OPS_SUBJ_RE.search(subj):  # ops before money
        return "ops", 0, 1 if _RECALL_RE.search(subj) else 0, "p60_ops"
    if MONEY_SUBJ_RE.search(subj) or (addr and addr in rules.money_addrs):
        if BULK_SUBJ_RE.search(subj):
            return "bulk", 1, 0, "money_subj_but_promo"
        return "money", 0, 0, "p20_money"
    if BULK_SUBJ_RE.search(subj):
        return "bulk", 1, 0, "p70_bulk"
    pcn = _pcn_rule(rules, subj)
    if pcn:
        return "people", 0, 0, pcn
    if folder == "Junk":
        return "bulk", 1, 0, "folder_junk"
    return "unknown", 0, 0, "p90_unknown"


def ensure_audit(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audit (ts TEXT NOT NULL, actor TEXT NOT NULL, tool TEXT,
      message_id TEXT, lane TEXT, action TEXT NOT NULL, detail TEXT)"""
    )


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            "Rules-first header classifier for imap-live rows with lane IS NULL. "
            "Copy DB only (fail closed). Personal lists stay in a local JSON file."
        )
    )
    parser.add_argument("--source", default=ALLOWED_SOURCE)
    parser.add_argument(
        "--folder",
        default=None,
        help="Classify this folder only. Default: every folder.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include rows that already have a lane. Requires --folder.",
    )
    parser.add_argument("--db", default=None)
    return parser


def _bind_db(argv):
    """child_main bind, then opened_db=<basename> (not the full path)."""
    held = io.StringIO()
    with redirect_stdout(held):
        rc = child_main(argv, name="classify")
    if rc != 0:
        return None, rc
    db = Path(os.environ["MAILROOM_DB"])
    sys.stdout.write("opened_db=%s\n" % db.name)
    sys.stdout.flush()
    return db, 0


def _emit_rules_status(status, count):
    if status == "none":
        print("classify_rules=none", flush=True)
        return
    print("classify_rules=loaded n=%s" % count, flush=True)


def _emit_summary(folder_n, folder_lanes, counts, total, auth, urgent, unknown, mode):
    for folder in sorted(folder_n):
        bits = ["folder=%s" % folder, "n=%s" % folder_n[folder]]
        lanes = folder_lanes[folder]
        for lane in sorted(lanes):
            bits.append("%s=%s" % (lane, lanes[lane]))
        print(" ".join(bits), flush=True)
    ordered = {key: counts[key] for key in sorted(counts)}
    print("counts %s n=%s" % (ordered, total), flush=True)
    print("auth %s urgent %s unknown %s" % (auth, urgent, unknown), flush=True)
    print("mode %s" % mode, flush=True)


def _select_sql(folder, include_all):
    sql = (
        "SELECT id, from_addr, subject, lane, folder "
        "FROM messages WHERE source=?"
    )
    params = [ALLOWED_SOURCE]
    if folder is not None:
        sql += " AND folder=?"
        params.append(folder)
    if not include_all:
        sql += " AND lane IS NULL"
    sql += " ORDER BY date_utc DESC"
    return sql, params


def _apply_rows(conn, rows, rules):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    folder_n = {}
    folder_lanes = {}
    counts = {}
    auth = 0
    urgent_n = 0
    unknown = 0
    for row in rows:
        frm = decode_mime(row["from_addr"])
        subj = decode_mime(row["subject"])
        folder = row["folder"] if row["folder"] is not None else ""
        lane, junk, urgent, rule = classify(frm, subj, folder, rules)
        detail = {"rule": rule, "junk": junk, "urgent": urgent}
        if folder in SENT_FOLDERS and urgent:
            urgent = 0
            detail["urgent"] = 0
            detail["sent_urgent_suppressed"] = True
        counts[lane] = counts.get(lane, 0) + 1
        folder_n[folder] = folder_n.get(folder, 0) + 1
        lanes = folder_lanes.setdefault(folder, {})
        lanes[lane] = lanes.get(lane, 0) + 1
        if lane == "auth":
            auth += 1
        if urgent:
            urgent_n += 1
        if lane == "unknown":
            unknown += 1
        conn.execute(
            "UPDATE messages SET lane=?, junk=?, urgent=?, from_addr=?, subject=? WHERE id=?",
            (lane, junk, urgent, frm, subj, row["id"]),
        )
        conn.execute(
            "INSERT INTO audit(ts,actor,tool,message_id,lane,action,detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                now,
                "cos",
                "classify.py",
                row["id"],
                lane,
                "classify",
                json.dumps(detail, sort_keys=True),
            ),
        )
    return folder_n, folder_lanes, counts, len(rows), auth, urgent_n, unknown


def _classify_db(db, args):
    try:
        rules, status, count = load_rules(rules_path_for(db))
    except RulesRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    _emit_rules_status(status, count)
    if not db.is_file():
        return 0
    mode = "all" if args.all else "null_only"
    conn = sqlite3.connect(str(db))
    try:
        conn.row_factory = sqlite3.Row
        ensure_audit(conn)
        sql, params = _select_sql(args.folder, args.all)
        rows = conn.execute(sql, params).fetchall()
        summary = _apply_rows(conn, rows, rules)
        conn.commit()
    except sqlite3.Error:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        sys.stderr.write("error: classify database write failed\n")
        return 1
    finally:
        conn.close()
    _emit_summary(*summary, mode)
    return 0


def main(argv=None):
    if argv is None:
        argv = list(sys.argv[1:])
    try:
        refuse_destructive_cli(argv)
        refuse_imap_purge_cli(argv)
    except DestructiveRefuse as exc:
        mailroom_copy_db.emit_db_mode("refused")
        sys.stderr.write("error: %s\n" % exc)
        return 2
    args = _parser().parse_args(argv)
    if args.source != ALLOWED_SOURCE:
        sys.stderr.write(
            "error: refuse --source; classify.py only accepts imap-live\n"
        )
        return 2
    if args.all and args.folder is None:
        sys.stderr.write("error: --all requires an explicit --folder\n")
        return 2
    db, rc = _bind_db(argv)
    if rc != 0 or db is None:
        return rc
    return _classify_db(db, args)


if __name__ == "__main__":
    raise SystemExit(main())
