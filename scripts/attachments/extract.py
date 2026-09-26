#!/usr/bin/env python3
"""File-only ATT-0 text extraction.

Stdlib for plain text, markdown, csv, PDF text layers, and Office
Open XML. Optional pypdf is imported only inside ImportError guard
and is used when the stdlib PDF reader does not recognize the file.
Missing optional modules yield status ``unsupported``.

Does not spawn a process, invoke a shell, or execute attachment bytes.
v1 skips OCR, images, video, audio, archives, and executables.
"""

from __future__ import annotations

import importlib
import io
import re
import signal
import threading
import time
import zipfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

EXTRACTOR_VERSION = "1"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_PAGES = 500
DEFAULT_MAX_CHARS = 2 * 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

_HARD_SKIPS = {
    "skip_image",
    "skip_video",
    "skip_audio",
    "skip_executable",
    "skip_archive",
}

_SUFFIX_KIND = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".png": "skip_image",
    ".jpg": "skip_image",
    ".jpeg": "skip_image",
    ".gif": "skip_image",
    ".webp": "skip_image",
    ".tif": "skip_image",
    ".tiff": "skip_image",
    ".bmp": "skip_image",
    ".mp4": "skip_video",
    ".mov": "skip_video",
    ".mkv": "skip_video",
    ".webm": "skip_video",
    ".avi": "skip_video",
    ".mp3": "skip_audio",
    ".wav": "skip_audio",
    ".m4a": "skip_audio",
    ".flac": "skip_audio",
    ".ogg": "skip_audio",
    ".zip": "skip_archive",
    ".rar": "skip_archive",
    ".7z": "skip_archive",
    ".tar": "skip_archive",
    ".gz": "skip_archive",
    ".tgz": "skip_archive",
    ".exe": "skip_executable",
    ".dll": "skip_executable",
    ".dmg": "skip_executable",
    ".pkg": "skip_executable",
    ".msi": "skip_executable",
    ".app": "skip_executable",
    ".so": "skip_executable",
}

_MIME_KIND = {
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/zip": "skip_archive",
    "application/x-rar-compressed": "skip_archive",
    "application/vnd.rar": "skip_archive",
    "application/x-7z-compressed": "skip_archive",
    "application/gzip": "skip_archive",
    "application/x-tar": "skip_archive",
    "application/x-msdownload": "skip_executable",
    "application/x-dosexec": "skip_executable",
    "application/x-apple-diskimage": "skip_executable",
    "application/vnd.microsoft.portable-executable": "skip_executable",
    "application/x-mach-binary": "skip_executable",
}

_EXTRACTOR_NAME = {
    "text": "text-stdlib",
    "markdown": "markdown-stdlib",
    "csv": "csv-stdlib",
    "pdf": "pdf-stdlib",
    "docx": "docx-stdlib",
    "xlsx": "xlsx-stdlib",
    "pptx": "pptx-stdlib",
}

_timeout_armed = False


class ExtractTimeout(Exception):
    """Per-file timeout. Internal; callers see status ``timeout``."""


@dataclass
class PageText:
    page: int
    text: str


@dataclass
class ExtractResult:
    status: str
    extractor: str
    extractor_version: str
    text: str
    pages: tuple[PageText, ...]
    page_count: int
    error: str | None
    timings: dict[str, int]
    truncated: bool = False
    extra: dict[str, str] = field(default_factory=dict)


