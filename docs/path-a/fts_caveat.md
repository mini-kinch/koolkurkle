# FTS caveat (Path A paste / wire)

## Query wording
- Prefer **"American Express"** (multi-word) over bare **"Amex"**.
- SQLite FTS multi-token queries are AND-combined; bare `Amex` can **zero** the pack even when SoR SQL finds American Express alerts (proven 2026-09-24: `Amex Low Balance 2015` → 0 hits; `American Express low balance 2015` → hits).

## Top-k vs full SQL count
- `ask_mail_paste.sh` / wire default **`ASK_MAIL_PASTE_K=20`** (raised from 8 on 2026-09-24 so Amex-class multi-hit packs keep real hits instead of ALPA/Re-Fwd noise).
- Wire surfaces **top-k FTS/RRF hits**, not a full SQL COUNT.
- Example (Amex “Low Balance Alert” 2015): precise SoR SQL → **6** rows; FTS pack at old k=8 → only **3** of those 6 message_ids (noise filled other slots). At **k≥20** FTS returns all 6.
- Paste/wire apply **no** folder filter and **no** year/date filter unless `--after`/`--before` are passed to `ask_mail.py`.

## Rem-safe retrieve
- Paste path uses `sqlite3 "$SOR" ".backup '$SNAP'"` then queries the snap only (no SoR writes; `ask_audit` not on live SoR).
