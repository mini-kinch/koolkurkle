#!/usr/bin/env python3
"""ATT-1/ATT-2 P1: copy-DB attachment byte fetch and sidecar text.

Reads an attachment catalog from a copy database and, unless
``--dry-run``, downloads part bytes into a local stage directory and
extracts P1 text in a child process.

Refuses basename ``mailroom.sqlite``. There is no override flag.
``lane=auth`` rows are not fetched and not extracted. A catalog size
over 50 MB is ``too_big`` and is not fetched. Apply still downloads
other parts as ``BODY.PEEK[part]<offset.count>`` chunks so a wrong
catalog size cannot pull the whole literal into memory. Archives are
recorded as skipped and are never unpacked. Extracted text is
truncated at 2 MB.
"""

from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
import selectors
import signal
import sqlite3
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import att0_constraints as att0  # noqa: E402
from imap_keychain import KeychainError, read_imap_app_password  # noqa: E402
from mailroom_copy_db import COPY_DB_BASENAMES  # noqa: E402
from refuse_destructive import DestructiveRefuse, refuse_destructive_cli  # noqa: E402
from sor_writer_gate import (  # noqa: E402
    SOR_BASENAME,
    SorWriterRefuse,
    refuse_if_sor_writer_conflict,
)

WORKER = HERE / "p1_worker.py"
BLOB_CAP = att0.BLOB_TOO_BIG_BYTES
CHUNK_BYTES = 1024 * 1024
OUTPUT_CAP = 64 * 1024
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
DEFAULT_TIMEOUT_S = 30.0
_IMAP_SSL_PORT = 993

_PART_RE = re.compile(r"^[1-9]\d*(?:\.[1-9]\d*)*$")
_UID_RE = re.compile(r"^[1-9]\d*$")
_LITERAL_RE = re.compile(br"\{(\d+)\}\s*$")
_QUOTED_RE = re.compile(br'BODY(?:\.PEEK)?\[[0-9.]+\]\s+"((?:\\.|[^"\\])*)"')

_ARCHIVE_MIMES = frozenset({
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-bzip2",
    "application/x-xz",
    "application/x-gtar",
    "application/x-compressed-tar",
})
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
_P1_KINDS = {
    "text/plain": "text",
    "text/csv": "csv",
    "application/csv": "csv",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/pdf": "pdf",
}
_STATUSES = (
    "excluded_auth",
    "too_big",
    "skipped_archive",
    "skipped",
    "planned",
    "ok",
    "extractor_missing",
    "timeout",
    "error",
)
_CHILD_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
)


class FetchRefuse(RuntimeError):
    """Fail-closed fetch/extract refuse. Never includes secrets or absolute paths."""


class PayloadTooBig(Exception):
    """Fetched literal is over the byte cap. Internal; status is too_big."""


class WorkerOutputOverflow(Exception):
    """Worker stdout or stderr exceeded the capture cap."""


def quote_imap_mailbox(name: str) -> str:
    """IMAP atom quoting. Spaces, quotes, and backslashes stay one mailbox."""
    escaped = str(name).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _mime_base(mime: str | None) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


def _suffix(filename: str | None) -> str:
    if not filename:
        return ""
    leaf = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    return Path(leaf).suffix.lower()


def classify_part(mime: str | None, filename: str | None) -> str:
    """Return archive, a P1 worker kind, or skip. Does not read bytes."""
    if _mime_base(mime) in _ARCHIVE_MIMES or _suffix(filename) in _ARCHIVE_SUFFIXES:
        return "archive"
    return _P1_KINDS.get(_mime_base(mime), "skip")


def _lane_blocked(lane: str | None) -> bool:
    try:
        att0.auth_hard_gate(lane=lane)
    except att0.Att0Refuse:
        return True
    return False


def _basenames(path: Path) -> set[str]:
    names = {path.name}
    try:
        names.add(path.resolve().name)
    except OSError:
        return names
    return names


def refuse_sor_basename(path: Path) -> None:
    """Refuse the system-of-record basename. No override."""
    if SOR_BASENAME in _basenames(path):
        raise FetchRefuse("refuse: basename mailroom.sqlite")


def refuse_copy_basename(path: Path) -> None:
    names = _basenames(path)
    if not names.issubset(COPY_DB_BASENAMES):
        raise FetchRefuse("refuse: basename %s is not a copy database" % path.name)


