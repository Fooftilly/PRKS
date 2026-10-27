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
state (L-R-1 PR 2), through :mod:`backend.domain.research.concepts`;
Positions, Arguments and Stances with their targets, citations, verdicts
and revision state (PR 3), through :mod:`backend.domain.research.positions`
and :mod:`backend.domain.research.arguments`. Refusals are SQLite's
``ResearchError`` codes, messages and statuses. Differences from SQLite,
each deliberate:

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
- An id holding NUL is no Concept, Position or Argument (``not_found``;
  PostgreSQL text cannot hold it).
- A citation pins a Version (``docs/work-identity-model.md`` §8.5): a
  replaced entry keeps the Version of the one stored row it matches by Work
  and pages, a new one with pages is pinned to the Work's only Version, and
  an entry the legacy shape cannot pin (it matches several stored rows, or
  its Work has several Versions) refuses the list with
  ``argument_source_version_required`` (409). SQLite pins the primary.
- ``create_argument`` and ``replace_argument_sources`` also take a
  Version-aware list, ``[{work_id, manifestation_id, pages}]``
  (``manifestation_id`` null for the Work in general), which may cite one
  Work several times and refuses ``argument_source_version_not_found``
  (404) and ``argument_source_version_mismatch`` (409); every citation row
  read back carries ``manifestation_id`` (:mod:`backend.domain.research.arguments`).
- A Work delete removes its citations and advances each citing Argument's
  ``argument-sources`` (SQLite's cascade advances nothing).
- A delete of a Position or an Argument leaves every scope it owned one
  revision on, never-edited ones too (SQLite leaves them).
- Ties SQLite leaves to storage (equal ``order_index`` in a list, Authors
  of equal order and last name) are broken by id.
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


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
    """One Research command, run once more when a concurrent writer of a Concept key won on its index.

    The index aborted the first transaction; the rerun reads the winner and
    refuses with the rule's own ``ResearchError``. A command that adds a
    citation runs SERIALIZABLE (§7.1: ``argument_sources`` is a peer table
    of the Work merge).
    """
    from backend.db.database import Isolation
    from backend.db.errors import ConstraintViolationError
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command
    from backend.domain.research import concepts

    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED

    def run() -> T:
        return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                               isolation=isolation)

    try:
        return run()
    except ConstraintViolationError as error:
        if error.constraint != concepts.KEY_INDEX:
            raise
        return run()


class ResearchMixin(FacadeMixin):
    """Concepts (L-R-1 PR 2); Positions, Arguments and Stances (PR 3)."""

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

    @ported
    def list_positions(self) -> List[dict]:
        from backend.domain.research import positions

        return _read(self, positions.catalog)

    @ported
    def get_position(self, position_id: str) -> Optional[dict]:
        from backend.domain.research import positions

        pid = (position_id or "").strip()
        if not pid:
            return None
        return _read(self, lambda scope: positions.detail(scope, pid))

    @ported
    def create_position(self, name, description: str = "") -> dict:
        from backend.domain.research import positions
        from backend.entity_ids import generate

        new_id = generate("P")
        return _command(self, lambda scope: positions.create(scope, new_id, name, description))

    @ported
    def update_position(self, position_id: str, *, name=None, description=None) -> dict:
        from backend.domain.research import positions

        return _command(self, lambda scope: positions.update(scope, position_id, name, description))

    @ported
    def delete_position(self, position_id: str) -> None:
        from backend.domain.research import positions

        _command(self, lambda scope: positions.remove(scope, position_id))

    @ported
    def get_position_sync_state(self, position_id: str) -> Optional[dict]:
        from backend.domain.research import positions

        return _read(self, lambda scope: positions.sync_state(scope, position_id))

    @ported
    def list_verdicts(self) -> List[dict]:
        from backend.domain.research import arguments

        return _read(self, arguments.verdicts)

    @ported
    def list_arguments(self, kind: Optional[str] = None) -> List[dict]:
        from backend.domain.research import arguments

        return _read(self, lambda scope: arguments.catalog(scope, kind))

    @ported
    def get_argument(self, argument_id: str) -> Optional[dict]:
        from backend.domain.research import arguments

        aid = (argument_id or "").strip()
        if not aid:
            return None
        return _read(self, lambda scope: arguments.detail(scope, aid))

    @ported
    def create_argument(self, *, name, kind, main_text: str = "", sources=None, targets=None) -> dict:
        from backend.domain.research import arguments
        from backend.entity_ids import generate

        new_id = generate("A")
        return _command(self, lambda scope: arguments.create(scope, new_id, name, kind, main_text, sources,
                                                             targets),
                        serializable=True)

    @ported
    def update_argument(self, argument_id: str, *, name=None, kind=None, main_text=None) -> dict:
        from backend.domain.research import arguments

        return _command(self, lambda scope: arguments.update(scope, argument_id, name, kind, main_text))

    @ported
    def replace_argument_sources(self, argument_id: str, sources) -> dict:
        from backend.domain.research import arguments

        return _command(self, lambda scope: arguments.replace_sources(scope, argument_id, sources),
                        serializable=True)

    @ported
    def replace_argument_targets(self, argument_id: str, targets) -> dict:
        from backend.domain.research import arguments

        return _command(self, lambda scope: arguments.replace_targets(scope, argument_id, targets))

    @ported
    def delete_argument(self, argument_id: str) -> None:
        from backend.domain.research import arguments

        _command(self, lambda scope: arguments.remove(scope, argument_id))

    @ported
    def get_argument_sync_state(self, argument_id: str) -> Optional[dict]:
        from backend.domain.research import arguments

        return _read(self, lambda scope: arguments.sync_state(scope, argument_id))
