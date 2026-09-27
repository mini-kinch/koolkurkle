#!/usr/bin/env python3
"""Out-of-process P1 text extractor. One file per invocation.

The parent process must not parse attachment bytes. This worker reads
one staged file and writes a sidecar .txt. It never unpacks an archive
and never executes attachment contents.

Kinds: text, csv, html, pdf. PDF uses ``pdftotext`` when it is on
PATH. A missing ``pdftotext`` records ``extractor_missing`` and exits 0.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import att0_constraints as att0  # noqa: E402

_KINDS = frozenset({"text", "csv", "html", "pdf"})
_ARCHIVE_SUFFIXES = frozenset({
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
})
_SKIP_HTML = frozenset({"script", "style"})


class _Charset(Exception):
    """UTF-8 decode failed. Callers record status error."""


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


def _fail(error: str, *, extractor: str = "p1-worker") -> int:
    return _emit(
        {
            "bytes_out": 0,
            "error": error,
            "extractor": extractor,
            "status": "error",
            "truncated": False,
        }
    )


def _archive_magic(head: bytes) -> bool:
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return True
    if head.startswith(b"Rar!\x1a\x07"):
        return True
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return True
    if head.startswith(b"\x1f\x8b"):
        return True
    if len(head) > 262 and head[257:262] == b"ustar":
        return True
    return False


def _looks_like_archive(path: Path) -> bool:
    name = path.name.lower()
    if any(name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        return True
    try:
        with path.open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return False
    return _archive_magic(head)


def _skipped_archive() -> int:
    return _emit(
        {
            "bytes_out": 0,
            "error": "skipped_archive",
            "extractor": "none",
            "status": "skipped_archive",
            "truncated": False,
        }
    )


def _too_big(size: int) -> int:
    return _emit(
        {
            "bytes_out": 0,
            "error": "too_big",
            "extractor": "none",
            "status": "too_big",
            "truncated": False,
            "size": int(size),
        }
    )


def _decode_utf8(blob: bytes) -> str:
    if blob.startswith(b"\xef\xbb\xbf"):
        blob = blob[3:]
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Charset() from exc


def _html_to_text(raw: str) -> str:
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs) -> None:
            del attrs
            if tag.lower() in _SKIP_HTML:
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() in _SKIP_HTML and self._skip:
                self._skip -= 1

        def handle_data(self, data: str) -> None:
            if self._skip or not data:
                return
            self.parts.append(data)

    parser = _Text()
    parser.feed(raw)
    parser.close()
    return "\n".join(part.strip() for part in parser.parts if part.strip())


def _write_text(dest: Path, text: str, *, extractor: str) -> int:
    clipped = att0.truncate_extract_text(text)
    body = clipped["text"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    return _emit(
        {
            "bytes_out": dest.stat().st_size,
            "error": None,
            "extractor": extractor,
            "status": "ok",
            "truncated": bool(clipped["truncated"]),
            "flag": clipped["flag"],
        }
    )


def _extract_text_kind(src: Path, dest: Path, *, kind: str) -> int:
    try:
        blob = src.read_bytes()
        text = _decode_utf8(blob)
    except _Charset:
        return _fail("charset")
    except OSError:
        return _fail("read_failed")
    if kind == "html":
        text = _html_to_text(text)
        extractor = "html-p1"
    elif kind == "csv":
        extractor = "csv-p1"
    else:
        extractor = "text-p1"
    return _write_text(dest, text, extractor=extractor)


def _extract_pdf(src: Path, dest: Path, *, timeout_s: float) -> int:
    binary = shutil.which("pdftotext")
    if not binary:
        return _emit(
            {
                "bytes_out": 0,
                "error": "extractor_missing",
                "extractor": "pdftotext",
                "status": "extractor_missing",
                "truncated": False,
            }
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [binary, "-q", "-enc", "UTF-8", str(src), str(dest)],
            timeout=timeout_s,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        dest.unlink(missing_ok=True)
        return _emit(
            {
                "bytes_out": 0,
                "error": "timeout",
                "extractor": "pdftotext",
                "status": "timeout",
                "truncated": False,
            }
        )
    if proc.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        return _fail("pdftotext_failed", extractor="pdftotext")
    try:
        text = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        dest.unlink(missing_ok=True)
        return _fail("pdftotext_output", extractor="pdftotext")
    return _write_text(dest, text, extractor="pdftotext")


def extract_one(src: Path, dest: Path, *, kind: str, timeout_s: float) -> int:
    """Extract one staged file. Exit 0 when the status was recorded."""
    if kind not in _KINDS:
        return _fail("bad_kind")
    if not src.is_file():
        return _fail("not_a_file")
    try:
        size = src.stat().st_size
    except OSError:
        return _fail("read_failed")
    if size > att0.BLOB_TOO_BIG_BYTES:
        return _too_big(size)
    if _looks_like_archive(src):
        return _skipped_archive()
    if kind == "pdf":
        return _extract_pdf(src, dest, timeout_s=timeout_s)
    return _extract_text_kind(src, dest, kind=kind)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P1 attachment text worker.")
    parser.add_argument("--kind", required=True)
    parser.add_argument("--src", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timeout_s = float(args.timeout)
    if timeout_s <= 0:
        return _emit(
            {
                "bytes_out": 0,
                "error": "timeout",
                "extractor": "p1-worker",
                "status": "timeout",
                "truncated": False,
            }
        )
    return extract_one(
        Path(args.src),
        Path(args.dest),
        kind=str(args.kind),
        timeout_s=timeout_s,
    )


if __name__ == "__main__":
    raise SystemExit(main())
