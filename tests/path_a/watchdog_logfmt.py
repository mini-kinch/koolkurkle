"""Format one qwen-mlx-watchdog.sh log() line from that script's printf."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / "scripts" / "qwen-mlx-watchdog.sh"

# log() {
#   printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
_LOG_PRINTF = re.compile(r"^log\(\) \{\n(?:.*\n)*?[ \t]*printf '([^']+)'", re.M)


def log_printf_format() -> str:
    text = WATCHDOG.read_text(encoding="utf-8")
    match = _LOG_PRINTF.search(text)
    if not match:
        raise RuntimeError("watchdog log() printf format not found")
    return match.group(1)


def format_watchdog_line(ts: str, message: str) -> str:
    """Apply log()'s printf format. Includes the trailing newline printf writes."""
    fmt = log_printf_format()
    out = []
    args = [ts, message]
    index = 0
    i = 0
    while i < len(fmt):
        if fmt.startswith("%s", i):
            if index >= len(args):
                raise RuntimeError("watchdog printf has more %s than arguments")
            out.append(args[index])
            index += 1
            i += 2
            continue
        if fmt.startswith("\\n", i):
            out.append("\n")
            i += 2
            continue
        out.append(fmt[i])
        i += 1
    if index != len(args):
        raise RuntimeError("watchdog printf did not consume every argument")
    return "".join(out)
