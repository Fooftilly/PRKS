function prksTypesEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function prksDocTypeLabel(value) {
    if (typeof prksDocTypeMeta === 'function') {
        return prksDocTypeMeta(value).label || value || 'Misc';
    }
    return value || 'misc';
}

function renderTypesIndex(works, container) {
    const list = Array.isArray(works) ? works : [];
    const counts = Object.create(null);
    for (const w of list) {
        const dt = typeof prksNormalizeDocType === 'function' ? prksNormalizeDocType(w?.doc_type) : (w?.doc_type || 'misc');
        counts[dt] = (counts[dt] || 0) + 1;
    }

    const types = typeof PRKS_DOC_TYPES !== 'undefined' && Array.isArray(PRKS_DOC_TYPES)
        ? PRKS_DOC_TYPES.map((d) => d.value)
        : Object.keys(counts).sort();

    const rows = types
        .map((t) => ({
            value: t,
            label: prksDocTypeLabel(t),
            count: counts[t] || 0,
        }))
        .filter((r) => r.count > 0)
        .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));

    window.__prksRouteSidebar = { typeCount: rows.length };

    container.innerHTML = `
        <div class="page-header">
            <h2>File types</h2>
        </div>
        <p class="meta-row" style="margin: 0 0 14px 0;">
            Browse all files by BibTeX document type (independent of folders).
        </p>
        <div class="list-view">
            ${
                rows.length
                    ? rows
                          .map((r) => {
                              const badge =
                                  typeof prksDocTypeBadgeHtml === 'function'
                                      ? prksDocTypeBadgeHtml(r.value)
                                      : `<span class="status-badge Planned">${prksTypesEsc(r.value)}</span>`;
                              return `
                                <div class="project-card" data-prks-middleclick-nav="1"
                                    onclick="window.location.hash='#/types/${encodeURIComponent(r.value)}'">
                                    ${badge}
                                    <div class="card-title">${prksTypesEsc(r.label)}</div>
                                    <div class="meta-row">${r.count} file${r.count === 1 ? '' : 's'}</div>
                                </div>`;
                          })
                          .join('')
                    : `<p class="meta-row" style="color: var(--text-secondary);">No files yet.</p>`
            }
        </div>
    `;
}

function renderWorksByDocType(works, docType, container) {
    const dt = typeof prksNormalizeDocType === 'function' ? prksNormalizeDocType(docType) : (docType || 'misc');
    const label = prksDocTypeLabel(dt);
    const all = Array.isArray(works) ? works : [];
    const filtered = all
        .filter((w) => (typeof prksNormalizeDocType === 'function' ? prksNormalizeDocType(w?.doc_type) : w?.doc_type) === dt)
        .sort((a, b) => String(a?.title || '').localeCompare(String(b?.title || ''), undefined, { sensitivity: 'base' }));

    window.__prksRouteSidebar = { docType: dt, docTypeLabel: label, workCount: filtered.length };

    container.innerHTML = `
        <div class="page-header" style="gap: 12px; flex-wrap: wrap;">
            <h2>${prksTypesEsc(label)}</h2>
            <div style="flex: 1 1 auto;"></div>
            <a class="route-sidebar__link" href="#/types">All types</a>
        </div>
        <div class="card-grid">
            ${
                filtered.length
                    ? filtered
                          .map((w) => {
                              const subtitle = w.abstract ? String(w.abstract).slice(0, 90) + '…' : '';
                              return typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w, { subtitle }) : '';
                          })
                          .join('')
                    : `<p class="meta-row" style="color: var(--text-secondary);">No files of this type yet.</p>`
            }
        </div>
    `;
}

