"""The People constraints the PostgreSQL error mapping names (PG-C2 #641).

Registered when any PostgreSQL module of this package is imported, not by the
package itself, so the backend-neutral ``rules`` and ``errors`` modules load
without the PostgreSQL driver.
"""

from backend.db import register_constraint

# A rename that races another rename or create of the same name: both pass
# the read check, and the unique index refuses the second (§7.1); a sync
# operation then reruns once (``retry_constraints``). Creates use ON
# CONFLICT and never reach these.
register_constraint("person_groups_library_id_name_key_key", "NAME_TAKEN")
register_constraint("publisher_keys_pkey", "NAME_TAKEN")
# A role committed after a Person delete read the Person's roles: the key
# from roles to persons is the last word on "this Person has links".
register_constraint("roles_person_fkey", "PERSON_HAS_LINKS")
