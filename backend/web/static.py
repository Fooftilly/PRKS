"""The frontend's static files (#1424).

``GET``/``HEAD`` of any path outside ``/api/`` that no route takes is a file
of the frontend directory, or the application shell (``index.html``) for
``/`` and unknown paths (the SPA's own routes). During a first run's setup
the setup page stands in for the shell. Every path is resolved (symlinks
included) and must stay beneath the frontend directory; only regular files
are served, never a directory listing. ``If-Modified-Since`` (without
``If-None-Match``) answers 304 for an unchanged file.
"""

from __future__ import annotations

import datetime
import email.utils
import mimetypes
import os
import posixpath
from typing import BinaryIO, Optional
from urllib.parse import unquote

from backend.web.http import Request, Response, Write, error_response

#: The page a first run serves while it waits for setup (#612).
SETUP_PAGE = "setup.html"
SHELL_PAGE = "index.html"
STATIC_CHUNK_BYTES = 65536

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
# Non-fingerprinted JS/CSS must revalidate so users don't get stale app versions.
_REVALIDATE_EXTS = frozenset({".css", ".js", ".map"})
_LONG_CACHE_EXTS = frozenset({".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2"})


def static_content_type(path: str) -> str:
    """The ``Content-Type`` for a static file at ``path``."""
    ext = posixpath.splitext(path)[1]
    known = STATIC_CONTENT_TYPES.get(ext) or STATIC_CONTENT_TYPES.get(ext.lower())
    if known:
        return known
    guess, _ = mimetypes.guess_type(path)
    return guess or "application/octet-stream"


def cache_headers(path: str, *, setup: bool) -> list[tuple[str, str]]:
    """``Cache-Control`` (and the setup marker) for an answer at URL ``path``.

    Browsers and embedded viewers may revalidate small assets often when
    ``Cache-Control`` is missing, so every static answer names one.
    """
    leaf = path.rstrip("/").split("/")[-1].lower()
    if path in ("/", "/" + SHELL_PAGE, "/" + SETUP_PAGE):
        # The SPA shell is never cached by intermediary proxies or CDNs.
        headers = [("Cache-Control", "no-store, max-age=0")]
        if setup:
            # The setup page stands in for the shell (#612): the service
            # worker must not keep it as the offline shell.
            headers.append(("X-PRKS-Shell", "setup"))
        return headers
    if leaf == "sw.js":
        return [("Cache-Control", "no-cache")]
    if leaf == "manifest.webmanifest":
        # Browsers re-check the manifest often; no-cache caused a 304 storm in logs.
        return [("Cache-Control", "public, max-age=3600")]
    ext = os.path.splitext(path)[1].lower()
    if ext in _REVALIDATE_EXTS:
        # App bundles are stable paths (no content hash), so force revalidation.
        return [("Cache-Control", "public, max-age=0, must-revalidate")]
    if ext in _LONG_CACHE_EXTS:
        return [("Cache-Control", "public, max-age=604800, immutable")]
    return []


def serve_frontend(request: Request) -> Response:
    """A file of the frontend, the shell for ``/`` and unknown paths, else 404."""
    app = request.app
    setup = app.setup is not None
    frontend_root = os.path.realpath(app.frontend_dir)

    def inside(path: str) -> bool:
        return path.startswith(frontend_root + os.sep)

    url_path = request.path or "/"
    requested = unquote(url_path)
    candidate = os.path.realpath(os.path.join(frontend_root, requested.lstrip("/")))
    if candidate != frontend_root and not inside(candidate):
        return _with(error_response(404, "Not Found"), cache_headers(url_path, setup=setup))
    shell = SHELL_PAGE
    if setup:
        # The setup phase of a first run (#612): the setup page stands in
        # for the application shell, which has no Library to show yet.
        shell = SETUP_PAGE
        if candidate == os.path.join(frontend_root, SHELL_PAGE):
            candidate = frontend_root
    if candidate == frontend_root or not os.path.exists(candidate):
        if candidate != frontend_root:
            # The shell's Cache-Control goes by the shell's path.
            url_path = "/" + shell
        candidate = os.path.realpath(os.path.join(frontend_root, shell))
    headers = cache_headers(url_path, setup=setup)
    if not inside(candidate) or not os.path.isfile(candidate):
        return _with(error_response(404, "Not Found"), headers)
    return _with(static_file(request, candidate), headers)


def _with(response: Response, headers: list[tuple[str, str]]) -> Response:
    response.headers.extend(headers)
    return response


def static_file(request: Request, file_path: str) -> Response:
    """The regular file at ``file_path`` (resolved and checked by the caller), else 404."""
    if not os.path.isfile(file_path):
        return error_response(404, "File not found")
    try:
        f: BinaryIO = open(file_path, "rb")
    except OSError:
        return error_response(404, "File not found")
    try:
        fs = os.fstat(f.fileno())
        if _not_modified_since(request, fs.st_mtime):
            f.close()
            return Response(304)
        headers = [
            ("Content-Type", static_content_type(file_path)),
            ("Content-Length", str(fs.st_size)),
            ("Last-Modified", email.utils.formatdate(fs.st_mtime, usegmt=True)),
        ]
        if fs.st_size <= STATIC_CHUNK_BYTES:
            body = f.read()
            f.close()
            return Response(200, headers, body)
    except BaseException:
        f.close()
        raise

    def stream(write: Write) -> None:
        while True:
            chunk = f.read(STATIC_CHUNK_BYTES)
            if not chunk:
                return
            write(chunk)

    return Response(200, headers, stream=stream, cleanup=f.close)


def _not_modified_since(request: Request, mtime: float) -> bool:
    headers = request.headers
    since: Optional[str] = headers.get("If-Modified-Since")
    if since is None or "If-None-Match" in headers:
        return False
    try:
        ims = email.utils.parsedate_to_datetime(since)
    except (TypeError, IndexError, OverflowError, ValueError):
        return False  # an ill-formed value is ignored
    if ims.tzinfo is None:  # obsolete format without a zone: RFC 7231 §7.1.1.1
        ims = ims.replace(tzinfo=datetime.timezone.utc)
    if ims.tzinfo is not datetime.timezone.utc:
        return False
    last_modified = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).replace(microsecond=0)
    return last_modified <= ims
