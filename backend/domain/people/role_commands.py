"""The Work-Person role operations for one actor: what ``backend/api/work_roles.py`` calls (PG-C3 #642).

The roles a Work is born with, a role added to, re-credited on or removed
from an existing Work, and a Work's role revision state. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
Adding a role and inserting a new Work's roles run SERIALIZABLE; a credit
change and a removal run READ COMMITTED. The methods write Work-scoped
roles (``backend.domain.people.roles`` also takes a Manifestation).
Refusals are ``ValueError`` with SQLite's message (a missing Person at
create is ``MissingPersonError``), and ``AccessRefused``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, TypeVar

from backend import work_role_sync
from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.people import roles
from backend.domain.people.rules import eligible_initial_roles

T = TypeVar("T")


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


# -- Reads ---------------------------------------------------------------------
def missing_person_ids(db: Database, ctx: RequestContext, person_ids: Optional[Iterable[Any]]) -> list[str]:
    """The ids among ``person_ids`` that name no Person."""
    wanted = [str(p).strip() for p in (person_ids or []) if str(p or "").strip()]
    if not wanted:
        return []
    return library_query(db, ctx, lambda scope: roles.missing_persons(scope, wanted))


def missing_initial_person_ids(db: Database, ctx: RequestContext, initial_roles: Any) -> list[str]:
    """The missing Persons among the roles a new Work would be born with (skipped ones excluded)."""
    eligible = eligible_initial_roles(initial_roles)
    return missing_person_ids(db, ctx, (p for _i, p, _t, _c in eligible))


def work_state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """The Work's role revisions, tombstones included; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: roles.roles_state(scope, work_id))


def next_order_index(db: Database, ctx: RequestContext, work_id: str) -> int:
    return library_query(db, ctx, lambda scope: roles.next_order_index(scope, work_id))


# -- Writes --------------------------------------------------------------------
def insert_initial(db: Database, ctx: RequestContext, work_id: str, initial_roles: Any) -> int:
    """All the relationships a new Work is born with, in one transaction; how many were inserted."""
    wanted = [roles.InitialRole(p, t, i, c) for i, p, t, c in eligible_initial_roles(initial_roles)]
    if not wanted:
        return 0
    return _command(db, ctx, lambda scope: roles.insert_initial_roles(
        scope, work_id, wanted, skip_invalid=True), serializable=True)


def add(db: Database, ctx: RequestContext, person_id: str, work_id: str, role_type: str,
        credit_name: str = "") -> None:
    """A role on an existing Work, appended, with its revision."""
    work_role_sync.validate_role_type(role_type)

    def run(scope: LibraryScope) -> None:
        role = roles.Role(work_id, person_id, role_type)
        # The mode set_role takes again: a share lock is never upgraded.
        roles.lock_targets(scope, role,
                           writes_person=work_role_sync.credit_promotable_as_alias(credit_name))
        if roles.has_role(scope, work_id, person_id, role_type):
            raise ValueError(f"This person is already linked to this file as {role_type}.")
        roles.set_role(scope, role, True, credit_name)

    _command(db, ctx, run, serializable=True)


def update_credit(db: Database, ctx: RequestContext, work_id: str, person_id: str, role_type: str,
                  credit_name: str) -> bool:
    """The credit of an existing role; ``False`` when there is no such role."""
    role = roles.Role(work_id, person_id, role_type)

    def run(scope: LibraryScope) -> bool:
        try:
            roles.lock_targets(scope, role, writes_person=work_role_sync.credit_promotable_as_alias(
                credit_name))
        except roles.RoleTargetMissing:
            return False
        if roles.state(scope, role) is None:
            return False
        roles.set_role(scope, role, True, credit_name)
        return True

    return _command(db, ctx, run)


def remove(db: Database, ctx: RequestContext, work_id: str, person_id: str, role_type: str) -> bool:
    """Remove one role; ``True`` when a row was deleted."""
    return _command(db, ctx, lambda scope: roles.set_role(
        scope, roles.Role(work_id, person_id, role_type), False))
