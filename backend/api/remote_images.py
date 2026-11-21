"""Saved remote images HTTP controller (#1351).

``GET /api/remote-images?url=<http(s) URL>`` serves the Library's saved copy
of a remote image its content names: a video File's thumbnail or a figure in
a note. The page loads every such image from here, never from the other
site (CSP ``img-src 'self'``).

On a miss an editor fetches the URL and saves the copy in one command, at
the epoch admission read (``backend.api.session``: an ``<img>`` sends none),
as the portrait route does. A reader never writes (§9.4): its miss, and a
fill a restore fenced, serve the fetched bytes uncached. A URL the Library
does not name, one PRKS never fetches (not public http(s)), and a fetch that
fails are ``404``. The commands are
``backend.domain.objects.remote_images``.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from backend.log_safety import safe_log_id
from backend.person_image import fetch_and_prepare

LOGGER = logging.getLogger("prks.server")

PATH = "/api/remote-images"
# A saved copy is revalidated on each use against its hash (``ETag``), so a
# restore or a repaired copy that names other bytes for the URL is seen at
# once; an unchanged copy costs a 304.
SAVED_CACHE_CONTROL = "private, no-cache"
# An uncached copy (a reader, a fenced fill) is fetched again after an hour.
UNCACHED_MAX_AGE = 3600


def _module() -> Any:
    """``backend.domain.objects.remote_images``, imported when the route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.objects import remote_images

    return remote_images


def _objects(db: Any) -> Any:
    from backend.storage.objects import LocalFilesystemStorage

    if db.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(db.storage)


def _prepare(url: str) -> Any:
    # ``fetch_and_prepare`` is looked up when called, so a test's patch of
    # this module's binding is the one used.
    return fetch_and_prepare(url, _module().MAX_EDGE)


def _unavailable(handler: Any) -> None:
    """The route's own 404 (JSON), so an ``<img>`` error is told apart from an unknown path."""
    handler.send_json(404, {"error": "Image not available", "code": "IMAGE_NOT_AVAILABLE"})


def _send_saved(handler: Any, copy: Mapping[str, Any]) -> None:
    """A saved copy, validated by its hash: a 304 when the browser holds those bytes."""
    etag = '"%s"' % copy["sha256"]
    if handler._prks_if_none_match(etag):
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Cache-Control", SAVED_CACHE_CONTROL)
        handler.end_headers()
        return
    handler._send_person_profile_image_bytes(copy["body"], copy["subtype"],
                                             cache_control=SAVED_CACHE_CONTROL, etag=etag)


def handle_get(handler: Any, db: Any, path: str, query: Mapping[str, Sequence[str]]) -> bool:
    if path != PATH:
        return False
    from backend.domain.identity.authorization import AccessRefused, Refusal

    values = query.get("url") or []
    module = _module()
    url = module.normalize_url(values[0]) if len(values) == 1 else None
    if url is None:
        _unavailable(handler)
        return True
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("the remote image route ran without an admitted request context")
    database = db.database
    objects = _objects(db)
    cached = module.saved(database, ctx, objects, url)
    if cached is not None:
        _send_saved(handler, cached)
        return True
    try:
        fresh = module.save(database, ctx, objects, url, _prepare)
    except AccessRefused as exc:
        # A reader may not save, and a restore since admission fences the
        # save: either way a URL the Library names is served uncached.
        if exc.refusal not in (Refusal.FORBIDDEN, Refusal.LIBRARY_EPOCH_CHANGED):
            raise
        prepared = _prepare(url) if module.is_named(database, ctx, url) else None
        if prepared is None:
            LOGGER.info("remote_image_unavailable request_id=%s", safe_log_id(handler._prks_request_id))
            _unavailable(handler)
            return True
        handler._send_person_profile_image_bytes(prepared.body, prepared.subtype, max_age=UNCACHED_MAX_AGE)
        return True
    if fresh is None:
        LOGGER.info("remote_image_unavailable request_id=%s", safe_log_id(handler._prks_request_id))
        _unavailable(handler)
        return True
    if "sha256" in fresh:
        _send_saved(handler, fresh)
    else:
        handler._send_person_profile_image_bytes(fresh["body"], fresh["subtype"], max_age=UNCACHED_MAX_AGE)
    return True
