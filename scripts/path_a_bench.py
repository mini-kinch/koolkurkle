#!/usr/bin/env python3
"""Read-only benchmark for the Path A local Qwen chat server.

Scores probe, warm, cold, soak, and memory bars by sending HTTP chat requests
and reading system stats. Does not restart services, change configuration, or
touch launchd. Writes nothing except an optional JSONL log and an optional
mem baseline snapshot.

Test hooks (ignored unless PATH_A_BENCH_TEST_HOOKS=1):
  PATH_A_BENCH_TIME_SCALE       multiply measured wall seconds
  PATH_A_BENCH_WALL_ADD         add seconds to measured wall
  PATH_A_BENCH_WARM_WALL_MAX    override the 45s per-request warm bar
  PATH_A_BENCH_WARM_MEDIAN_MAX  override the 35s warm median bar
  PATH_A_BENCH_COLD_WALL_MAX    override the 90s cold bar
  PATH_A_BENCH_SETTLE_SLEEP=0   skip real settle/idle sleeps (lines still print)
  PATH_A_BENCH_NOW              fixed local ISO timestamp for local_iso()
  PATH_A_BENCH_INJECT_SWAP_MB   fake swap MB and parse mem as macOS text
  PATH_A_BENCH_OLLAMA_PROCESS   down, up, or unknown when swap is injected
  PATH_A_BENCH_OLLAMA_PORT      closed, open, or unknown when swap is injected
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

SYSTEM_PROMPT = (
    "Answer only from DATA. Cite date/from/subject when relevant. "
    "If DATA is insufficient, say so. Do not invent. "
    "Reply with the final answer only — no chain-of-thought."
)
TEMPERATURE = 0.2
SWAP_LIMIT_MB = 1024.0
CONTENT_MIN = 300
DEFAULT_TIMEOUT = {"probe": 60.0, "warm": 60.0, "cold": 120.0, "soak": 300.0}
PROBE_TIMEOUT = 60.0
WARM_WALL_MAX = 45.0
WARM_MEDIAN_MAX = 35.0
COLD_WALL_MAX = 90.0
WATCHDOG_WINDOW_S = 300.0
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434

COLD_NOTE = (
    "NOTE cold: operator is responsible for a 30-minute idle before this "
    "request; this harness does not enforce or check idle time"
)
COLD_WATCHDOG_NOTE = (
    "NOTE cold: operator is responsible for booting out any watchdog beforehand; "
    "this harness never touches launchd"
)
SOAK_NOTE = (
    "NOTE soak: run in the foreground on the host; "
    "do not background this inside a remote shell. "
    "A 4 h soak must not run as a background job inside a remote shell"
)

_SWAP_USED = re.compile(
    r"used\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT])",
    re.IGNORECASE,
)
_WATCHDOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_ISO_WALL = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
_TIMEOUT_TYPES = (TimeoutError, socket.timeout)


class ConfigError(Exception):
    """Usage or startup configuration problem. Exit 2."""


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_fixture() -> Path:
    return repo_root() / "tests" / "fixtures" / "path_a" / "paste_4k_synthetic.txt"


def default_ask_mail() -> Path:
    return Path.home() / "MailArchive" / "scripts" / "ask_mail.py"


def local_iso() -> str:
    if os.environ.get("PATH_A_BENCH_TEST_HOOKS", "").strip() == "1":
        fixed = os.environ.get("PATH_A_BENCH_NOW", "").strip()
        if fixed:
            return fixed
    now = datetime.datetime.now().astimezone().replace(microsecond=0)
    return now.isoformat()


def hash_ask_mail(path: Path) -> str:
    """sha256 hex, 'absent' if missing, 'unreadable' if it cannot be read."""
    if not path.is_file():
        return "absent"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return "unreadable"
    return digest.hexdigest()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        value = float(str(raw).strip())
    except ValueError:
        raise ConfigError("bad %s" % name)
    if value != value or value == float("inf") or value == float("-inf"):
        raise ConfigError("bad %s" % name)
    return value


def load_limits() -> dict:
    """Acceptance thresholds. Test hooks apply only when explicitly enabled."""
    limits = {
        "warm_wall": WARM_WALL_MAX,
        "warm_median": WARM_MEDIAN_MAX,
        "cold_wall": COLD_WALL_MAX,
        "scale": 1.0,
        "add": 0.0,
    }
    if os.environ.get("PATH_A_BENCH_TEST_HOOKS", "").strip() != "1":
        return limits
    limits["warm_wall"] = _env_float("PATH_A_BENCH_WARM_WALL_MAX", WARM_WALL_MAX)
    limits["warm_median"] = _env_float(
        "PATH_A_BENCH_WARM_MEDIAN_MAX", WARM_MEDIAN_MAX
    )
    limits["cold_wall"] = _env_float("PATH_A_BENCH_COLD_WALL_MAX", COLD_WALL_MAX)
    limits["scale"] = _env_float("PATH_A_BENCH_TIME_SCALE", 1.0)
    limits["add"] = _env_float("PATH_A_BENCH_WALL_ADD", 0.0)
    return limits


def adjusted_wall(elapsed: float, limits: dict) -> float:
    return round((elapsed * limits["scale"]) + limits["add"], 3)


def _sensitive_patterns():
    """Build redaction patterns without embedding home paths or mail domains."""
    users = "/" + "Users" + "/"
    home = "/" + "home" + "/"
    icloud = "@" + "icloud"
    me = "@" + "me" + ".com"
    flags = re.IGNORECASE
    return (
        re.compile(re.escape(users) + r"\S+"),
        re.compile(re.escape(home) + r"\S+"),
        re.compile(r"\S+" + re.escape(icloud) + r"\S*", flags),
        re.compile(r"\S+" + re.escape(me) + r"\S*", flags),
    )


_SENSITIVE = None


def scrub_text(text: str) -> str:
    global _SENSITIVE
    if _SENSITIVE is None:
        _SENSITIVE = _sensitive_patterns()
    cleaned = (text or "").replace("\n", " ").replace("\r", " ")
    for pattern in _SENSITIVE:
        cleaned = pattern.sub("[redacted]", cleaned)
    if len(cleaned) > 200:
        cleaned = cleaned[:200]
    return cleaned


def safe_label(text: str) -> str:
    label = scrub_text(text).strip()
    if not label or len(label) > 200 or "[redacted]" in label:
        return "(redacted)"
    return label


def v1_root(base_url: str) -> str:
    root = (base_url or "").strip().rstrip("/")
    if not (root.startswith("http://") or root.startswith("https://")):
        raise ConfigError("bad base-url")
    if root.endswith("/v1"):
        return root
    return root + "/v1"


def build_probe_body(model: str) -> dict:
    """Same tiny body as qwen_paste_chat_post.py's max-tokens-1 probe."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": "/no_think"}],
        "max_tokens": 1,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def build_chat_body(paste: str, model: str, max_tokens: int, retry: bool = False) -> dict:
    """Same chat body as qwen_paste_chat_post.py with thinking left off."""
    if retry:
        user = paste.rstrip() + "\n\n/no_think\nFinal answer only."
        tokens = max(max_tokens, 768)
    else:
        user = paste.rstrip() + "\n\n/no_think"
        tokens = max_tokens
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": tokens,
        "temperature": TEMPERATURE,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def extract_choice(data: dict) -> dict:
    """Pull finish, stripped content, and reasoning the way post.py does."""
    if not isinstance(data, dict):
        raise ValueError("response is not an object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("missing choices")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("missing message")
    finish = str(choice.get("finish_reason") or "")
    content = message.get("content")
    content_s = content.strip() if isinstance(content, str) else ""
    reasoning_len = None
    reasoning_s = ""
    if "reasoning" in message and isinstance(message.get("reasoning"), str):
        reasoning_s = message["reasoning"].strip()
        reasoning_len = len(reasoning_s)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "content": content_s,
        "reasoning": reasoning_s,
        "reasoning_len": reasoning_len,
        "finish": finish,
        "usage": usage,
    }


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, _TIMEOUT_TYPES):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return isinstance(getattr(exc, "reason", None), _TIMEOUT_TYPES)
    return False


