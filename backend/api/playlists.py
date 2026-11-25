"""Playlists HTTP controller (PG-C3 #642).

``/api/playlists`` (GET the catalog with an ETag, POST create),
``/api/playlists/:id`` (GET with its Works in order, PATCH fields,
DELETE), ``POST /api/playlists/:id/items`` (put a Work in it, at an
optional ``position``), ``DELETE /api/playlists/:id/items/:work_id``,
``POST /api/playlists/:id/reorder``, and the revision states
``GET /api/playlists/:id/sync-state`` and ``GET /api/works/:id/playlist-state``.
A domain refusal (``ValueError``) on add or reorder is ``400`` with its
message. :func:`add_item` also serves the Work upload's optional
``playlist_id``.

The calls go to ``backend/domain/organization/playlist_commands`` for the
admitted request's context on the served Library's database. The commands
open the transactions, so authorization stays in ``library_query`` and
``library_command``, and a write's maintenance and epoch fence in
``library_command``.
The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from backend.api.common import optional_pdfs_dir, revalidated
from backend.api_contract.operation import Operation, body, error, ok, plain
from backend.api_contract.operation import revalidated as revalidated_read
from backend.api_contract.organization import (
    Created,
    Done,
    PlaylistCard,
    PlaylistCreateRequest,
    PlaylistDetail,
    PlaylistItemRequest,
    PlaylistPatch,
    PlaylistReorderRequest,
    PlaylistState,
    WorkPlaylistState,
)
from backend.domain.organization.playlist_rules import PlaylistRuleError
from backend.web.http import Request, Response, error_response, json_response
from backend.web.routing import route


def add_item(req: Request, playlist_id: Optional[str], work_id: Optional[str],
             position: Optional[int] = None) -> None:
    """Put a Work in a Playlist for this request; refusals raise ``ValueError``."""
    from backend.domain.organization import playlist_commands

    playlist_commands.add_item(*req.actor, playlist_id, work_id, position)


def catalog(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    return revalidated(req, "playlists", playlist_commands.catalog(*req.actor))


def detail(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    data = playlist_commands.detail(*req.actor, req.params["playlist_id"], optional_pdfs_dir(req))
    if data:
        return json_response(req, 200, data)
    return error_response(404, "Playlist not found")


def sync_state(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    # REVISIONS ONLY: the three field revisions and the ORDER revision. The
    # playlist detail already carries its items in order, so echoing them
    # here would make a second cached copy.
    data = playlist_commands.sync_state(*req.actor, unquote(req.params["playlist_id"]))
    return revalidated(req, "playlist-state", data, missing="Playlist not found")


def work_state(req: Request) -> Response:
    """``GET /api/works/:id/playlist-state``."""
    from backend.domain.organization import playlist_commands

    data = playlist_commands.work_state(*req.actor, unquote(req.params["work_id"]))
    return revalidated(req, "work-playlist-state", data)


def update(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    try:
        playlist_commands.update(*req.actor, req.params["playlist_id"], req.data)
    except PlaylistRuleError as e:  # a refused value
        return json_response(req, 400, {"error": str(e)})
    except ValueError as e:  # no such Playlist
        return json_response(req, 404, {"error": str(e)})
    return json_response(req, 200, {"status": "updated"})


def _position(data: dict[str, Any]) -> Optional[int]:
    try:
        raw = data.get("position", None)
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except Exception:
        return None


def create(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    data = req.data
    try:
        pl_id = playlist_commands.create(
            *req.actor,
            title=data.get("title", "") or "",
            description=data.get("description", "") or "",
            original_url=data.get("original_url", "") or "",
        )
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"id": pl_id})


def post_item(req: Request) -> Response:
    data = req.data
    raw = data.get("work_id")
    w_id = raw.strip() if isinstance(raw, str) else ""
    if not w_id:
        return json_response(req, 400, {"error": "work_id is required"})
    try:
        add_item(req, req.params["playlist_id"], w_id, _position(data))
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "added"})


def reorder(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    work_ids = req.data.get("work_ids", [])
    if not isinstance(work_ids, list):
        return json_response(req, 400, {"error": "work_ids must be a JSON array"})
    try:
        playlist_commands.reorder(*req.actor, req.params["playlist_id"], [str(x) for x in work_ids])
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, {"status": "reordered"})


def delete(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    playlist_commands.delete(*req.actor, req.params["playlist_id"])
    return json_response(req, 200, {"status": "deleted"})


def remove_item(req: Request) -> Response:
    from backend.domain.organization import playlist_commands

    playlist_commands.remove_item(*req.actor, req.params["playlist_id"], req.params["work_id"])
    return json_response(req, 200, {"status": "removed"})


_TAG = "playlists"
_LIMITS = "title 300 bytes, the others 4000"
_REFUSED_NEW = error(f"A value that is not a string, or past its limit ({_LIMITS}).")
_TOO_LONG = error(f"A value past its limit ({_LIMITS}).")


ROUTES = (
    route("GET", "/api/playlists", catalog, contract=revalidated_read(
        "listPlaylists", "Every Playlist with its item count", _TAG, list[PlaylistCard],
        "Every Playlist, last changed first.")),
    route("POST", "/api/playlists", create, contract=Operation(
        "createPlaylist", "Create a Playlist", _TAG, {200: ok(Created, "Created."), 400: _REFUSED_NEW},
        request=body(PlaylistCreateRequest, "Left out: an empty Playlist.", required=False))),
    route("GET", "/api/playlists/{playlist_id}", detail, contract=Operation(
        "getPlaylist", "A Playlist with its Works in order", _TAG,
        {200: ok(PlaylistDetail, "The Playlist."), 404: plain("No Playlist with that id.")})),
    route("PATCH", "/api/playlists/{playlist_id}", update, contract=Operation(
        "updatePlaylist", "Change a Playlist's fields", _TAG,
        {200: ok(Done, "Updated."), 400: _TOO_LONG, 404: error("No Playlist with that id (a change was sent).")},
        request=body(PlaylistPatch, "Left out: nothing changes.", required=False))),
    route("DELETE", "/api/playlists/{playlist_id}", delete, contract=Operation(
        "deletePlaylist", "Delete a Playlist and release its Works", _TAG, {200: ok(Done, "Deleted.")})),
    route("GET", "/api/playlists/{playlist_id}/sync-state", sync_state, contract=revalidated_read(
        "getPlaylistSyncState", "A Playlist's field and order revisions", _TAG, PlaylistState,
        "The revisions only.", {404: error("No Playlist with that id.")})),
    route("POST", "/api/playlists/{playlist_id}/items", post_item, contract=Operation(
        "addPlaylistItem", "Put a Work in a Playlist, optionally at a position", _TAG,
        {200: ok(Done, "Added."), 400: error("No ``work_id``, or no such Playlist or Work.")},
        request=body(PlaylistItemRequest))),
    route("DELETE", "/api/playlists/{playlist_id}/items/{work_id}", remove_item, contract=Operation(
        "removePlaylistItem", "Take a Work out of a Playlist", _TAG,
        {200: ok(Done, "Removed (or was not in it).")})),
    route("POST", "/api/playlists/{playlist_id}/reorder", reorder, contract=Operation(
        "reorderPlaylist", "Rewrite a Playlist's order", _TAG,
        {200: ok(Done, "Reordered."),
         400: error("``work_ids`` is not an array, or no such Playlist.")},
        request=body(PlaylistReorderRequest, "Left out: an empty order.", required=False),
        description=("Unknown and repeated ids are ignored; the members left out follow the named ones "
                     "in their current order."))),
    route("GET", "/api/works/{work_id}/playlist-state", work_state, contract=revalidated_read(
        "getWorkPlaylistState", "Which Playlist a Work is in, and its revision", _TAG, WorkPlaylistState,
        "The Work's Playlist (empty for none) and the revision.", {404: error("No Work with that id.")})),
)