def optional_module(name: str):
    """Import ``name`` or return None when it is not installed."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _handle_alarm(signum: int, frame: object) -> None:
    del signum, frame
    if _timeout_armed:
        raise ExtractTimeout()


def _call_with_timeout(fn, timeout_s: float):
    if timeout_s <= 0:
        raise ExtractTimeout()
    if threading.current_thread() is not threading.main_thread():
        return fn()
    global _timeout_armed
    old = signal.signal(signal.SIGALRM, _handle_alarm)
    _timeout_armed = True
    signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
    try:
        return fn()
    finally:
        _timeout_armed = False
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old)


def _empty(
    status: str,
    *,
    error: str | None = None,
    extractor: str = "none",
    elapsed_ms: int = 0,
    nbytes: int = 0,
    truncated: bool = False,
    page_count: int = 0,
    pages: tuple[PageText, ...] = (),
    text: str = "",
) -> ExtractResult:
    timings = {"elapsed_ms": int(elapsed_ms)}
    if nbytes:
        timings["bytes"] = int(nbytes)
    return ExtractResult(
        status=status,
        extractor=extractor,
        extractor_version=EXTRACTOR_VERSION,
        text=text,
        pages=pages,
        page_count=page_count,
        error=error,
        timings=timings,
        truncated=truncated,
    )


def _magic_kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"):
        return "skip_image"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "skip_image"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "skip_image"
    if data.startswith(b"ID3") or data.startswith(b"\xff\xfb") or data.startswith(b"\xff\xf3"):
        return "skip_audio"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return "skip_audio"
    if data.startswith(b"OggS"):
        return "skip_audio"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "skip_video"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "skip_video"
    if (
        data.startswith(b"MZ")
        or data.startswith(b"\x7fELF")
        or data.startswith(b"\xfe\xed\xfa")
        or data.startswith(b"\xcf\xfa\xed\xfe")
        or data.startswith(b"\xca\xfe\xba\xbe")
    ):
        return "skip_executable"
    if data.startswith(b"Rar!") or data.startswith(b"7z\xbc\xaf\x27\x1c") or data.startswith(b"\x1f\x8b"):
        return "skip_archive"
    if len(data) >= 262 and data[257:262] == b"ustar":
        return "skip_archive"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(b"%PDF"):
        return "pdf"
    return None


def _mime_kind(mime: str | None) -> str | None:
    if not mime:
        return None
    kind = mime.split(";", 1)[0].strip().lower()
    if kind in _MIME_KIND:
        return _MIME_KIND[kind]
    if kind.startswith("image/"):
        return "skip_image"
    if kind.startswith("audio/"):
        return "skip_audio"
    if kind.startswith("video/"):
        return "skip_video"
    return None


def _declared_kind(mime: str | None, suffix: str) -> str | None:
    from_mime = _mime_kind(mime)
    if from_mime:
        return from_mime
    return _SUFFIX_KIND.get(suffix.lower())


def _zip_office_kind(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names or any(
        name.startswith("ppt/slides/slide") for name in names
    ):
        return "pptx"
    return None


def _classify(data: bytes, mime: str | None, suffix: str) -> str:
    magic = _magic_kind(data)
    declared = _declared_kind(mime, suffix)
    if magic in _HARD_SKIPS:
        return magic
    if declared in _HARD_SKIPS:
        return declared
    if declared in ("docx", "xlsx", "pptx"):
        return declared
    if magic == "zip":
        sniffed = _zip_office_kind(data)
        if sniffed:
            return sniffed
        return "skip_archive"
    if declared:
        return declared
    if magic == "pdf":
        return "pdf"
    return "unsupported"


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", data, 0, 1, "charset")


def _pages_from_text(text: str) -> list[tuple[int, str]]:
    """Split on form feed. Page numbers keep the original section index."""
    parts = text.split("\f")
    return [(index, part.strip()) for index, part in enumerate(parts, 1)]


def _cap_pages(
    all_pages: list[tuple[int, str]],
    *,
    max_pages: int,
    max_chars: int,
    page_count: int,
) -> tuple[tuple[PageText, ...], str | None, int]:
    kept: list[PageText] = []
    used = 0
    reason: str | None = None
    visited = 0
    for page_no, text in all_pages:
        if visited >= max_pages:
            reason = "max_pages"
            break
        visited += 1
        if not text:
            continue
        room = max_chars - used
        if room <= 0:
            reason = "max_chars"
            break
        if len(text) > room:
            kept.append(PageText(page=page_no, text=text[:room]))
            reason = "max_chars"
            break
        kept.append(PageText(page=page_no, text=text))
        used += len(text)
    return tuple(kept), reason, page_count


def _result_from_pages(
    kind: str,
    all_pages: list[tuple[int, str]],
    *,
    page_count: int,
    max_pages: int,
    max_chars: int,
    nbytes: int,
    extractor: str | None = None,
) -> ExtractResult:
    pages, reason, count = _cap_pages(
        all_pages,
        max_pages=max_pages,
        max_chars=max_chars,
        page_count=page_count,
    )
    name = extractor or _EXTRACTOR_NAME.get(kind, "none")
    body = "\n".join(page.text for page in pages)
    if kind == "pdf" and not body and reason is None:
        return _empty(
            "skip_ocr",
            error="skip_ocr",
            extractor=name,
            nbytes=nbytes,
            page_count=count,
        )
    if reason:
        return _empty(
            "capped",
            error=reason,
            extractor=name,
            nbytes=nbytes,
            truncated=True,
            page_count=count,
            pages=pages,
            text=body,
        )
    return _empty(
        "ok",
        error=None,
        extractor=name,
        nbytes=nbytes,
        page_count=count,
        pages=pages,
        text=body,
    )


def _find_endobj(data: bytes, start: int) -> int | None:
    pos = start
    limit = len(data)
    while pos < limit:
        endobj = data.find(b"endobj", pos)
        if endobj == -1:
            return None
        stream = data.find(b"stream", pos)
        if stream == -1 or stream > endobj:
            return endobj
        if stream >= 3 and data[stream - 3 : stream + 6] == b"endstream":
            pos = stream + 6
            continue
        endstream = data.find(b"endstream", stream + 6)
        if endstream == -1:
            return None
        pos = endstream + len(b"endstream")
    return None


def _iter_objects(data: bytes):
    header = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
    pos = 0
    seen = 0
    while seen < 20000:
        match = header.search(data, pos)
        if not match:
            return
        number = int(match.group(1))
        start = match.end()
        end = _find_endobj(data, start)
        if end is None:
            return
        yield number, data[start:end]
        pos = end + len(b"endobj")
        seen += 1


def _split_stream(body: bytes) -> tuple[bytes, bytes]:
    marker = body.find(b"stream")
    if marker == -1:
        return body, b""
    if marker >= 3 and body[marker - 3 : marker + 6] == b"endstream":
        return body, b""
    dict_bytes = body[:marker]
    start = marker + len(b"stream")
    if body[start : start + 2] == b"\r\n":
        start += 2
    elif start < len(body) and body[start : start + 1] in (b"\n", b"\r"):
        start += 1
    end = body.find(b"endstream", start)
    if end == -1:
        return dict_bytes, b""
    payload = body[start:end]
    if payload.endswith(b"\r\n"):
        payload = payload[:-2]
    elif payload.endswith(b"\n"):
        payload = payload[:-1]
    return dict_bytes, payload


def _inflate(dict_bytes: bytes, payload: bytes) -> bytes | None:
    if not payload:
        return b""
    if b"FlateDecode" not in dict_bytes:
        return payload
    for args in ((payload,), (payload, -15)):
        try:
            return zlib.decompress(*args)
        except zlib.error:
            continue
    return None


def _read_literal(text: str, index: int) -> tuple[str, int]:
    index += 1
    out: list[str] = []
    depth = 1
    n = len(text)
    while index < n and depth:
        char = text[index]
        if char == "\\":
            index += 1
            if index >= n:
                break
            esc = text[index]
            mapping = {
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "b": "\b",
                "f": "\f",
                "(": "(",
                ")": ")",
                "\\": "\\",
            }
            if esc in mapping:
                out.append(mapping[esc])
                index += 1
            elif esc.isdigit():
                octal = esc
                index += 1
                for _ in range(2):
                    if index < n and text[index].isdigit():
                        octal += text[index]
                        index += 1
                    else:
                        break
                out.append(chr(int(octal, 8) % 256))
            elif esc in "\n\r":
                index += 1
            else:
                out.append(esc)
                index += 1
            continue
        if char == "(":
            depth += 1
            out.append("(")
            index += 1
            continue
        if char == ")":
            depth -= 1
            if depth:
                out.append(")")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out), index


def _read_hex(text: str, index: int) -> tuple[str, int]:
    index += 1
    hex_chars: list[str] = []
    n = len(text)
    while index < n and text[index] != ">":
        if text[index] not in " \t\r\n":
            hex_chars.append(text[index])
        index += 1
    if index < n and text[index] == ">":
        index += 1
    blob = "".join(hex_chars)
    if len(blob) % 2:
        blob += "0"
    try:
        raw = bytes.fromhex(blob)
    except ValueError:
        return "", index
    return raw.decode("latin-1", errors="ignore"), index


def _next_op(text: str, index: int) -> str | None:
    match = re.match(r"\s*(Tj|TJ|')", text[index:])
    if not match:
        return None
    return match.group(1)


def _pdf_text(stream: bytes) -> str:
    text = stream.decode("latin-1", errors="ignore")
    parts: list[str] = []
    index = 0
    n = len(text)
    while index < n:
        char = text[index]
        if char == "[":
            index += 1
            buf: list[str] = []
            while index < n and text[index] != "]":
                if text[index] == "(":
                    literal, index = _read_literal(text, index)
                    buf.append(literal)
                elif text[index] == "<" and (index + 1 >= n or text[index + 1] != "<"):
                    literal, index = _read_hex(text, index)
                    buf.append(literal)
                else:
                    index += 1
            if index < n and text[index] == "]":
                index += 1
            if _next_op(text, index) == "TJ":
                parts.append("".join(buf))
            continue
        if char == "(":
            literal, index = _read_literal(text, index)
            op = _next_op(text, index)
            if op in ("Tj", "'"):
                parts.append(literal)
                if op == "'":
                    parts.append("\n")
            continue
        if char == "<" and (index + 1 >= n or text[index + 1] != "<"):
            literal, index = _read_hex(text, index)
            if _next_op(text, index) in ("Tj", "'"):
                parts.append(literal)
            continue
        index += 1
    return "".join(parts).strip()


def _pdf_encrypted(data: bytes) -> bool:
    if re.search(br"/Encrypt\s+\d+\s+\d+\s+R", data):
        return True
    if re.search(br"/Encrypt\s*<<", data):
        return True
    return False


def _content_refs(dict_bytes: bytes) -> list[int]:
    match = re.search(rb"/Contents\s*(\[.*?\]|\d+\s+\d+\s+R)", dict_bytes, re.S)
    if not match:
        return []
    return [int(num) for num in re.findall(rb"(\d+)\s+\d+\s+R", match.group(1))]


def _is_page(dict_bytes: bytes) -> bool:
    return re.search(rb"/Type\s*/Page(?!s)\b", dict_bytes) is not None


def _ordered_pages(obj_map: dict[int, bytes]) -> list[int]:
    catalog = b""
    for body in obj_map.values():
        dict_bytes, _payload = _split_stream(body)
        if re.search(rb"/Type\s*/Catalog\b", dict_bytes):
            catalog = dict_bytes
            break
    pages_ref = None
    if catalog:
        match = re.search(rb"/Pages\s+(\d+)\s+\d+\s+R", catalog)
        if match:
            pages_ref = int(match.group(1))
    ordered: list[int] = []
    seen: set[int] = set()

    def walk(ref: int) -> None:
        if ref in seen or ref not in obj_map:
            return
        seen.add(ref)
        dict_bytes, _payload = _split_stream(obj_map[ref])
        if _is_page(dict_bytes):
            ordered.append(ref)
            return
        kids = re.search(rb"/Kids\s*\[(.*?)\]", dict_bytes, re.S)
        if not kids:
            return
        for num in re.findall(rb"(\d+)\s+\d+\s+R", kids.group(1)):
            walk(int(num))

    if pages_ref is not None:
        walk(pages_ref)
    if ordered:
        return ordered
    for num in sorted(obj_map):
        dict_bytes, _payload = _split_stream(obj_map[num])
        if _is_page(dict_bytes):
            ordered.append(num)
    return ordered


def _stdlib_pdf_pages(data: bytes) -> list[tuple[int, str]] | None:
    if not data.startswith(b"%PDF"):
        return None
    obj_map = {num: body for num, body in _iter_objects(data)}
    if not obj_map:
        return None
    page_ids = _ordered_pages(obj_map)
    if not page_ids:
        return None
    pages: list[tuple[int, str]] = []
    for index, page_id in enumerate(page_ids, 1):
        dict_bytes, _payload = _split_stream(obj_map[page_id])
        chunks: list[str] = []
        for ref in _content_refs(dict_bytes):
            body = obj_map.get(ref)
            if body is None:
                continue
            content_dict, payload = _split_stream(body)
            inflated = _inflate(content_dict, payload)
            if inflated is None:
                continue
            text = _pdf_text(inflated)
            if text:
                chunks.append(text)
        pages.append((index, "\n".join(chunks).strip()))
    return pages


def _pypdf_pages(data: bytes) -> list[tuple[int, str]] | None:
    module = optional_module("pypdf")
    if module is None:
        return None
    try:
        reader = module.PdfReader(io.BytesIO(data))
        pages = []
        for index, page in enumerate(reader.pages, 1):
            extracted = page.extract_text() or ""
            pages.append((index, extracted.strip()))
        return pages
    except Exception:
        return None


def _pdf_pages(data: bytes) -> tuple[list[tuple[int, str]] | None, str]:
    stdlib_pages = _stdlib_pdf_pages(data)
    if stdlib_pages is not None:
        return stdlib_pages, "pdf-stdlib"
    optional_pages = _pypdf_pages(data)
    if optional_pages is not None:
        return optional_pages, "pdf-pypdf"
    return None, "pdf-stdlib"


def _member_bytes(zf: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise _TooBig()
    return zf.read(name)


class _TooBig(Exception):
    pass


def _local(tag: str, ns: str) -> str:
    return "%s%s" % (ns, tag)


def _docx_pages(data: bytes, max_bytes: int) -> list[tuple[int, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "word/document.xml" not in zf.namelist():
            raise _Unsupported()
        xml_bytes = _member_bytes(zf, "word/document.xml", max_bytes)
    root = ET.fromstring(xml_bytes)
    pages: list[list[str]] = [[]]
    for paragraph in root.iter(_local("p", _W_NS)):
        bucket: list[str] = []
        for node in paragraph.iter():
            break_type = node.attrib.get(_local("type", _W_NS), node.attrib.get("type"))
            if node.tag == _local("br", _W_NS) and break_type == "page":
                pages[-1].append("".join(bucket))
                bucket = []
                pages.append([])
            elif node.tag == _local("t", _W_NS) and node.text:
                bucket.append(node.text)
            elif node.tag == _local("tab", _W_NS):
                bucket.append("\t")
        pages[-1].append("".join(bucket))
    out = []
    for index, chunks in enumerate(pages, 1):
        text = "\n".join(part for part in chunks if part).strip()
        out.append((index, text))
    return out


def _shared_strings(zf: zipfile.ZipFile, max_bytes: int) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(_member_bytes(zf, "xl/sharedStrings.xml", max_bytes))
    strings = []
    for item in root.findall(_local("si", _S_NS)):
        parts = [node.text or "" for node in item.iter(_local("t", _S_NS))]
        strings.append("".join(parts))
    return strings


def _sheet_names(zf: zipfile.ZipFile) -> list[str]:
    names = [
        name
        for name in zf.namelist()
        if re.match(r"xl/worksheets/sheet\d+\.xml$", name)
    ]

    def sort_key(name: str) -> int:
        match = re.search(r"sheet(\d+)", name)
        return int(match.group(1)) if match else 0

    return sorted(names, key=sort_key)


def _xlsx_pages(data: bytes, max_bytes: int) -> list[tuple[int, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        sheets = _sheet_names(zf)
        if not sheets:
            raise _Unsupported()
        strings = _shared_strings(zf, max_bytes)
        pages = []
        for index, name in enumerate(sheets, 1):
            root = ET.fromstring(_member_bytes(zf, name, max_bytes))
            rows: list[str] = []
            for row in root.iter(_local("row", _S_NS)):
                cells: list[str] = []
                for cell in row.findall(_local("c", _S_NS)):
                    value = _xlsx_cell(cell, strings)
                    if value:
                        cells.append(value)
                if cells:
                    rows.append("\t".join(cells))
            pages.append((index, "\n".join(rows).strip()))
        return pages


def _xlsx_cell(cell: ET.Element, strings: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        parts = [node.text or "" for node in cell.iter(_local("t", _S_NS))]
        return "".join(parts)
    node = cell.find(_local("v", _S_NS))
    if node is None or node.text is None:
        return ""
    if kind == "s":
        try:
            idx = int(node.text)
        except ValueError:
            return ""
        if 0 <= idx < len(strings):
            return strings[idx]
        return ""
    return node.text


def _pptx_pages(data: bytes, max_bytes: int) -> list[tuple[int, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slides = [
            name
            for name in zf.namelist()
            if re.match(r"ppt/slides/slide\d+\.xml$", name)
        ]

        def sort_key(name: str) -> int:
            match = re.search(r"slide(\d+)", name)
            return int(match.group(1)) if match else 0

        slides = sorted(slides, key=sort_key)
        if not slides:
            raise _Unsupported()
        pages = []
        for index, name in enumerate(slides, 1):
            root = ET.fromstring(_member_bytes(zf, name, max_bytes))
            parts = [node.text or "" for node in root.iter(_local("t", _A_NS))]
            pages.append((index, "\n".join(part for part in parts if part).strip()))
        return pages


class _Unsupported(Exception):
    pass


def extract_file_body(
    path: Path,
    *,
    mime: str | None,
    max_bytes: int,
    max_pages: int,
    max_chars: int,
) -> ExtractResult:
    if not path.is_file():
        return _empty("error", error="not_a_file")
    size = path.stat().st_size
    if size > max_bytes:
        return _empty("too_big", error="max_bytes", nbytes=size)
    data = path.read_bytes()
    kind = _classify(data, mime, path.suffix)
    if kind in _HARD_SKIPS or kind.startswith("skip_"):
        return _empty(kind, error=kind, nbytes=size)
    if kind in ("text", "markdown", "csv"):
        try:
            decoded = _decode_text(data)
        except UnicodeDecodeError:
            return _empty("error", error="charset", nbytes=size)
        pages = _pages_from_text(decoded)
        return _result_from_pages(
            kind,
            pages,
            page_count=len(pages),
            max_pages=max_pages,
            max_chars=max_chars,
            nbytes=size,
        )
    if kind == "pdf":
        if _pdf_encrypted(data):
            return _empty("skip_encrypted", error="skip_encrypted", extractor="pdf-stdlib", nbytes=size)
        pages, extractor = _pdf_pages(data)
        if pages is None:
            return _empty("unsupported", error="unsupported", extractor=extractor, nbytes=size)
        return _result_from_pages(
            "pdf",
            pages,
            page_count=len(pages),
            max_pages=max_pages,
            max_chars=max_chars,
            nbytes=size,
            extractor=extractor,
        )
    if kind in ("docx", "xlsx", "pptx"):
        reader = {"docx": _docx_pages, "xlsx": _xlsx_pages, "pptx": _pptx_pages}[kind]
        try:
            pages = reader(data, max_bytes)
        except _TooBig:
            return _empty("too_big", error="max_bytes", nbytes=size)
        except _Unsupported:
            return _empty("unsupported", error="unsupported", nbytes=size)
        except zipfile.BadZipFile:
            return _empty("error", error="office_container", nbytes=size)
        except ET.ParseError:
            return _empty("error", error="office_xml", nbytes=size)
        return _result_from_pages(
            kind,
            pages,
            page_count=len(pages),
            max_pages=max_pages,
            max_chars=max_chars,
            nbytes=size,
        )
    return _empty("unsupported", error="unsupported", nbytes=size)


def extract_file(
    path: str | Path,
    *,
    mime: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ExtractResult:
    """Extract text from one file. Never executes attachment contents."""
    if max_bytes < 1 or max_pages < 1 or max_chars < 1:
        raise ValueError("caps must be positive")
    target = Path(path)
    started = time.monotonic()

    def _body() -> ExtractResult:
        return extract_file_body(
            target,
            mime=mime,
            max_bytes=max_bytes,
            max_pages=max_pages,
            max_chars=max_chars,
        )

    try:
        result = _call_with_timeout(_body, timeout_s)
    except ExtractTimeout:
        elapsed = int((time.monotonic() - started) * 1000)
        return _empty("timeout", error="timeout", elapsed_ms=elapsed)
    result.timings["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result