def _request(url: str, timeout: float, body: Optional[dict] = None):
    data = None
    headers = {}
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read(), None, False
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except Exception:
            pass
        return int(exc.code), b"", "HTTP %s" % exc.code, False
    except Exception as exc:
        timed_out = _is_timeout(exc)
        if timed_out:
            return None, b"", "timeout", True
        return None, b"", "request failed", False


def _parse_raw(raw: bytes):
    if raw is None or not raw.strip():
        return None, "empty body"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None, "malformed json"
    try:
        return extract_choice(data), None
    except Exception:
        return None, "bad response shape"


def _one_post(url: str, body: dict, timeout: float):
    status, raw, err, timed_out = _request(url, timeout, body)
    if timed_out or err or status is None or status < 200 or status >= 300:
        if err is None and status is not None:
            err = "HTTP %s" % status
        return status, None, err, timed_out
    parsed, parse_err = _parse_raw(raw)
    return status, parsed, parse_err, False


def perform_chat(url: str, paste: str, model: str, max_tokens: int, timeout: float) -> dict:
    """One bench request. Retries once when the reply is reasoning-only.

    content_len is the stripped assistant content post.py would print.
    Thinking stays off, so a reasoning-only reply is not counted as content.
    """
    started = time.monotonic()
    body = build_chat_body(paste, model, max_tokens, retry=False)
    status, parsed, err, timed_out = _one_post(url, body, timeout)
    if (
        parsed is not None
        and not parsed["content"]
        and parsed["reasoning"]
        and not timed_out
        and not err
    ):
        retry = build_chat_body(paste, model, max_tokens, retry=True)
        status, parsed, err, timed_out = _one_post(url, retry, timeout)
    elapsed = time.monotonic() - started
    result = {
        "http_status": status,
        "finish_reason": None,
        "content_len": None,
        "reasoning_len": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "error": err,
        "timed_out": timed_out,
        "elapsed": elapsed,
    }
    if parsed is not None and not err and not timed_out:
        result["finish_reason"] = parsed["finish"]
        result["content_len"] = len(parsed["content"])
        result["reasoning_len"] = parsed["reasoning_len"]
        usage = parsed["usage"]
        if "prompt_tokens" in usage:
            result["prompt_tokens"] = usage.get("prompt_tokens")
        if "completion_tokens" in usage:
            result["completion_tokens"] = usage.get("completion_tokens")
    return result


def perform_probe(url: str, model: str, timeout: float) -> dict:
    """One max-tokens-1 POST. No paste and no reasoning retry."""
    started = time.monotonic()
    status, parsed, err, timed_out = _one_post(url, build_probe_body(model), timeout)
    elapsed = time.monotonic() - started
    result = {
        "http_status": status,
        "finish_reason": None,
        "content_len": None,
        "reasoning_len": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "error": err,
        "timed_out": timed_out,
        "elapsed": elapsed,
    }
    if parsed is not None and not err and not timed_out:
        result["finish_reason"] = parsed["finish"]
        result["content_len"] = len(parsed["content"])
        result["reasoning_len"] = parsed["reasoning_len"]
        usage = parsed["usage"]
        if "prompt_tokens" in usage:
            result["prompt_tokens"] = usage.get("prompt_tokens")
        if "completion_tokens" in usage:
            result["completion_tokens"] = usage.get("completion_tokens")
    return result


def classify_probe(result: dict):
    """PASS is HTTP 200 plus a parseable choice. Any finish_reason is fine."""
    if result.get("timed_out"):
        return "hung", "timeout"
    if result.get("error"):
        return "fail", scrub_text(str(result["error"]))
    if result.get("http_status") != 200:
        return "fail", "http_status=%s" % result.get("http_status")
    finish = result.get("finish_reason")
    content_len = result.get("content_len")
    if finish is None or not isinstance(content_len, int):
        return "fail", "bad response shape"
    return "pass", None


