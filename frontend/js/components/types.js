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

    const totals = rows.map((r) => Number(r.count) || 0);
    const totalFiles = totals.reduce((acc, n) => acc + n, 0);

    window.__prksRouteSidebar = { typeCount: rows.length, totalFiles };

    const rowsHtml = rows.length
        ? rows
              .map((r) => {
                  const count = Number(r.count) || 0;
                  const typePath = '#/types/' + encodeURIComponent(r.value);
                  const badge =
                      typeof prksDocTypeBadgeHtml === 'function'
                          ? prksDocTypeBadgeHtml(r.value)
                          : `<span class="status-badge Planned">${prksTypesEsc(r.label)}</span>`;
                  return (
                      `<div class="project-card types-page__list-item" data-prks-middleclick-nav="1"` +
                      ` onclick="window.location.hash='${typePath}'"` +
                      ` onauxclick="return prksMaybeOpenHashInNewTab(event,'${typePath}')">` +
                      `<div class="types-page__list-main">` +
                      `${badge}` +
                      `<p class="meta-row types-page__list-stats">${count} file${count === 1 ? '' : 's'}</p>` +
                      `</div>` +
                      `<span class="types-page__list-arrow" aria-hidden="true">${typeof prksIcon === 'function' ? prksIcon('chevronRight', { size: 'sm' }) : '→'}</span>` +
                      `</div>`
                  );
              })
              .join('')
        : '<p class="tags-page__empty types-page__empty">No files in library yet. Add file to start grouping by BibTeX type.</p>';

    container.innerHTML = `
        <div class="types-page">
            <div class="page-header tags-page__header">
                <h2>File types</h2>
                <p class="tags-page__sub types-page__sub">Browse files by BibTeX document type. Click row to open matching files.</p>
            </div>
            <div class="list-view types-page__list">
                ${rowsHtml}
            </div>
        </div>
    `;
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function renderWorksByDocType(works, docType, container) {
    const dt = typeof prksNormalizeDocType === 'function' ? prksNormalizeDocType(docType) : (docType || 'misc');
    const label = prksDocTypeLabel(dt);
    const all = Array.isArray(works) ? works : [];
    const filtered = all
        .filter((w) => (typeof prksNormalizeDocType === 'function' ? prksNormalizeDocType(w?.doc_type) : w?.doc_type) === dt)
        .sort((a, b) => String(a?.title || '').localeCompare(String(b?.title || ''), undefined, { sensitivity: 'base' }));

    window.__prksRouteSidebar = { docType: dt, docTypeLabel: label, workCount: filtered.length };
    const typeBadge =
        typeof prksDocTypeBadgeHtml === 'function'
            ? prksDocTypeBadgeHtml(dt)
            : `<span class="status-badge Planned">${prksTypesEsc(label)}</span>`;

    container.innerHTML = `
        <div class="types-page types-page--detail">
            <div class="page-header types-page__detail-header">
                <h2>Files</h2>
                <div class="types-page__detail-type">${typeBadge}</div>
            </div>
        <div class="card-grid types-page__detail-grid">
            ${
                filtered.length
                    ? filtered
                          .map((w) => {
                              return typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w, { hideDocTypeBadge: true }) : '';
                          })
                          .join('')
                    : `<p class="tags-page__empty types-page__empty">No files in this type yet.</p>`
            }
        </div>
        </div>
    `;
    if (typeof prksInitLazyWorkThumbs === 'function') prksInitLazyWorkThumbs(container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

