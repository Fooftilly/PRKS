"""The response surface ``PRKSHandler`` writes to (#1329 Phase B3).

It replaces ``http.server.SimpleHTTPRequestHandler`` as the handler's base:

- the response head (``send_response``, ``send_header``, ``end_headers``),
  written to ``wfile`` as an HTTP/1.1 head that ``backend.web.asgi`` turns
  into ASGI messages;
- ``send_error``: a plain-text error answer that closes the connection;
- static frontend files, with ``Last-Modified`` and ``If-Modified-Since``;
- the ``log_*`` hooks, which ``PRKSHandler`` overrides.

The request side (``command``, ``path``, ``headers``, ``rfile``,
``client_address``, ``server``) is set by whoever builds the handler
(``backend.web.asgi._AsgiHandler``). Directory listings are never served.
"""

from __future__ import annotations

import datetime
import email.utils
import mimetypes
import os
import posixpath
import shutil
from http import HTTPStatus
from typing import Any, BinaryIO, Optional

_NO_BODY_STATUSES = frozenset({HTTPStatus.NO_CONTENT, HTTPStatus.RESET_CONTENT, HTTPStatus.NOT_MODIFIED})

#: Content types for the files the frontend ships, so they do not depend on
#: the host's ``mimetypes`` tables. Anything else falls back to ``mimetypes``.
STATIC_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".map": "application/json",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".woff2": "font/woff2",
    ".csl": "application/vnd.citationstyles.style+xml",
    ".xml": "application/xml",
    ".txt": "text/plain",
    ".gz": "application/gzip",
}


def _phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return ""


def static_content_type(path: str) -> str:
    """The ``Content-Type`` for a static file at ``path``."""
    ext = posixpath.splitext(path)[1]
    known = STATIC_CONTENT_TYPES.get(ext) or STATIC_CONTENT_TYPES.get(ext.lower())
    if known:
        return known
    guess, _ = mimetypes.guess_type(path)
    return guess or "application/octet-stream"


class HttpHandlerBase:
    """Response head, error pages and static files for one request."""

    # Set by the code that builds the handler.
    command: str
    path: str
    headers: Any
    rfile: Any
    wfile: Any
    server: Any
    connection: Any
    request: Any
    client_address: tuple[str, int]
    close_connection: bool = True

    def __init__(self) -> None:
        self._prks_head: list[bytes] = []

    # -- response head -------------------------------------------------------

    def send_response(self, code: int, message: Optional[str] = None) -> None:
        """Start the response head with the status line, and log the request."""
        self.log_request(code)
        if message is None:
            message = _phrase(code)
        self._prks_head = [f"HTTP/1.1 {int(code)} {message}\r\n".encode("latin-1", "strict")]

    def send_header(self, keyword: str, value: Any) -> None:
        self._prks_buffer().append(f"{keyword}: {value}\r\n".encode("latin-1", "strict"))
        if keyword.lower() == "connection":
            lowered = str(value).lower()
            if lowered == "close":
                self.close_connection = True
            elif lowered == "keep-alive":
                self.close_connection = False

    def end_headers(self) -> None:
        """End the head and write it to ``wfile``."""
        head = self._prks_buffer()
        head.append(b"\r\n")
        self.wfile.write(b"".join(head))
        self._prks_head = []

    def _prks_buffer(self) -> list[bytes]:
        if not hasattr(self, "_prks_head"):
            self._prks_head = []
        return self._prks_head

    def send_error(self, code: int, message: Optional[str] = None) -> None:
        """A plain-text error answer; the connection closes after it."""
        message = message or _phrase(code)
        self.log_error("code %d", code)
        self.send_response(code, message)
        self.send_header("Connection", "close")
        body = b""
        if code >= 200 and code not in _NO_BODY_STATUSES:
            body = f"{int(code)} {message}\n".encode("utf-8", "replace")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    # -- logging hooks -------------------------------------------------------

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Called by :meth:`send_response`; subclasses log metadata only."""

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        """Called by :meth:`send_error`; subclasses log metadata only."""

    # -- static files --------------------------------------------------------

    def send_static_file(self, file_path: str, *, head: bool = False) -> None:
        """Answer with the regular file at ``file_path`` (resolved and checked by the caller).

        Anything else (a directory included) is answered 404: no index and no
        listing is served from here. ``If-Modified-Since`` (without
        ``If-None-Match``) answers 304 when the file has not changed.
        """
        if not os.path.isfile(file_path):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        try:
            f: BinaryIO = open(file_path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        with f:
            fs = os.fstat(f.fileno())
            if self._not_modified_since(fs.st_mtime):
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.end_headers()
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-type", static_content_type(file_path))
            self.send_header("Content-Length", str(fs.st_size))
            self.send_header("Last-Modified", email.utils.formatdate(fs.st_mtime, usegmt=True))
            self.end_headers()
            if not head:
                shutil.copyfileobj(f, self.wfile)

    def _not_modified_since(self, mtime: float) -> bool:
        headers = self.headers
        if "If-Modified-Since" not in headers or "If-None-Match" in headers:
            return False
        try:
            ims = email.utils.parsedate_to_datetime(headers["If-Modified-Since"])
        except (TypeError, IndexError, OverflowError, ValueError):
            return False  # an ill-formed value is ignored
        if ims.tzinfo is None:  # obsolete format without a zone: RFC 7231 §7.1.1.1
            ims = ims.replace(tzinfo=datetime.timezone.utc)
        if ims.tzinfo is not datetime.timezone.utc:
            return False
        last_modified = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).replace(microsecond=0)
        return last_modified <= ims
