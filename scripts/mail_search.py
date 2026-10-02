
#!/usr/bin/env python3
"""Loopback page: mail hits from 127.0.0.1:8743, file hits from Apple sqlite."""
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ASK = "http://127.0.0.1:8743/ask"
DB = Path.home() / "MailArchive" / "mailroom.sqlite"
SQL = (
    "SELECT a.message_id AS message_id, COALESCE(a.filename,'') AS filename, "
    "c.page_start AS page_start, c.page_end AS page_end, c.text AS text, "
    "COALESCE(m.subject,'') AS subject, COALESCE(m.from_addr,'') AS from_addr, "
    "COALESCE(m.date_utc,'') AS date_utc, COALESCE(m.message_id_header,'') AS message_id_header "
    "FROM (SELECT rowid AS chunk_id, rank FROM attachment_chunks_fts "
    "WHERE attachment_chunks_fts MATCH %s ORDER BY rank LIMIT 8) AS hit "
    "JOIN attachment_chunks AS c ON c.chunk_id = hit.chunk_id "
    "JOIN attachment_extracts AS e ON e.extract_id = c.extract_id "
    "JOIN attachments AS a ON a.attachment_id = e.attachment_id "
    "LEFT JOIN messages AS m ON m.id = a.message_id ORDER BY hit.rank"
)
PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mail and files</title>
<style>
body{margin:0;font:16px/1.45 -apple-system,sans-serif;background:#f4f1ea;color:#1c1915}
main{max-width:820px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:22px;margin:0 0 14px} h2{font-size:15px;margin:18px 0 8px;color:#5c564c}
form{display:flex;gap:8px} input{flex:1;font:inherit;padding:10px 12px;border:1px solid #c9c1b4;border-radius:8px}
button{font:inherit;padding:10px 16px;border:0;border-radius:8px;background:#1c1915;color:#fff}
button:disabled{opacity:.55} .status{min-height:1.4em;margin:12px 0;color:#5c564c}
.err{color:#8a2b1a} article{background:#fff;border:1px solid #e2dacd;border-radius:10px;padding:12px 14px;margin:0 0 10px}
.sub{font-weight:650} .meta{color:#5c564c;font-size:14px;margin-top:2px} .snip{margin-top:8px}
a.mail{display:inline-block;margin-top:8px}
</style></head><body><main>
<h1>Mail and files</h1>
<form id="f" action="javascript:void(0)" onsubmit="return false;">
<input id="q" placeholder="Search mail and files" autocomplete="off" autofocus>
<button id="go" type="button">Ask</button></form>
<p class="status" id="status"></p><div id="out"></div>
<script>
var form=document.getElementById("f"), box=document.getElementById("q");
var status=document.getElementById("status"), out=document.getElementById("out"), go=document.getElementById("go");
function text(v){return v==null?"":String(v)}
function field(hit, names){for(var i=0;i<names.length;i++){if(hit[names[i]])return text(hit[names[i]])}return ""}
function link(art, hit){
  var raw=text(hit.message_id_header)||text(hit.mail_url);
  var low=raw.toLowerCase();
  if(low.indexOf("message://")===0) raw=raw.slice(10);
  else if(low.indexOf("message:")===0) raw=raw.slice(8);
  else if(low.indexOf("mid:")===0) raw=raw.slice(4);
  while(raw.charAt(0)==="<"||raw.charAt(0)===" ") raw=raw.slice(1);
  while(raw.length&&(raw.charAt(raw.length-1)===">"||raw.charAt(raw.length-1)===" ")) raw=raw.slice(0,-1);
  if(raw.slice(0,3).toLowerCase()==="%3c") raw=raw.slice(3);
  if(raw.slice(-3).toLowerCase()==="%3e") raw=raw.slice(0,-3);
  if(!raw||raw.indexOf("@")<0) return;
  var a=document.createElement("a");
  a.className="mail";
  a.href="message://%3C"+raw+"%3E";
  a.textContent="Open in Mail";
  art.appendChild(a);
}
function card(hit, title, meta, snip){
  var node=document.createElement("div");
  node.innerHTML='<article><div class="sub"></div><div class="meta"></div><div class="snip"></div></article>';
  var art=node.firstChild;
  art.querySelector(".sub").textContent=title;
  art.querySelector(".meta").textContent=meta;
  art.querySelector(".snip").textContent=snip;
  link(art, hit); return art;
}
function heading(label){var h=document.createElement("h2"); h.textContent=label; return h}
go.addEventListener("click", function(event){
  event.preventDefault();
  var query=box.value.trim(); if(!query)return;
  go.disabled=true; status.className="status"; status.textContent="Searching…"; out.textContent="";
  fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query:query,generate:false})})
  .then(function(r){return r.json()})
  .then(function(data){
    data=data||{}; var hits=data.hits||[], files=data.files||[];
    status.textContent=(hits.length||0)+" messages, "+(files.length||0)+" files";
    if(hits.length){out.appendChild(heading("Messages")); hits.forEach(function(hit){
      out.appendChild(card(hit, field(hit,["subject","title"])||"(no subject)",
        [field(hit,["from_addr","from","from_name"]), field(hit,["date_utc","date"])].filter(Boolean).join("  "),
        field(hit,["snippet","preview","text"])));
    })}
    if(files.length){out.appendChild(heading("Files")); files.forEach(function(hit){
      var page=hit.page_start?(hit.page_end&&hit.page_end!==hit.page_start?("pages "+hit.page_start+"-"+hit.page_end):("page "+hit.page_start)):"";
      out.appendChild(card(hit, field(hit,["filename"])||"(unnamed file)",
        [page, field(hit,["subject"])].filter(Boolean).join("  "), field(hit,["snippet"])));
    })}
    var parts=[hits.length?hits.length+" messages":"No matching messages", files.length?files.length+" files":"No matching files"];
    if(data.file_note) parts.push(data.file_note);
    if(data.searched) parts.unshift("Searched: "+data.searched);
    if(data.rewrite_note) parts.push(data.rewrite_note);
    status.textContent=parts.join(", ");
    if(!hits.length&&!files.length) status.className="status err";
  }).catch(function(err){status.textContent=status.textContent+" — "+String(err&&err.message||err); status.className="status err";
  }).then(function(){go.disabled=false});
});
</script></main></body></html>
"""


def snippet(text, query):
    hay = text or ""
    token = ""
    for part in (query or "").split():
        if part:
            token = part
            break
    if not token:
        return hay[:160]
    idx = hay.lower().find(token.lower())
    if idx < 0:
        return hay[:160]
    start = max(0, idx - 60)
    return hay[start:idx + len(token) + 80]



_LM = [None]

def _needs_qwen(text):
    filler = set("give me everything that looks like a an the my your please show find get all any of for from with in on to".split())
    toks = []
    for tok in text.split():
        word = tok.strip("?.!,").lower()
        if word:
            toks.append(word)
    if any(word in ("or", "and", "not") for word in toks):
        return True
    if any(word in filler for word in toks):
        return True
    return len(toks) > 4

def _clean_match(raw):
    text = raw or ""
    marker = "</think>"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = text.strip().strip("`").strip('"').strip("'")
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    if line.lower().startswith("search:"):
        line = line.split(":", 1)[1].strip()
    kept = []
    for tok in line.replace(",", " ").split():
        if tok.upper() in ("OR", "AND", "NOT"):
            kept.append(tok.upper())
            continue
        word = "".join(ch for ch in tok if ch.isalnum() or ch in "-_'")
        if word:
            kept.append(word)
    while kept and kept[0] in ("OR", "AND", "NOT"):
        kept.pop(0)
    while kept and kept[-1] in ("OR", "AND", "NOT"):
        kept.pop()
    return " ".join(kept)

def _lm_base_from_ask_mail():
    base = "http://127.0.0.1:1234/v1"
    path = Path.home() / "MailArchive/scripts/ask_mail.py"
    if not path.is_file():
        return base
    src = path.read_text(errors="replace")
    start = src.find("def default_lm_studio_url")
    if start < 0:
        return base
    chunk = src[start:start + 600]
    stop = chunk.find("\ndef ")
    if stop > 0:
        chunk = chunk[:stop]
    found = []
    i = 0
    while True:
        a = chunk.find("http", i)
        if a < 0:
            break
        b = a
        while b < len(chunk) and chunk[b] not in " \"'\n\r)":
            b += 1
        found.append(chunk[a:b])
        i = b
    if found:
        base = found[-1].rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base

def _lm():
    if _LM[0]:
        return _LM[0]
    base = _lm_base_from_ask_mail()
    req = Request(base + "/models", method="GET")
    with urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("data") or []
    ids = [row.get("id") or "" for row in rows if row.get("id")]
    model = ""
    for mid in ids:
        if "qwen" in mid.lower() and "embed" not in mid.lower():
            model = mid
            break
    if not model and ids:
        model = ids[0]
    if not model:
        raise RuntimeError("no model")
    _LM[0] = (base, model)
    return _LM[0]

def _ask_qwen(text):
    base, model = _lm()
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 60,
        "messages": [
            {"role": "system", "content": "Turn the user's request into one SQLite FTS5 search. Reply with only the search words. No sentence. Use capital OR between alternatives. Do not use parentheses. Drop filler such as give, me, everything, that, looks, like, a, the. Drop any date or time, such as last month or last week. The date is applied separately. You may add one close synonym of the document type as its own alternative. If they already typed search words, return those words."},
            {"role": "user", "content": text},
        ],
    }
    req = Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    content = payload["choices"][0]["message"].get("content") or ""
    if not content.strip():
        raise RuntimeError("empty")
    return content


def time_bounds(text):
    import re
    from datetime import date, timedelta
    def shift_months(day, n):
        month = day.month - n
        year = day.year
        while month <= 0:
            month += 12
            year -= 1
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        last = (nxt - timedelta(days=1)).day
        return date(year, month, min(day.day, last))
    raw = (text or "").lower()
    today = date.today()
    after = ""
    before = ""
    numbered = re.search(r"\b(?:last|past)\s+(\d+)\s+(day|week|month|year)s?\b", raw)
    if numbered:
        n = int(numbered.group(1))
        unit = numbered.group(2)
        if unit == "month":
            after = shift_months(today, n).isoformat()
        elif unit == "year":
            after = shift_months(today, n * 12).isoformat()
        else:
            after = (today - timedelta(days=n * {"day": 1, "week": 7}[unit])).isoformat()
    elif re.search(r"\b(?:last|past)\s+month\b", raw) or "within a month" in raw:
        after = shift_months(today, 1).isoformat()
    elif re.search(r"\b(?:last|past)\s+week\b", raw):
        after = (today - timedelta(days=7)).isoformat()
    elif re.search(r"\b(?:last|past)\s+year\b", raw):
        after = shift_months(today, 12).isoformat()
    elif "this month" in raw:
        after = today.replace(day=1).isoformat()
    elif "this year" in raw:
        after = "%04d-01-01" % today.year
    elif re.search(r"\btoday\b", raw):
        after = today.isoformat()
    elif re.search(r"\byesterday\b", raw):
        after = (today - timedelta(days=1)).isoformat()
        before = after
    since = re.search(r"\bsince\s+(\d{4}-\d{2}-\d{2})\b", raw)
    if since:
        after = since.group(1)
    return after, before

def _day(value):
    text = str(value or "").strip()
    return text[:10]

def keep_dated(rows, after, before):
    if not after and not before:
        return rows
    kept = []
    for hit in rows or []:
        if not isinstance(hit, dict):
            continue
        day = _day(hit.get("date") or hit.get("date_utc"))
        if not day:
            continue
        if after and day < after:
            continue
        if before and day > before:
            continue
        kept.append(hit)
    return kept

def rewrite_query(typed):
    text = (typed or "").strip()
    if not text:
        return text, ""
    if not _needs_qwen(text):
        return text, ""
    try:
        cleaned = _clean_match(_ask_qwen(text))
    except Exception:
        return text, "Qwen did not answer, so the words were searched as typed"
    if not cleaned or len(cleaned.split()) > 12:
        return text, "Qwen did not answer, so the words were searched as typed"
    return cleaned, ""

def _short(text):
    text = " ".join((text or "").split())
    if len(text) > 300:
        text = text[:300]
    return text

def _lms_bin():
    import os
    import shutil
    import subprocess
    found = shutil.which("lms")
    if found:
        return found
    candidates = [
        Path.home() / ".lmstudio/bin/lms",
        Path.home() / ".cache/lm-studio/bin/lms",
        Path("/Applications/LM Studio.app/Contents/MacOS/lms"),
        Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms"),
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    done = subprocess.run(["/usr/bin/mdfind", "-name", "lms"], capture_output=True, text=True, timeout=20)
    for line in (done.stdout or "").splitlines():
        cand = Path(line.strip())
        if cand.name == "lms" and cand.is_file():
            return str(cand)
    return ""

def _lms_json(lms, args):
    import subprocess
    done = subprocess.run([lms] + args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(_short(done.stderr or done.stdout) or ("lms failed: " + " ".join(args)))
    text = (done.stdout or "").strip()
    if not text:
        return []
    for i, ch in enumerate(text):
        if ch in "[{":
            return json.loads(text[i:])
    raise RuntimeError("lms did not return a model list")

def _walk_models(obj):
    rows = []
    if isinstance(obj, dict):
        if any(k in obj for k in ("identifier", "modelKey", "model_key", "path", "key")):
            rows.append(obj)
        for value in obj.values():
            if isinstance(value, (dict, list)):
                rows.extend(_walk_models(value))
    elif isinstance(obj, list):
        for item in obj:
            rows.extend(_walk_models(item))
    return rows

def _blob(row):
    parts = []
    for key in ("identifier", "modelKey", "model_key", "path", "key", "type", "architecture"):
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts).lower()

def _is_embed(row):
    return "embed" in _blob(row)

def _pick(row, names):
    for key in names:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

def _named_qwen():
    import os
    for env_name in ("MAILROOM_GENERATE_MODEL", "MAILROOM_LM_MODEL"):
        env = os.environ.get(env_name) or ""
        if "qwen" in env.lower():
            return env
    path = Path.home() / "MailArchive/scripts/ask_mail.py"
    if not path.is_file():
        return ""
    src = path.read_text(errors="replace")
    start = src.find("def default_generate_model")
    if start < 0:
        return ""
    chunk = src[start:start + 800]
    stop = chunk.find("\ndef ")
    if stop > 0:
        chunk = chunk[:stop]
    found = []
    quote = None
    buf = []
    for ch in chunk:
        if quote is None:
            if ch in "'\"":
                quote = ch
                buf = []
        elif ch == quote:
            word = "".join(buf)
            if "qwen" in word.lower():
                found.append(word)
            quote = None
        else:
            buf.append(ch)
    return found[-1] if found else ""

def _choose_qwen(choices, named):
    if named:
        hits = [key for key in choices if named.lower() in key.lower() or key.lower() in named.lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            choices = hits
    if len(choices) == 1:
        return choices[0]
    preferred = [key for key in choices if "3.5" in key and "9" in key]
    if len(preferred) == 1:
        return preferred[0]
    return ""

def _lm_port():
    base = _lm_base_from_ask_mail()
    number = ""
    for ch in base.split(":", 2)[-1]:
        if ch.isdigit():
            number += ch
        else:
            break
    return number or "1234"

def _http_json(method, url, body=None, timeout=30):
    from urllib.error import HTTPError
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 401:
            raise RuntimeError("LM Studio refused the request because it wants a login token. The page was not started.")
        raise RuntimeError("LM Studio returned an error. " + _short(detail or str(exc)))
    return json.loads(raw) if raw.strip() else {}

def _rows_from(payload):
    if isinstance(payload, dict):
        for key in ("data", "models"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    if isinstance(payload, list):
        return payload
    return []

def _row_id(row):
    for key in ("id", "identifier", "model", "key", "instance_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

def _row_loaded(row):
    state = str(row.get("state") or row.get("status") or "").lower()
    if state in ("loaded", "running"):
        return True
    return row.get("loaded") is True

def _model_rows(base):
    rows = []
    errors = []
    for path in ("/api/v0/models", "/api/v1/models"):
        try:
            rows.extend(_rows_from(_http_json("GET", base + path, timeout=10)))
        except Exception as exc:
            errors.append(str(exc))
    if not rows and errors:
        raise RuntimeError("Could not list models. " + _short(" ".join(errors)))
    return rows

def _qwen_serving():
    try:
        payload = _http_json("GET", "http://127.0.0.1:1234/v1/models", timeout=3)
    except Exception:
        return ""
    for row in _rows_from(payload):
        ident = _row_id(row)
        if "qwen" in ident.lower():
            return ident
    return ""

def _close_ollama():
    import subprocess
    import time
    names = ("Ollama", "ollama")
    found = []
    for name in names:
        done = subprocess.run(["/usr/bin/pgrep", "-x", name], capture_output=True, text=True)
        if done.returncode == 0 and done.stdout.strip():
            found.append(name)
    if not found:
        sys.stderr.write("Llama is not open.\n")
        return
    sys.stderr.write("Closing Ollama.\n")
    for name in found:
        subprocess.run(["/usr/bin/pkill", "-TERM", "-x", name], check=False)
    time.sleep(2)
    still = []
    for name in names:
        done = subprocess.run(["/usr/bin/pgrep", "-x", name], capture_output=True, text=True)
        if done.returncode == 0 and done.stdout.strip():
            still.append(name)
    if still:
        raise RuntimeError("Ollama is still running. The page was not started.")

def _start_qwen():
    import subprocess
    import time
    script = Path.home() / "qwen-mlx" / "qwen-chat-up.sh"
    if not script.is_file():
        raise RuntimeError("Qwen is not running, and qwen-chat-up.sh is not in the qwen-mlx folder. The page was not started.")
    sys.stderr.write("Starting Qwen.\n")
    sys.stderr.write("Wait here. The page address is printed only after Qwen is answering.\n")
    subprocess.Popen(["/bin/bash", str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        ident = _qwen_serving()
        if ident:
            return ident
        time.sleep(2)
    raise RuntimeError("Qwen did not start. The page was not started.")

def _prepare_qwen_http():
    _close_ollama()
    ident = _qwen_serving()
    if ident:
        sys.stderr.write("Qwen is already loaded: %s\n" % ident)
    else:
        ident = _start_qwen()
        sys.stderr.write("Qwen is loaded: %s\n" % ident)
    sys.stderr.write("Qwen is ready for search.\n")

def prepare_qwen():
    import subprocess
    lms = _lms_bin()
    if not lms:
        _prepare_qwen_http()
        return
    sys.stderr.write("Checking LM Studio models.\n")
    loaded = _walk_models(_lms_json(lms, ["ps", "--json"]))
    unloaded = []
    seen = set()
    for row in loaded:
        blob = _blob(row)
        if "llama" not in blob or "qwen" in blob or _is_embed(row):
            continue
        ident = _pick(row, ("identifier", "modelKey", "path", "key"))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        sys.stderr.write("Unloading Llama model: %s\n" % ident)
        done = subprocess.run([lms, "unload", ident], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        if done.returncode != 0:
            raise RuntimeError("Could not unload %s. %s" % (ident, _short(done.stderr or done.stdout)))
        unloaded.append(ident)
    if not unloaded:
        sys.stderr.write("Llama is not loaded.\n")
    loaded = _walk_models(_lms_json(lms, ["ps", "--json"]))
    qwen_loaded = [row for row in loaded if "qwen" in _blob(row) and not _is_embed(row)]
    if qwen_loaded:
        ident = _pick(qwen_loaded[0], ("identifier", "modelKey", "path", "key"))
        sys.stderr.write("Qwen is already loaded: %s\n" % ident)
    else:
        catalog = _walk_models(_lms_json(lms, ["ls", "--json"]))
        choices = []
        for row in catalog:
            if "qwen" not in _blob(row) or _is_embed(row):
                continue
            key = _pick(row, ("modelKey", "model_key", "path", "identifier", "key"))
            if key and key not in choices:
                choices.append(key)
        pick = _choose_qwen(choices, _named_qwen())
        if not pick:
            if not choices:
                raise RuntimeError("No Qwen chat model is downloaded in LM Studio. The page was not started.")
            raise RuntimeError("More than one Qwen model is downloaded. The page was not started. Models: " + " | ".join(choices))
        sys.stderr.write("Loading Qwen: %s\n" % pick)
        sys.stderr.write("Wait here. The page address is printed only after Qwen has loaded.\n")
        done = subprocess.run([lms, "load", pick, "-y"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        if done.returncode != 0:
            err = _short(done.stderr or done.stdout)
            if "-y" in err.lower() or "unknown" in err.lower():
                done = subprocess.run([lms, "load", pick], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
                err = _short(done.stderr or done.stdout)
            if done.returncode != 0:
                raise RuntimeError("Could not load %s. %s" % (pick, err))
        sys.stderr.write("Qwen is loaded.\n")
    port = _lm_port()
    sys.stderr.write("Starting the model server on port %s if it is not already up.\n" % port)
    done = subprocess.run([lms, "server", "start", "--port", port], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    text = _short((done.stdout or "") + " " + (done.stderr or ""))
    if done.returncode != 0 and "already" not in text.lower():
        raise RuntimeError("Could not start the model server. " + text)
    sys.stderr.write("Qwen is ready for search.\n")

def add_mail_headers(hits):
    import sqlite3
    if not isinstance(hits, list) or not DB.is_file():
        return
    wanted = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        header = str(hit.get("message_id_header") or "").strip()
        url = str(hit.get("mail_url") or "")
        if header or url.startswith("message:"):
            continue
        mid = str(hit.get("message_id") or hit.get("id") or "").strip()
        if mid:
            wanted.append((hit, mid))
    if not wanted:
        return
    ids = []
    for _, mid in wanted:
        if mid not in ids:
            ids.append(mid)
    conn = sqlite3.connect(str(DB), timeout=5)
    try:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT id, COALESCE(message_id_header,'') FROM messages WHERE id IN (%s)" % marks,
            ids,
        ).fetchall()
    finally:
        conn.close()
    found = {}
    for row in rows:
        found[str(row[0])] = str(row[1] or "")
    for hit, mid in wanted:
        header = found.get(mid, "")
        if header:
            hit["message_id_header"] = header




def hit_limit(text):
    import re
    raw = (text or "").lower()
    raw = re.sub(r"\b\d+\s*(day|days|week|weeks|month|months|year|years)\b", " ", raw)
    found = re.search(r"\b(?:top|first|limit|only)\s+(\d+)\b", raw)
    if not found:
        found = re.search(r"\b(\d+)\s+(?:hits|results|messages|files)\b", raw)
    if not found:
        return None
    n = int(found.group(1))
    return n if n > 0 else None

def files_on_listed(ids, after, before):
    import json
    import subprocess
    ids = [mid for mid in (ids or []) if mid][:200]
    if not ids or not DB.is_file():
        return []
    idlist = ",".join("'" + mid.replace("'", "''") + "'" for mid in ids)
    sql = (
        "SELECT a.message_id AS message_id, "
        "COALESCE(NULLIF(a.filename,''), COALESCE(a.mime,'file')) AS filename, "
        "c.page_start AS page_start, c.page_end AS page_end, COALESCE(c.text,'') AS text, "
        "COALESCE(m.subject,'') AS subject, COALESCE(m.from_addr,'') AS from_addr, "
        "COALESCE(m.date_utc,'') AS date_utc, COALESCE(m.message_id_header,'') AS message_id_header "
        "FROM attachments AS a "
        "LEFT JOIN attachment_extracts AS e ON e.attachment_id = a.attachment_id "
        "LEFT JOIN attachment_chunks AS c ON c.extract_id = e.extract_id "
        "LEFT JOIN messages AS m ON m.id = a.message_id "
        "WHERE a.message_id IN (%s) "
        "AND lower(COALESCE(a.mime,'')) NOT LIKE 'multipart/%%' "
        "AND lower(COALESCE(a.mime,'')) NOT LIKE 'image/%%' "
        "AND lower(COALESCE(a.mime,'')) NOT LIKE 'audio/%%' "
        "AND lower(COALESCE(a.mime,'')) NOT LIKE 'video/%%' "
        "AND COALESCE(a.part_id,'') GLOB '[1-9]*'"
    ) % idlist
    proc = subprocess.run(
        ["/usr/bin/sqlite3", "-json", str(DB)],
        input=sql + "\n",
        text=True,
        capture_output=True,
        timeout=20,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    hits = []
    for row in json.loads(proc.stdout):
        hits.append({
            "message_id": row.get("message_id") or "",
            "filename": row.get("filename") or "",
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "snippet": snippet(row.get("text") or "", row.get("filename") or ""),
            "subject": row.get("subject") or "",
            "from_addr": row.get("from_addr") or "",
            "date_utc": row.get("date_utc") or "",
            "message_id_header": row.get("message_id_header") or "",
        })
    return hits

def one_per_file(rows):
    out = []
    seen = set()
    for hit in rows or []:
        if not isinstance(hit, dict):
            continue
        name = str(hit.get("filename") or "").strip()
        if not name:
            name = str(hit.get("page_start") or "") + ":" + str(hit.get("snippet") or "")[:40]
        key = (str(hit.get("message_id") or ""), name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out

def wants_newest(text):
    raw = (text or "").lower()
    return ("newest" in raw) or ("most recent" in raw) or ("latest first" in raw) or ("sorted by date" in raw)

def sort_newest(rows):
    dated = []
    undated = []
    for hit in rows or []:
        if not isinstance(hit, dict):
            continue
        day = str(hit.get("date") or hit.get("date_utc") or "").strip()
        if day:
            dated.append(hit)
        else:
            undated.append(hit)
    dated.sort(key=lambda hit: str(hit.get("date") or hit.get("date_utc") or ""), reverse=True)
    return dated + undated

def content_words(text):
    skip = set("give me everything that looks like a an the my your please show find get all any of for from with in on to within last past month week year years day days this today yesterday and or not sorted sort sorting newest latest recent top first by".split())
    words = []
    for tok in (text or "").replace(",", " ").split():
        word = "".join(ch for ch in tok if ch.isalnum() or ch in "-_'")
        low = word.lower()
        if len(low) < 4 or low in skip or low.isdigit():
            continue
        if low not in words:
            words.append(low)
        if low.endswith("s") and len(low) > 4 and low[:-1] not in words:
            words.append(low[:-1])
    return words[:8]

def _file_rows_like(words, ids, after, before):
    import json
    import subprocess
    words = [w for w in (words or []) if w]
    ids = [mid for mid in (ids or []) if mid][:40]
    if not words or not ids or not DB.is_file():
        return []
    idlist = ",".join("'" + mid.replace("'", "''") + "'" for mid in ids)
    likes = []
    for word in words:
        safe = "".join(ch for ch in word if ch.isalnum())
        if not safe:
            continue
        likes.append("lower(COALESCE(c.text,'')) LIKE '%%" + safe + "%%'")
        likes.append("lower(COALESCE(a.filename,'')) LIKE '%%" + safe + "%%'")
    if not likes:
        return []
    sql = (
        "SELECT a.message_id AS message_id, COALESCE(a.filename,'') AS filename, "
        "c.page_start AS page_start, c.page_end AS page_end, c.text AS text, "
        "COALESCE(m.subject,'') AS subject, COALESCE(m.from_addr,'') AS from_addr, "
        "COALESCE(m.date_utc,'') AS date_utc, COALESCE(m.message_id_header,'') AS message_id_header "
        "FROM attachment_chunks AS c "
        "JOIN attachment_extracts AS e ON e.extract_id = c.extract_id "
        "JOIN attachments AS a ON a.attachment_id = e.attachment_id "
        "LEFT JOIN messages AS m ON m.id = a.message_id "
        "WHERE a.message_id IN (%s) AND (%s)"
    ) % (idlist, " OR ".join(likes))
    if after:
        sql += " AND COALESCE(m.date_utc,'') >= '%s'" % after.replace("'", "")
    if before:
        sql += " AND COALESCE(m.date_utc,'') <= '%sT23:59:59'" % before.replace("'", "")
    sql += ""
    proc = subprocess.run(
        ["/usr/bin/sqlite3", "-json", str(DB)],
        input=sql + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    label = " ".join(words)
    hits = []
    for row in json.loads(proc.stdout):
        hits.append({
            "message_id": row.get("message_id") or "",
            "filename": row.get("filename") or "",
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "snippet": snippet(row.get("text") or "", label),
            "subject": row.get("subject") or "",
            "from_addr": row.get("from_addr") or "",
            "date_utc": row.get("date_utc") or "",
            "message_id_header": row.get("message_id_header") or "",
        })
    return hits

def loose_terms(text):
    skip = set("give me everything that looks like a an the my your please show find get all any of for from with in on to within last past month week year this today yesterday and or not".split())
    words = []
    for tok in (text or "").replace(",", " ").split():
        word = "".join(ch for ch in tok if ch.isalnum() or ch in "-_'")
        if word and word.lower() not in skip and word not in words:
            words.append(word)
    return " OR ".join(words[:8])

def _file_rows(match, ids, after, before):
    import json
    import subprocess
    literal = "'" + match.replace("'", "''") + "'"
    idlist = ",".join("'" + mid.replace("'", "''") + "'" for mid in ids)
    sql = (
        "SELECT a.message_id AS message_id, COALESCE(a.filename,'') AS filename, "
        "c.page_start AS page_start, c.page_end AS page_end, c.text AS text, "
        "COALESCE(m.subject,'') AS subject, COALESCE(m.from_addr,'') AS from_addr, "
        "COALESCE(m.date_utc,'') AS date_utc, COALESCE(m.message_id_header,'') AS message_id_header "
        "FROM attachment_chunks_fts "
        "JOIN attachment_chunks AS c ON c.chunk_id = attachment_chunks_fts.rowid "
        "JOIN attachment_extracts AS e ON e.extract_id = c.extract_id "
        "JOIN attachments AS a ON a.attachment_id = e.attachment_id "
        "LEFT JOIN messages AS m ON m.id = a.message_id "
        "WHERE attachment_chunks_fts MATCH %s AND a.message_id IN (%s)"
    ) % (literal, idlist)
    if after:
        sql += " AND COALESCE(m.date_utc,'') >= '%s'" % after.replace("'", "")
    if before:
        sql += " AND COALESCE(m.date_utc,'') <= '%sT23:59:59'" % before.replace("'", "")
    sql += ""
    proc = subprocess.run(
        ["/usr/bin/sqlite3", "-json", str(DB)],
        input=sql + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    hits = []
    for row in json.loads(proc.stdout):
        hits.append({
            "message_id": row.get("message_id") or "",
            "filename": row.get("filename") or "",
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "snippet": snippet(row.get("text") or "", match),
            "subject": row.get("subject") or "",
            "from_addr": row.get("from_addr") or "",
            "date_utc": row.get("date_utc") or "",
            "message_id_header": row.get("message_id_header") or "",
        })
    return hits

def files_on_messages(matches, ids, after, before):
    if not DB.is_file():
        return []
    ids = [] if not ids else [mid for mid in ids if mid]
    seen_ids = []
    for mid in ids:
        if mid not in seen_ids:
            seen_ids.append(mid)
    ids = seen_ids[:40]
    if not ids:
        return []
    found = []
    for match in matches:
        match = (match or "").strip()
        if not match:
            continue
        try:
            found.extend(_file_rows(match, ids, after, before))
        except (OSError, ValueError, RuntimeError):
            continue
    return found

def _file_key(hit):
    return (str(hit.get("message_id") or ""), str(hit.get("filename") or ""), str(hit.get("page_start") or ""))

def merge_file_hits(first, second):
    out = []
    seen = set()
    for hit in list(first or []) + list(second or []):
        if not isinstance(hit, dict):
            continue
        key = _file_key(hit)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out

def file_hits(query, after="", before="", cap=None):
    text = (query or "").strip()
    if not text or not DB.is_file():
        return []
    literal = "'" + text.replace("'", "''") + "'"
    query_sql = SQL % literal
    extra = []
    if after:
        extra.append("COALESCE(m.date_utc,'') >= '%s'" % after.replace("'", ""))
    if before:
        extra.append("COALESCE(m.date_utc,'') <= '%sT23:59:59'" % before.replace("'", ""))
    if extra:
        query_sql = query_sql.strip().rstrip(';')
        import re as _re
        query_sql = _re.sub(r'\s+LIMIT\s+\d+', ' ', query_sql, count=1, flags=_re.I)
        tail = query_sql.upper().split('JOIN')[-1]
        query_sql += (' AND ' if ' WHERE ' in tail else ' WHERE ') + ' AND '.join(extra)
    import re as _re
    query_sql = _re.sub(r"\s+LIMIT\s+\d+", " ", query_sql, count=1, flags=_re.I)
    if cap:
        query_sql = query_sql.strip().rstrip(";") + " LIMIT " + str(int(cap))
    proc = subprocess.run(
        ["/usr/bin/sqlite3", "-json", str(DB)],
        input=query_sql + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        note = err[0] if err else "file search failed"
        if "@" in note or "/" in note or len(note) > 120:
            note = "file search failed"
        raise RuntimeError(note)
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    hits = []
    for row in json.loads(raw):
        hits.append({
            "message_id": row.get("message_id") or "",
            "filename": row.get("filename") or "",
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            "snippet": snippet(row.get("text") or "", text),
            "subject": row.get("subject") or "",
            "from_addr": row.get("from_addr") or "",
            "date_utc": row.get("date_utc") or "",
            "message_id_header": row.get("message_id_header") or "",
        })
    return one_per_file(hits)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("mail-search: %s\n" % (fmt % args))

    def _send(self, status, body, content_type):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?", 1)[0] in ("/", "/ui"):
            self._send(200, PAGE, "text/html; charset=utf-8")
            return
        self._send(404, '{"error":"not found"}', "application/json")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/ask":
            self._send(404, '{"error":"not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        except (ValueError, UnicodeError):
            self._send(400, '{"error":"invalid JSON"}', "application/json")
            return
        if not isinstance(data, dict):
            self._send(400, '{"error":"JSON object required"}', "application/json")
            return
        typed = str(data.get("query") or data.get("q") or "")
        query, rewrite_note = rewrite_query(typed)
        after, before = time_bounds(typed)
        cap = hit_limit(typed)
        data["query"] = query
        data["k"] = cap or 1000
        if after:
            data["after"] = after
        if before:
            data["before"] = before
        if after or before:
            window = "Only mail from " + after
            if before:
                window += " through " + before
            rewrite_note = (rewrite_note + ", " + window).strip(", ")
        files, file_note = [], ""
        try:
            files = file_hits(query, after, before, cap)
        except (RuntimeError, OSError, ValueError) as exc:
            file_note = str(exc) or "file search failed"
        data["generate"] = False
        request = Request(ASK, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            response = urlopen(request, timeout=25)
            payload, status = response.read(), response.status
        except HTTPError as exc:
            payload, status = exc.read(), exc.code
        except (URLError, TimeoutError):
            payload, status = b'{"error":"mail server did not answer"}', 502
        try:
            body = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeError):
            body, status = {"error": "mail server returned an unreadable answer"}, 502
        if not isinstance(body, dict):
            body, status = {"error": "mail server returned an unreadable answer"}, 502
        add_mail_headers(body.get("hits") or [])
        if after or before:
            body["hits"] = keep_dated(body.get("hits") or [], after, before)
            files = keep_dated(files, after, before)
        ids = []
        for hit in body.get("hits") or []:
            mid = str(hit.get("message_id") or hit.get("id") or "")
            if mid and mid not in ids:
                ids.append(mid)
        matches = [query]
        loose = loose_terms(typed)
        if loose and loose not in matches:
            matches.append(loose)
        try:
            linked = files_on_messages(matches, ids, after, before)
            linked.extend(_file_rows_like(content_words(typed), ids, after, before))
        except (OSError, RuntimeError, ValueError):
            linked = []
        listed = []
        try:
            listed = files_on_listed(ids, after, before)
        except (OSError, RuntimeError, ValueError):
            listed = []
        if after or before:
            linked = keep_dated(linked, after, before)
        files = one_per_file(merge_file_hits(listed, merge_file_hits(linked, files)))
        if wants_newest(typed):
            body["hits"] = sort_newest(body.get("hits") or [])
            files = sort_newest(files)
            body["rewrite_note"] = (str(body.get("rewrite_note") or "") + ", Newest first").strip(", ")
        if cap:
            body["hits"] = (body.get("hits") or [])[:cap]
            files = files[:cap]
            body["rewrite_note"] = (str(body.get("rewrite_note") or "") + ", Limit " + str(cap)).strip(", ")
        body["files"] = files
        body["searched"] = query
        if file_note:
            body["file_note"] = file_note
        if rewrite_note:
            body["rewrite_note"] = rewrite_note
        if files or body.get("hits"):
            status = 200
        self._send(status, json.dumps(body).encode("utf-8"), "application/json; charset=utf-8")


if __name__ == "__main__":
    try:
        prepare_qwen()
    except Exception as exc:
        sys.stderr.write("STOP %s\n" % exc)
        raise SystemExit(1)
    sys.stderr.write("mail search http://127.0.0.1:8755/\n")
    ThreadingHTTPServer(("127.0.0.1", 8755), Handler).serve_forever()
PServer(("127.0.0.1", 8755), Handler).serve_forever()
# PAGE-END
