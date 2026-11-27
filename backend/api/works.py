"""Works HTTP controller (PG-C3 #642, #1424).

``GET /api/works`` (every Work summary, or with ``?projection=browse`` the
compact browse catalog; both with ETags), ``POST /api/works`` (create a Work
from an upload, an existing managed PDF, a video URL or no file),
``GET /api/works/:id`` (the detail with its Research refs),
``GET /api/works/:id/metadata-state``, ``/source-state`` and ``/notes-state``
(with ETags), ``GET /api/recent``, ``GET /api/recently-added`` and
``GET /api/bibtex/:id``; ``PATCH`` and ``DELETE /api/works/:id``,
``POST /api/works/bulk``, ``POST /api/citations`` and
``POST /api/works/:id/opened``. :func:`thumbnail_fields` serves the
thumbnail route.

The calls go to ``backend/domain/works/work_commands`` for the admitted
request's context on the served Library's database, so authorization stays
in ``library_query`` and ``library_command``. The commands are imported
when a route runs: the served app loads no PostgreSQL module at import time
(``tests/test_e2e_policy.py``).

A video Work is YouTube only: its URL is validated before any mutation, and
its title, author and thumbnail are filled from YouTube's oEmbed answer
(:func:`fetch_youtube_oembed`) when the request leaves them empty.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import quote, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from backend.domain.objects.content_url import parse_content_url
from backend.domain.works import source_rules
from backend.api.common import objects, revalidated, text_field
from backend.api_contract.citations import CitationsRequest, CitationsResult
from backend.api_contract.errors import NotPortedEnvelope
from backend.api_contract.operation import Operation, Param, Reply, body, error, ok, plain
from backend.api_contract.operation import revalidated as revalidated_read
from backend.api_contract.works import (
    BulkWorkRequest,
    BulkWorkResult,
    RecentlyAddedWork,
    RecentWork,
    WorkCatalog,
    WorkCreated,
    WorkCreateRequest,
    WorkDetail,
    WorkMetadataState,
    WorkNotesState,
    WorkPatch,
    WorkSourceState,
    WorkStatus,
)
from backend.domain.people.errors import MissingPersonError
from backend.domain.works.rules import effective_source_kind
from backend.runtime.log_safety import safe_error_type, safe_log_id, safe_log_label
from backend.web.dispatch import not_ported
from backend.web.http import Request, Response, bytes_response, error_response, json_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")

COLLECTION = "/api/works"


# -- for other routes ---------------------------------------------------------------


def thumbnail_fields(req: Request, work_id: str) -> Optional[dict[str, Any]]:
    """The Work's primary Asset ``file_path`` and thumbnail fields; ``None`` for a missing Work."""
    from backend.domain.works import work_commands

    return work_commands.thumbnail_fields(*req.actor, work_id)


# -- reads --------------------------------------------------------------------------


def catalog(req: Request) -> Response:
    from backend.domain.works import work_commands

    # ``?projection=browse`` is an explicit, additive contract: the compact
    # catalog the browse routes cache. The default keeps its semantics for
    # every other caller. Each list is built once and the ETag comes from
    # these rows; file_size_bytes is the primary Asset's byte_size.
    if req.query_value("projection") == "browse":
        return revalidated(req, "works-browse", work_commands.browse_catalog(*req.actor))
    return revalidated(req, "works", work_commands.catalog(*req.actor))


def metadata_state(req: Request) -> Response:
    from backend.domain.works import work_commands

    return revalidated(req, "work-metadata-state", work_commands.metadata_state(*req.actor, req.params["work_id"]))


