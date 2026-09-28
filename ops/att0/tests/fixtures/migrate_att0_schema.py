"""Test double for the ATT-0 migrate. No secrets."""

import os
import sqlite3
import sys

TABLES = (
    "attachments",
    "attachment_extracts",
    "attachment_chunks",
    "attachment_meta_scans",
    "attachment_folder_uidvalidity",
)


def main(argv):
    if "--db" not in argv:
        sys.stderr.write("error: --db required\n")
        return 2
    db = argv[argv.index("--db") + 1]
    marker = db + ".migrated"
    first = not os.path.exists(marker)
    handle = open(marker, "a")
    handle.write("1\n")
    handle.close()
    forced = os.environ.get("ATT0_FAKE_MIGRATE_RC", "")
    if forced:
        return int(forced)
    legacy = "renamed_empty" if first else "unchanged"
    state = "created" if first else "exists"
    sys.stdout.write("att0 schema migrate\n")
    sys.stdout.write("user_version=1\n")
    sys.stdout.write("legacy_attachments=%s\n" % legacy)
    for name in TABLES:
        sys.stdout.write("%s=%s\n" % (name, state))
    conn = sqlite3.connect(db)
    present = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='attachments'"
    ).fetchone()
    renamed = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='attachments_pr1_empty'"
    ).fetchone()
    if present and not renamed:
        conn.execute("ALTER TABLE attachments RENAME TO attachments_pr1_empty")
    conn.execute("CREATE TABLE IF NOT EXISTS attachments_pr1_empty (id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS attachment_extracts (id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS attachment_chunks (id INTEGER)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attachment_meta_scans (message_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attachment_folder_uidvalidity (folder TEXT, uidvalidity INTEGER)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS attachment_chunks_fts USING fts5(body, content='')"
    )
    present_vec = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='message_embeddings'"
    ).fetchone()
    if not present_vec:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "INSERT INTO sqlite_master(type, name, tbl_name, rootpage, sql) "
            "VALUES ('table', 'message_embeddings', 'message_embeddings', 0, "
            "'CREATE VIRTUAL TABLE message_embeddings USING vec0(embedding float[8])')"
        )
        conn.execute("PRAGMA writable_schema=RESET")
    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
