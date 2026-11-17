"""Search lane (L-S-1, #637; ``docs/postgresql-architecture.md`` §8.3, §8.6, §8.7).

Work search on PostgreSQL. ``derived.work_search`` holds one row per Work,
written only through :func:`backend.domain.search.refresh.refresh_work_search`
(``refresh``); the schema is ``backend/db/schema/200_derived_search.sql``.
"""
