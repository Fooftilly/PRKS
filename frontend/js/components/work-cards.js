function prksWorkCardsEscapeHtml(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** @param {object} w */
function prksWorkFileSizeMbHtml(w) {
    const raw = w && w.file_size_bytes;
    const n = raw != null && raw !== '' ? Number(raw) : NaN;
    if (!Number.isFinite(n) || n <= 0) return '';
    const mb = n / (1024 * 1024);
    const s = mb >= 0.01 ? mb.toFixed(2) : mb.toFixed(3);
    return `<span class="work-card__file-size">${prksWorkCardsEscapeHtml(s)} MB</span>`;
}

function prksWorkThumbUrl(workId, page) {
    const wid = encodeURIComponent(String(workId || '').trim());
    if (!wid) return '';
    const p = page != null && String(page).trim() !== '' ? Number(page) : null;
    if (p && Number.isFinite(p) && p > 0) {
        return `/api/works/${wid}/thumbnail?page=${encodeURIComponent(String(p))}`;
    }
    return `/api/works/${wid}/thumbnail`;
}

const PRKS_WORK_THUMB_PLACEHOLDER =
    'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';

function prksHydrateLazyWorkThumb(img) {
    if (!img || !(img instanceof HTMLImageElement)) return;
    if (img.dataset.prksThumbLoaded === '1') return;
    const src = String(img.getAttribute('data-prks-thumb-src') || '').trim();
    if (!src) return;
    img.setAttribute('src', src);
    img.dataset.prksThumbLoaded = '1';
    img.removeAttribute('data-prks-thumb-src');
}

function prksLazyThumbObserver() {
    if (!('IntersectionObserver' in window)) return null;
    if (!window.__prksWorkThumbObserver) {
        window.__prksWorkThumbObserver = new IntersectionObserver(
            (entries, obs) => {
                entries.forEach((entry) => {
                    if (!entry || !entry.isIntersecting) return;
                    prksHydrateLazyWorkThumb(entry.target);
                    obs.unobserve(entry.target);
                    entry.target.removeAttribute('data-prks-thumb-observing');
                });
            },
            { root: null, rootMargin: '240px 0px', threshold: 0.01 }
        );
    }
    return window.__prksWorkThumbObserver;
}

function prksInitLazyWorkThumbs(root) {
    const host = root && typeof root.querySelectorAll === 'function' ? root : document;
    const imgs = host.querySelectorAll('img[data-prks-thumb-src]');
    if (!imgs.length) return;
    const observer = prksLazyThumbObserver();
    if (!observer) {
        imgs.forEach((img) => prksHydrateLazyWorkThumb(img));
        return;
    }
    imgs.forEach((img) => {
        if (img.dataset.prksThumbObserving === '1') return;
        img.dataset.prksThumbObserving = '1';
        observer.observe(img);
    });
}

/** Plain year for meta row: `year` field, else leading YYYY from ISO `published_date`. */
function prksWorkCardYearPlain(w) {
    if (!w) return '';
    const y = (w.year || '').trim();
    if (y) return prksWorkCardsEscapeHtml(y);
    const pd = (w.published_date || '').trim();
    if (!pd) return '';
    const m = pd.match(/^(\d{4})/);
    return m ? prksWorkCardsEscapeHtml(m[1]) : prksWorkCardsEscapeHtml(pd);
}

/**
 * Credit line: linked Author, else `author_text`, else linked Editor.
 * @returns {string} escaped HTML fragment e.g. `Author: …` or `Editor: …`, or ''
 */
function prksWorkCardCreditLine(w) {
    if (!w) return '';
    let name = w.primary_author != null ? String(w.primary_author).trim() : '';
    if (name) return `Author: ${prksWorkCardsEscapeHtml(name)}`;
    if (w.author_text != null) {
        const at = String(w.author_text).trim();
        if (at) return `Author: ${prksWorkCardsEscapeHtml(at)}`;
    }
    name = w.primary_editor != null ? String(w.primary_editor).trim() : '';
    if (name) return `Editor: ${prksWorkCardsEscapeHtml(name)}`;
    return '';
}

/**
 * Work card HTML for card-grid layouts.
 * @param {object} w
 * @param {object} options { subtitle?: string, thumbPage?: number } — subtitle = extra tail (abstract, last opened, …)
 */
function prksWorkCardHtml(w, options = {}) {
    if (!w) return '';
    const title = prksWorkCardsEscapeHtml(w.title || 'Untitled');
    const wid = prksWorkCardsEscapeHtml(w.id || '');
    const status = w.status ? String(w.status) : '';
    const statusClass = status ? status.replace(/ /g, '.') : '';
    const statusHtml = status
        ? `<span class="status-badge ${prksWorkCardsEscapeHtml(statusClass)}">${prksWorkCardsEscapeHtml(status)}</span>`
        : '';
    const typeBadge = typeof prksDocTypeBadgeHtml === 'function' ? prksDocTypeBadgeHtml(w.doc_type) : '';
    const subtitleRaw = options.subtitle != null ? String(options.subtitle) : '';
    const subtitle = prksWorkCardsEscapeHtml(subtitleRaw);

    const filePath = w.file_path ? String(w.file_path).trim() : '';
    const hasPdf = !!filePath && filePath.startsWith('/api/pdfs/');
    const inferredKind = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(w) : '';
    const thumbPage = options.thumbPage != null ? options.thumbPage : w.thumb_page;
    const thumbSrc =
        hasPdf
            ? prksWorkThumbUrl(w.id, thumbPage)
            : inferredKind === 'video' && w.thumb_url
              ? String(w.thumb_url).trim()
              : '';

    const thumbHtml = thumbSrc
        ? `<div class="work-card__thumb"><img loading="lazy" alt="" src="${PRKS_WORK_THUMB_PLACEHOLDER}" data-prks-thumb-src="${prksWorkCardsEscapeHtml(
              thumbSrc
          )}" onerror="this.closest('.work-card__thumb')?.classList.add('work-card__thumb--error'); this.remove();" /></div>`
        : `<div class="work-card__thumb work-card__thumb--empty" aria-hidden="true"></div>`;

    const fileSizeHtml = prksWorkFileSizeMbHtml(w);

    const metaChunks = [];
    const credit = prksWorkCardCreditLine(w);
    if (credit) metaChunks.push(credit);
    const yearPlain = prksWorkCardYearPlain(w);
    if (yearPlain) metaChunks.push(yearPlain);
    if (subtitle) metaChunks.push(subtitle);
    const metaHtml = metaChunks.length
        ? `<div class="meta-row work-card__meta">${metaChunks.join(' · ')}</div>`
        : '';

    return `
        <div class="project-card project-card--work-card" data-prks-middleclick-nav="1"
            onclick="window.location.hash='#/works/${wid}'"
            onauxclick="return prksMaybeOpenHashInNewTab(event,'#/works/${wid}')">
            ${thumbHtml}
            <div class="work-card__body">
                <div class="card-title">${title}</div>
                ${metaHtml}
                <div class="work-card__badges">
                    <div class="work-card__badges-left">
                        ${statusHtml}
                        ${typeBadge}
                    </div>
                    ${fileSizeHtml ? `<div class="work-card__badges-right">${fileSizeHtml}</div>` : ''}
                </div>
            </div>
        </div>
    `;
}

window.prksInitLazyWorkThumbs = prksInitLazyWorkThumbs;