def short_content_only(error) -> bool:
    """True when the only miss is content_len <= CONTENT_MIN.

    HTTP, finish_reason, and transport errors are not this case. A soak
    --continue-on-hang timing line ignores these so a short answer cannot
    look like a hang or a failed recovery.
    """
    if not isinstance(error, str) or not error.strip():
        return False
    parts = [part.strip() for part in error.split(";") if part.strip()]
    if not parts:
        return False
    prefix = "content_len="
    for part in parts:
        if not part.startswith(prefix) or part == prefix:
            return False
    return True


def timing_ok(row: dict) -> bool:
    """Request came back in time with a usable HTTP stop, ignoring length."""
    verdict = row.get("verdict")
    if verdict == "pass":
        return True
    if verdict == "fail" and short_content_only(row.get("error")):
        return True
    return False


def soak_content_line(rows: list):
    """Own summary line for content_len <= CONTENT_MIN. Returns (line, ok)."""
    limit = "content_len<=%d" % CONTENT_MIN
    if not rows:
        return "PASS soak_content short=0 limit=%s" % limit, True
    bits = []
    for row in rows:
        bits.append("idx=%s content_len=%s" % (row.get("idx"), row.get("content_len")))
    line = "FAIL soak_content short=%d limit=%s %s" % (len(rows), limit, " ".join(bits))
    return line, False


def classify_request(mode: str, wall_s: float, result: dict, limits: dict):
    """Return (verdict, error). Soak timeouts are 'hung'; other modes fail."""
    if result.get("timed_out"):
        if mode == "soak":
            return "hung", "timeout"
        return "fail", "timeout"
    if result.get("error"):
        return "fail", scrub_text(str(result["error"]))
    reasons = []
    status = result.get("http_status")
    if status is None or status < 200 or status >= 300:
        reasons.append("http_status=%s" % status)
    finish = result.get("finish_reason") or ""
    if finish != "stop":
        reasons.append("finish_reason=%s" % finish)
    content_len = result.get("content_len")
    if content_len is None or content_len <= CONTENT_MIN:
        shown = "na" if content_len is None else str(content_len)
        reasons.append("content_len=%s" % shown)
    if mode == "warm" and wall_s > limits["warm_wall"]:
        reasons.append("wall_s=%.3f" % wall_s)
    if mode == "cold" and wall_s > limits["cold_wall"]:
        reasons.append("wall_s=%.3f" % wall_s)
    if reasons:
        return "fail", "; ".join(reasons)
    return "pass", None


