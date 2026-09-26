"""MIME tree metadata. Headers and sizes only; part bytes are not kept."""

from __future__ import annotations

import email
from email import policy


class MimePart:
    """One MIME node. ``filename`` is what the parser saw, not a write decision."""

    def __init__(
        self,
        part_id: str,
        mime: str,
        size: int | None,
        content_disposition: str | None,
        filename: str | None,
    ) -> None:
        self.part_id = part_id
        self.mime = mime
        self.size = size
        self.content_disposition = content_disposition
        self.filename = filename

    def identity(self) -> tuple:
        return (
            self.part_id,
            self.mime,
            self.content_disposition,
            self.filename,
        )


_BODY_TEXT = frozenset({"text/plain", "text/html"})
_MEDIA_MAJOR = frozenset({"image", "audio", "video", "application"})


def is_attachment(part: MimePart) -> bool:
    """True when the part is an attachment rather than a body or container.

    Multipart containers are not attachments. ``text/plain`` and ``text/html``
    are not attachments unless the disposition is ``attachment``. Inline,
    image, audio, video, application, and ``message/rfc822`` parts are.
    """
    mime = (part.mime or "").lower()
    if mime.startswith("multipart/"):
        return False
    disp = (part.content_disposition or "").lower()
    if disp == "attachment":
        return True
    if mime == "message/rfc822":
        return True
    major = mime.split("/", 1)[0]
    if major in _MEDIA_MAJOR:
        return True
    if disp == "inline" and mime not in _BODY_TEXT:
        return True
    return False


def has_attachments_flag(parts: list[MimePart]) -> int:
    return 1 if any(is_attachment(part) for part in parts) else 0


def parts_from_rfc822(raw: bytes) -> list[MimePart]:
    """Walk an RFC822 message. Leaf size is the decoded length, then dropped."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    if (msg.get_content_maintype() or "") == "multipart":
        return list(_walk_email(msg, "0", True))
    return list(_walk_email(msg, "1", False))


def _norm_disp(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower()
    if text in ("attachment", "inline"):
        return text
    return None


def _decoded_size(msg: email.message.Message) -> int | None:
    try:
        payload = msg.get_payload(decode=True)
    except Exception:
        payload = None
    if isinstance(payload, (bytes, bytearray)):
        size = len(payload)
        del payload
        return size
    return None


def _inner_message(msg: email.message.Message):
    payload = msg.get_payload()
    if isinstance(payload, list):
        return payload[0] if payload else None
    if hasattr(payload, "get_content_type"):
        return payload
    return None


def _child_messages(msg: email.message.Message) -> list:
    payload = msg.get_payload()
    if isinstance(payload, list):
        return list(payload)
    return []


def _walk_email(msg: email.message.Message, part_id: str, root: bool):
    ctype = (msg.get_content_type() or "application/octet-stream").lower()
    if ctype.startswith("multipart/"):
        yield MimePart(
            part_id,
            ctype,
            None,
            _norm_disp(msg.get_content_disposition()),
            msg.get_filename(),
        )
        for index, child in enumerate(_child_messages(msg), 1):
            child_id = str(index) if root else "%s.%s" % (part_id, index)
            yield from _walk_email(child, child_id, False)
        return
    if ctype == "message/rfc822":
        yield MimePart(
            part_id,
            ctype,
            _decoded_size(msg),
            _norm_disp(msg.get_content_disposition()),
            msg.get_filename(),
        )
        inner = _inner_message(msg)
        if inner is None:
            return
        inner_type = (inner.get_content_type() or "").lower()
        if inner_type.startswith("multipart/"):
            for index, child in enumerate(_child_messages(inner), 1):
                yield from _walk_email(child, "%s.%s" % (part_id, index), False)
        else:
            yield from _walk_email(inner, "%s.1" % part_id, False)
        return
    yield MimePart(
        part_id,
        ctype,
        _decoded_size(msg),
        _norm_disp(msg.get_content_disposition()),
        msg.get_filename(),
    )
