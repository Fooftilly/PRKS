"""PDF annotations on PostgreSQL (L-A-1 #635; ``docs/postgresql-architecture.md`` §6.5, §7.4).

The schema is ``backend/db/schema/140_annotations.sql``. An annotation
belongs to an Asset, the file it was drawn on; the Work-addressed API and
sync families reach the Work's primary managed PDF. :mod:`.store` holds the
rows, revisions and reads; :mod:`.operations` decides the
``CREATE``/``SET``/``DELETE_PDF_ANNOTATION`` families, the legacy full-list
replace and the byte-only markup adoption with SQLite's rules;
:mod:`.materialize` commits the viewer's rendered bytes; and
:mod:`.annotation_commands` is what the HTTP routes call
(``backend/api/annotations.py``, PG-C3 #642).

SQLite's three materialization claim and mark methods survive only as the
tests' names (``tests/library_adapter.py``), over :mod:`.materialize`. They
keep SQLite's signatures, errors and returns, but acknowledge only committed
bytes:

- ``accept_work_pdf_materialization_claim`` validates as on SQLite (a Work
  without a managed PDF is ``LookupError``: there are no bytes to replace).
- ``mark_work_pdf_materialized_if_claim_current`` marks nothing: it returns
  the claim only when the committed bytes embed it and it is still canonical
  (the materialization committed them), else ``ValueError(STALE)``.
- ``mark_work_pdf_materialized`` may only lower the materialized revision
  (record lag) or, without ``at_revision``, report it; a revision above it
  is ``ValueError(STALE)``, since no committed bytes embed one.
"""
