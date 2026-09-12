/** Matches works.status CHECK constraint in db_schema.sql */
const PRKS_PROGRESS_STATUSES = ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'];

function progressEscapeHtml(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function normalizeProgressStatusParam(raw) {
    if (raw == null || String(raw).trim() === '') return null;
    const decoded = decodeURIComponent(String(raw).trim());
    return PRKS_PROGRESS_STATUSES.includes(decoded) ? decoded : null;
}

/**
 * Parse status from location.hash like #/progress?status=In%20Progress
 * @returns {string|null} canonical status or null if invalid/missing
 */
function parseProgressStatusFromHash(hash) {
    const h = hash || '';
    const withoutHash = h.startsWith('#') ? h.slice(1) : h;
    if (!withoutHash.startsWith('/progress')) return null;
    const q = withoutHash.indexOf('?');
    if (q < 0) return null;
    const params = new URLSearchParams(withoutHash.slice(q + 1));
    return normalizeProgressStatusParam(params.get('status'));
}

function renderProgressByStatus(works, status, container, options = {}) {
    const offlineCached = !!(options && options.offlineCached);
    const title = `Files · ${progressEscapeHtml(status)}`;
    const list = (works || [])
        .filter((w) => w && w.status === status)
        .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }));

    let html = `<div class="prks-page-header page-header"><h2 class="prks-page-title">${title}</h2></div><div class="card-grid">`;
    if (list.length > 0) {
        list.forEach((w) => {
            /* `abstract_excerpt` arrives ALREADY bounded to 100 Unicode code
             * points by the server's SUBSTR(). Re-truncating it here with
             * substring() counted UTF-16 code units instead, so an abstract
             * containing emoji rendered barely half the intended excerpt and
             * could end mid-surrogate-pair -- a broken glyph. The only case
             * that still needs bounding is the older full summary, which ships
             * the whole `abstract`. */
            const excerpt = w.abstract_excerpt != null
                ? String(w.abstract_excerpt)
                : prksAbstractExcerpt(w.abstract);
            const subtitle = excerpt ? excerpt + '…' : '';
            html += typeof prksWorkCardHtml === 'function'
                ? prksWorkCardHtml(w, offlineCached ? { subtitle, suppressThumbnail: true } : { subtitle })
                : '';
        });
    } else {
        html += `<p class="progress-empty-msg">No files with this progress status yet.</p>`;
    }
    html += `</div>`;
    container.innerHTML = html;
}

function syncProgressSidebarActive(statusOrNull) {
    document.querySelectorAll('.progress-filter').forEach((el) => {
        const s = el.getAttribute('data-status');
        if (statusOrNull && s === statusOrNull) {
            el.classList.add('active');
        } else {
            el.classList.remove('active');
        }
    });
}
