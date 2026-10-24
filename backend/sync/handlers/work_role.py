"""``ADD/REMOVE_WORK_PERSON_ROLE`` and ``SET_WORK_PERSON_ROLE_CREDIT`` on PostgreSQL (L-P-1 #632).

Validated by ``backend/work_role_sync.py``'s rule and decided by
:func:`backend.domain.people.roles.apply_role_operation`, the same decision
table with the same results. The operations name no Manifestation, so they
change Work-scoped roles, as on SQLite.

A role add is a peer-table write of the protected Person delete (§7.1), so
it runs SERIALIZABLE; an unlink or a credit change runs READ COMMITTED
behind the Work and Person row locks.

:class:`RoleFiling` is the People half of ``CREATE_WORK``'s filing
(:class:`~backend.sync.families.WorkFiling`): the roles a new Work is born
with. The Organization lane's filing checks its folder and playlist first,
as SQLite does, and delegates the roles here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import work_role_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons, roles
from backend.domain.works import revisions
from backend.sync.families import Environment, FamilyHandler, Result


def apply(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    role = roles.Role(op["entity_id"], payload["person_id"], payload["role_type"])
    return roles.apply_role_operation(scope, role, op["operation"], payload.get("credit_name"),
                                      op["base_revision"])


HANDLER = FamilyHandler(rules.validate, apply)
ADD_HANDLER = FamilyHandler(rules.validate, apply, isolation=Isolation.SERIALIZABLE)


class RoleFiling:
    """The roles of ``CREATE_WORK`` (``fields["roles"]`` from ``canonical_create_payload``).

    ``prepare`` refuses the first role naming a missing Person, in payload
    order, with SQLite's ``PERSON_NOT_FOUND``; ``file`` inserts the roles at
    their payload index (construction: no role revision moves) and reports
    each Person whose ``aliases`` revision a promoted credit advanced. A
    Person deleted between the two is a serialization failure of the
    SERIALIZABLE create, which reruns and refuses.
    """

    def prepare(self, scope: LibraryScope, work_id: str,
                fields: dict[str, Any]) -> dict[str, Any] | Result:
        wanted: list[dict[str, Any]] = fields["roles"]
        missing = roles.missing_persons(scope, [role["person_id"] for role in wanted])
        if missing:
            return 404, {"code": "PERSON_NOT_FOUND", "work_id": work_id, "person_id": missing[0]}
        return {"role_count": len(wanted)}

    def file(self, scope: LibraryScope, work_id: str, fields: dict[str, Any],
             prepared: dict[str, Any]) -> dict[str, Any]:
        del prepared
        wanted: list[dict[str, Any]] = fields["roles"]
        if not wanted:
            return {}
        aliases = {pid: persons.scope_id(pid, "aliases")
                   for pid in dict.fromkeys(role["person_id"] for role in wanted)}
        before = revisions.read(scope, persons.SCOPE_TYPE, list(aliases.values()))
        roles.insert_initial_roles(scope, work_id, [
            roles.InitialRole(role["person_id"], role["role_type"], index, role["credit_name"])
            for index, role in enumerate(wanted)
        ], skip_invalid=True)
        after = revisions.read(scope, persons.SCOPE_TYPE, list(aliases.values()))
        advanced = {pid: after[key] for pid, key in aliases.items() if after[key] != before[key]}
        return {"aliases_revisions": advanced} if advanced else {}
