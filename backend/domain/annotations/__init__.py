"""PDF annotations on PostgreSQL (L-A-1 #635; ``docs/postgresql-architecture.md`` §6.5, §7.4).

The schema is ``backend/db/schema/140_annotations.sql``. An annotation
belongs to an Asset, the file it was drawn on; the Work-addressed API and
sync families reach the Work's primary managed PDF. :mod:`.store` holds the
rows, revisions and reads; :mod:`.operations` decides the
``CREATE``/``SET``/``DELETE_PDF_ANNOTATION`` families, the legacy full-list
replace and the byte-only markup adoption with SQLite's rules.
"""