def source_state(req: Request) -> Response:
    from backend.domain.works import work_commands

    # The revision and the stored source, from one snapshot: the editor's
    # base is this pair, never a revision beside a separately read Work.
    data = work_commands.source_state(*req.actor, req.params["work_id"])
    if data == source_rules.INVALID_SOURCE_STATE:
        # The product reads this row as a video, but its identity cannot
        # be stated. Said plainly rather than answered with a revision the
        # mutation would then refuse to act on.
        return json_response(req, 409, {
            "error": "This file's video source cannot be read. Its link is not a "
                     "supported video URL.",
            "code": source_rules.INVALID_SOURCE_STATE})
    return revalidated(req, "work-source-state", data)


def notes_state(req: Request) -> Response:
    from backend.domain.works import work_commands

    return revalidated(req, "work-notes-state", work_commands.notes_state(*req.actor, req.params["work_id"]))


def detail(req: Request) -> Response:
    from backend.domain.works import work_commands

    w_id = req.params["work_id"]
    database, ctx = req.actor
    data = work_commands.detail(database, ctx, w_id)
    if not data:
        return error_response(404, "Work not found")
    try:
        from backend.domain.research import index_commands

        data["research_refs"] = index_commands.research_refs(database, ctx, w_id)
    except Exception as e:  # noqa: BLE001 - the Work still answers without its refs
        LOGGER.warning("research_index_refs_failed work_id=%s error_type=%s",
                       safe_log_id(w_id), safe_error_type(e))
    return json_response(req, 200, data)


# Representation-derived ETags: a COUNT + MAX(last_opened_at) probe cannot
# see a re-open of the most recent Work, nor a display field changing on a
# row already in the result.
def recent(req: Request) -> Response:
    from backend.domain.works import work_commands

    return revalidated(req, "recent", work_commands.recent_browse(*req.actor))


def recently_added(req: Request) -> Response:
    from backend.domain.works import work_commands

    return revalidated(req, "recently-added", work_commands.recently_added_browse(*req.actor))


def bibtex(req: Request) -> Response:
    from backend.domain.works import work_commands

    entry = work_commands.bibtex(*req.actor, req.params["work_id"])
    return bytes_response(200, entry.encode(), "text/plain")


# -- create -------------------------------------------------------------------------


