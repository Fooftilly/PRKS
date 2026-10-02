/**
 * File types browse model.
 *
 * Index and type detail share the coordinator's effective works-browse rows.
 * This module groups and filters those rows. It does not fetch, read the
 * durable queue, or paint. Vue paints the model.
 */

function prksTypesNormalize(raw) {
    if (typeof prksNormalizeDocType === 'function') return prksNormalizeDocType(raw);
    if (raw == null || String(raw).trim() === '') return 'misc';
    return String(raw).trim().toLowerCase();
}

function prksTypesDocTypeLabel(value) {
    if (typeof prksDocTypeMeta === 'function') {
        const meta = prksDocTypeMeta(value);
        return (meta && meta.label) || value || 'Misc';
    }
    return value || 'misc';
}

function prksTypesCatalogValues(counts) {
    if (typeof PRKS_DOC_TYPES !== 'undefined' && Array.isArray(PRKS_DOC_TYPES)) {
        return PRKS_DOC_TYPES.map((d) => d.value);
    }
    return Object.keys(counts).sort();
}

/**
 * Types with at least one file, count descending, then label.
 * Unknown and blank document types collapse to misc. Zero counts are omitted.
 * @param {unknown} works
 * @returns {{ rows: { value: string, label: string, count: number }[], typeCount: number, totalFiles: number }}
 */
function prksTypesIndexModel(works) {
    const list = Array.isArray(works) ? works : [];
    const counts = Object.create(null);
    for (const w of list) {
        const dt = prksTypesNormalize(w && w.doc_type);
        counts[dt] = (counts[dt] || 0) + 1;
    }
    const rows = prksTypesCatalogValues(counts)
        .map((t) => ({
            value: t,
            label: prksTypesDocTypeLabel(t),
            count: counts[t] || 0,
        }))
        .filter((r) => r.count > 0)
        .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));
    const totalFiles = rows.reduce((acc, r) => acc + (Number(r.count) || 0), 0);
    return {
        rows: rows,
        typeCount: rows.length,
        totalFiles: totalFiles,
    };
}

/**
 * Files of one BibTeX type, title order. The type is normalized first, so an
 * unknown route segment is misc.
 * @param {unknown} works
 * @param {unknown} docType
 * @returns {{ docType: string, label: string, works: object[], workCount: number }}
 */
function prksTypesDetailModel(works, docType) {
    const dt = prksTypesNormalize(docType);
    const label = prksTypesDocTypeLabel(dt);
    const all = Array.isArray(works) ? works : [];
    const filtered = all
        .filter((w) => prksTypesNormalize(w && w.doc_type) === dt)
        .sort((a, b) => String((a && a.title) || '').localeCompare(String((b && b.title) || ''), undefined, { sensitivity: 'base' }));
    return {
        docType: dt,
        label: label,
        works: filtered,
        workCount: filtered.length,
    };
}

if (typeof window !== 'undefined') {
    window.prksTypesIndexModel = prksTypesIndexModel;
    window.prksTypesDetailModel = prksTypesDetailModel;
}
