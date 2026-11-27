"""Work roles, Persons and Person groups wire shapes (#1431).

Shape and JSON types only. The rows are the queries of
``backend/domain/people``; names, role types, credit lengths and the merge
and group rules stay in that package, which refuses them with its own message.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.boundary import omittable
from backend.api_contract.organization import Revision
from backend.api_contract.works import WorkSummary

_FORBID = ConfigDict(extra="forbid")
# A request names the fields it sends; the routes ignore keys they do not know.
_IGNORE = ConfigDict(extra="ignore")


# -- Work roles ----------------------------------------------------------------------

class RoleAddRequest(BaseModel):
    model_config = _IGNORE

    person_id: str
    work_id: str
    role_type: str
    credit_name: Optional[str] = omittable("The name as credited on this Work; left out or null: none.")


class RoleCreditRequest(BaseModel):
    model_config = _IGNORE

    person_id: str
    role_type: str
    credit_name: Optional[str] = omittable("The new credit; left out or null: none.")
    order_index: Optional[Union[int, str]] = omittable("Checked to be an integer, else ignored.")


class RoleAdded(BaseModel):
    model_config = _FORBID

    status: Literal["success"]


class RoleDone(BaseModel):
    model_config = _FORBID

    status: Literal["updated", "removed"]


class RoleScope(BaseModel):
    """One Person-role relationship of the Work: its revision, and whether it is live."""

    model_config = _FORBID

    person_id: str
    role_type: str
    revision: int
    present: bool


class WorkPeopleState(BaseModel):
    """``GET /api/works/{id}/people-state``: role revisions, tombstones included."""

    model_config = _FORBID

    work_id: str
    scopes: list[RoleScope]


# -- Persons -------------------------------------------------------------------------

class PersonProfile(BaseModel):
    """A Person's profile row."""

    model_config = _FORBID

    id: str
    first_name: Optional[str]
    last_name: Optional[str]
    aliases: str = Field(description="The aliases, comma-separated; empty when none.")
    about: Optional[str]
    image_url: Optional[str]
    link_wikipedia: Optional[str]
    link_stanford_encyclopedia: Optional[str]
    link_iep: Optional[str]
    links_other: Optional[str]
    birth_date: Optional[str]
    death_date: Optional[str]
    created_at: str
    updated_at: str


class GroupChip(BaseModel):
    model_config = _FORBID

    id: str
    name: str


class PersonCard(PersonProfile):
    """``GET /api/persons``: a profile with its role types and groups, by last name."""

    assigned_roles: list[str] = Field(description="The Person's distinct role types, sorted.")
    groups: list[GroupChip]


class PersonGroupRef(GroupChip):
    parent_id: Optional[str]


class PersonWork(WorkSummary):
    """One credit of the Person: the Work row, its people columns and the role."""

    text_content: Optional[str]
    private_notes: Optional[str]
    hide_pdf_link_annotations: Literal[0, 1]
    citation_style: str
    canonical_annotation_set_revision: int
    materialized_pdf_annotation_revision: int
    role_type: str
    order_index: int
    credit_name: Optional[str]


class PersonDetail(PersonProfile):
    """``GET /api/persons/{id}``: the profile with one Work row per credit and its groups."""

    works: list[PersonWork]
    groups: list[PersonGroupRef]


class PersonCreateRequest(BaseModel):
    """The profile; a field left out or null is empty."""

    model_config = _IGNORE

    first_name: Optional[str] = omittable()
    last_name: Optional[str] = omittable()
    aliases: Optional[str] = omittable("Comma-separated.")
    about: Optional[str] = omittable()
    image_url: Optional[str] = omittable("A public http(s) URL (else 400).")
    link_wikipedia: Optional[str] = omittable()
    link_stanford_encyclopedia: Optional[str] = omittable()
    link_iep: Optional[str] = omittable()
    links_other: Optional[str] = omittable()
    birth_date: Optional[str] = omittable()
    death_date: Optional[str] = omittable()


class PersonPatch(PersonCreateRequest):
    """The profile fields to change, and with ``group_ids`` the exact group set, together."""

    group_ids: Optional[list[str]] = omittable("The Person's whole group set; left out or null: unchanged.")


class PersonFieldRevisions(BaseModel):
    model_config = _FORBID

    first_name: Revision
    last_name: Revision
    aliases: Revision
    about: Revision
    image_url: Revision
    link_wikipedia: Revision
    link_stanford_encyclopedia: Revision
    link_iep: Revision
    links_other: Revision
    birth_date: Revision
    death_date: Revision


class PersonMetadataState(BaseModel):
    """``GET /api/persons/{id}/metadata-state``: field revisions only."""

    model_config = _FORBID

    person_id: str
    fields: PersonFieldRevisions


# -- Person groups -------------------------------------------------------------------

class PersonGroup(BaseModel):
    model_config = _FORBID

    id: str
    name: str
    parent_id: Optional[str]
    description: Optional[str]
    created_at: str
    updated_at: str


class PersonGroupCard(PersonGroup):
    """``GET /api/person-groups``: a group with its counts."""

    member_count: int
    child_count: int


class PersonGroupChild(BaseModel):
    model_config = _FORBID

    id: str
    name: str
    parent_id: Optional[str]
    description: Optional[str]
    member_count: int


class PersonGroupDetail(PersonGroup):
    """``GET /api/person-groups/{id}``: the group, its children, members and parent."""

    member_count: int
    children: list[PersonGroupChild]
    members: list[PersonCard]
    parent: Optional[GroupChip]


class PersonGroupCreateRequest(BaseModel):
    model_config = _IGNORE

    name: str
    parent_id: Optional[str] = omittable("The parent group; empty or null: none (then ``parent_name``).")
    parent_name: Optional[str] = omittable("A parent by name, found or created, when no ``parent_id``.")
    description: Optional[str] = omittable()


class PersonGroupPatch(BaseModel):
    """The fields to change; a ``parent_name`` is found or created."""

    model_config = _IGNORE

    name: Optional[str] = omittable()
    description: Optional[str] = omittable()
    parent_id: Optional[str] = omittable("Null or empty: a top-level group.")
    parent_name: Optional[str] = omittable("A parent by name, found or created; empty: top level.")


class GroupMemberRequest(BaseModel):
    model_config = _IGNORE

    person_id: str


class PersonGroupFieldRevisions(BaseModel):
    model_config = _FORBID

    description: Revision
    name: Revision
    parent_id: Revision


class GroupMemberScope(BaseModel):
    model_config = _FORBID

    person_id: str
    revision: int
    present: bool


class PersonGroupState(BaseModel):
    """``GET /api/person-groups/{id}/sync-state``: field and membership revisions, tombstones included."""

    model_config = _FORBID

    group_id: str
    fields: PersonGroupFieldRevisions
    members: list[GroupMemberScope]


class PersonMembershipScope(BaseModel):
    model_config = _FORBID

    group_id: str
    revision: int
    present: bool


class PersonMembershipState(BaseModel):
    """``GET /api/persons/{id}/group-state``: membership revisions, tombstones included."""

    model_config = _FORBID

    person_id: str
    groups: list[PersonMembershipScope]