def first_model_id(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    items = data.get("data")
    if not isinstance(items, list):
        items = data.get("models")
    if not isinstance(items, list) or not items:
        return ""
    item = items[0]
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        ident = item.get("id") or item.get("name")
        if ident:
            return str(ident).strip()
    return ""


def resolve_model(base_url: str, explicit: str, timeout: float) -> str:
    url = v1_root(base_url) + "/models"
    preflight = timeout if timeout < 5.0 else 5.0
    status, raw, err, timed_out = _request(url, preflight, body=None)
    if timed_out or err or status != 200 or not raw or not raw.strip():
        raise ConfigError("server unreachable at start")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ConfigError("server unreachable at start")
    if explicit:
        return explicit
    model = first_model_id(data)
    if not model:
        raise ConfigError("no model id from /v1/models")
    return model


def load_fixture(path: Path) -> str:
    if not path.is_file():
        raise ConfigError("bad fixture")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ConfigError("bad fixture")
    if not text.strip():
        raise ConfigError("bad fixture")
    return text


class Jsonl:
    def __init__(self, path: str):
        self.fp = None
        if not path:
            return
        try:
            self.fp = open(path, "w", encoding="utf-8", newline="\n")
        except OSError:
            raise ConfigError("cannot write JSONL")

    def write(self, row: dict) -> None:
        if self.fp is None:
            return
        self.fp.write(json.dumps(row, sort_keys=True) + "\n")
        self.fp.flush()

    def close(self) -> None:
        if self.fp is not None:
            self.fp.close()
            self.fp = None


def fmt_num(value, digits: int = 3) -> str:
    if value is None:
        return "na"
    return "%.*f" % (digits, value)


def ask_mail_bar(start: str, end: str):
    if start == end and start != "unreadable":
        return "PASS", "ask_mail sha256=%s" % start
    if start != end:
        return "FAIL", "ask_mail sha256 changed start=%s end=%s" % (start, end)
    return "FAIL", "ask_mail sha256 unreadable"


def _wall_bits(rows: list) -> tuple:
    walls = [row["wall_s"] for row in rows if isinstance(row.get("wall_s"), (int, float))]
    if not walls:
        return None, None, None
    return min(walls), statistics.median(walls), max(walls)


def chat_summary(
    mode: str,
    rows: list,
    limits: dict,
    ask_start: str,
    ask_end: str,
    wedged: bool,
    continue_on_hang: bool = False,
    hang_notes: Optional[list] = None,
    hangs_recovered: bool = True,
    mem_line: Optional[str] = None,
    mem_ok: bool = True,
):
    failures = sum(1 for row in rows if row.get("verdict") == "fail")
    hung = sum(1 for row in rows if row.get("verdict") == "hung")
    content_short_rows = []
    if mode == "soak" and continue_on_hang:
        content_short_rows = [
            row
            for row in rows
            if row.get("verdict") == "fail" and short_content_only(row.get("error"))
        ]
        failures = failures - len(content_short_rows)
    n = len(rows)
    min_s, med_s, max_s = _wall_bits(rows)
    numbers = "n=%d failures=%d hung=%d min_s=%s median_s=%s max_s=%s" % (
        n,
        failures,
        hung,
        fmt_num(min_s),
        fmt_num(med_s),
        fmt_num(max_s),
    )
    lines = []
    ok = True
    if mode == "warm":
        req_ok = n > 0 and failures == 0 and hung == 0
        med_ok = n > 0 and med_s is not None and med_s <= limits["warm_median"]
        lines.append(
            "%s warm_requests %s limit_s=%s"
            % ("PASS" if req_ok else "FAIL", numbers, fmt_num(limits["warm_wall"]))
        )
        lines.append(
            "%s warm_median median_s=%s limit_s=%s"
            % ("PASS" if med_ok else "FAIL", fmt_num(med_s), fmt_num(limits["warm_median"]))
        )
        ok = req_ok and med_ok
    elif mode == "probe":
        req_ok = n == 1 and failures == 0 and hung == 0
        lines.append("%s probe %s" % ("PASS" if req_ok else "FAIL", numbers))
        ok = req_ok
    elif mode == "cold":
        generate = [row for row in rows if row.get("role") != "probe"]
        probes = [row for row in rows if row.get("role") == "probe"]
        g_fail = sum(1 for row in generate if row.get("verdict") == "fail")
        g_hung = sum(1 for row in generate if row.get("verdict") == "hung")
        g_n = len(generate)
        gmin, gmed, gmax = _wall_bits(generate)
        g_numbers = "n=%d failures=%d hung=%d min_s=%s median_s=%s max_s=%s" % (
            g_n,
            g_fail,
            g_hung,
            fmt_num(gmin),
            fmt_num(gmed),
            fmt_num(gmax),
        )
        req_ok = g_n == 1 and g_fail == 0 and g_hung == 0
        lines.append(
            "%s cold_request %s limit_s=%s"
            % ("PASS" if req_ok else "FAIL", g_numbers, fmt_num(limits["cold_wall"]))
        )
        ok = req_ok
        if probes:
            p_ok = len(probes) == 1 and probes[0].get("verdict") == "pass"
            p_verdict = probes[0].get("verdict") or "missing"
            lines.append(
                "%s cold_probe verdict=%s" % ("PASS" if p_ok else "FAIL", p_verdict)
            )
            if not p_ok:
                ok = False
    else:
        if continue_on_hang:
            req_ok = n > 0 and failures == 0 and (hung == 0 or hangs_recovered)
        else:
            req_ok = n > 0 and failures == 0 and hung == 0 and not wedged
        lines.append("%s soak %s" % ("PASS" if req_ok else "FAIL", numbers))
        if hang_notes:
            lines.extend(hang_notes)
        ok = req_ok
        if continue_on_hang:
            content_line, content_ok = soak_content_line(content_short_rows)
            lines.append(content_line)
            if not content_ok:
                ok = False
        if mem_line:
            lines.append(mem_line)
            if not mem_ok:
                ok = False
    ask_status, ask_detail = ask_mail_bar(ask_start, ask_end)
    lines.append("%s %s" % (ask_status, ask_detail))
    if ask_status != "PASS":
        ok = False
    if wedged:
        overall = "WEDGE"
        code = 4
    elif ok:
        overall = "PASS"
        code = 0
    else:
        overall = "FAIL"
        code = 1
    lines.append("OVERALL %s" % overall)
    summary = {
        "kind": "summary",
        "ts": local_iso(),
        "mode": mode,
        "n": n,
        "failures": failures,
        "hung": hung,
        "min_s": min_s,
        "median_s": med_s,
        "max_s": max_s,
        "ask_mail_sha256_start": ask_start,
        "ask_mail_sha256_end": ask_end,
        "overall": overall,
    }
    if mem_line:
        summary["soak_mem_at"] = mem_line
    if mode == "soak" and continue_on_hang:
        summary["content_short"] = len(content_short_rows)
    return lines, code, summary


def request_row(
    mode: str,
    idx: int,
    wall_s: float,
    result: dict,
    verdict: str,
    error,
    fixture_name: str,
    role: Optional[str] = None,
) -> dict:
    row = {
        "kind": "request",
        "ts": local_iso(),
        "mode": mode,
        "idx": idx,
        "fixture": fixture_name,
        "wall_s": wall_s,
        "http_status": result.get("http_status"),
        "finish_reason": result.get("finish_reason"),
        "content_len": result.get("content_len"),
        "verdict": verdict,
        "error": error,
    }
    if role:
        row["role"] = role
    if result.get("reasoning_len") is not None:
        row["reasoning_len"] = result["reasoning_len"]
    if result.get("prompt_tokens") is not None:
        row["prompt_tokens"] = result["prompt_tokens"]
    if result.get("completion_tokens") is not None:
        row["completion_tokens"] = result["completion_tokens"]
    return row


def _progress(mode: str, idx: int, total: int, row: dict) -> str:
    content = row.get("content_len")
    content_s = "na" if content is None else str(content)
    finish = row.get("finish_reason") or "na"
    status = row.get("http_status")
    status_s = "na" if status is None else str(status)
    return (
        "%s %d/%d fixture=%s verdict=%s wall_s=%s http=%s finish=%s content_len=%s"
        % (
            mode,
            idx,
            total,
            row.get("fixture") or "na",
            row["verdict"],
            fmt_num(row["wall_s"]),
            status_s,
            finish,
            content_s,
        )
    )


def fixture_index(request_idx: int, count: int) -> int:
    """0-based file index. Request i (0-based) uses file i mod count.

    request_idx is 1-based, matching JSONL idx.
    """
    if count < 1 or request_idx < 1:
        raise ConfigError("bad fixture rotation")
    return (request_idx - 1) % count


def resolve_chat_fixtures(args):
    """Return [(filename, text), ...] in rotation order.

    --fixture and --fixtures-dir together are a config error. Neither flag
    uses the default single paste.
    """
    fixture = (args.fixture or "").strip()
    folder = (getattr(args, "fixtures_dir", "") or "").strip()
    if fixture and folder:
        raise ConfigError("pass only one of --fixture and --fixtures-dir")
    if folder:
        directory = Path(folder)
        if not directory.is_dir():
            raise ConfigError("bad fixtures-dir")
        paths = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix == ".txt" and not path.name.startswith(".")
            ),
            key=lambda path: path.name,
        )
        if not paths:
            raise ConfigError("bad fixtures-dir")
        return [(path.name, load_fixture(path)) for path in paths]
    path = Path(fixture) if fixture else default_fixture()
    return [(path.name, load_fixture(path))]


def _finite_non_negative(value: float) -> bool:
    return value >= 0 and value == value and value != float("inf")