def fetch_youtube_oembed(url: str) -> Optional[dict[str, Any]]:
    """Best-effort metadata for a supported YouTube URL via oEmbed (no API key).

    The outbound request is built from PRKS's validated provider identity, not
    from the caller's URL spelling. This keeps the network boundary fixed even
    when the source URL contains unrelated query parameters or malformed input.
    """
    if not url or not str(url).strip():
        return None
    try:
        video_id = source_rules.validate_youtube_url(str(url).strip())
        if not video_id:
            return None
        canonical_url = "https://www.youtube.com/watch?v=" + quote(video_id, safe="")
        oembed_url = "https://www.youtube.com/oembed?format=json&url=" + quote(canonical_url, safe="")
        request = UrlRequest(
            oembed_url,
            headers={
                "User-Agent": "PRKS/1.0 (oEmbed metadata fetch)",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=6) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _new_work(req: Request, file_path: str, upload: Optional[tuple[str, bytes]], fields: dict[str, Any]) -> str:
    """Create a Work from an upload, another Asset's managed PDF, a stream or no file; its id.

    ``fields`` are the create's other fields (``work_commands.NEW_WORK_DEFAULTS``).
    No adoption lock: the command checks an existing file for an actor who
    may edit and adopts it in the create's transaction, and the settle that
    removes bytes re-checks every Asset under the per-object lock (§7.4,
    §11.2). An upload is stored under fresh keys.
    """
    from backend.domain.works import work_commands

    writing = None
    if upload is not None:
        from backend.domain.processing.uploads import PDF_MAGIC

        name, data = upload
        # The same rule as a Processing upload (#772): nothing is stored
        # for bytes that are not a PDF, and the caller is told why.
        if not data:
            raise ValueError("The uploaded file is empty.")
        if not data.startswith(PDF_MAGIC):
            raise ValueError("The upload is not a PDF.")

        def write(out: Any) -> None:
            out.write(data)

        writing = (name, write)
    # An upload is the Work's file: a content URL echoed beside it is not read.
    stored_key = ""
    if upload is None and file_path:
        # Adopting a file is sharing the bytes of an Asset of this Library,
        # named by its content URL (#1427); nothing else names a file. A
        # versioned URL adopts only the bytes it names: an earlier version is
        # refused, never resolved to a replacement's bytes.
        reference = parse_content_url(file_path)
        stored_key = (work_commands.asset_object(*req.actor, *reference) if reference else None) or ""
        if not stored_key:
            raise ValueError("file_path is not a managed PDF")
    return work_commands.create(*req.actor, objects(req),
                                work_commands.new_work_fields(stored_key=stored_key, **fields), writing)


def create(req: Request) -> Response:
    """``POST /api/works``: create, file, credit and optionally list a new Work."""
    from backend.api import folders as folders_api
    from backend.api import playlists as playlists_api
    from backend.api import work_roles as work_roles_api

    data = dict(req.data)
    raw_file_path = data.get("file_path", "")
    file_path = raw_file_path if isinstance(raw_file_path, str) else ""
    source_kind = text_field(data, "source_kind").lower()
    source_url = text_field(data, "source_url")

    # Video sources are currently supported for YouTube only. Validate
    # authoritatively before any mutation so a malformed/non-YouTube URL can
    # never create a broken Video Work.
    if source_kind == "video" and not source_rules.validate_youtube_url(source_url):
        return json_response(req, 400, {"error": "Invalid YouTube URL"})

    # A named destination that does not exist is refused before any bytes
    # are stored or a row is created, matching the durable CREATE_WORK
    # boundary. Filing only after the commit used to fail with the Work
    # already created, so a retry duplicated it.
    requested_folder = data.get("folder_id")
    requested_folder = str(requested_folder).strip() if requested_folder is not None else ""
    if requested_folder and not folders_api.exists(req, requested_folder):
        return json_response(req, 404, {
            "error": "The selected folder no longer exists.",
            "code": "FOLDER_NOT_FOUND",
        })
    # Same for the People the Work is born with: a Person this server has
    # not heard of yet (e.g. still queued on the device that created it)
    # would fail its foreign key only after the Work was committed. Only
    # roles creation would insert count: one it skips (no role type,
    # unknown type) must not block the Work.
    if work_roles_api.missing_initial_person_ids(req, data.get("roles")):
        return json_response(req, 409, {
            "error": "A person on this file has not finished saving yet.",
            "code": "PERSON_NOT_FOUND",
        })

    upload = None
    # A chosen file is sent even when it is empty, so a 0-byte file is
    # refused below rather than read as "no file" (#772).
    if data.get("file_name") and isinstance(data.get("file_b64"), str):
        try:
            decoded_pdf = base64.b64decode(data["file_b64"], validate=True)
        except (binascii.Error, ValueError):
            return json_response(req, 400, {"error": "Invalid file_b64 payload"})
        # The create command stores the bytes as objects and adopts them in
        # its create (objects.ingest, §10, §11.2).
        upload = (str(data["file_name"]), decoded_pdf)

    provider = text_field(data, "provider").lower()
    provider_id = text_field(data, "provider_id")
    thumb_url = text_field(data, "thumb_url")
    source_mime = text_field(data, "source_mime")
    urldate = text_field(data, "urldate")

    # Video ingest: URL + oEmbed metadata.
    #
    # Decided by the EFFECTIVE kind, using the same authoritative function
    # the create classifies with. A caller that omitted a redundant
    # `source_kind` still created a video -- the inference makes "no file,
    # has a URL" one -- and enriching only the explicitly labelled ones
    # meant two Works with identical canonical identity got different
    # titles, authors and thumbnails purely because one request said so
    # twice. An upload the create has not stored yet is still a file.
    classified_file = file_path or ("upload" if upload else "")
    if effective_source_kind(source_kind, source_url, classified_file) == "video":
        if not provider:
            try:
                host = (urlparse(source_url).netloc or "").lower()
            except ValueError:
                host = ""
            if source_rules.is_youtube_host(host):
                provider = "youtube"
        if provider == "youtube" and not provider_id:
            provider_id = source_rules.youtube_video_id(source_url) or ""
        meta = fetch_youtube_oembed(source_url) if provider == "youtube" else None
        if meta:
            if not thumb_url and meta.get("thumbnail_url"):
                thumb_url = str(meta.get("thumbnail_url") or "").strip()
            # An empty title or author is filled from oEmbed.
            if not text_field(data, "title") and meta.get("title"):
                data["title"] = str(meta.get("title") or "").strip()
            if not text_field(data, "author_text") and meta.get("author_name"):
                data["author_text"] = str(meta.get("author_name") or "").strip()
        if not urldate:
            urldate = time.strftime("%Y-%m-%d")

    # A source identity that contradicts itself is the caller's mistake,
    # not a server fault: the create refuses it at the creation boundary,
    # and that refusal is a 400.
    work_fields = dict(
        title=data.get("title", "Untitled"),
        status=data.get("status", "Not Started"),
        abstract=data.get("abstract", ""),
        text_content=data.get("text_content", ""),
        published_date=data.get("published_date", ""),
        author_text=data.get("author_text", ""),
        year=data.get("year", ""),
        publisher=data.get("publisher", ""),
        location=data.get("location", ""),
        edition=data.get("edition", ""),
        journal=data.get("journal", ""),
        volume=data.get("volume", ""),
        issue=data.get("issue", ""),
        pages=data.get("pages", ""),
        isbn=data.get("isbn", ""),
        doi=data.get("doi", ""),
        doc_type=data.get("doc_type", "article"),
        source_kind=source_kind,
        source_url=source_url,
        source_mime=source_mime,
        thumb_url=thumb_url,
        provider=provider,
        provider_id=provider_id,
        urldate=urldate,
        thumb_page=data.get("thumb_page"),
        private_notes=data.get("private_notes", ""),
    )
    # No adoption lock or discard here: the create checks an adopted file
    # under the actor's edit right, and bytes a refused create stored are
    # the orphan sweep's (§11.2).
    try:
        w_id = _new_work(req, file_path, upload, work_fields)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})

    # Undo this create: a PDF this request uploaded goes with it; existing
    # bytes it only adopted were never its to remove.
    def undo_create() -> None:
        delete_work(req, w_id, keep_managed_pdf=not upload)

    try:
        folder_id = requested_folder or folders_api.ensure_uncategorized(req)
        folders_api.file_work(req, folder_id, w_id)
    except ValueError as e:
        undo_create()
        return json_response(req, 409, {"error": str(e)})

    # Link persons/roles provided during upload: all of them in one
    # transaction, which also re-checks every Person under the write lock,
    # so a refusal leaves no role and no alias change behind.
    try:
        work_roles_api.insert_initial(req, w_id, data.get("roles", []))
    except MissingPersonError:
        # Deleted after the up-front check: same outcome as that check,
        # through the existing compensation.
        undo_create()
        return json_response(req, 409, {
            "error": "A person on this file no longer exists.",
            "code": "PERSON_NOT_FOUND",
        })

    # Optionally attach to playlist. Last: it writes playlist revisions that
    # the refusals above could not undo.
    playlist_id = text_field(data, "playlist_id")
    if playlist_id:
        try:
            playlists_api.add_item(req, playlist_id, w_id)
        except Exception as exc:
            # best-effort: do not fail work creation if playlist link fails
            LOGGER.warning(
                "work_playlist_attach_failed playlist_id=%s work_id=%s request_id=%s error_type=%s",
                safe_log_id(playlist_id),
                safe_log_id(w_id),
                safe_log_id(req.request_id),
                safe_error_type(exc),
            )
    return json_response(req, 200, {"id": w_id})


