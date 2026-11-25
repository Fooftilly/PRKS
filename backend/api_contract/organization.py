"""Tags, Folders and Playlists wire shapes (#1431).

Shape and JSON types only. The rows are the queries of
``backend/domain/organization`` (``tags``, ``links``, ``folders``,
``playlists``); names, colours, titles and the Folder tree rules stay in
that package, which refuses them with its own message.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.boundary import omittable
from backend.api_contract.works import WorkSummary, WorkTag

_FORBID = ConfigDict(extra="forbid")
# A request names the fields it sends; the routes ignore keys they do not know.
_IGNORE = ConfigDict(extra="ignore")


class Revision(BaseModel):
    model_config = _FORBID

    revision: int


class Done(BaseModel):
    """``{"status": ...}``: what the request did."""

    model_config = _FORBID

    status: Literal["added", "removed", "deleted", "updated", "reordered"]


class Created(BaseModel):
    model_config = _FORBID

    id: str


# -- Tags ----------------------------------------------------------------------------

class Tag(WorkTag):
    """``GET /api/tags``: a Tag with its aliases, by name."""

    aliases: list[str]


class TagInUse(Tag):
    """``GET /api/tags?used=1``: a Tag linked to a Work or a Folder, with the counts."""

    work_count: int
    folder_count: int


TagCatalog = Union[list[TagInUse], list[Tag]]


class TagCreateRequest(BaseModel):
    model_config = _IGNORE

    name: str
    color: Optional[str] = omittable("A ``#rrggbb`` colour; left out: the default colour.")


class TagCreated(BaseModel):
    """The Tag holding the name: a new one, or an existing one (``existed``) that took the colour."""

    model_config = _FORBID

    id: str
    name: str
    color: Optional[str]
    existed: bool


class TagMergeRequest(BaseModel):
    model_config = _IGNORE

    source_tag_id: str
    target_tag_id: str


class TagAffected(BaseModel):
    """The Works and Folders whose cached Tags or tag options a delete or merge stales."""

    model_config = _FORBID

    affected_work_ids: list[str]
    affected_folder_ids: list[str]
    affected_tag_options_work_ids: list[str]
    affected_tag_options_folder_ids: list[str]


class TagMerged(TagAffected):
    status: Literal["merged"]
    canonical_tag_id: str
    canonical_name: str


class TagDeleted(TagAffected):
    status: Literal["deleted"]


class TagAliasRequest(BaseModel):
    model_config = _IGNORE

    alias: str


class TagLinkRequest(BaseModel):
    model_config = _IGNORE

    tag_id: str


class AssignedTag(BaseModel):
    model_config = _FORBID

    tag_id: str
    relation_revision: int


class _TagOptions(BaseModel):
    model_config = _FORBID

    assigned: list[AssignedTag] = Field(description="The owner's Tags with their link revisions.")
    known_absent: dict[str, int] = Field(
        description="Tags not linked to the owner whose link has a revision: tag id -> revision.")


class WorkTagOptions(_TagOptions):
    work_id: str


class FolderTagOptions(_TagOptions):
    folder_id: str


# -- Folders -------------------------------------------------------------------------

class Folder(BaseModel):
    """A Folder row."""

    model_config = _FORBID

    id: str
    title: str
    description: Optional[str]
    private_notes: Optional[str]
    parent_id: Optional[str]
    created_at: str
    updated_at: str


class FolderCard(Folder):
    """``GET /api/folders``: a Folder with its counts (an empty root Uncategorized is left out)."""

    work_count: int
    child_count: int


class FolderChild(BaseModel):
    model_config = _FORBID

    id: str
    title: str
    parent_id: Optional[str]
    description: Optional[str]
    work_count: int
    child_count: int


class FolderParent(BaseModel):
    model_config = _FORBID

    id: str
    title: str


class FolderDetail(Folder):
    """``GET /api/folders/{id}``: the Folder with its children, parent, Works and Tags."""

    children: list[FolderChild]
    parent: Optional[FolderParent]
    works: list[WorkSummary]
    tags: list[WorkTag]


class FolderCreateRequest(BaseModel):
    model_config = _IGNORE

    title: Optional[str] = omittable("Left out: ``Untitled Folder``.")
    description: Optional[str] = omittable("Left out: empty.")
    parent_id: Optional[str] = omittable("Left out or null: a root Folder.")


class FolderPatch(BaseModel):
    """The fields to change; a field left out stays."""

    model_config = _IGNORE

    title: Optional[str] = omittable()
    description: Optional[str] = omittable()
    private_notes: Optional[str] = omittable()
    parent_id: Optional[str] = omittable("Null: a root Folder.")


class FolderUpdated(BaseModel):
    model_config = _FORBID

    status: Literal["updated"]
    member_work_ids: list[str] = Field(
        description="The member Works whose cached detail a rename stales; empty without ``title``.")


class FileWorkRequest(BaseModel):
    model_config = _IGNORE

    work_id: str


class FolderFieldRevisions(BaseModel):
    model_config = _FORBID

    description: Revision
    parent_id: Revision
    private_notes: Revision
    title: Revision


class FolderState(BaseModel):
    """``GET /api/folders/{id}/sync-state``: field revisions only."""

    model_config = _FORBID

    folder_id: str
    fields: FolderFieldRevisions


class WorkFolderState(BaseModel):
    """``GET /api/works/{id}/folder-state``."""

    model_config = _FORBID

    work_id: str
    folder_id: str = Field(description="The Work's Folder; empty when it is in none.")
    revision: int


# -- Playlists -----------------------------------------------------------------------

class Playlist(BaseModel):
    model_config = _FORBID

    id: str
    title: str
    description: Optional[str]
    original_url: Optional[str] = Field(description="Null when empty.")
    created_at: str
    updated_at: str


class PlaylistCard(Playlist):
    """``GET /api/playlists``: a Playlist with its item count, last changed first."""

    item_count: int


class PlaylistItem(WorkSummary):
    position: int


class PlaylistDetail(Playlist):
    """``GET /api/playlists/{id}``: the Playlist with its Works in order."""

    items: list[PlaylistItem]


class PlaylistCreateRequest(BaseModel):
    model_config = _IGNORE

    title: Optional[str] = omittable("Left out or null: empty.")
    description: Optional[str] = omittable("Left out or null: empty.")
    original_url: Optional[str] = omittable("Left out or null: empty.")


class PlaylistPatch(BaseModel):
    """The fields to change; null is empty, a field left out stays."""

    model_config = _IGNORE

    title: Optional[str] = omittable()
    description: Optional[str] = omittable()
    original_url: Optional[str] = omittable()


class PlaylistItemRequest(BaseModel):
    model_config = _IGNORE

    work_id: str
    position: Optional[Union[float, str]] = omittable(
        "Where the Work goes; left out, null, blank or not a number: the end.")


class PlaylistReorderRequest(BaseModel):
    model_config = _IGNORE

    work_ids: list[str] = omittable("The member Works in their new order.")


class PlaylistFieldRevisions(BaseModel):
    model_config = _FORBID

    description: Revision
    original_url: Revision
    title: Revision


class PlaylistState(BaseModel):
    """``GET /api/playlists/{id}/sync-state``: field revisions and the order revision only."""

    model_config = _FORBID

    playlist_id: str
    fields: PlaylistFieldRevisions
    order_revision: int


class WorkPlaylistState(BaseModel):
    """``GET /api/works/{id}/playlist-state``."""

    model_config = _FORBID

    work_id: str
    playlist_id: str = Field(description="The Work's Playlist; empty when it is in none.")
    revision: int
