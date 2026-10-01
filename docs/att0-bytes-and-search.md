# ATT-0 bytes and search

Measured on 2026-09-28. These are the counts and the fetcher behavior from that night. The fetcher source was not in the Mini drop, so this page records the measurement and does not add that source.

This commit adds `scripts/attachment_embed.py` and replaces `scripts/semantic_search.py`. Paths below use `/Users/<operator>/MailArchive`.

## Limits of this record

- The embed finish line was not provided.
- `ask_mail.py` from the Mini was not in this drop. This commit does not change that file.
- `att0_fetch_store.py`, its test, and `att0_l1.patched-20260928.sh` were not in this drop.
- The 2026-09-28 plist arguments were not re-read.
- Chunk text is not copied into the Qwen prompt.
- The database, the wal file, the shm file, and the att0-bytes tree are not in this commit.

## Fetcher

L1 metadata was already in `attachments` before this run. `meta_fill.py` does not store part bytes.

`att0_fetch_store.py` fetches one IMAP part with `BODY.PEEK`, not the whole message. Mailbox names with a space are quoted. A folder with non-ASCII characters is modified UTF-7 first. `EXAMINE` is read-only. There is no `STORE` and no `EXPUNGE`.

Bytes go to `/Users/<operator>/MailArchive/att0-bytes/<sha256 first 2 hex>/<sha256>`. There is no blob column. `attachments.sha256` stays empty until the file and the extract row commit together.

After a live apply the script closes sqlite and runs `/usr/bin/sqlite3` with `.filectrl persist_wal 1` then `PRAGMA journal_mode=WAL`. Apple's sqlite drops the wal and shm sidecars on last close if this is skipped, and the next `-readonly` open fails.

Live apply requires `--allow-mailroom-sqlite` and `with_writer_lock.py --purpose att0-part-bytes`. The default is dry-run. `--max-parts` defaults to 20. `--timeout` defaults to 30. `0` means no cap.

IMAP host and user come from `MAILROOM_IMAP_HOST` and `MAILROOM_IMAP_USER`. The password comes from the existing Keychain helper. The summary line does not print the user, the password, subjects, or folder names.

`extract.py` and `chunk.py` are called as they are. They are not modified. v1 does not OCR. Encrypted PDFs stay `skip_encrypted`. Images, audio, and video are not fetched.

## Counts

Chunk inserts are supposed to update `attachment_chunks_fts` by the existing triggers. Measured after the run: `attachment_chunks` 31491, `attachment_chunks_fts` 31491. `MATCH 'the'` returned 15238.

PDF counts the same night: 5456 saved, 40 not saved. Those 40 total 114341245 bytes.

Extract status: ok 4007, skip_ocr 882, skip_encrypted 214, unsupported 375, skip_image 100, capped 2.

Filenames are empty. The metadata fill and this runner leave `attachments.filename` NULL unless `--store-filenames` was used, and it was not. A hit is tied to the email by `attachments.message_id` = `messages.id`. From there the message has `date_utc`, `from_addr`, `subject`, `folder`, and `uid`.

## Ask path

`ask_mail.py` calls `search_attachments`. On 2026-09-28 the server label was `com.mailroom.ask-mail-serve`, bound at `127.0.0.1:8743`. The plist arguments from that reading were not re-read, and the pid from that reading is not current. On 2026-10-01 the same label was bootstrapped again, and a generate request returned `mode lm_studio`.

Email search and attachment search are different. Email hits go through `citations_from_hits` in `ask_mail.py`. The 2026-09-28 note placed that definition at line 243 of the Mini file, and `load_mail_data` at line 291. That Mini file was not in this drop. Attachment hits return a message id, pages, and a snippet. They are not hyperlinks. The body of `citations_from_hits` was not re-read, so this page does not describe the URL shape.

## Scripts in this commit

`scripts/attachment_embed.py` embeds `attachment_chunks` into the same database with local Ollama `qwen3-embedding:8b`, 1024 dimensions. It does not write `message_embeddings` or `embedding_meta`. Vectors live in `attachment_embeddings` (`vec0`, `chunk_id` primary key, `embedding float[1024]`). `attachment_embedding_meta` is one row per chunk, keyed by `(chunk_id, model, model_version)`, model version `att0-v1`. Resume skips a chunk when that meta row exists for the same model, version, and text hash. `--dry-run` does not call Ollama and does not write. `--lock` takes the writer lock per batch, purpose `embed_batch`, around schema create and commit. The Ollama call sits outside the lock. Apple `/usr/bin/python3` cannot load sqlite-vec.

`scripts/semantic_search.py` `retrieve` imports `attachment_embed.evidence` and folds those hits into the existing RRF list. It reuses the query vector already computed for mail and does not embed the query again. A failure in that import or call is a warning on stderr, and the mail ranks stay as they were. A hit is still a `message_id`. The snippet can become the attachment chunk, with a `p.N` prefix.

## Gaps

- 40 PDFs are still unfetched.
- 882 extracts are `skip_ocr`. No OCR in v1.
- 214 are `skip_encrypted`.
- iCloud returns an empty literal for a nested part id such as `3.2` inside a `message/rfc822` part. Fetching the `message/rfc822` part itself returns the forwarded message. Some PDFs inside those messages were pulled out later by a python one-off typed into the terminal. That one-off was never saved. Nested PDFs inside forwarded messages are not handled by `att0_fetch_store.py`.
- The email-result hyperlink was not re-verified from source on 2026-09-28. A follow-up may wire attachment hits through the same link. Not this PR.
- Daily was started again earlier on 2026-09-28 and was still running at the last check (`mailroom_daily.py --skip-if-fresh`).