# -- writes -------------------------------------------------------------------------


def delete_work(req: Request, work_id: str, *, keep_managed_pdf: bool = False) -> None:
    """Delete a Work, then its thumbnails (derived files under the root), best effort.

    The delete records its files' cleanup claims in its own transaction and
    the orphan sweep removes the bytes (§11.2); the text and Research indexes
    are the database's.
    """
    from backend.media.derived_caches import prks_delete_pdf_thumbnails_for_work_id
    from backend.domain.works import work_commands

    deleted = work_commands.delete(*req.actor, work_id, claim_pdf=not keep_managed_pdf)
    storage = req.library.storage
    thumbs_dir = storage.thumbs_dir if storage is not None else None
    if deleted is None or not thumbs_dir:
        return
    # Log the id the database returned, never the request's own value.
    logged_id = safe_log_id(deleted.work_id)
    try:
        failed = prks_delete_pdf_thumbnails_for_work_id(deleted.work_id, thumbs_dir)
        if failed:
            LOGGER.warning("work_delete_thumbnails_cleanup_failed work_id=%s failed_count=%s",
                           logged_id, len(failed))
    except Exception as e:  # noqa: BLE001 - the Work is deleted; the thumbnails are best effort
        LOGGER.warning("work_delete_thumbnails_cleanup_failed work_id=%s error_type=%s",
                       logged_id, safe_error_type(e))


