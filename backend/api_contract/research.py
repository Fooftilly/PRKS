"""Concepts, Arguments and Research graph wire shapes (#1431).

Shape and JSON types only. The rows are the queries of
``backend/domain/research`` (``concepts``, ``arguments``, ``graph``); names,
kinds, verdicts, pages, cycles and the "still referenced" rules stay in that
package, which refuses them with its own ``{error, code}``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.boundary import omittable
from backend.api_contract.organization import Revision

_FORBID = ConfigDict(extra="forbid")
# A request names the fields it sends; the routes ignore keys they do not know.
_IGNORE = ConfigDict(extra="ignore")


# -- Mentions ------------------------------------------------------------------------

class MentionOccurrence(BaseModel):
    model_config = _FORBID

    start: int
    end: int
    snippet: str = Field(description="Cut from the Research Note; empty while the Work's reindex is pending.")


class Mention(BaseModel):
    """A Work whose Research Note names the record, with each occurrence in note order."""

    model_config = _FORBID

    work_id: str
    title: str
    occurrences: list[MentionOccurrence]


# -- Concepts ------------------------------------------------------------------------

class ConceptChip(BaseModel):
    model_config = _FORBID

    id: str
    name: str


class Concept(BaseModel):
    """A Concept row."""

    model_config = _FORBID

    id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class ConceptCard(Concept):
    """``GET /api/concepts``: a Concept with its parents, aliases and counts, by name."""

    subconcept_count: int
    parents: list[ConceptChip]
    aliases: list[str]
    mention_count: int


class ConceptDetail(Concept):
    """A Concept with its aliases, parents and children (the answer of every write)."""

    aliases: list[str]
    parents: list[ConceptChip]
    children: list[ConceptChip]


class ConceptRead(ConceptDetail):
    """``GET /api/concepts/{id}``: the detail with the Works that mention it."""

    mention_count: int
    mentions: list[Mention]


class ConceptCreateRequest(BaseModel):
    model_config = _IGNORE

    name: str
    description: Optional[str] = omittable("Left out or null: empty.")


class ConceptPatch(BaseModel):
    """The fields to change; at least one (else ``nothing_to_update``)."""

    model_config = _IGNORE

    name: Optional[str] = omittable("A rename; the old name joins the aliases. Null: unchanged.")
    description: Optional[str] = omittable("Null: unchanged.")


class ConceptParentsRequest(BaseModel):
    model_config = _IGNORE

    parent_ids: list[str] = Field(description="The whole parent set.")


class ConceptAliasesRequest(BaseModel):
    model_config = _IGNORE

    aliases: list[str] = Field(description="The whole alias set.")


class ConceptFieldValue(BaseModel):
    model_config = _FORBID

    value: str
    revision: int


class ConceptFieldStates(BaseModel):
    model_config = _FORBID

    description: ConceptFieldValue


class ConceptIdentity(BaseModel):
    model_config = _FORBID

    name: str
    aliases: list[str]


class ConceptState(BaseModel):
    """``GET /api/concepts/{id}/sync-state``: field values and revisions, and the identity and parent sets."""

    model_config = _FORBID

    concept_id: str
    fields: ConceptFieldStates
    identity: ConceptIdentity
    identity_revision: int
    parent_ids: list[str]
    parents_revision: int


# -- Arguments -----------------------------------------------------------------------

ArgumentKind = Literal["argument", "stance"]


class Verdict(BaseModel):
    """An enabled verdict, in its order."""

    model_config = _FORBID

    id: str
    label: str
    sort_order: int
    enabled: Literal[1]


class SourceAuthor(BaseModel):
    model_config = _FORBID

    id: str
    first_name: Optional[str]
    last_name: Optional[str]
    role_type: Literal["Author"]
    credit_name: Optional[str]
    order_index: int


class ArgumentSource(BaseModel):
    """A citation: the Work (its primary Version's title), the pinned Version and the pages."""

    model_config = _FORBID

    work_id: str
    manifestation_id: Optional[str] = Field(description="The cited Version; null: the Work as a whole.")
    pages: str
    order_index: int
    work_title: Optional[str]
    authors: list[SourceAuthor]


class ArgumentTarget(BaseModel):
    """A Position or Argument the Argument responds to, with the verdict."""

    model_config = _FORBID

    type: Literal["position", "argument"]
    id: str
    name: str
    kind: ArgumentKind = omittable("An Argument target's kind; left out for a Position.")
    verdict_id: str
    verdict_label: str
    order_index: int


class ArgumentResponse(BaseModel):
    """An Argument that responds to this one."""

    model_config = _FORBID

    id: str
    name: str
    kind: ArgumentKind
    verdict_id: str
    verdict_label: str


class ArgumentCard(BaseModel):
    """``GET /api/arguments``: an Argument with its lists and counts, by name."""

    model_config = _FORBID

    id: str
    name: str
    kind: ArgumentKind
    created_at: str
    updated_at: str
    targets: list[ArgumentTarget]
    sources: list[ArgumentSource]
    response_count: int
    mention_count: int


class ArgumentDetail(BaseModel):
    """An Argument with its sources, targets and responses (the answer of every write)."""

    model_config = _FORBID

    id: str
    name: str
    kind: ArgumentKind
    main_text: str
    created_at: str
    updated_at: str
    sources: list[ArgumentSource]
    targets: list[ArgumentTarget]
    responses: list[ArgumentResponse]


class ArgumentRead(ArgumentDetail):
    """``GET /api/arguments/{id}``: the detail with its mentions and the verdicts."""

    mention_count: int
    mentions: list[Mention]
    verdicts: list[Verdict]


class SourceInput(BaseModel):
    """A citation; every entry names ``manifestation_id`` (a Version or null), or none does."""

    model_config = _IGNORE

    work_id: str
    manifestation_id: Optional[str] = omittable("The cited Version; null: the Work as a whole.")
    pages: Optional[str] = omittable("Left out or null: none.")


class TargetInput(BaseModel):
    model_config = _IGNORE

    type: Literal["position", "argument"]
    id: str
    verdict_id: str = Field(description="An enabled verdict.")


class ArgumentCreateRequest(BaseModel):
    model_config = _IGNORE

    name: str
    kind: ArgumentKind
    main_text: Optional[str] = omittable("Left out or null: empty.")
    sources: Optional[list[SourceInput]] = omittable("Left out or null: none.")
    targets: Optional[list[TargetInput]] = omittable("Left out or null: none.")


class ArgumentPatch(BaseModel):
    """The fields to change; at least one (else ``nothing_to_update``)."""

    model_config = _IGNORE

    name: Optional[str] = omittable("Null: unchanged.")
    kind: Optional[ArgumentKind] = omittable("Null: unchanged.")
    main_text: Optional[str] = omittable("Null: unchanged.")


class ArgumentSourcesRequest(BaseModel):
    model_config = _IGNORE

    sources: list[SourceInput] = Field(description="The whole citation list, in order.")


class ArgumentTargetsRequest(BaseModel):
    model_config = _IGNORE

    targets: list[TargetInput] = Field(description="The whole target list, in order.")


class ArgumentFieldRevisions(BaseModel):
    model_config = _FORBID

    kind: Revision
    main_text: Revision
    name: Revision


class ArgumentState(BaseModel):
    """``GET /api/arguments/{id}/sync-state``: revisions only."""

    model_config = _FORBID

    argument_id: str
    fields: ArgumentFieldRevisions
    sources: Revision
    targets: Revision


# -- Research graph ------------------------------------------------------------------

class GraphNode(BaseModel):
    model_config = _FORBID

    id: str = Field(description="``<type>:<record id>``.")
    record_id: str
    type: Literal["concept", "position", "argument", "work", "person"]
    label: str
    route: str = Field(description="The record's app route, ``#/<plural>/<id>``.")
    kind: ArgumentKind = omittable("An Argument's kind.")
    doc_type: str = omittable("A Work's document type, when it has one.")


class GraphEdge(BaseModel):
    model_config = _FORBID

    id: str = Field(description="``<type>:<source>><target>``.")
    type: Literal["concept_parent", "argument_position", "argument_argument", "argument_source",
                  "mentions_concept", "mentions_argument", "work_author"]
    source: str
    target: str
    verdict_id: str = omittable("An Argument target's verdict.")
    verdict_label: str = omittable("An Argument target's verdict.")
    pages: str = omittable("An Argument source's pages, when it has some.")
    count: int = omittable("A mention edge's occurrences.")


class GraphMeta(BaseModel):
    model_config = _FORBID

    node_count: int
    edge_count: int
    derived_note_edges_available: bool
    people_included: bool


class ResearchGraph(BaseModel):
    """``GET /api/research-graph``: nodes by type and label, edges by type and ends."""

    model_config = _FORBID

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    meta: GraphMeta


class GraphTooLarge(BaseModel):
    model_config = _FORBID

    error: str
    code: Literal["graph_too_large"]
    node_count: int
    edge_count: int
