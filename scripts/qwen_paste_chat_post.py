#!/usr/bin/env python3
"""DATA+QUESTION paste on stdin → /v1/chat/completions → assistant text on stdout.

v4 2026-09-25 — fail-fast generate probe + main timeout 180s. thinking OFF.
Never dump JSON to stdout. Stdout stays empty on failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SCRIPT_VER = "qwen_paste_chat_post.py v4 2026-09-25"

RESTART_HINT = (
    "hint: /v1/models may be up but generate is wedged — run qwen-chat-down.sh "
    "then qwen-chat-up.sh, confirm a tiny chat probe, then retry "
    "(see docs/path-a/mini-install.md)"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _probe_enabled() -> bool:
    return os.environ.get("QWEN_PROBE", "1").strip() != "0"


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return True
        return reason is not None and "timed out" in str(reason).lower()
    return False


def _hint() -> None:
    print(RESTART_HINT, file=sys.stderr)


def _post(url: str, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _extract(data: dict):
    choice = data["choices"][0]
    msg = choice["message"]
    finish = str(choice.get("finish_reason") or "")
    content = msg.get("content")
    reasoning = msg.get("reasoning")
    content_s = content.strip() if isinstance(content, str) else ""
    reasoning_s = reasoning.strip() if isinstance(reasoning, str) else ""
    return content_s, reasoning_s, finish, msg


def _probe(url: str, model: str, timeout: int) -> None:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "/no_think"}],
        "max_tokens": 1,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    _post(url, body, timeout)


def main() -> int:
    default_timeout = _env_int("QWEN_TIMEOUT", 180)
    probe_on = _probe_enabled()
    probe_timeout = _env_int("QWEN_PROBE_TIMEOUT", 20)

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:1234/v1")
    ap.add_argument("--model", default="mlx-community/Qwen3.8-27B-4bit")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument(
        "--thinking",
        action="store_true",
        help="Allow Qwen thinking/reasoning tokens (default: OFF).",
    )
    ap.add_argument("--timeout", type=int, default=default_timeout)
    args = ap.parse_args()

    paste = sys.stdin.read()
    if not paste.strip():
        print("error: empty paste on stdin", file=sys.stderr)
        return 2

    enable_thinking = bool(args.thinking)
    url = args.base.rstrip("/") + "/chat/completions"
    user_content = paste if enable_thinking else (paste.rstrip() + "\n\n/no_think")

    body = {
        "model": args.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Answer only from DATA. Cite date/from/subject when relevant. "
                    "If DATA is insufficient, say so. Do not invent. "
                    "Reply with the final answer only — no chain-of-thought."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }

    print(
        "%s | posting %s | enable_thinking=%s | max_tokens=%s | paste_bytes=%s | probe=%s | probe_timeout=%s | timeout=%s"
        % (
            SCRIPT_VER,
            url,
            enable_thinking,
            args.max_tokens,
            len(paste.encode()),
            "on" if probe_on else "off",
            probe_timeout,
            args.timeout,
        ),
        file=sys.stderr,
    )

    if probe_on:
        try:
            _probe(url, args.model, probe_timeout)
        except Exception as e:
            print("error: probe failed: %s" % e, file=sys.stderr)
            _hint()
            return 3

    try:
        data = _post(url, body, args.timeout)
    except urllib.error.HTTPError as e:
        print("error: HTTP %s %s" % (e.code, e.read()[:500]), file=sys.stderr)
        return 2
    except Exception as e:
        print("error: %s" % e, file=sys.stderr)
        if _is_timeout(e):
            _hint()
            return 3
        return 2

    try:
        content_s, reasoning_s, finish, msg = _extract(data)
    except Exception as e:
        print("error: bad response shape: %s" % e, file=sys.stderr)
        print("error: top_keys=%s" % list(data.keys()), file=sys.stderr)
        return 2

    print(
        "response | msg_keys=%s | content_len=%s | reasoning_len=%s | finish=%s | usage=%s"
        % (
            sorted(msg.keys()),
            len(content_s),
            len(reasoning_s),
            finish,
            data.get("usage"),
        ),
        file=sys.stderr,
    )

    if not content_s and reasoning_s and not enable_thinking:
        print(
            "warn: reasoning-only despite enable_thinking=false; retry once",
            file=sys.stderr,
        )
        body2 = dict(body)
        body2["max_tokens"] = max(args.max_tokens, 768)
        body2["messages"] = [
            body["messages"][0],
            {
                "role": "user",
                "content": paste.rstrip() + "\n\n/no_think\nFinal answer only.",
            },
        ]
        body2["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            data = _post(url, body2, args.timeout)
            content_s, reasoning_s, finish, msg = _extract(data)
            print(
                "retry | msg_keys=%s | content_len=%s | reasoning_len=%s | finish=%s | usage=%s"
                % (
                    sorted(msg.keys()),
                    len(content_s),
                    len(reasoning_s),
                    finish,
                    data.get("usage"),
                ),
                file=sys.stderr,
            )
        except Exception as e:
            print("error: retry failed: %s" % e, file=sys.stderr)
            if _is_timeout(e):
                _hint()
                return 3
            return 2

    if content_s:
        text = content_s
    elif reasoning_s and enable_thinking:
        text = reasoning_s
    elif reasoning_s:
        print(
            "error: still reasoning-only after retry (finish=%s). "
            "Server needs --chat-template-args {\"enable_thinking\":false}."
            % finish,
            file=sys.stderr,
        )
        return 3
    else:
        print("error: empty assistant content and reasoning", file=sys.stderr)
        return 2

    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