def delete(req: Request) -> Response:
    delete_work(req, req.params["work_id"])
    return json_response(req, 200, {"status": "deleted"})


def _stored_file_path(req: Request, work_id: str) -> str:
    """The Work's stored ``file_path``, its primary Asset's (``""`` when none or no such Work)."""
    return str((thumbnail_fields(req, work_id) or {}).get("file_path") or "")


def update(req: Request) -> Response:
    """``PATCH /api/works/:id``: a Folder move, the Research Note and the metadata fields."""
    from backend.api import folders as folders_api
    from backend.api import research_notes as research_notes_api
    from backend.domain.research.rules import ResearchError
    from backend.domain.works import work_commands

    w_id = req.params["work_id"]
    body = dict(req.data)
    # Only a file_path that would change the stored one is refused. Echoing
    # the same file_path on a title/status PATCH must not fail when the
    # bytes are already gone; the echo is dropped below so this request
    # cannot rewrite it after a concurrent retarget or cleanup.
    if "file_path" in body and str(body.get("file_path") or "") != _stored_file_path(req, w_id):
        # Adopting another file is not ported. Refuse before any part of the
        # PATCH (a folder move, notes) commits, so a 501 never hides an
        # applied change.
        return not_ported(req, "Changing a Work's file_path is not ported to PostgreSQL yet.")
    if "folder_id" in body:
        raw_folder = body.pop("folder_id")
        try:
            folders_api.move_work(req, w_id, raw_folder)
        except ValueError as e:
            return json_response(req, 400, {"error": str(e)})
    body.pop("file_path", None)
    if "text_content" in body:
        notes_text = body.pop("text_content")
        try:
            research_notes_api.save(req, w_id, notes_text)
        except ResearchError as e:
            return json_response(req, e.http_status, {"error": str(e), "code": e.code})
    if body:
        try:
            work_commands.update_metadata(*req.actor, w_id, body)
        except ValueError as e:
            # A refused value or length is the caller's mistake, not a
            # server fault: say so rather than 500.
            return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "updated"})


