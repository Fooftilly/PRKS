"""Identity lane (L-ID-1, #626; ``docs/postgresql-architecture.md`` §9, §11.1).

Users, Libraries, memberships, bootstrap, authentication, sessions and
authorization, plus the database ↔ data-root pairing of
:mod:`backend.domain.identity.root_pairing`. Nothing in the running application
imports this package yet: SQLite stays the only backend until the cutover
slices (§15.3).
"""