def run_probe(args, limits: Optional[dict] = None) -> int:
    """One max-tokens-1 POST. Does not load a paste."""
    limits = load_limits() if limits is None else limits
    timeout = args.timeout if args.timeout is not None else DEFAULT_TIMEOUT["probe"]
    if not (timeout > 0):
        raise ConfigError("bad timeout")
    ask_path = Path(args.ask_mail)
    ask_start = hash_ask_mail(ask_path)
    model = resolve_model(args.base_url, (args.model or "").strip(), timeout)
    url = v1_root(args.base_url) + "/chat/completions"
    jsonl = Jsonl(args.out)
    try:
        print("model %s" % safe_label(model), flush=True)
        result = perform_probe(url, model, timeout)
        wall_s = adjusted_wall(result["elapsed"], limits)
        verdict, error = classify_probe(result)
        row = request_row(
            "probe", 1, wall_s, result, verdict, error, "probe", role="probe"
        )
        wedged = verdict == "hung"
        jsonl.write(row)
        print(_progress("probe", 1, 1, row), flush=True)
        if wedged:
            wedge = "WEDGE probe idx=1 local_time=%s timeout_s=%s" % (
                row["ts"],
                fmt_num(timeout),
            )
            print(wedge, flush=True)
            print(wedge, file=sys.stderr, flush=True)
        ask_end = hash_ask_mail(ask_path)
        lines, code, summary = chat_summary(
            "probe", [row], limits, ask_start, ask_end, wedged
        )
        jsonl.write(summary)
        for line in lines:
            print(line, flush=True)
        return code
    finally:
        jsonl.close()


def run_chat(args, limits: Optional[dict] = None) -> int:
    mode = args.mode
    limits = load_limits() if limits is None else limits
    timeout = args.timeout if args.timeout is not None else DEFAULT_TIMEOUT[mode]
    if not (timeout > 0):
        raise ConfigError("bad timeout")
    if args.max_tokens < 1:
        raise ConfigError("bad max-tokens")
    n = 1 if mode == "cold" else args.n
    if n < 1:
        raise ConfigError("bad n")
    interval = 0.0
    idle = None
    continue_on_hang = False
    watchdog_log = ""
    mem_at = None
    if mode == "cold":
        idle = getattr(args, "idle", None)
        if idle is not None and not _finite_non_negative(float(idle)):
            raise ConfigError("bad idle")
    if mode == "soak":
        interval = args.interval
        if interval < 0:
            raise ConfigError("bad interval")
        continue_on_hang = bool(getattr(args, "continue_on_hang", False))
        watchdog_log = (getattr(args, "watchdog_log", "") or "").strip()
        mem_at = getattr(args, "mem_at", None)
        if mem_at is not None and (mem_at < 1 or mem_at > n):
            raise ConfigError("bad mem-at")
    fixtures = resolve_chat_fixtures(args)
    ask_path = Path(args.ask_mail)
    ask_start = hash_ask_mail(ask_path)
    model = resolve_model(args.base_url, (args.model or "").strip(), timeout)
    url = v1_root(args.base_url) + "/chat/completions"
    jsonl = Jsonl(args.out)
    try:
        if mode == "cold":
            if idle is None:
                print(COLD_NOTE, flush=True)
            else:
                print(COLD_WATCHDOG_NOTE, flush=True)
        elif mode == "soak":
            print(SOAK_NOTE, flush=True)
        if mode == "warm":
            if (getattr(args, "fixtures_dir", "") or "").strip():
                print(
                    "NOTE warm_honest fixtures-dir rotation (clock gate)",
                    flush=True,
                )
            else:
                print(
                    "NOTE warm_cached single fixture "
                    "(prompt-cache hit, falsifier-2 style)",
                    flush=True,
                )
        print("model %s" % safe_label(model), flush=True)
        rows = []
        wedged = False
        mem_sample = None
        if mode == "cold" and idle is not None and float(idle) > 0:
            idle_wait(float(idle))
        if mode == "cold" and idle is not None:
            probe_result = perform_probe(url, model, PROBE_TIMEOUT)
            probe_wall = adjusted_wall(probe_result["elapsed"], limits)
            probe_verdict, probe_error = classify_probe(probe_result)
            probe_row = request_row(
                mode,
                1,
                probe_wall,
                probe_result,
                probe_verdict,
                probe_error,
                "probe",
                role="probe",
            )
            rows.append(probe_row)
            jsonl.write(probe_row)
            print(_progress(mode, 1, 2, probe_row), flush=True)
            if probe_verdict == "hung":
                wedge = "WEDGE cold idx=1 local_time=%s timeout_s=%s" % (
                    probe_row["ts"],
                    fmt_num(PROBE_TIMEOUT),
                )
                print(wedge, flush=True)
                print(wedge, file=sys.stderr, flush=True)
                wedged = True
        if not wedged:
            for idx in range(1, n + 1):
                if idx > 1 and interval > 0:
                    time.sleep(interval)
                fixture_name, paste = fixtures[fixture_index(idx, len(fixtures))]
                result = perform_chat(url, paste, model, args.max_tokens, timeout)
                wall_s = adjusted_wall(result["elapsed"], limits)
                verdict, error = classify_request(mode, wall_s, result, limits)
                row_idx = idx
                total = n
                role = None
                if mode == "cold" and idle is not None:
                    row_idx = idx + 1
                    total = 2
                    role = "generate"
                row = request_row(
                    mode, row_idx, wall_s, result, verdict, error, fixture_name, role=role
                )
                rows.append(row)
                jsonl.write(row)
                print(_progress(mode, row_idx, total, row), flush=True)
                if mode == "soak" and mem_at is not None and idx == mem_at:
                    mem_sample = evaluate_mem(*collect_host_mem())
                if mode == "soak" and verdict == "hung":
                    if continue_on_hang:
                        print(
                            "HUNG soak idx=%d local_time=%s" % (idx, row["ts"]),
                            flush=True,
                        )
                    else:
                        wedge = "WEDGE soak idx=%d local_time=%s timeout_s=%s" % (
                            idx,
                            row["ts"],
                            fmt_num(timeout),
                        )
                        print(wedge, flush=True)
                        print(wedge, file=sys.stderr, flush=True)
                        wedged = True
                        break
        ask_end = hash_ask_mail(ask_path)
        hang_notes = None
        hangs_recovered = True
        if mode == "soak" and continue_on_hang:
            if watchdog_log:
                watchdog_status, stamps = read_watchdog_log(watchdog_log)
            else:
                watchdog_status, stamps = None, []
            hangs_recovered, hang_notes = hang_recoveries(rows, watchdog_status, stamps)
        mem_line = None
        mem_ok = True
        if mode == "soak" and mem_at is not None:
            mem_line, mem_ok = soak_mem_line(mem_at, mem_sample)
        lines, code, summary = chat_summary(
            mode,
            rows,
            limits,
            ask_start,
            ask_end,
            wedged,
            continue_on_hang=continue_on_hang,
            hang_notes=hang_notes,
            hangs_recovered=hangs_recovered,
            mem_line=mem_line,
            mem_ok=mem_ok,
        )
        summary["fixtures"] = [name for name, _text in fixtures]
        jsonl.write(summary)
        for line in lines:
            print(line, flush=True)
        return code
    finally:
        jsonl.close()