def bulk(req: Request) -> Response:
    """``POST /api/works/bulk``: one action on many Works, all or nothing."""
    from backend.domain.works import work_commands
    from backend.domain.works.rules import BulkWorkError

    try:
        result = work_commands.bulk_update(*req.actor, req.data)
    except BulkWorkError as e:
        return json_response(req, int(e.http_status), {"error": str(e)})
    LOGGER.info(
        "bulk_work_update action=%s work_count=%s target_count=%s request_id=%s",
        safe_log_label(result.get("action")),
        int(result.get("requested") or 0),
        int(result.get("target_count") or 0),
        safe_log_id(req.request_id),
    )
    return json_response(req, 200, {
        "status": result.get("status"),
        "action": result.get("action"),
        "requested": result.get("requested"),
        "updated": result.get("updated"),
    })


def citations(req: Request) -> Response:
    """``POST /api/citations``: CSL-JSON items or combined BibTeX for selected Works.

    A read: it changes nothing. It is a POST because a selection of up to
    ``PRKS_BULK_WORK_MAX`` ids does not fit a query string.
    """
    from backend.domain.works import work_commands
    from backend.domain.works.rules import BulkWorkError

    try:
        result = work_commands.citations(*req.actor, req.data)
    except BulkWorkError as e:
        return json_response(req, int(e.http_status), {"error": str(e)})
    return json_response(req, 200, result)


def opened(req: Request) -> Response:
    """``POST /api/works/:id/opened``: an open now, with server time as its event time.

    ``GET /api/works/:id`` is a pure read. PRKS's own UI takes the durable
    ``MARK_WORK_OPENED`` path instead, so that an open made from cache while
    the server is unreachable is recorded identically; this endpoint remains
    for other canonical callers and shares the same max-register.
    """
    from backend.domain.works import work_commands

    try:
        marked = work_commands.mark_opened(*req.actor, req.params["work_id"])
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    if not marked:
        return error_response(404, "Work not found")
    return json_response(req, 200, {"status": "opened"})


def linearize_existing(req: Request) -> Response:
    # Managed PDFs on PostgreSQL are immutable stored objects; rewriting
    # them in place is not ported.
    return not_ported(req, "Linearizing existing PDFs is not ported to PostgreSQL yet.")


_TAG = "works"
_MISSING = error("No Work with that id in this Library.")
_MISSING_TEXT = plain("No Work with that id in this Library.")
_NOT_PORTED = error("Not ported to PostgreSQL yet (not_ported).", NotPortedEnvelope)


def _revalidated(op_id: str, summary: str, schema: Any, description: str,
                 more: Optional[dict[int, Reply]] = None, params: tuple[Param, ...] = ()) -> Operation:
    return revalidated_read(op_id, summary, _TAG, schema, description, more, params)


_PROJECTION = Param("projection", schema={"type": "string", "enum": ["browse"]},
                    description="``browse``: the compact card rows, by title. Left out: every summary, newest first.")

