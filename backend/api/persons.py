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

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path
belongs to this family (including refusals already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import unquote

from backend.derived_caches import prks_delete_person_image_cache
from backend.etags import etag_for_representation
from backend.log_safety import safe_error_type, safe_log_id
from backend.person_image import PersonImageUrlError, fetch_and_prepare, normalize_person_image_url

LOGGER = logging.getLogger("prks.server")

COLLECTION = "/api/persons"
_PREFIX = COLLECTION + "/"


def _commands() -> Any:
    """``backend.domain.people.person_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.people import person_commands

    return person_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Person route ran without an admitted request context")
    return db.database, ctx


def _pdfs_dir(db: Any) -> Optional[str]:
    """The data root's PDF directory; ``None`` when the Library has none (the command decides)."""
    return None if db.storage is None else db.storage.pdfs_dir


def _objects(db: Any) -> Any:
    """The served Library's managed objects (portraits live there)."""
    from backend.storage.objects import LocalFilesystemStorage

    if db.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(db.storage)


def _item_id(path: str) -> Optional[str]:
    """The Person id of ``/api/persons/:id`` (still quoted), else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return path.split("/")[-1]
    return None


def _sub(path: str, leaf: str) -> Optional[str]:
    """The Person id of ``/api/persons/:id/<leaf>`` (unquoted), else ``None``."""
    parts = path.split("/")
    if path.startswith(_PREFIX) and len(parts) == 5 and parts[4] == leaf:
        return unquote(parts[3])
    return None


def _send_revalidated(handler: Any, label: str, data: Any) -> None:
    etag = etag_for_representation(label, data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


def handle_get(handler: Any, db: Any, path: str) -> bool:
    if path == COLLECTION:
        _send_revalidated(handler, "persons", _commands().catalog(*_actor(handler, db)))
        return True
    return False


def handle_metadata_state(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/persons/:id/metadata-state``."""
    p_id = _sub(path, "metadata-state")
    if p_id is None:
        return False
    # REVISIONS ONLY. The Person detail already carries every profile value
    # and none of them has a length bound, so echoing them here would make a
    # second copy of the whole biography in what this sends and what
    # IndexedDB stores.
    data = _commands().metadata_state(*_actor(handler, db), p_id)
    if data is None:
        handler.send_json(404, {"error": "Person not found"})
    else:
        _send_revalidated(handler, "person-metadata-state", data)
    return True


def handle_detail(handler: Any, db: Any, path: str) -> bool:
    """``GET /api/persons/:id/profile-image`` and ``GET /api/persons/:id``."""
    if path.startswith(_PREFIX) and path.endswith("/profile-image"):
        parts = path.split("/")
        if len(parts) != 5:
            handler.send_error(404, "API endpoint not found")
            return True
        _send_portrait(handler, db, unquote(parts[3]))
        return True
    p_id = _item_id(path)
    if p_id is None:
        return False
    data = _commands().detail(*_actor(handler, db), unquote(p_id), _pdfs_dir(db))
    if data:
        handler.send_json(200, data)
    else:
        handler.send_error(404, "Person not found")
    return True


def _send_portrait(handler: Any, db: Any, person_id: str) -> None:
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

    commands = _commands()
    database, ctx = _actor(handler, db)
    row = commands.detail(database, ctx, person_id, _pdfs_dir(db))
    if not row:
        handler.send_error(404, "Person not found")
        return
    url = (row.get("image_url") or "").strip()
    if not url:
        handler.send_error(404, "Profile image not available")
        return
    objects = _objects(db)
    try:
        cached = commands.portrait(database, ctx, objects, person_id)
        if cached is not None:
            handler._send_person_profile_image_bytes(cached["body"], cached["subtype"])
            return
        # ``fetch_and_prepare`` is looked up when called, so the module's
        # binding is the one used.
        fresh = commands.cache_portrait(database, ctx, objects, person_id,
                                        lambda source: fetch_and_prepare(source))
    except LookupError:
        handler.send_error(404, "Person not found")
        return
    except AccessRefused as exc:
        # A reader may not fill the cache, and a restore since admission
        # fences the fill: either way the image is served uncached.
        if exc.refusal not in (Refusal.FORBIDDEN, Refusal.LIBRARY_EPOCH_CHANGED):
            raise
        prepared = fetch_and_prepare(url)
        fresh = None if prepared is None else {"body": prepared.body, "subtype": prepared.subtype}
    if fresh is None:
        LOGGER.info("portrait_fetch_failed request_id=%s", safe_log_id(handler._prks_request_id))
        handler.send_error(404, "Profile image not available")
        return
    handler._send_person_profile_image_bytes(fresh["body"], fresh["subtype"], max_age=3600)


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    p_id = _item_id(path)
    if p_id is None:
        return False
    group_ids = data.pop("group_ids", None)
    image_url_changed = "image_url" in data
    if image_url_changed:
        try:
            data["image_url"] = normalize_person_image_url(data.get("image_url"))
        except PersonImageUrlError:
            handler.send_json(400, {"error": "Invalid image_url"})
            return True
    if group_ids is not None and not isinstance(group_ids, list):
        handler.send_json(400, {"error": "group_ids must be a JSON array"})
        return True
    # Metadata and memberships commit or roll back together, so an unknown
    # group id cannot leave the profile half-updated. A failed request must
    # not have changed canonical state -- that is what makes "failed
    # mutation keeps its cache" sound.
    try:
        _commands().update_profile(*_actor(handler, db), p_id, data, group_ids)
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    if image_url_changed and db.storage is not None:
        # Disposable per-Person cache of the retired layout, cleared only after
        # the canonical profile change committed; a failure here never fails
        # the PATCH.
        try:
            prks_delete_person_image_cache(p_id, db.storage.people_dir)
        except Exception as e:
            LOGGER.warning(
                "person_image_cache_cleanup_failed person_id=%s error_type=%s",
                safe_log_id(p_id),
                safe_error_type(e),
            )
    handler.send_json(200, {"status": "updated"})
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if path != COLLECTION:
        return False
    try:
        image_url = normalize_person_image_url(data.get("image_url", "") or "")
    except PersonImageUrlError:
        handler.send_json(400, {"error": "Invalid image_url"})
        return True
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
    p_id = _commands().create(*_actor(handler, db), fields)
    handler.send_json(200, {"id": p_id})
    return True


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    p_id = _item_id(path)
    if p_id is None:
        return False
    try:
        _commands().delete_if_unlinked(*_actor(handler, db), p_id)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            handler.send_json(404, {"error": msg})
        elif "linked works" in msg.lower():
            handler.send_json(409, {"error": msg})
        else:
            handler.send_json(400, {"error": msg})
        return True
    handler.send_json(200, {"status": "deleted"})
    return True