def accept_payload(blob: bytes, cap: int = BLOB_CAP) -> bytes | None:
    """Return the bytes at or under the cap. Over the cap, return None."""
    if len(blob) > cap:
        return None
    return blob


def _unescape_quoted(raw: bytes) -> bytes:
    out = bytearray()
    escaped = False
    for byte in raw:
        if escaped:
            out.append(byte)
            escaped = False
            continue
        if byte == 0x5C:  # backslash
            escaped = True
            continue
        out.append(byte)
    return bytes(out)


def parse_fetch_literal(data: Any) -> bytes:
    """Pull the BODY.PEEK literal out of an imaplib FETCH response."""
    if not data:
        raise FetchRefuse("empty part fetch")
    literals: list[bytes] = []
    quoted: list[bytes] = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple) and len(item) >= 2:
            literal = item[1]
            if isinstance(literal, (bytes, bytearray)):
                literals.append(bytes(literal))
            continue
        if isinstance(item, (bytes, bytearray)):
            match = _QUOTED_RE.search(bytes(item))
            if match:
                quoted.append(_unescape_quoted(match.group(1)))
    if literals:
        return b"".join(literals)
    if quoted:
        return b"".join(quoted)
    raise FetchRefuse("part fetch had no literal")


def declared_literal_size(data: Any) -> int | None:
    """Return the IMAP ``{n}`` size when the response declares one."""
    if not data:
        return None
    for item in data:
        meta = item[0] if isinstance(item, tuple) and item else item
        if isinstance(meta, (bytes, bytearray)):
            match = _LITERAL_RE.search(bytes(meta))
            if match:
                return int(match.group(1))
    return None


def _mkdir_private(path: Path) -> None:
    """Create ``path`` and force mode 0700. mkdir applies umask, so chmod after."""
    if path.is_symlink():
        raise FetchRefuse("stage path is not a directory")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise FetchRefuse("stage path is not a directory")
    os.chmod(path, _DIR_MODE)


def _ensure_stage_dirs(stage: Path) -> None:
    for path in (stage, stage / "bytes", stage / "text", stage / "status"):
        _mkdir_private(path)


def _partial_path(final: Path) -> Path:
    token = os.urandom(4).hex()
    return final.with_name(".%s.%d.%s.partial" % (final.name, os.getpid(), token))


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return


def _discard_partials(final: Path) -> None:
    parent = final.parent
    if parent.is_symlink() or not parent.is_dir():
        return
    prefix = ".%s." % final.name
    try:
        names = list(parent.iterdir())
    except OSError:
        return
    for entry in names:
        if entry.name.startswith(prefix) and (
            entry.name.endswith(".partial") or entry.name.endswith(".tmp")
        ):
            _unlink_quiet(entry)


class _PrivateFile:
    """Temp file next to ``final``, published with replace so a symlink is not followed."""

    def __init__(self, final: Path) -> None:
        _mkdir_private(final.parent)
        self.final = final
        self.tmp = _partial_path(final)
        self.fd = -1
        self.published = False
        self.fd = os.open(self.tmp, _OPEN_FLAGS, _FILE_MODE)
        try:
            os.fchmod(self.fd, _FILE_MODE)
        except OSError:
            self.abort()
            raise

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        try:
            while view:
                wrote = os.write(self.fd, view)
                if wrote <= 0:
                    raise FetchRefuse("stage write failed")
                view = view[wrote:]
        finally:
            view.release()

    def finish(self) -> None:
        fd = self.fd
        self.fd = -1
        try:
            os.fsync(fd)
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(self.tmp, self.final)
        self.published = True

    def abort(self) -> None:
        fd = self.fd
        self.fd = -1
        if isinstance(fd, int) and fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if not self.published:
            tmp = getattr(self, "tmp", None)
            if tmp is not None:
                _unlink_quiet(tmp)


def _publish_bytes(final: Path, data: bytes) -> None:
    handle = _PrivateFile(final)
    try:
        handle.write(data)
        handle.finish()
    except Exception:
        handle.abort()
        raise