def parse_swap_used_mb(text: str):
    """Parse `sysctl vm.swapusage` and return used swap in MB."""
    if not text:
        return None
    match = _SWAP_USED.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    scale = {"K": 1.0 / 1024.0, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024.0}[unit]
    return value * scale


def _page_count(text: str, label: str):
    match = re.search(
        r"^%s:\s+([0-9]+)" % re.escape(label),
        text or "",
        re.MULTILINE,
    )
    if not match:
        return None
    return int(match.group(1))


def parse_vm_stat_pages(text: str) -> dict:
    """Return free, active, and wired page counts from `vm_stat` text."""
    wired = _page_count(text, "Pages wired down")
    if wired is None:
        wired = _page_count(text, "Pages wired")
    return {
        "free": _page_count(text, "Pages free"),
        "active": _page_count(text, "Pages active"),
        "wired": wired,
    }


def ollama_word(process: str, port: str) -> str:
    if process == "down" and port == "closed":
        return "down"
    if process == "up" or port == "open":
        return "up"
    return "unknown"


def soak_mem_line(k: int, result) -> tuple:
    """Mid-soak idle swap bar. Pass when used swap is under 1024 MB."""
    if result is None:
        return "FAIL soak_mem_at k=%d missing" % k, False
    used = result.get("swap_used_mb")
    ok = used is not None and used < SWAP_LIMIT_MB
    used_s = "unknown" if used is None else "%.2f" % used
    line = "%s soak_mem_at k=%d swap_used_mb=%s limit_mb=1024 ollama=%s" % (
        "PASS" if ok else "FAIL",
        k,
        used_s,
        ollama_word(result.get("process"), result.get("port")),
    )
    return line, ok


