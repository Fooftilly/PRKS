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
from typing import Any, Mapping

from backend.api.common import objects
from backend.api.persons import image_response
from backend.log_safety import safe_log_id
from backend.person_image import fetch_and_prepare
from backend.web.http import Request, Response, json_response, not_modified
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")

PATH = "/api/remote-images"
# A saved copy is revalidated on each use against its hash (``ETag``), so a
# restore or a repaired copy that names other bytes for the URL is seen at
# once; an unchanged copy costs a 304.
SAVED_CACHE_CONTROL = "private, no-cache"
# An uncached copy (a reader, a fenced fill) is fetched again after an hour.
UNCACHED_MAX_AGE = 3600


def _prepare(url: str) -> Any:
    from backend.domain.objects import remote_images

    # ``fetch_and_prepare`` is looked up when called, so a test's patch of
    # this module's binding is the one used.
    return fetch_and_prepare(url, remote_images.MAX_EDGE)


def _unavailable(req: Request) -> Response:
    """The route's own 404 (JSON), so an ``<img>`` error is told apart from an unknown path."""
    return json_response(req, 404, {"error": "Image not available", "code": "IMAGE_NOT_AVAILABLE"})


def _saved(req: Request, copy: Mapping[str, Any]) -> Response:
    """A saved copy, validated by its hash: a 304 when the browser holds those bytes."""
    etag = '"%s"' % copy["sha256"]
    if req.if_none_match(etag):
        return not_modified(etag, SAVED_CACHE_CONTROL, vary=False)
    return image_response(copy["body"], copy["subtype"], cache_control=SAVED_CACHE_CONTROL, etag=etag)


def remote_image(req: Request) -> Response:
    from backend.domain.identity.authorization import AccessRefused, Refusal
    from backend.domain.objects import remote_images

    values = req.query.get("url") or []
    url = remote_images.normalize_url(values[0]) if len(values) == 1 else None
    if url is None:
        return _unavailable(req)
    database, ctx = req.actor
    store = objects(req)
    cached = remote_images.saved(database, ctx, store, url)
    if cached is not None:
        return _saved(req, cached)
    try:
        fresh = remote_images.save(database, ctx, store, url, _prepare)
    except AccessRefused as exc:
        # A reader may not save, and a restore since admission fences the
        # save: either way a URL the Library names is served uncached.
        if exc.refusal not in (Refusal.FORBIDDEN, Refusal.LIBRARY_EPOCH_CHANGED):
            raise
        prepared = _prepare(url) if remote_images.is_named(database, ctx, url) else None
        if prepared is None:
            LOGGER.info("remote_image_unavailable request_id=%s", safe_log_id(req.request_id))
            return _unavailable(req)
        return image_response(prepared.body, prepared.subtype, max_age=UNCACHED_MAX_AGE)
    if fresh is None:
        LOGGER.info("remote_image_unavailable request_id=%s", safe_log_id(req.request_id))
        return _unavailable(req)
    if "sha256" in fresh:
        return _saved(req, fresh)
    return image_response(fresh["body"], fresh["subtype"], max_age=UNCACHED_MAX_AGE)


ROUTES = (
    route("GET", PATH, remote_image),
)