class ImapPartClient:
    """UID FETCH of partial ``BODY.PEEK[part]<offset.count>`` ranges.

    Readonly EXAMINE on imaplib.IMAP4_SSL port 993. The password comes
    from ``password_fn`` (default Keychain via ``read_imap_app_password``).
    Plain IMAP is refused. Each chunk is written to ``dest`` and dropped
    so peak memory stays one chunk.
    """

    def __init__(
        self,
        host: str,
        user: str | None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        imap_factory: Any = None,
        password_fn: Callable[[], str] | None = None,
    ) -> None:
        if not host:
            raise FetchRefuse("imap host is required")
        self.host = host
        self.user = user or ""
        self.port = _IMAP_SSL_PORT
        self.timeout = timeout
        self._factory = imaplib.IMAP4_SSL if imap_factory is None else imap_factory
        if self._factory is imaplib.IMAP4:
            raise FetchRefuse("plain IMAP is refused")
        self._password_fn = password_fn
        self._conn: Any = None
        self.mailbox: str | None = None

    def __enter__(self) -> "ImapPartClient":
        fn = self._password_fn or read_imap_app_password
        password = ""
        try:
            try:
                password = fn()
            except KeychainError:
                raise FetchRefuse("imap keychain password is missing") from None
            context = ssl.create_default_context()
            try:
                self._conn = self._factory(
                    self.host,
                    self.port,
                    timeout=self.timeout,
                    ssl_context=context,
                )
                self._conn.login(self.user, password)
            except FetchRefuse:
                self._close()
                raise
            except Exception:
                self._close()
                raise FetchRefuse("imap login failed") from None
        finally:
            password = ""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close()

    def _close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.logout()
        except Exception:
            return

    def _examine(self, folder: str) -> None:
        if self._conn is None:
            raise FetchRefuse("imap select failed")
        if folder is None or str(folder).strip() == "":
            raise FetchRefuse("missing mailbox")
        if any(char in str(folder) for char in "\r\n\x00"):
            raise FetchRefuse("missing mailbox")
        quoted = quote_imap_mailbox(str(folder))
        typ, _data = self._conn.select(quoted, readonly=True)
        if typ != "OK":
            raise FetchRefuse("imap select failed")
        self.mailbox = str(folder)

    def fetch_part(
        self,
        folder: str,
        uid: str,
        part_id: str,
        dest: Path,
        *,
        cap: int = BLOB_CAP,
        chunk_size: int = CHUNK_BYTES,
    ) -> int:
        """Stream one part into ``dest``. Return the stored size.

        Requests ``BODY.PEEK[part]<offset.chunk>`` (still PEEK, still
        readonly EXAMINE). Stops and raises PayloadTooBig as soon as the
        cumulative size would exceed ``cap``, and does not leave ``dest``.
        """
        if not _PART_RE.match(str(part_id)):
            raise FetchRefuse("part id refused")
        if not _UID_RE.match(str(uid)):
            raise FetchRefuse("uid refused")
        if cap < 1 or cap > BLOB_CAP:
            raise FetchRefuse("byte cap refuses above 50 MB")
        if chunk_size < 1:
            raise FetchRefuse("chunk size refused")
        if self._conn is None:
            raise FetchRefuse("imap select failed")
        if self.mailbox != str(folder):
            self._examine(str(folder))
        handle = _PrivateFile(Path(dest))
        total = 0
        offset = 0
        try:
            while True:
                room = cap - total
                span = chunk_size if chunk_size <= room else room + 1
                if span < 1:
                    break
                item = "(BODY.PEEK[%s]<%d.%d>)" % (part_id, offset, span)
                try:
                    typ, data = self._conn.uid("FETCH", str(uid), item)
                except Exception:
                    raise FetchRefuse("part fetch failed") from None
                if typ != "OK":
                    raise FetchRefuse("part fetch failed")
                declared = declared_literal_size(data)
                try:
                    blob = parse_fetch_literal(data)
                except FetchRefuse:
                    if offset == 0:
                        raise
                    blob = b""
                except Exception:
                    raise FetchRefuse("part fetch failed") from None
                n = len(blob)
                counted = n
                if declared is not None and declared > counted:
                    counted = declared
                if total + counted > cap:
                    raise PayloadTooBig()
                if n:
                    handle.write(blob)
                total += n
                del blob
                del data
                if n != span:
                    break
                offset += n
            handle.finish()
        except Exception:
            handle.abort()
            raise
        return total


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = "%s?mode=ro" % path.resolve().as_uri()
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        raise FetchRefuse("sqlite read failed") from None
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        conn.close()
        raise FetchRefuse("sqlite read failed") from None
    return conn