ROUTES = (
    route("GET", COLLECTION, catalog, contract=_revalidated(
        "listWorks", "Every Work, as summaries or browse rows", WorkCatalog,
        "Summaries newest first, or with ``projection=browse`` browse rows by title.",
        params=(_PROJECTION,))),
    route("POST", COLLECTION, create, contract=Operation(
        "createWork", "Create a Work from an upload, a managed PDF, a video URL or no file", _TAG,
        {200: ok(WorkCreated, "Created, filed and credited."),
         400: error("An invalid YouTube URL, file_b64, upload or field value."),
         404: error("The named Folder does not exist (FOLDER_NOT_FOUND)."),
         409: error("A credited Person is missing or not saved yet (PERSON_NOT_FOUND), or the file "
                    "cannot be filed.")},
        request=body(WorkCreateRequest, "Left out: an Untitled Work with no file.", required=False))),
    route("POST", COLLECTION + "/bulk", bulk, contract=Operation(
        "bulkUpdateWorks", "One action on many Works, all or nothing", _TAG,
        {200: ok(BulkWorkResult, "Applied."),
         400: error("Unknown action, a bad status or Folder, or an empty, malformed or too large selection."),
         404: error("A selected Work, the Folder or a Tag does not exist; nothing changed.")},
        request=body(BulkWorkRequest))),
    route("POST", COLLECTION + "/linearize-existing-pdfs", linearize_existing, contract=Operation(
        "linearizeExistingPdfs", "Rewrite the managed PDFs for fast web view (not ported)", _TAG,
        {501: _NOT_PORTED}, request=body({"type": "object"}, "A JSON object, ignored; may be left out.", required=False))),
    route("GET", COLLECTION + "/{work_id}", detail, contract=Operation(
        "getWork", "A Work with its notes, credits, Tags, annotations and Research refs", _TAG,
        {200: ok(WorkDetail, "The Work."), 404: _MISSING_TEXT})),
    route("PATCH", COLLECTION + "/{work_id}", update, contract=Operation(
        "updateWork", "Change a Work's metadata, notes or Folder", _TAG,
        {200: ok(WorkStatus, "Updated."),
         400: error("A refused value, length, Folder or note. The Folder move, the note and the metadata "
                    "commit in that order, so a later refusal leaves the earlier steps applied: read the "
                    "Work again. A changed file_path is refused (501) before any step."),
         404: error("No Work with that id (not_found)."),
         409: error("The Research Note was refused."),
         501: _NOT_PORTED},
        request=body(WorkPatch, "Left out: nothing changes.", required=False))),
    route("DELETE", COLLECTION + "/{work_id}", delete, contract=Operation(
        "deleteWork", "Delete a Work", _TAG,
        {200: ok(WorkStatus, "Deleted, or there was no such Work.")})),
    route("GET", COLLECTION + "/{work_id}/metadata-state", metadata_state, contract=_revalidated(
        "getWorkMetadataState", "A Work's synchronized field revisions", WorkMetadataState,
        "Each synchronized field's revision, with the value of the small ones.", {404: _MISSING})),
    route("GET", COLLECTION + "/{work_id}/source-state", source_state, contract=_revalidated(
        "getWorkSourceState", "A Work's stored source and its revision", WorkSourceState,
        "The stored source and its revision, from one read.",
        {404: _MISSING,
         409: error("The video source cannot be read: its link is not a supported video URL "
                    "(INVALID_SOURCE_STATE).")})),
    route("GET", COLLECTION + "/{work_id}/notes-state", notes_state, contract=_revalidated(
        "getWorkNotesState", "A Work's note revisions", WorkNotesState, "The two revisions.",
        {404: _MISSING})),
    route("POST", COLLECTION + "/{work_id}/opened", opened, contract=Operation(
        "markWorkOpened", "Record an open of a Work now", _TAG,
        {200: ok(WorkStatus, "Recorded."), 400: error("The id cannot name a Work."), 404: _MISSING_TEXT},
        request=body({"type": "object"}, "A JSON object, ignored; may be left out.", required=False))),
    route("GET", "/api/recent", recent, contract=_revalidated(
        "listRecentWorks", "The most recently opened Works", list[RecentWork], "Newest open first.")),
    route("GET", "/api/recently-added", recently_added, contract=_revalidated(
        "listRecentlyAddedWorks", "The most recently created Works", list[RecentlyAddedWork],
        "Newest first.")),
    route("GET", "/api/bibtex/{work_id}", bibtex, contract=Operation(
        "getWorkBibtex", "A Work's BibTeX entry", "citations",
        {200: ok({"type": "string"}, "The entry; empty for a Work not in this Library.",
                 media_type="text/plain")})),
    route("POST", "/api/citations", citations, contract=Operation(
        "exportCitations", "Read the selected Works' citations as CSL-JSON or BibTeX", "citations",
        {200: ok(CitationsResult, "The found Works' citations, in request order."),
         400: error("Domain refusal: not an object, unknown format, or an empty, malformed or too "
                    "large selection.")},
        request=body(CitationsRequest),
        description="Changes nothing. Each Work is cited from its citation Manifestation. Ids this "
                    "Library has no Work for are listed in `missing` and left out.")),
)
