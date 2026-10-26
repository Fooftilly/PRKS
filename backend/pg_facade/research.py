"""Research family of the PostgreSQL transition facade (L-R-1).

Owned by the Research lane (``docs/postgresql-architecture.md`` §15.2):
Concepts, Positions, Arguments and derived mentions. Each ported method
keeps the ``PRKSDatabase`` signature and return shape and is registered by
one line, ``@ported`` from :mod:`backend.pg_facade.registry`, directly
above it.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one READ COMMITTED ``library_command`` transaction
(§7.1: a Concept's uniqueness is decided by its key index). The domain
modules (and so psycopg) are imported inside the methods.

Ported here: Concepts, their aliases and hierarchy, and their revision
state (L-R-1 PR 2), through :mod:`backend.domain.research.concepts`.
Refusals are SQLite's ``ResearchError`` codes, messages and statuses.
Differences from SQLite, each deliberate:

- Names and aliases compare by ``prks.identity_key`` (§8.1), which is
  ``normalize_concept_key``; resolution reads the ``concept_keys`` index
  instead of scanning every Concept, and since no two Concepts can share a
  key, ``ambiguous_concept`` is never answered.
- A delete leaves every scope the Concept owned one revision on (its
  definition, identity and parents, never-edited ones too) and advances
  ``concept-parents`` of each Concept on the other end of an edge (SQLite
  advances the identity and those neighbours), so a base observed before
  the delete is stale on a Concept created again under the id.
- Aliases with equal ``LOWER()`` are ordered by spelling, and the parents
  and children of a command's answer by name and then id (SQLite gives
  storage order).
- An id holding NUL is no Concept (``not_found``; PostgreSQL text cannot
  hold it).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")


def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    """One Concept command, run once more when a concurrent writer of a key won on its index.

    The index aborted the first transaction; the rerun reads the winner and
    refuses with the rule's own ``ResearchError``.
    """
    from backend.db.errors import ConstraintViolationError
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command
    from backend.domain.research import concepts

    def run() -> T:
        return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn)

    try:
        return run()
    except ConstraintViolationError as error:
        if error.constraint != concepts.KEY_INDEX:
            raise
        return run()


class ResearchMixin(FacadeMixin):
    """Concepts (L-R-1 PR 2)."""

    @ported
    def list_concepts(self) -> List[dict]:
        from backend.domain.research import concepts

        return _read(self, concepts.catalog)

    @ported
    def get_concept(self, concept_id: str) -> Optional[dict]:
        from backend.domain.research import concepts

        cid = (concept_id or "").strip()
        if not cid:
            return None
        return _read(self, lambda scope: concepts.detail(scope, cid))

    @ported
    def create_concept(self, name, description: str = "") -> dict:
        from backend.domain.research import concepts
        from backend.entity_ids import generate

        new_id = generate("C")
        return _command(self, lambda scope: concepts.create(scope, new_id, name, description))

    @ported
    def update_concept(self, concept_id: str, *, name=None, description=None) -> dict:
        from backend.domain.research import concepts

        return _command(self, lambda scope: concepts.update(scope, concept_id, name, description))

    @ported
    def replace_concept_aliases(self, concept_id: str, aliases) -> dict:
        from backend.domain.research import concepts

        return _command(self, lambda scope: concepts.replace_aliases(scope, concept_id, aliases))

    @ported
    def replace_concept_parents(self, concept_id: str, parent_ids) -> dict:
        from backend.domain.research import concepts

        return _command(self, lambda scope: concepts.replace_parents(scope, concept_id, parent_ids))

    @ported
    def delete_concept(self, concept_id: str) -> None:
        from backend.domain.research import concepts

        _command(self, lambda scope: concepts.remove(scope, concept_id))

    @ported
    def get_concept_sync_state(self, concept_id: str) -> Optional[dict]:
        from backend.domain.research import concepts

        return _read(self, lambda scope: concepts.sync_state(scope, concept_id))