def _require_catalog(conn: sqlite3.Connection) -> None:
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    except sqlite3.Error:
        raise FetchRefuse("sqlite read failed") from None
    if "messages" not in tables or "attachments" not in tables:
        raise FetchRefuse("attachments catalog is missing; migrate the copy first")
    message_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(messages)")}
    for column in ("id", "lane", "uid", "folder"):
        if column not in message_cols:
            raise FetchRefuse("messages.%s is missing" % column)
    attachment_cols = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(attachments)")
    }
    for column in ("attachment_id", "message_id", "part_id", "mime", "size"):
        if column not in attachment_cols:
            raise FetchRefuse("attachments.%s is missing" % column)


def _load_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    try:
        return list(
            conn.execute(
                "SELECT a.attachment_id, a.message_id, a.part_id, a.filename, "
                "a.mime, a.size, m.lane, m.uid, m.folder "
                "FROM attachments AS a "
                "INNER JOIN messages AS m ON m.id = a.message_id "
                "ORDER BY a.attachment_id ASC"
            )
        )
    except sqlite3.Error:
        raise FetchRefuse("sqlite read failed") from None


def _token(attachment_id: Any) -> str | None:
    if isinstance(attachment_id, bool):
        return None
    try:
        number = int(attachment_id)
    except (TypeError, ValueError):
        return None
    if number < 1:
        return None
    return str(number)


def _record(
    *,
    attachment_id: Any,
    message_id: Any,
    part_id: Any,
    mime: Any,
    size: int | None,
    lane: Any,
    status: str,
    truncated: bool = False,
    error: str | None = None,
    extractor: str | None = None,
    text_path: str | None = None,
    bytes_path: str | None = None,
    fetched_bytes: int = 0,
) -> dict[str, Any]:
    return {
        "attachment_id": attachment_id,
        "message_id": message_id,
        "part_id": part_id,
        "mime": mime,
        "size": size,
        "lane": lane,
        "status": status,
        "truncated": bool(truncated),
        "error": error,
        "extractor": extractor,
        "text_path": text_path,
        "bytes_path": bytes_path,
        "fetched_bytes": int(fetched_bytes),
    }


def _counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in _STATUSES}
    counts["rows"] = len(records)
    for record in records:
        status = str(record.get("status") or "")
        if status in counts:
            counts[status] += 1
        else:
            counts["error"] += 1
    return counts


def _sizes(records: list[dict[str, Any]]) -> dict[str, int]:
    planned = 0
    too_big = 0
    fetched = 0
    for record in records:
        size = record.get("size")
        if record["status"] == "planned" and isinstance(size, int):
            planned += size
        if record["status"] == "too_big" and isinstance(size, int) and size > 0:
            too_big += size
        fetched += int(record.get("fetched_bytes") or 0)
    return {
        "planned_bytes": planned,
        "too_big_bytes": too_big,
        "fetched_bytes": fetched,
    }


def _child_env(env: dict[str, str] | None) -> dict[str, str]:
    source = os.environ if env is None else env
    child: dict[str, str] = {}
    for key in _CHILD_ENV_KEYS:
        value = source.get(key)
        if value:
            child[key] = value
    child.setdefault("PATH", "")
    child["PYTHONDONTWRITEBYTECODE"] = "1"
    child["PYTHONIOENCODING"] = "utf-8"
    return child


def _default_worker_argv(kind: str, src: Path, dest: Path, timeout_s: float) -> list[str]:
    return [
        sys.executable,
        str(WORKER),
        "--kind",
        kind,
        "--src",
        str(src),
        "--dest",
        str(dest),
        "--timeout",
        str(timeout_s),
    ]


def _close_pipes(proc: subprocess.Popen) -> None:
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the worker session, then wait so grandchildren are gone too."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()


def _pipe_extra(fd: int) -> bool:
    """True when another byte is already buffered. That byte is consumed."""
    try:
        extra = os.read(fd, 1)
    except (BlockingIOError, OSError):
        return False
    return extra != b""


