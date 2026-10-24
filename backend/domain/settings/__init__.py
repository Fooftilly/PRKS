"""Application settings on PostgreSQL (L-O-1 #633; ``docs/postgresql-architecture.md`` §6.5).

The settings ``/api/settings`` reads and patches, stored by scope: both
keys the API has today (``annotation_author``, ``bibtex_export_fields``)
are the Library's, in ``prks.library_settings``. No key is instance-wide,
so ``prks.instance_settings`` holds none of them, and nothing here reads
it. Which further keys go where is #612's configuration inventory. The
commands are ``app_settings``.
"""
