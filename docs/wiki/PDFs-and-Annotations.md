# PDFs and Annotations

Managed PDFs are first-class PRKS Work sources. PRKS separates annotation meaning from rendered PDF bytes so synchronization and UI state do not depend on rewriting a binary file for every edit.

## PDF viewing

The Work page mounts the PRKS PDF viewer for managed PDF Works. Viewer lifecycle is scoped to the Work's TabContext, which is important when several Works are open or visible in split panes.

The viewer integration is implemented across the `frontend/js/pdf-*.js`, `components/works-pdf.js`, backend PDF modules, and the vendored PRKS viewer package.

## Canonical annotation data

Structured annotation rows are the canonical source for PRKS annotation meaning. They contain the annotation type/content/page/color/geometry needed by the sidebar, comments, synchronization, and offline durable operations.

Managed PDF bytes are a materialized representation of a known annotation-set generation. They are not the synchronization protocol.

This separation allows structured annotation intent to remain authoritative even if PDF-byte export/materialization lags behind.

## Legacy annotation adoption

Older PDF bytes may contain user markup that predates structured annotation rows. PRKS can adopt supported legacy user markup into canonical annotation metadata when a Work is mounted. Non-user artifacts such as document links are not treated as user annotations.

## Materialization

The backend tracks whether the managed PDF bytes reflect the latest canonical annotation set. If materialization is stale, that condition is explicit rather than silently treating stale bytes as current data.

Relevant backend modules include:

- `pdf_annotations.py`;
- `pdf_annotation_sync.py`;
- `pdf_annotation_adopt.py`;
- `pdf_materialization.py`;
- `pdf_linearize.py`.

## PDF text search

PRKS keeps PDF text search in a separate derived SQLite index. It is reconciled against managed PDFs and can be rebuilt. The index is disposable and is intentionally excluded from canonical backup payloads.

Image-only/scanned PDFs may have no searchable text; PRKS does not currently provide OCR as part of this indexer.

## Backup behavior

Managed PDFs are included in PRKS backups. Derived thumbnail/search data is not. After restore, derived indexes can be rebuilt from canonical data.

## Testing

PDF behavior has both structural/unit coverage and real-browser coverage. Pointer capture, viewer integration, annotation persistence, and offline behavior have specialized tests because synthetic DOM events are not sufficient for every browser interaction.

Use [Testing](Testing.md) and the repository's PDF-specific tests when modifying this subsystem.