def _communicate_capped(
    proc: subprocess.Popen,
    timeout: float,
    cap: int,
) -> tuple:
    """Read at most ``cap`` bytes from stdout and from stderr.

    On timeout or overflow, kill the process group and wait.
    The third value is None, ``timeout``, or ``overflow``.
    """
    out_parts: list = []
    err_parts: list = []
    counts = {"out": 0, "err": 0}
    problem = None
    sel = selectors.DefaultSelector()
    open_streams: dict = {}
    try:
        for stream, name in ((proc.stdout, "out"), (proc.stderr, "err")):
            if stream is None:
                continue
            os.set_blocking(stream.fileno(), False)
            sel.register(stream, selectors.EVENT_READ, name)
            open_streams[stream] = name
        deadline = time.monotonic() + timeout
        while open_streams and problem is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                problem = "timeout"
                break
            events = sel.select(timeout=remaining)
            if not events:
                if time.monotonic() >= deadline:
                    problem = "timeout"
                    break
                continue
            for key, _mask in events:
                stream = key.fileobj
                name = key.data
                if stream not in open_streams:
                    continue
                room = cap - counts[name]
                if room <= 0:
                    problem = "overflow"
                    break
                try:
                    chunk = os.read(stream.fileno(), room)
                except BlockingIOError:
                    continue
                except OSError:
                    sel.unregister(stream)
                    del open_streams[stream]
                    continue
                if chunk == b"":
                    sel.unregister(stream)
                    del open_streams[stream]
                    continue
                if name == "out":
                    out_parts.append(chunk)
                else:
                    err_parts.append(chunk)
                counts[name] += len(chunk)
                if counts[name] >= cap and _pipe_extra(stream.fileno()):
                    problem = "overflow"
                    break
        if problem is None and proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                problem = "timeout"
            else:
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    problem = "timeout"
    finally:
        sel.close()
    stdout = b"".join(out_parts)
    stderr = b"".join(err_parts)
    if problem is not None:
        _kill_group(proc)
        _close_pipes(proc)
        return stdout, stderr, problem
    proc.wait()
    _close_pipes(proc)
    return stdout, stderr, None


def _default_runner(
    argv: list[str],
    timeout: float,
    env: dict[str, str],
) -> subprocess.CompletedProcess:
    """Run p1_worker in its own session and bound its output.

    No shell. On timeout or an output flood, SIGKILL the process group
    so pdftotext grandchildren die too, then wait.
    """
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        raise FetchRefuse("worker argv must be a list")
    command = [str(part) for part in argv]
    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr, problem = _communicate_capped(proc, float(timeout), OUTPUT_CAP)
    except BaseException:
        _kill_group(proc)
        _close_pipes(proc)
        raise
    if problem == "timeout":
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    if problem == "overflow":
        raise WorkerOutputOverflow()
    code = proc.returncode
    if code is None:
        code = 1
    return subprocess.CompletedProcess(command, int(code), stdout, stderr)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    _publish_bytes(path, raw)


def _status_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachment_id": record["attachment_id"],
        "status": record["status"],
        "truncated": bool(record["truncated"]),
        "error": record["error"],
        "extractor": record["extractor"],
        "mime": record["mime"],
        "size": record["size"],
        "fetched_bytes": record["fetched_bytes"],
        "text_path": record["text_path"],
    }


def _parse_worker_stdout(blob: bytes | None) -> dict[str, Any] | None:
    if not blob:
        return None
    text = blob.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    line = text.splitlines()[-1]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _run_worker(
    *,
    kind: str,
    src: Path,
    dest: Path,
    timeout_s: float,
    runner: Callable,
    worker_argv: Callable,
    env: dict[str, str] | None,
) -> dict[str, Any]:
    if timeout_s <= 0:
        return {
            "status": "timeout",
            "truncated": False,
            "error": "timeout",
            "extractor": "p1-worker",
        }
    argv = worker_argv(kind, src, dest, timeout_s)
    try:
        proc = runner(argv, timeout_s, _child_env(env))
    except subprocess.TimeoutExpired:
        _discard_partials(dest)
        dest.unlink(missing_ok=True)
        return {
            "status": "timeout",
            "truncated": False,
            "error": "timeout",
            "extractor": "p1-worker",
        }
    except WorkerOutputOverflow:
        _discard_partials(dest)
        dest.unlink(missing_ok=True)
        return {
            "status": "error",
            "truncated": False,
            "error": "output_overflow",
            "extractor": "p1-worker",
        }
    stdout = getattr(proc, "stdout", b"") or b""
    code = int(getattr(proc, "returncode", 1))
    if code != 0:
        _discard_partials(dest)
        dest.unlink(missing_ok=True)
        return {
            "status": "error",
            "truncated": False,
            "error": "worker_exit_%s" % code,
            "extractor": "p1-worker",
        }
    payload = _parse_worker_stdout(stdout if isinstance(stdout, (bytes, bytearray)) else str(stdout).encode("utf-8"))
    if payload is None:
        _discard_partials(dest)
        dest.unlink(missing_ok=True)
        return {
            "status": "error",
            "truncated": False,
            "error": "worker_output",
            "extractor": "p1-worker",
        }
    status = str(payload.get("status") or "error")
    if status not in {"ok", "extractor_missing", "skipped_archive", "error", "too_big", "timeout"}:
        status = "error"
    if status != "ok":
        _discard_partials(dest)
        dest.unlink(missing_ok=True)
    return {
        "status": status,
        "truncated": bool(payload.get("truncated")),
        "error": payload.get("error"),
        "extractor": payload.get("extractor") or "p1-worker",
    }


