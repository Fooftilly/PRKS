"""Persons HTTP controller (PG-C3 #642).

``/api/persons`` (GET the catalog with an ETag, POST create),
``/api/persons/:id`` (GET with its Works and groups; PATCH profile fields
and, with ``group_ids``, the exact group set in the same transaction;
DELETE one no role names), ``GET /api/persons/:id/metadata-state`` (field
revisions), and ``GET /api/persons/:id/profile-image`` (the cached portrait).
An ``image_url`` that is not a public http(s) URL is ``400 Invalid
image_url``; a domain refusal (``ValueError``) on PATCH is ``400``, on
DELETE ``404`` (not found), ``409`` (linked Works) or ``400``.
``GET /api/persons/:id/group-state`` belongs to ``backend/api/person_groups.py``.

The calls go to ``backend/domain/people/person_commands`` for the admitted
request's context on the served Library's database. The commands open the
transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import unquote

from backend.api.common import objects, optional_pdfs_dir, revalidated
from backend.derived_caches import prks_delete_person_image_cache
from backend.log_safety import safe_error_type, safe_log_id
from backend.person_image import (
    PersonImageUrlError,
    fetch_and_prepare,
    identify_cached_portrait_subtype,
    normalize_person_image_url,
)
from backend.web.http import Request, Response, bytes_response, error_response, json_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")


def image_response(body: bytes, subtype: str, *, max_age: int = 86400,
                   cache_control: Optional[str] = None, etag: Optional[str] = None) -> Response:
    """A cached image (a portrait or a saved remote image), typed by its bytes."""
    actual = identify_cached_portrait_subtype(body) or subtype
    mime = "image/webp" if actual == "webp" else "image/jpeg"
    headers = [("Cache-Control", cache_control or f"private, max-age={max_age}")]
    if etag:
        headers.append(("ETag", etag))
    return bytes_response(200, body, mime, headers)


def catalog(req: Request) -> Response:
    from backend.domain.people import person_commands

    return revalidated(req, "persons", person_commands.catalog(*req.actor))


def metadata_state(req: Request) -> Response:
    """``GET /api/persons/:id/metadata-state``."""
    from backend.domain.people import person_commands

    # REVISIONS ONLY. The Person detail already carries every profile value
    # and none of them has a length bound, so echoing them here would make a
    # second copy of the whole biography in what this sends and what
    # IndexedDB stores.
    data = person_commands.metadata_state(*req.actor, unquote(req.params["person_id"]))
    return revalidated(req, "person-metadata-state", data, missing="Person not found")


def detail(req: Request) -> Response:
    from backend.domain.people import person_commands

    data = person_commands.detail(*req.actor, unquote(req.params["person_id"]), optional_pdfs_dir(req))
    if data:
        return json_response(req, 200, data)
    return error_response(404, "Person not found")


def profile_image(req: Request) -> Response:
    """Serve the Person's portrait from the Library's ``portraits`` objects.

    A cached portrait is the immutable object the Person row names
    (``portrait_locator``), so it is backed up, restored and cleaned up with
    the Library. On a miss an editor fetches ``image_url`` and makes it the
    cached portrait in one command, at the epoch admission read
    (``backend.api.session``: an ``<img>`` sends none). A reader never
    writes (§9.4): its miss serves the fetched bytes uncached, as does a fill
    a restore fenced. Nothing is read from or written to ``people_dir``.
    """
    from backend.domain.identity.authorization import AccessRefused, Refusal
    from backend.domain.people import person_commands

    person_id = unquote(req.params["person_id"])
    database, ctx = req.actor
    row = person_commands.detail(database, ctx, person_id, optional_pdfs_dir(req))
    if not row:
        return error_response(404, "Person not found")
    url = (row.get("image_url") or "").strip()
    if not url:
        return error_response(404, "Profile image not available")
    store = objects(req)
    try:
        cached = person_commands.portrait(database, ctx, store, person_id)
        if cached is not None:
            return image_response(cached["body"], cached["subtype"])
        # ``fetch_and_prepare`` is looked up when called, so the module's
        # binding is the one used.
        fresh = person_commands.cache_portrait(database, ctx, store, person_id,
                                               lambda source: fetch_and_prepare(source))
    except LookupError:
        return error_response(404, "Person not found")
    except AccessRefused as exc:
        # A reader may not fill the cache, and a restore since admission
        # fences the fill: either way the image is served uncached.
        if exc.refusal not in (Refusal.FORBIDDEN, Refusal.LIBRARY_EPOCH_CHANGED):
            raise
        prepared = fetch_and_prepare(url)
        fresh = None if prepared is None else {"body": prepared.body, "subtype": prepared.subtype}
    if fresh is None:
        LOGGER.info("portrait_fetch_failed request_id=%s", safe_log_id(req.request_id))
        return error_response(404, "Profile image not available")
    return image_response(fresh["body"], fresh["subtype"], max_age=3600)


def update(req: Request) -> Response:
    from backend.domain.people import person_commands

    p_id = req.params["person_id"]
    data = dict(req.data)
    group_ids = data.pop("group_ids", None)
    image_url_changed = "image_url" in data
    if image_url_changed:
        try:
            data["image_url"] = normalize_person_image_url(data.get("image_url"))
        except PersonImageUrlError:
            return json_response(req, 400, {"error": "Invalid image_url"})
    if group_ids is not None and not isinstance(group_ids, list):
        return json_response(req, 400, {"error": "group_ids must be a JSON array"})
    # Metadata and memberships commit or roll back together, so an unknown
    # group id cannot leave the profile half-updated. A failed request must
    # not have changed canonical state -- that is what makes "failed
    # mutation keeps its cache" sound.
    try:
        person_commands.update_profile(*req.actor, p_id, data, group_ids)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    storage = req.library.storage
    if image_url_changed and storage is not None:
        # Disposable per-Person cache of the retired layout, cleared only after
        # the canonical profile change committed; a failure here never fails
        # the PATCH.
        try:
            prks_delete_person_image_cache(p_id, storage.people_dir)
        except Exception as e:
            LOGGER.warning(
                "person_image_cache_cleanup_failed person_id=%s error_type=%s",
                safe_log_id(p_id),
                safe_error_type(e),
            )
    return json_response(req, 200, {"status": "updated"})


def create(req: Request) -> Response:
    from backend.domain.people import person_commands

    data = req.data
    try:
        image_url = normalize_person_image_url(data.get("image_url", "") or "")
    except PersonImageUrlError:
        return json_response(req, 400, {"error": "Invalid image_url"})
    fields = {
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "aliases": data.get("aliases", ""),
        "about": data.get("about", ""),
        "image_url": image_url,
    }
    for name in ("link_wikipedia", "link_stanford_encyclopedia", "link_iep", "links_other",
                 "birth_date", "death_date"):
        fields[name] = data.get(name, "") or ""
    p_id = person_commands.create(*req.actor, fields)
    return json_response(req, 200, {"id": p_id})


def delete(req: Request) -> Response:
    from backend.domain.people import person_commands

    try:
        person_commands.delete_if_unlinked(*req.actor, req.params["person_id"])
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            return json_response(req, 404, {"error": msg})
        if "linked works" in msg.lower():
            return json_response(req, 409, {"error": msg})
        return json_response(req, 400, {"error": msg})
    return json_response(req, 200, {"status": "deleted"})


ROUTES = (
    route("GET", "/api/persons", catalog),
    route("POST", "/api/persons", create),
    route("GET", "/api/persons/{person_id}", detail),
    route("PATCH", "/api/persons/{person_id}", update),
    route("DELETE", "/api/persons/{person_id}", delete),
    route("GET", "/api/persons/{person_id}/metadata-state", metadata_state),
    route("GET", "/api/persons/{person_id}/profile-image", profile_image),
)
