"""Parse an IMAP BODYSTRUCTURE value into MIME metadata.

The fetch item is the structure itself. This module does not request a
message body and does not keep part bytes.
"""

from __future__ import annotations

try:
    from .mime_meta import MimePart
except ImportError:  # running as a script sibling
    from mime_meta import MimePart


class ParseError(ValueError):
    """BODYSTRUCTURE text could not be parsed. Never includes secrets."""


_DISP_NAMES = frozenset({"ATTACHMENT", "INLINE"})


def parts_from_bodystructure(source: str | list) -> list[MimePart]:
    """Return MIME nodes for one BODYSTRUCTURE S-expression or parsed list."""
    if isinstance(source, str):
        node = parse_sexp(source)
    elif isinstance(source, list):
        node = source
    else:
        raise ParseError("bodystructure type")
    if not isinstance(node, list):
        raise ParseError("bodystructure is not a list")
    if _is_multipart(node):
        return list(_walk_node(node, "0", True))
    return list(_walk_node(node, "1", False))


def bodystructure_from_fetch(data: object) -> str:
    """Slice the structure out of an imaplib FETCH payload."""
    text = _flatten(data)
    upper = text.upper()
    marker = "BODYSTRUCTURE"
    idx = upper.find(marker)
    if idx < 0:
        raise ParseError("bodystructure missing from fetch")
    rest = text[idx + len(marker) :]
    parser = _Parser(rest)
    parser.skip_ws()
    start = parser.i
    parser.parse()
    return rest[start : parser.i]


def parse_sexp(text: str):
    parser = _Parser(text)
    parser.skip_ws()
    if parser.i >= parser.n:
        raise ParseError("empty bodystructure")
    return parser.parse()


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)

    def skip_ws(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t\r\n":
            self.i += 1

    def parse(self):
        self.skip_ws()
        if self.i >= self.n:
            raise ParseError("truncated bodystructure")
        ch = self.s[self.i]
        if ch == "(":
            return self._parse_list()
        if ch == '"':
            return self._parse_string()
        if ch == "{":
            return self._parse_literal()
        if ch == ")":
            raise ParseError("unexpected close")
        return self._parse_atom()

    def _parse_list(self) -> list:
        self.i += 1
        items = []
        while True:
            self.skip_ws()
            if self.i >= self.n:
                raise ParseError("unclosed list")
            if self.s[self.i] == ")":
                self.i += 1
                return items
            items.append(self.parse())

    def _parse_string(self) -> str:
        self.i += 1
        out = []
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == '"':
                self.i += 1
                return "".join(out)
            if ch == "\\":
                self.i += 1
                if self.i >= self.n:
                    raise ParseError("bad escape")
                out.append(self.s[self.i])
                self.i += 1
                continue
            out.append(ch)
            self.i += 1
        raise ParseError("unclosed string")

    def _parse_literal(self) -> str:
        end = self.s.find("}", self.i + 1)
        if end < 0:
            raise ParseError("literal length")
        try:
            length = int(self.s[self.i + 1 : end])
        except ValueError as exc:
            raise ParseError("literal length") from exc
        if length < 0:
            raise ParseError("literal length")
        self.i = end + 1
        if self.s.startswith("\r\n", self.i):
            self.i += 2
        elif self.i < self.n and self.s[self.i] == "\n":
            self.i += 1
        else:
            raise ParseError("literal newline")
        data = self.s[self.i : self.i + length]
        if len(data) != length:
            raise ParseError("literal short")
        self.i += length
        return data

    def _parse_atom(self):
        start = self.i
        while self.i < self.n and self.s[self.i] not in " \t\r\n()":
            self.i += 1
        atom = self.s[start : self.i]
        if not atom:
            raise ParseError("empty atom")
        if atom.upper() == "NIL":
            return None
        if atom.isdigit() or (atom[0] == "-" and atom[1:].isdigit()):
            return int(atom)
        return atom


def _flatten(data: object) -> str:
    chunks: list[str] = []

    def rec(item: object) -> None:
        if item is None:
            return
        if isinstance(item, (bytes, bytearray)):
            chunks.append(bytes(item).decode("utf-8", "replace"))
            return
        if isinstance(item, str):
            chunks.append(item)
            return
        if isinstance(item, (list, tuple)):
            for part in item:
                rec(part)

    rec(data)
    return "".join(chunks)


def _is_multipart(node: list) -> bool:
    return bool(node) and isinstance(node[0], list)


def _param_map(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    out: dict[str, str] = {}
    index = 0
    while index + 1 < len(value):
        key = value[index]
        val = value[index + 1]
        if isinstance(key, str) and isinstance(val, str):
            out[key.upper()] = val
        index += 2
    return out


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _filename(disp_params: dict[str, str], body_params: dict[str, str]) -> str | None:
    name = disp_params.get("FILENAME") or body_params.get("NAME")
    if not name:
        return None
    text = name.strip()
    return text or None


def _find_disp(items: list) -> tuple[str | None, dict[str, str]]:
    for item in items:
        if not isinstance(item, list) or not item or not isinstance(item[0], str):
            continue
        name = item[0].upper()
        if name not in _DISP_NAMES:
            continue
        params = _param_map(item[1]) if len(item) > 1 else {}
        return name.lower(), params
    return None, {}


def _extension_items(node: list) -> list:
    type_ = str(node[0] or "").upper() if node else ""
    subtype = str(node[1] or "").upper() if len(node) > 1 else ""
    if type_ == "MESSAGE" and subtype == "RFC822":
        return node[10:]
    if type_ == "TEXT":
        return node[8:]
    return node[7:]


def _split_multi(node: list):
    index = 0
    children = []
    while index < len(node) and isinstance(node[index], list):
        children.append(node[index])
        index += 1
    if index >= len(node) or not isinstance(node[index], str):
        raise ParseError("multipart subtype missing")
    subtype = node[index].lower()
    rest = node[index + 1 :]
    params = _param_map(rest[0]) if rest else {}
    disp, disp_params = _find_disp(rest)
    return children, subtype, params, disp, disp_params


def _walk_node(node: list, part_id: str, root: bool):
    if not isinstance(node, list) or len(node) < 2:
        raise ParseError("short bodystructure")
    if _is_multipart(node):
        children, subtype, params, disp, disp_params = _split_multi(node)
        yield MimePart(
            part_id,
            "multipart/%s" % subtype,
            None,
            disp,
            _filename(disp_params, params),
        )
        for index, child in enumerate(children, 1):
            child_id = str(index) if root else "%s.%s" % (part_id, index)
            if not isinstance(child, list):
                raise ParseError("multipart child")
            yield from _walk_node(child, child_id, False)
        return
    type_ = str(node[0] or "").lower()
    subtype = str(node[1] or "").lower()
    mime = "%s/%s" % (type_, subtype)
    params = _param_map(node[2]) if len(node) > 2 else {}
    size = _as_int(node[6]) if len(node) > 6 else None
    disp, disp_params = _find_disp(_extension_items(node))
    yield MimePart(part_id, mime, size, disp, _filename(disp_params, params))
    if type_ == "message" and subtype == "rfc822" and len(node) > 8:
        inner = node[8]
        if not isinstance(inner, list):
            return
        if _is_multipart(inner):
            children, _subtype, _params, _disp, _disp_params = _split_multi(inner)
            for index, child in enumerate(children, 1):
                if not isinstance(child, list):
                    raise ParseError("rfc822 child")
                yield from _walk_node(child, "%s.%s" % (part_id, index), False)
        else:
            yield from _walk_node(inner, "%s.1" % part_id, False)