def _plan_row(row: sqlite3.Row, *, cap: int) -> dict[str, Any]:
    attachment_id = row["attachment_id"]
    message_id = row["message_id"]
    part_id = row["part_id"]
    mime = row["mime"]
    lane = row["lane"]
    filename = row["filename"]
    raw_size = row["size"]
    size: int | None
    if raw_size is None:
        size = None
    else:
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            size = None
    base = dict(
        attachment_id=attachment_id,
        message_id=message_id,
        part_id=part_id,
        mime=mime,
        size=size,
        lane=lane,
    )
    if _lane_blocked(None if lane is None else str(lane)):
        return _record(**base, status="excluded_auth", error="lane_auth")
    if size is None or size < 0:
        return _record(**base, status="error", error="size_unknown")
    if size > cap:
        return _record(**base, status="too_big", error="too_big")
    kind = classify_part(None if mime is None else str(mime), None if filename is None else str(filename))
    if kind == "archive":
        return _record(**base, status="skipped_archive", error="skipped_archive")
    if kind == "skip":
        return _record(**base, status="skipped", error="not_p1")
    if _token(attachment_id) is None:
        return _record(**base, status="error", error="bad_attachment_id")
    if not _PART_RE.match("" if part_id is None else str(part_id)):
        return _record(**base, status="error", error="bad_part")
    folder = "" if row["folder"] is None else str(row["folder"])
    uid = "" if row["uid"] is None else str(row["uid"])
    if not folder.strip() or any(char in folder for char in "\r\n\x00"):
        return _record(**base, status="error", error="bad_folder")
    if not _UID_RE.match(uid):
        return _record(**base, status="error", error="bad_uid")
    return _record(**base, status="planned", extractor=kind)


