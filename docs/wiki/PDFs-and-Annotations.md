# PDFs and Annotations

Managed PDFs are first-class PRKS Work sources. PRKS separates canonical annotation meaning from rendered PDF bytes so synchronization and UI state do not depend on rewriting a binary file for every edit.

## PDF annotations

PRKS stores annotation *meaning* as structured rows in the `annotations` table
(id, type, content, page, color, geometry). That metadata is the canonical
source for the sidebar, comments, sync, and offline durable ops
(`CREATE_PDF_ANNOTATION` / `SET_PDF_ANNOTATION` / `DELETE_PDF_ANNOTATION`).
Managed PDF bytes are a materialized rendering of a known annotation-set
generation — never the sync protocol, and never placed inside a durable
operation envelope.

The `annotations` table is used by the sidebar, comments, and
`GET /api/works/{id}/annotations`. Extra EmbedPDF fields that are not
first-class columns are stored in `geometry_json` so the submitted object can
be reconstructed.

Legacy byte-only user markup (highlights present in PDF bytes but missing from
metadata) is adopted into `annotations` on mount via
`POST /api/works/{id}/annotations/adopt`. Links and other non-user artifacts
are left alone. The older full-list
`POST /api/works/{id}/annotations` replace path remains only as online-legacy
compat when the durable store is unavailable.

Schema v13 removed the former `work_annotations` JSON snapshot. Schema v15
tracks `canonical_annotation_set_revision` vs
`materialized_pdf_annotation_revision` so PDF export can lag without data loss
(`ANNOTATION_MATERIALIZATION_STALE`).

## PDF text search

PRKS keeps a separate derived PDF text-search index (`prks_text_index.db`). It is automatically reconciled with managed PDFs at startup. Unchanged PDFs are compared by stored source fingerprint and filesystem metadata; they are not re-extracted and do not trigger a full FTS integrity scan. Image-only or scanned PDFs may contain no searchable text; PRKS does not perform OCR.

The index is disposable and is rebuilt after backup restore. **Settings → Rebuild PDF text index** forces a complete re-extraction and FTS integrity verification if search results seem incomplete or stale.

## Viewer lifecycle

The Work page mounts the PRKS PDF viewer for managed PDF Works. Viewer lifecycle is scoped to the Work's TabContext, which matters when several Works are open or visible in split panes.

Relevant implementation areas include `frontend/js/pdf-*.js`, `frontend/js/components/works-pdf.js`, the backend `pdf_*.py` modules, and the vendored PRKS PDF viewer package.

## Backup behavior

Managed PDFs and canonical annotation metadata are included in supported PRKS backups. Derived PDF text-search data is rebuilt after restore.

## Testing

PDF behavior has structural/unit coverage and real-browser coverage. Pointer capture, viewer integration, annotation persistence, and offline behavior have specialized tests because synthetic DOM events are not sufficient for every browser interaction.

See [Testing](Testing.md).