def naive_wall(ts: str):
    """Local wall time from an ISO timestamp, ignoring any timezone suffix."""
    match = _ISO_WALL.search(ts or "")
    if not match:
        return None
    try:
        return datetime.datetime.strptime(
            match.group(1) + " " + match.group(2),
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None


def parse_watchdog_stamps(text: str) -> list:
    """Timestamps at the start of a line: YYYY-MM-DD HH:MM:SS."""
    stamps = []
    for line in (text or "").splitlines():
        match = _WATCHDOG_TS.match(line)
        if not match:
            continue
        try:
            stamps.append(
                datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            )
        except ValueError:
            continue
    return stamps


def watchdog_stamp_for_hang(hang_ts: str, stamps: list):
    """First log stamp in [hang, hang + 5 min], or None."""
    hang_at = naive_wall(hang_ts)
    if hang_at is None:
        return None
    for stamp in stamps:
        delta = (stamp - hang_at).total_seconds()
        if 0 <= delta <= WATCHDOG_WINDOW_S:
            return stamp
    return None


def read_watchdog_log(path: str):
    """Read-only. Returns ('ok', stamps) or ('missing', [])."""
    file = Path(path)
    if not file.is_file():
        return "missing", []
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "missing", []
    return "ok", parse_watchdog_stamps(text)


def hang_recoveries(rows: list, watchdog_status, stamps: list):
    """Each hung row needs an immediate next request that passed.

    watchdog_status is None when no log was requested, 'missing' when the
    file cannot be read, or 'ok' when stamps were parsed.
    """
    notes = []
    all_ok = True
    for index, row in enumerate(rows):
        if row.get("verdict") != "hung":
            continue
        nxt = rows[index + 1] if index + 1 < len(rows) else None
        if nxt is None:
            next_v = "missing"
            recovered = False
        elif timing_ok(nxt):
            next_v = "pass"
            recovered = True
        else:
            next_v = nxt.get("verdict") or "missing"
            recovered = False
        evidence = "next=%s" % next_v
        if watchdog_status is not None:
            if watchdog_status == "missing":
                watch = "missing"
                recovered = False
            else:
                stamp = watchdog_stamp_for_hang(row.get("ts") or "", stamps)
                if stamp is None:
                    watch = "none"
                    recovered = False
                else:
                    watch = stamp.strftime("%Y-%m-%d %H:%M:%S")
            evidence = "%s watchdog=%s" % (evidence, watch)
        if not recovered:
            all_ok = False
        notes.append(
            "HUNG soak idx=%s local_time=%s %s"
            % (row.get("idx"), row.get("ts"), evidence)
        )
    return all_ok, notes


def judge_ollama(process: str, port: str) -> str:
    if process == "up" or port == "open":
        return "FAIL"
    if process == "down" and port == "closed":
        return "PASS"
    return "UNKNOWN"


def evaluate_mem(platform_name: str, swap_text, vm_text, process: str, port: str) -> dict:
    pages = {"free": None, "active": None, "wired": None}
    used = None
    if platform_name != "darwin":
        swap_status = "UNKNOWN"
    else:
        used = parse_swap_used_mb(swap_text or "")
        pages = parse_vm_stat_pages(vm_text or "")
        if used is None:
            swap_status = "UNKNOWN"
        elif used < SWAP_LIMIT_MB:
            swap_status = "PASS"
        else:
            swap_status = "FAIL"
    if process not in ("up", "down", "unknown"):
        process = "unknown"
    if port not in ("open", "closed", "unknown"):
        port = "unknown"
    return {
        "platform": platform_name,
        "swap_status": swap_status,
        "swap_used_mb": used,
        "pages": pages,
        "process": process,
        "port": port,
        "ollama_status": judge_ollama(process, port),
    }


def render_mem(result: dict, ask_start: str, ask_end: str, baseline_swap=None):
    used = result.get("swap_used_mb")
    used_s = "unknown" if used is None else "%.2f" % used
    pages = result.get("pages") or {}

    def page(key: str) -> str:
        value = pages.get(key)
        return "unknown" if value is None else str(value)

    lines = [
        "%s swap used_mb=%s limit_mb=1024" % (result["swap_status"], used_s),
        "INFO vm_stat free_pages=%s active_pages=%s wired_pages=%s"
        % (page("free"), page("active"), page("wired")),
        "%s ollama process=%s port_11434=%s"
        % (result["ollama_status"], result["process"], result["port"]),
    ]
    if baseline_swap is not None:
        lines.append(
            "INFO baseline swap_used_mb=%.2f limit_mb=1024 clean_before_load=yes"
            % baseline_swap
        )
    ask_status, ask_detail = ask_mail_bar(ask_start, ask_end)
    lines.append("%s %s" % (ask_status, ask_detail))
    ok = (
        result["swap_status"] == "PASS"
        and result["ollama_status"] == "PASS"
        and ask_status == "PASS"
    )
    lines.append("OVERALL %s" % ("PASS" if ok else "FAIL"))
    return lines, (0 if ok else 1)


def _fmt_remaining(seconds: float) -> str:
    if abs(seconds - round(seconds)) < 0.001:
        return str(int(round(seconds)))
    return "%.3f" % seconds


def sleep_seconds(seconds: float) -> None:
    """Real sleep, unless the settle test hook says to skip it."""
    if os.environ.get("PATH_A_BENCH_TEST_HOOKS", "").strip() == "1":
        if os.environ.get("PATH_A_BENCH_SETTLE_SLEEP", "").strip() == "0":
            return
    time.sleep(seconds)


def countdown_wait(
    seconds: float,
    step: float,
    label: str,
    sleep_fn=None,
    emit=None,
) -> None:
    """Sleep seconds, printing `label remaining_s=` every `step` seconds."""
    if sleep_fn is None:
        sleep_fn = sleep_seconds
    if emit is None:
        emit = lambda line: print(line, flush=True)
    remaining = float(seconds)
    if remaining < 0 or remaining != remaining or remaining == float("inf"):
        raise ConfigError("bad %s" % label)
    if not (step > 0):
        raise ConfigError("bad %s" % label)
    while remaining > 0:
        emit("%s remaining_s=%s" % (label, _fmt_remaining(remaining)))
        chunk = step if remaining > step else remaining
        sleep_fn(chunk)
        remaining -= chunk
        if remaining < 1e-6:
            remaining = 0.0


def settle_wait(seconds: float, sleep_fn=None, emit=None) -> None:
    """Sleep seconds, printing a countdown line every 30 seconds."""
    countdown_wait(seconds, 30.0, "settle", sleep_fn=sleep_fn, emit=emit)


def idle_wait(seconds: float, sleep_fn=None, emit=None) -> None:
    """Sleep seconds, printing a countdown line every 60 seconds."""
    countdown_wait(seconds, 60.0, "idle", sleep_fn=sleep_fn, emit=emit)


def baseline_snapshot(result: dict, timestamp: Optional[str] = None) -> dict:
    pages = result.get("pages") or {}
    ollama_down = result.get("process") == "down" and result.get("port") == "closed"
    return {
        "timestamp": timestamp or local_iso(),
        "swap_used_mb": result.get("swap_used_mb"),
        "vm_stat": {
            "free_pages": pages.get("free"),
            "active_pages": pages.get("active"),
            "wired_pages": pages.get("wired"),
        },
        "ollama_down": ollama_down,
    }


def load_baseline(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError("baseline missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ConfigError("baseline unreadable")
    if not isinstance(data, dict):
        raise ConfigError("baseline unreadable")
    swap = data.get("swap_used_mb")
    if isinstance(swap, bool) or not isinstance(swap, (int, float)):
        raise ConfigError("baseline unreadable")
    value = float(swap)
    if value < 0 or value != value or value == float("inf") or value == float("-inf"):
        raise ConfigError("baseline unreadable")
    data["swap_used_mb"] = value
    return data


def write_baseline(path: Path, snapshot: dict) -> None:
    try:
        path.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        raise ConfigError("cannot write baseline")


def _hooks_enabled() -> bool:
    return os.environ.get("PATH_A_BENCH_TEST_HOOKS", "").strip() == "1"


def _run_text(argv: list) -> str:
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.decode("utf-8", errors="replace")


def ollama_process_state() -> str:
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "ollama"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if proc.returncode == 0:
        return "up"
    if proc.returncode == 1:
        return "down"
    return "unknown"


def ollama_port_state() -> str:
    sock = None
    try:
        sock = socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=1.0)
    except OSError:
        return "closed"
    else:
        return "open"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def collect_host_mem(platform_name: Optional[str] = None):
    injected = os.environ.get("PATH_A_BENCH_INJECT_SWAP_MB", "").strip()
    if _hooks_enabled() and injected:
        try:
            used = float(injected)
        except ValueError:
            raise ConfigError("bad injected swap")
        swap_text = (
            "vm.swapusage: total = 8192.00M  used = %.2fM  free = 0.00M\n" % used
        )
        free = int(os.environ.get("PATH_A_BENCH_VM_FREE", "10") or "10")
        active = int(os.environ.get("PATH_A_BENCH_VM_ACTIVE", "20") or "20")
        wired = int(os.environ.get("PATH_A_BENCH_VM_WIRED", "30") or "30")
        vm_text = (
            "Pages free: %d.\nPages active: %d.\nPages wired down: %d.\n"
            % (free, active, wired)
        )
        process = os.environ.get("PATH_A_BENCH_OLLAMA_PROCESS", "down").strip() or "down"
        port = os.environ.get("PATH_A_BENCH_OLLAMA_PORT", "closed").strip() or "closed"
        return "darwin", swap_text, vm_text, process, port
    platform_name = platform_name or sys.platform
    if platform_name != "darwin":
        swap_text = None
        vm_text = None
    else:
        swap_text = _run_text(["sysctl", "vm.swapusage"])
        vm_text = _run_text(["vm_stat"])
    return (
        platform_name,
        swap_text,
        vm_text,
        ollama_process_state(),
        ollama_port_state(),
    )


def _mem_row(result, ask_start, ask_end, code, baseline_swap=None, invalid=False) -> dict:
    pages = (result or {}).get("pages") or {}
    if invalid:
        verdict = "invalid"
        error = "machine dirty before model load"
    elif code == 0:
        verdict = "pass"
        error = None
    else:
        verdict = "fail"
        error = None
    row = {
        "kind": "mem",
        "ts": local_iso(),
        "mode": "mem",
        "swap_used_mb": None if result is None else result.get("swap_used_mb"),
        "swap_status": None if result is None else result.get("swap_status"),
        "vm_free_pages": pages.get("free"),
        "vm_active_pages": pages.get("active"),
        "vm_wired_pages": pages.get("wired"),
        "ollama_process": None if result is None else result.get("process"),
        "ollama_port": None if result is None else result.get("port"),
        "ollama_status": None if result is None else result.get("ollama_status"),
        "ask_mail_sha256_start": ask_start,
        "ask_mail_sha256_end": ask_end,
        "verdict": verdict,
        "error": error,
    }
    if baseline_swap is not None:
        row["baseline_swap_used_mb"] = baseline_swap
    return row


def run_mem(args) -> int:
    ask_path = Path(args.ask_mail)
    baseline_path = (getattr(args, "baseline", "") or "").strip()
    baseline_out = (getattr(args, "baseline_out", "") or "").strip()
    settle = float(getattr(args, "settle", 0) or 0)
    if settle < 0:
        raise ConfigError("bad settle")
    if settle and not baseline_path:
        raise ConfigError("settle requires --baseline")
    baseline = load_baseline(Path(baseline_path)) if baseline_path else None
    jsonl = Jsonl(args.out)
    try:
        if baseline is not None and float(baseline["swap_used_mb"]) >= SWAP_LIMIT_MB:
            swap = float(baseline["swap_used_mb"])
            ask_start = hash_ask_mail(ask_path)
            ask_end = hash_ask_mail(ask_path)
            lines = [
                (
                    "INVALID baseline swap_used_mb=%.2f limit_mb=1024 "
                    "machine dirty before model load" % swap
                ),
                "%s %s" % ask_mail_bar(ask_start, ask_end),
                "OVERALL INVALID",
            ]
            jsonl.write(
                _mem_row(None, ask_start, ask_end, 5, baseline_swap=swap, invalid=True)
            )
            for line in lines:
                print(line, flush=True)
            return 5
        ask_start = hash_ask_mail(ask_path)
        if settle:
            settle_wait(settle)
        collected = collect_host_mem()
        ask_end = hash_ask_mail(ask_path)
        result = evaluate_mem(*collected)
        if baseline_out:
            write_baseline(Path(baseline_out), baseline_snapshot(result))
        base_swap = None if baseline is None else float(baseline["swap_used_mb"])
        lines, code = render_mem(result, ask_start, ask_end, baseline_swap=base_swap)
        jsonl.write(_mem_row(result, ask_start, ask_end, code, baseline_swap=base_swap))
        for line in lines:
            print(line, flush=True)
        return code
    finally:
        jsonl.close()


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("bad timeout")
    if not (timeout > 0):
        raise argparse.ArgumentTypeError("bad timeout")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default="http://127.0.0.1:1234")
    common.add_argument("--model", default="")
    common.add_argument("--fixture", default=None)
    common.add_argument("--max-tokens", type=int, default=512)
    common.add_argument("--timeout", type=_positive_timeout, default=None)
    common.add_argument("--out", default="")
    common.add_argument("--ask-mail", default=str(default_ask_mail()))

    parser = argparse.ArgumentParser(
        prog="path_a_bench.py",
        description="Read-only Path A Qwen chat benchmark (HTTP and system stats only).",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    sub.add_parser("probe", parents=[common], help="one max_tokens=1 readiness POST")

    warm = sub.add_parser("warm", parents=[common], help="N sequential paste requests")
    warm.add_argument("-n", "--n", type=int, default=10)
    warm.add_argument("--fixtures-dir", default="")

    cold = sub.add_parser("cold", parents=[common], help="one request after an operator idle")
    cold.add_argument("--fixtures-dir", default="")
    cold.add_argument("--idle", type=float, default=None)

    soak = sub.add_parser("soak", parents=[common], help="N requests on an interval")
    soak.add_argument("-n", "--n", type=int, default=48)
    soak.add_argument("--interval", type=float, default=300.0)
    soak.add_argument("--fixtures-dir", default="")
    soak.add_argument("--continue-on-hang", action="store_true")
    soak.add_argument("--watchdog-log", default="")
    soak.add_argument("--mem-at", type=int, default=None)

    mem = sub.add_parser("mem", parents=[common], help="read-only swap, vm_stat, and Ollama checks")
    mem.add_argument("--baseline-out", default="")
    mem.add_argument("--baseline", default="")
    mem.add_argument("--settle", type=float, default=0.0)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 2
    try:
        if args.mode == "mem":
            return run_mem(args)
        if args.mode == "probe":
            return run_probe(args)
        return run_chat(args)
    except ConfigError as exc:
        print("error: %s" % scrub_text(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