def run_fetch_extract(
    db: str | Path,
    stage: str | Path,
    *,
    dry_run: bool = True,
    fetch_part: Callable[[str, str, str], bytes] | None = None,
    part_client: Any = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = BLOB_CAP,
    chunk_size: int = CHUNK_BYTES,
    runner: Callable | None = None,
    worker_argv: Callable | None = None,
    env: dict[str, str] | None = None,
    argv: list[str] | None = None,
    cmdlines: Any = None,
    lock_held: bool | None = None,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Plan or run byte fetch plus P1 extract. Dry-run writes nothing.

    ``max_bytes`` cannot exceed the 50 MB cap. A smaller value is only
    for tests of the same boundary check. ``part_client`` streams partial
    BODY.PEEK ranges into the stage; ``fetch_part`` is the in-memory
    callback used by tests.
    """
    path = Path(db)
    stage_path = Path(stage)
    refuse_destructive_cli([] if argv is None else list(argv))
    refuse_sor_basename(path)
    refuse_copy_basename(path)
    if max_bytes < 1 or max_bytes > BLOB_CAP:
        raise FetchRefuse("byte cap refuses above 50 MB")
    if chunk_size < 1:
        raise FetchRefuse("chunk size refused")
    stage_text = str(stage_path)
    if "://" in stage_text or stage_text.startswith("//"):
        raise FetchRefuse("stage dir must be local")
    if not path.is_file():
        raise FetchRefuse("database not found")
    refuse_if_sor_writer_conflict(
        path,
        cmdlines=cmdlines,
        lock_path=lock_path,
        lock_held=lock_held,
    )
    if not dry_run and fetch_part is None and part_client is None:
        raise FetchRefuse("fetch client required")
    if not dry_run and stage_path.exists() and not stage_path.is_dir():
        raise FetchRefuse("stage path is not a directory")

    conn = _connect_ro(path)
    try:
        _require_catalog(conn)
        rows = _load_rows(conn)
    finally:
        conn.close()

    planned = [_plan_row(row, cap=max_bytes) for row in rows]
    if dry_run:
        return _report(path, stage_path, planned, dry_run=True)

    _ensure_stage_dirs(stage_path)
    call_runner = _default_runner if runner is None else runner
    call_argv = _default_worker_argv if worker_argv is None else worker_argv
    by_id = {row["attachment_id"]: row for row in rows}
    records: list[dict[str, Any]] = []
    for record in planned:
        if record["status"] != "planned":
            token = _token(record["attachment_id"])
            if token is not None:
                _write_json(
                    stage_path / "status" / ("%s.json" % token),
                    _status_payload(record),
                )
            records.append(record)
            continue
        row = by_id[record["attachment_id"]]
        token = _token(record["attachment_id"])
        assert token is not None
        kind = str(record["extractor"])
        bytes_path = stage_path / "bytes" / token
        text_rel = "text/%s.txt" % token
        bytes_rel = "bytes/%s" % token
        dest = stage_path / "text" / ("%s.txt" % token)
        try:
            if part_client is not None:
                fetched_len = int(
                    part_client.fetch_part(
                        str(row["folder"]),
                        str(row["uid"]),
                        str(row["part_id"]),
                        bytes_path,
                        cap=max_bytes,
                        chunk_size=chunk_size,
                    )
                )
            else:
                blob = fetch_part(str(row["folder"]), str(row["uid"]), str(row["part_id"]))
                if not isinstance(blob, (bytes, bytearray)):
                    raise FetchRefuse("part fetch failed")
                kept = accept_payload(bytes(blob), max_bytes)
                del blob
                if kept is None:
                    raise PayloadTooBig()
                _publish_bytes(bytes_path, kept)
                fetched_len = len(kept)
                del kept
        except PayloadTooBig:
            _discard_partials(bytes_path)
            record = _record(
                attachment_id=record["attachment_id"],
                message_id=record["message_id"],
                part_id=record["part_id"],
                mime=record["mime"],
                size=record["size"],
                lane=record["lane"],
                status="too_big",
                error="too_big",
            )
            _write_json(stage_path / "status" / ("%s.json" % token), _status_payload(record))
            records.append(record)
            continue
        except Exception:
            _discard_partials(bytes_path)
            record = _record(
                attachment_id=record["attachment_id"],
                message_id=record["message_id"],
                part_id=record["part_id"],
                mime=record["mime"],
                size=record["size"],
                lane=record["lane"],
                status="error",
                error="fetch_failed",
            )
            _write_json(stage_path / "status" / ("%s.json" % token), _status_payload(record))
            records.append(record)
            continue
        outcome = _run_worker(
            kind=kind,
            src=bytes_path,
            dest=dest,
            timeout_s=timeout_s,
            runner=call_runner,
            worker_argv=call_argv,
            env=env,
        )
        if outcome["status"] in {"skipped_archive", "too_big"}:
            bytes_path.unlink(missing_ok=True)
            bytes_rel = None
            fetched = 0
        else:
            fetched = fetched_len
        if outcome["status"] != "ok":
            text_rel = None
        record = _record(
            attachment_id=record["attachment_id"],
            message_id=record["message_id"],
            part_id=record["part_id"],
            mime=record["mime"],
            size=record["size"],
            lane=record["lane"],
            status=outcome["status"],
            truncated=bool(outcome["truncated"]),
            error=None if outcome["error"] is None else str(outcome["error"]),
            extractor=None if outcome["extractor"] is None else str(outcome["extractor"]),
            text_path=text_rel,
            bytes_path=bytes_rel,
            fetched_bytes=fetched,
        )
        _write_json(stage_path / "status" / ("%s.json" % token), _status_payload(record))
        records.append(record)
    return _report(path, stage_path, records, dry_run=False)


def _report(
    db: Path,
    stage: Path,
    records: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    counts = _counts(records)
    sizes = _sizes(records)
    return {
        "dry_run": bool(dry_run),
        "db_basename": db.name,
        "stage_basename": stage.name,
        "counts": counts,
        "sizes": sizes,
        "records": records,
    }


def format_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    sizes = report["sizes"]
    lines = [
        "att fetch p1",
        "dry_run=%s" % (1 if report["dry_run"] else 0),
        "db_basename=%s" % report["db_basename"],
        "stage_basename=%s" % report["stage_basename"],
        "rows=%s" % counts["rows"],
        "excluded_auth=%s" % counts["excluded_auth"],
        "too_big=%s" % counts["too_big"],
        "skipped_archive=%s" % counts["skipped_archive"],
        "skipped=%s" % counts["skipped"],
        "planned=%s" % counts["planned"],
        "ok=%s" % counts["ok"],
        "extractor_missing=%s" % counts["extractor_missing"],
        "timeout=%s" % counts["timeout"],
        "error=%s" % counts["error"],
        "planned_bytes=%s" % sizes["planned_bytes"],
        "too_big_bytes=%s" % sizes["too_big_bytes"],
        "fetched_bytes=%s" % sizes["fetched_bytes"],
        "summary_json=%s" % json.dumps(
            {
                "dry_run": report["dry_run"],
                "db_basename": report["db_basename"],
                "counts": counts,
                "sizes": sizes,
            },
            sort_keys=True,
        ),
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch attachment part bytes into a local stage dir and extract "
            "P1 text to sidecar .txt. Default is dry-run (no writes, no network). "
            "Refuses basename mailroom.sqlite. Copy databases only."
        )
    )
    parser.add_argument("--db", required=True, help="Existing copy SQLite database.")
    parser.add_argument("--stage", required=True, help="Local stage directory.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Fetch and extract. Omit for a dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only. Writes nothing and opens no network connection.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Per-file extractor timeout in seconds (default 30).",
    )
    parser.add_argument("--host", help="IMAP host. Else MAILROOM_IMAP_HOST.")
    parser.add_argument("--user", help="IMAP user. Else MAILROOM_IMAP_USER.")
    return parser


def _env_text(env: dict[str, str], args_value: str | None, key: str) -> str | None:
    if args_value is not None:
        return args_value
    value = env.get(key)
    if value is None or value == "":
        return None
    return value


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    fetch_part: Callable[[str, str, str], bytes] | None = None,
    runner: Callable | None = None,
    worker_argv: Callable | None = None,
    password_fn: Callable[[], str] | None = None,
    imap_factory: Any = None,
) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        refuse_destructive_cli(raw)
    except DestructiveRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    args = build_parser().parse_args(raw)
    if args.apply and args.dry_run:
        sys.stderr.write("error: pass only one of --apply and --dry-run\n")
        return 2
    try:
        refuse_sor_basename(Path(args.db))
        refuse_copy_basename(Path(args.db))
    except FetchRefuse as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    dry_run = not args.apply
    environ = os.environ if env is None else env
    client: ImapPartClient | None = None
    part_fn = fetch_part
    host = None
    user = None
    if not dry_run and part_fn is None:
        host = _env_text(environ, args.host, "MAILROOM_IMAP_HOST")
        user = _env_text(environ, args.user, "MAILROOM_IMAP_USER")
        if not host:
            sys.stderr.write("error: imap host is required\n")
            return 2
        if not user:
            sys.stderr.write("error: imap user is required\n")
            return 2
    try:
        if not dry_run and part_fn is None:
            client = ImapPartClient(
                host or "",
                user,
                timeout=args.timeout if args.timeout > 0 else DEFAULT_TIMEOUT_S,
                imap_factory=imap_factory,
                password_fn=password_fn,
            )
            client.__enter__()
        report = run_fetch_extract(
            args.db,
            args.stage,
            dry_run=dry_run,
            fetch_part=part_fn,
            part_client=client,
            timeout_s=args.timeout,
            runner=runner,
            worker_argv=worker_argv,
            env=environ,
            argv=raw,
        )
    except (FetchRefuse, SorWriterRefuse, DestructiveRefuse) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except sqlite3.Error:
        sys.stderr.write("error: sqlite read failed\n")
        return 2
    finally:
        if client is not None:
            client.__exit__(None, None, None)
    sys.stdout.write(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
