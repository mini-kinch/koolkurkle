-- ATT-0 attachment lane. FTS5 over extracted chunk text.
-- Idempotent. Does not ALTER or DROP pre-existing tables.
-- Does not create vector tables. Not applied by this repository.

CREATE TABLE IF NOT EXISTS attachments (
  attachment_id INTEGER PRIMARY KEY,
  message_id TEXT NOT NULL,
  part_id TEXT NOT NULL,
  filename TEXT,
  mime TEXT,
  size INTEGER,
  sha256 TEXT,
  status TEXT NOT NULL,
  FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attachments_message_part
  ON attachments(message_id, part_id);

CREATE INDEX IF NOT EXISTS idx_attachments_sha256
  ON attachments(sha256);

CREATE TABLE IF NOT EXISTS attachment_extracts (
  extract_id INTEGER PRIMARY KEY,
  attachment_id INTEGER NOT NULL,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  text TEXT,
  page_count INTEGER,
  status TEXT NOT NULL,
  error TEXT,
  timings TEXT,
  FOREIGN KEY (attachment_id) REFERENCES attachments(attachment_id)
);

CREATE INDEX IF NOT EXISTS idx_attachment_extracts_attachment_id
  ON attachment_extracts(attachment_id);

CREATE TABLE IF NOT EXISTS attachment_chunks (
  chunk_id INTEGER PRIMARY KEY,
  extract_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  page_start INTEGER,
  page_end INTEGER,
  text TEXT NOT NULL,
  FOREIGN KEY (extract_id) REFERENCES attachment_extracts(extract_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attachment_chunks_extract_index
  ON attachment_chunks(extract_id, chunk_index);

CREATE VIRTUAL TABLE IF NOT EXISTS attachment_chunks_fts USING fts5(
  text,
  content='attachment_chunks',
  content_rowid='chunk_id',
  tokenize='unicode61'
);
