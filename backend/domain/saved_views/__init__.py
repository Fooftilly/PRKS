"""Saved Views on PostgreSQL (L-O-1 #633; ``docs/postgresql-architecture.md`` §8.5).

Named search definitions, online only: no sync family and no revision
scope. The table is ``prks.saved_views`` in
``backend/db/schema/120_organization.sql``; the commands are ``views``.
"""

from backend.db import register_constraint

# A Saved View name is unique by identity key per Library; every writer of a
# name holds the Library's Saved View lock, so the index is the backstop
# (``views.NAME_KEY``). Registered here, on the Saved View import path, so the
# facade maps it whichever lane a process reaches first.
register_constraint("saved_views_library_id_name_key_key", "NAME_TAKEN")
