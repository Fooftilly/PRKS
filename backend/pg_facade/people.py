"""People family of the PostgreSQL transition facade (L-P-1).

Owned by the People lane (``docs/postgresql-architecture.md`` §15.2):
roles. Each method keeps the signature and return shape of the
SQLite library it replaced.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one ``library_command`` transaction. The domain
modules (and so psycopg) are imported inside the methods.

Ported here: Work-Person roles and, through
``backend.domain.works.queries``, the Work projection's people fields (L-P-1
PR 3). PG-C3 #642 moved Publishers to
``backend/domain/people/publisher_commands`` (``backend/api/publishers.py``),
Person Groups with their hierarchy and members to
``backend/domain/people/group_commands`` (``backend/api/person_groups.py``),
and Persons with their profile fields and portraits to
``backend/domain/people/person_commands`` (``backend/api/persons.py``).
Differences from SQLite across the People family, each deliberate:

- Names (groups, the parent-name lookup) compare by
  ``prks.identity_key`` (§8.1), not ASCII-only ``LOWER``.
- ``aliases`` is stored as rows and reads back joined by ``", "``, one
  spelling per identity key; profile fields and descriptions are ``""``,
  never NULL; a portrait URL is normalized on create too.
- A deleted group's or Person's scopes advance (tombstones): every field
  scope, never-edited ones included, and every membership pair it ever had.
- A role names an existing Work and Person: ``add_role`` and
  ``insert_initial_role`` refuse a missing one with ``ValueError`` (SQLite's
  foreign key raises ``IntegrityError``). ``add_role`` reports a duplicate
  role inside the transaction that would insert it, and
  ``insert_initial_role`` refuses one with ``ValueError`` (SQLite's unique
  key raises ``IntegrityError``).
- A credit is promoted into the Person's aliases unless it already is the
  Person's name or an alias by identity key (SQLite compares ``lower()``).
- The methods write Work-scoped roles, as SQLite does; the domain commands
  (``backend.domain.people.roles``) also take a Manifestation, and the
  credits read are ``credits(primary M)`` (``docs/work-identity-model.md``
  §7.5).
- A Work delete tombstones every role scope of the Work and its
  Manifestations, and a Person delete every role scope naming the Person.
- ``delete_person_if_unlinked``, and a change of ``image_url``, never delete
  the cached portrait's bytes: they drop its reference and record a cleanup
  claim in the same transaction (``backend.domain.objects.portraits``), and
  settling the claim is maintenance, never inline (§5.2).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, List, Optional, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")

def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
    """One role command."""
    from backend.db.database import Isolation
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command

    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                           isolation=isolation)


class PeopleMixin(FacadeMixin):
    """Work-Person roles (L-P-1 PR 3)."""

    # -- Roles ------------------------------------------------------------------
    def add_role(
        self,
        person_id: str,
        work_id: str,
        role_type: str,
        order_index: int = 0,
        credit_name: str = "",
    ):
        """A link on an existing Work: appended, with its revision (``order_index`` is ignored)."""
        from backend import work_role_sync
        from backend.domain.people import roles

        del order_index
        work_role_sync.validate_role_type(role_type)

        def run(scope: LibraryScope) -> None:
            role = roles.Role(work_id, person_id, role_type)
            # The mode set_role takes again: a share lock is never upgraded.
            roles.lock_targets(scope, role,
                               writes_person=work_role_sync.credit_promotable_as_alias(credit_name))
            if roles.has_role(scope, work_id, person_id, role_type):
                raise ValueError(f"This person is already linked to this file as {role_type}.")
            roles.set_role(scope, role, True, credit_name)

        _command(self, run, serializable=True)
    def _missing_person_ids(self, person_ids) -> List[str]:
        from backend.domain.people import roles

        wanted = [str(p).strip() for p in (person_ids or []) if str(p or "").strip()]
        if not wanted:
            return []
        return _read(self, lambda scope: roles.missing_persons(scope, wanted))
    def missing_initial_role_person_ids(self, roles) -> List[str]:
        from backend.domain.people.rules import eligible_initial_roles

        eligible = eligible_initial_roles(roles)
        return self._missing_person_ids(p for _i, p, _t, _c in eligible)
    def insert_initial_roles(self, work_id: str, roles) -> int:
        """All the relationships a new Work is born with, in one transaction."""
        from backend.domain.people import roles as role_domain
        from backend.domain.people.rules import eligible_initial_roles

        wanted = [role_domain.InitialRole(p, t, i, c)
                  for i, p, t, c in eligible_initial_roles(roles)]
        if not wanted:
            return 0
        return _command(self, lambda scope: role_domain.insert_initial_roles(
            scope, work_id, wanted, skip_invalid=True), serializable=True)
    def next_role_order_index(self, work_id: str) -> int:
        from backend.domain.people import roles

        return _read(self, lambda scope: roles.next_order_index(scope, work_id))
    def get_work_roles_state(self, work_id: str) -> Optional[dict]:
        from backend.domain.people import roles

        return _read(self, lambda scope: roles.roles_state(scope, work_id))
    def update_role_credit_name(
        self,
        work_id: str,
        person_id: str,
        role_type: str,
        order_index: int,
        credit_name: str,
    ) -> bool:
        """The credit of an existing role; False when there is no such role."""
        from backend import work_role_sync
        from backend.domain.people import roles

        del order_index
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

        return _command(self, run)
    def delete_work_role(self, work_id: str, person_id: str, role_type: str,
                         order_index: int = 0) -> bool:
        """Remove one role; True when a row was deleted (``order_index`` is ignored)."""
        from backend.domain.people import roles

        del order_index
        return _command(self, lambda scope: roles.set_role(
            scope, roles.Role(work_id, person_id, role_type), False))

