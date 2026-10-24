"""Works lane (L-W-1, #628; ``docs/postgresql-architecture.md`` §6.4, §15.3).

Works, Manifestations, Assets (catalogue), notes, lifecycle and the Work
projection on PostgreSQL. The schema is ``backend/db/schema/100_works.sql``.
Nothing in the running application imports this package yet: SQLite stays
the only backend until the cutover slices (§15.3).
"""
