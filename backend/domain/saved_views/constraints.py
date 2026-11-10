"""The Saved View constraints the PostgreSQL error mapping names (PG-C2 #641).

Registered when any PostgreSQL module of this package is imported, not by the
package itself, so the backend-neutral ``rules`` and ``errors`` modules load
without the PostgreSQL driver.
"""

from backend.db import register_constraint

# A Saved View name is unique by identity key per Library; every writer of a
# name holds the Library's Saved View lock, so the index is the backstop
# (``views.NAME_KEY``). Registered by every PostgreSQL Saved View module, so the
# facade maps it whichever lane a process reaches first.
register_constraint("saved_views_library_id_name_key_key", "NAME_TAKEN")
