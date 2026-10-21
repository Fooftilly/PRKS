"""``CREATE_PERSON``, ``DELETE_PERSON`` and ``SET_PERSON_METADATA_FIELD`` on PostgreSQL (L-P-1 #632).

Validated by the SQLite families' rules (``backend/person_sync.py``,
``backend/person_metadata_sync.py``) and decided by
:mod:`backend.domain.people.persons`, the commands behind the facade.

Creation is construction: the id is client-minted, a create of an id that
already exists acknowledges the stored row (``changed: false``) without
overwriting it, and an id of another Library is refused unledgered, as for
a Work. Two creates of one id queue on the primary key; the second
acknowledges the first's row. The acknowledged row is held until COMMIT,
so a concurrent delete either waits for the create or, committed first,
leaves it to create the Person again: never an acknowledgement of a
Person that is gone.

Deletion is the protected Person delete (§7.1): SERIALIZABLE, because a
role or a membership added concurrently must fail one of the two
transactions instead of pointing at nothing. A role committed by a writer
that is not SERIALIZABLE is refused by ``roles_person_fkey`` instead; the
operation then runs once more and ledgers ``PERSON_HAS_LINKS``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import person_metadata_sync as field_rules
from backend import person_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons, queries
from backend.sync.families import Environment, FamilyHandler, Result

# A create finds the id taken but no row of its own Library at most this
# often before it concludes the id is another Library's.
_ATTEMPTS = 2


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    person_id = op["entity_id"]
    fields = persons.canonical_fields(rules.canonical_fields(op["payload"]))
    # The row the acknowledgement reports is held until COMMIT: an existing
    # one FOR SHARE, so a concurrent delete waits for it (or, committed
    # first, is found and the Person is created again); a new one is ours.
    for _ in range(_ATTEMPTS):
        if persons.share(scope, person_id):
            return _acknowledged(scope, person_id, False)
        if persons.create_person(scope, person_id, fields, if_absent=True):
            return _acknowledged(scope, person_id, True)
        # The id exists: a concurrent create of it committed (and may since
        # have been deleted), or it names a Person of a Library the actor
        # cannot see. Look again.
    # Ids are unique across Libraries: refused, and not ledgered.
    raise AccessRefused(Refusal.FORBIDDEN)


def _acknowledged(scope: LibraryScope, person_id: str, created: bool) -> Result:
    return 200, {"code": "ACKNOWLEDGED", "person_id": person_id, "changed": created,
                 "person": queries.person_catalog_row(scope, person_id)}


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    person_id = op["entity_id"]
    deleted, linked = persons.delete_person(scope, person_id)
    if linked:
        return 409, {"code": "PERSON_HAS_LINKS", "person_id": person_id, "current_revision": linked}
    return 200, {"code": "ACKNOWLEDGED", "person_id": person_id, "changed": deleted}


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    field = op["payload"]["field"]
    return persons.apply_field_operation(scope, op["entity_id"], field,
                                         persons.canonical_field(field, op["payload"]["value"]),
                                         op["base_revision"])


CREATE_HANDLER = FamilyHandler(rules.validate, apply_create)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete, isolation=Isolation.SERIALIZABLE,
                               retry_constraints=frozenset({"roles_person_fkey"}))
FIELD_HANDLER = FamilyHandler(field_rules.validate, apply_field)
