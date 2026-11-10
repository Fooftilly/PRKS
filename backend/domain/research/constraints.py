"""The Research Network constraints the PostgreSQL error mapping names (PG-C2 #641).

Registered when any PostgreSQL module of this package is imported, not by the
package itself, so the backend-neutral ``rules`` module loads without the
PostgreSQL driver.
"""

from backend.db import register_constraint

# The name and every alias of every Concept are one key namespace
# (``concept_keys``, §8.1). A rename or an alias written while a concurrent
# writer holds the key fails on the primary key when that writer commits;
# the command then runs once more and refuses from what it reads
# (``concepts.KEY_INDEX``).
register_constraint("concept_keys_pkey", "CONCEPT_EXISTS")
