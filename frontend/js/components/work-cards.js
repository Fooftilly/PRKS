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

/**
 * The thumbnail resource for one PDF Work, with the page ALWAYS stated.
 *
 * Omitting the page used to mean "whatever page the server currently has
 * stored", which is not a resource identity the client can reason about --
 * and is wrong the moment a local edit is pending. If the server holds page 5
 * and the user clears the field offline, the effective value is null, meaning
 * page 1; a URL with no page would still render page 5 until the server heard
 * about it. So a null page is requested EXPLICITLY as page 1, which is the
 * same page the server picks for a stored NULL and the same cache artifact it
 * builds -- `prks_thumb_cache_stem()` normalizes None and any value below 1
 * to 1.
 *
 * Stating it always, rather than only while an edit is pending, means pending
 * and acknowledged rendering run the same code path, and the URL alone says
 * which page is on screen. The cost is one browser-cache miss per card the
 * first time, because `/thumbnail` and `/thumbnail?page=1` are different URLs
 * to the browser while being the same bytes to the server.
 */
function prksWorkThumbUrl(workId, page) {
    const wid = encodeURIComponent(String(workId || '').trim());
    if (!wid) return '';
    const p = page != null && String(page).trim() !== '' ? Number(page) : null;
    const resolved = p && Number.isFinite(p) && p > 0 ? Math.floor(p) : 1;
    return `/api/works/${wid}/thumbnail?page=${encodeURIComponent(String(resolved))}`;
}

const PRKS_WORK_THUMB_PLACEHOLDER =
    'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';

const PRKS_WORK_BROWSE_MODE_KEY = 'prks.ui.workBrowseMode';
const PRKS_WORK_BROWSE_MODES = ['cards', 'list'];

function prksNormalizeWorkBrowseMode(raw) {
    const v = String(raw || '')
        .trim()
        .toLowerCase();
    return PRKS_WORK_BROWSE_MODES.includes(v) ? v : 'cards';
}

/** Global Work browse density: cards (default) or compact list. */
function prksGetWorkBrowseMode() {
    try {
        if (typeof localStorage === 'undefined') return 'cards';
        return prksNormalizeWorkBrowseMode(localStorage.getItem(PRKS_WORK_BROWSE_MODE_KEY));
    } catch (_err) {
        return 'cards';
    }
}

/**
 * Persist Work browse density. Returns the canonical mode that was stored.
 * Does not re-fetch; callers apply the class change in-place.
 */
function prksSetWorkBrowseMode(mode) {
    const next = prksNormalizeWorkBrowseMode(mode);
    try {
        if (typeof localStorage !== 'undefined') {
            localStorage.setItem(PRKS_WORK_BROWSE_MODE_KEY, next);
        }
    } catch (_err) {
        /* Preference is best-effort; browsing still works without it. */
    }
    return next;
}

/** Collection wrapper class for the current browse mode (CSS drives layout). */
function prksWorkBrowseCollectionClass(extraClass) {
    const mode = prksGetWorkBrowseMode();
    const base =
        mode === 'list'
            ? 'work-browse-collection work-browse-collection--list'
            : 'work-browse-collection work-browse-collection--cards card-grid';
    const extra = extraClass != null && String(extraClass).trim() ? ' ' + String(extraClass).trim() : '';
    return base + extra;
}

/**
 * Segmented Cards | List control for Work collection chrome.
 * @param {string} [hiddenId]
 */
function prksWorkBrowseModeToggleHtml(hiddenId) {
    const id = hiddenId || 'prks-work-browse-mode';
    const mode = prksGetWorkBrowseMode();
    const labels = ['Cards', 'List'];
    const selected = mode === 'list' ? 'List' : 'Cards';
    if (typeof prksSegmentedControlHtml === 'function') {
        return (
            `<div class="work-browse-mode" data-prks-role="work-browse-mode">` +
            prksSegmentedControlHtml(id, 'Work browse layout', labels, selected, '', {
                compact: true,
            }) +
            `</div>`
        );
    }
    return (
        `<div class="work-browse-mode" data-prks-role="work-browse-mode">` +
        `<div class="prks-segmented-wrap prks-segmented-wrap--compact">` +
        `<input type="hidden" id="${prksWorkCardsEscapeHtml(id)}" value="${prksWorkCardsEscapeHtml(selected)}">` +
        `<div class="prks-segmented prks-segmented--compact" role="radiogroup" aria-label="Work browse layout">` +
        labels
            .map((l) => {
                const on = l === selected;
                return (
                    `<button type="button" class="prks-segmented__btn${on ? ' prks-segmented__btn--active' : ''}"` +
                    ` data-value="${prksWorkCardsEscapeHtml(l)}" aria-pressed="${on ? 'true' : 'false'}" role="radio">${prksWorkCardsEscapeHtml(l)}</button>`
                );
            })
            .join('') +
        `</div></div></div>`
    );
}

/**
 * Apply browse mode to every Work collection under root (class only — no refetch).
 * @param {ParentNode|null} root
 * @param {string} mode
 */
function prksApplyWorkBrowseModeToDom(root, mode) {
    const host = root && typeof root.querySelectorAll === 'function' ? root : document;
    const next = prksNormalizeWorkBrowseMode(mode);
    host.querySelectorAll('.work-browse-collection').forEach((el) => {
        el.classList.toggle('work-browse-collection--list', next === 'list');
        el.classList.toggle('work-browse-collection--cards', next === 'cards');
        el.classList.toggle('card-grid', next === 'cards');
    });
    host.querySelectorAll('[data-prks-role="work-browse-mode"]').forEach((wrap) => {
        const hidden = wrap.querySelector('input[type="hidden"]');
        const label = next === 'list' ? 'List' : 'Cards';
        if (hidden) hidden.value = label;
        wrap.querySelectorAll('.prks-segmented__btn').forEach((btn) => {
            const on = (btn.getAttribute('data-value') || '') === label;
            btn.classList.toggle('prks-segmented__btn--active', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    });
}

/**
 * Bind Cards|List toggles under root. Mode change persists and reclasses collections
 * in-place; optional onModeChange for surfaces that need a full re-paint.
 * @param {ParentNode|null} root
 * @param {{ onModeChange?: (mode: string) => void }} [options]
 */
function prksBindWorkBrowseMode(root, options) {
    const host = root && typeof root.querySelectorAll === 'function' ? root : document;
    const opts = options && typeof options === 'object' ? options : {};
    host.querySelectorAll('[data-prks-role="work-browse-mode"]').forEach((wrap) => {
        if (wrap.dataset.prksBrowseModeBound === '1') return;
        wrap.dataset.prksBrowseModeBound = '1';
        const hidden = wrap.querySelector('input[type="hidden"]');
        const seg = wrap.querySelector('.prks-segmented');
        if (!seg) return;
        if (hidden && typeof prksBindSegmentedHidden === 'function' && hidden.id) {
            prksBindSegmentedHidden(hidden.id);
        }
        seg.querySelectorAll('.prks-segmented__btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const label = btn.getAttribute('data-value') || 'Cards';
                const mode = label === 'List' ? 'list' : 'cards';
                const next = prksSetWorkBrowseMode(mode);
                prksApplyWorkBrowseModeToDom(host, next);
                if (typeof opts.onModeChange === 'function') opts.onModeChange(next);
                if (typeof window.prksInitLazyWorkThumbs === 'function') {
                    window.prksInitLazyWorkThumbs(host);
                }
            });
        });
    });
}

function prksSetWorkThumbState(thumb, state) {
    if (!thumb || !thumb.classList) return;
    thumb.classList.remove(
        'work-card__thumb--loading',
        'work-card__thumb--ready',
        'work-card__thumb--error',
        'work-card__thumb--empty'
    );
    const s = String(state || '');
    if (s === 'loading') thumb.classList.add('work-card__thumb--loading');
    else if (s === 'ready') thumb.classList.add('work-card__thumb--ready');
    else if (s === 'error') thumb.classList.add('work-card__thumb--error');
    else if (s === 'empty') thumb.classList.add('work-card__thumb--empty');
    thumb.setAttribute('data-prks-thumb-state', s || '');
}

function prksHydrateLazyWorkThumb(img) {
    if (!img || !(img instanceof HTMLImageElement)) return;
    if (img.dataset.prksThumbLoaded === '1') return;
    const src = String(img.getAttribute('data-prks-thumb-src') || '').trim();
    if (!src) return;
    const thumb = img.closest('.work-card__thumb');
    if (thumb) prksSetWorkThumbState(thumb, 'loading');
    const onLoad = () => {
        if (thumb) prksSetWorkThumbState(thumb, 'ready');
        img.removeEventListener('load', onLoad);
        img.removeEventListener('error', onError);
    };
    const onError = () => {
        if (thumb) prksSetWorkThumbState(thumb, 'error');
        img.removeEventListener('load', onLoad);
        img.removeEventListener('error', onError);
        img.remove();
    };
    img.addEventListener('load', onLoad);
    img.addEventListener('error', onError);
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
        const thumb = img.closest('.work-card__thumb');
        if (thumb && !thumb.classList.contains('work-card__thumb--ready')) {
            prksSetWorkThumbState(thumb, 'loading');
        }
        observer.observe(img);
    });
}

/** Plain year for meta row: `year` field, else leading YYYY from ISO `published_date`. */
function prksWorkCardYearPlain(w) {
    if (!w) return '';
    const y = typeof w.year === 'string' ? w.year.trim() : '';
    if (y) return prksWorkCardsEscapeHtml(y);
    const pd = typeof w.published_date === 'string' ? w.published_date.trim() : '';
    if (!pd) return '';
    const m = pd.match(/^(\d{4})/);
    return m ? prksWorkCardsEscapeHtml(m[1]) : prksWorkCardsEscapeHtml(pd);
}

/**
 * Plain-text credit line for summaries that escape later.
 * Linked Author(s), else `author_text`, else linked Editor.
 * @returns {string} unescaped text e.g. `Author: …` or '', never HTML
 */
function prksWorkCardCreditText(w) {
    if (!w) return '';
    let name = w.linked_authors != null ? String(w.linked_authors).trim() : '';
    if (!name && w.primary_author != null) name = String(w.primary_author).trim();
    if (name) return 'Author: ' + name;
    if (w.author_text != null) {
        const at = String(w.author_text).trim();
        if (at) return 'Author: ' + at;
    }
    name = w.primary_editor != null ? String(w.primary_editor).trim() : '';
    if (name) return 'Editor: ' + name;
    return '';
}

/**
 * Credit line: linked Author(s), else `author_text`, else linked Editor.
 * @returns {string} escaped HTML fragment e.g. `Author: …` or `Editor: …`, or ''
 */
function prksWorkCardCreditLine(w) {
    const plain = prksWorkCardCreditText(w);
    if (!plain) return '';
    return prksWorkCardsEscapeHtml(plain);
}

/**
 * Work card HTML for card-grid and compact-list layouts (CSS mode via collection class).
 * @param {object} w
 * @param {object} options { subtitle?: string, thumbPage?: number, hideDocTypeBadge?: boolean,
 *   suppressThumbnail?: boolean }
 *   — subtitle = contextual line (abstract excerpt, added/opened date, Person credit)
 *   rendered below the bibliographic meta line, not merged into it.
 */
function prksWorkCardHtml(w, options = {}) {
    if (!w) return '';
    const title = prksWorkCardsEscapeHtml(w.title || 'Untitled');
    const wid = prksWorkCardsEscapeHtml(w.id || '');
    const status = w.status ? String(w.status) : '';
    const statusClass = status ? status.replace(/ /g, '.') : '';
    const statusIcon =
        typeof prksProgressStatusIconHtml === 'function'
            ? prksProgressStatusIconHtml(status, { className: 'status-badge__icon', size: 'sm' })
            : '';
    const statusHtml = status
        ? `<span class="status-badge ${prksWorkCardsEscapeHtml(statusClass)}">${statusIcon}${prksWorkCardsEscapeHtml(status)}</span>`
        : '';
    const typeBadge =
        options.hideDocTypeBadge
            ? ''
            : typeof prksDocTypeBadgeHtml === 'function'
              ? prksDocTypeBadgeHtml(w.doc_type)
              : '';
    const subtitleRaw = options.subtitle != null ? String(options.subtitle) : '';
    const subtitle = prksWorkCardsEscapeHtml(subtitleRaw);

    const filePath = w.file_path ? String(w.file_path).trim() : '';
    const hasPdf = !!filePath && filePath.startsWith('/api/pdfs/');
    const inferredKind = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(w) : '';
    // `suppressThumbnail` is for pages rendered from cached offline data: a
    // thumbnail is a PRKS-server request that cannot succeed there, and a
    // broken image is worse than none. Normal card appearance is untouched.
    const suppressThumbnail = options.suppressThumbnail === true;
    /* `w` is already the EFFECTIVE Work here -- its caller overlays pending
     * metadata before rendering -- so a pending page reaches the URL without
     * this file learning anything about durable operations. */
    const thumbPage = options.thumbPage != null ? options.thumbPage : w.thumb_page;
    const isVideoKind = !hasPdf && inferredKind === 'video';
    const thumbKindClass = isVideoKind ? 'work-card__thumb--video' : 'work-card__thumb--pdf';
    const thumbSrc = suppressThumbnail
        ? ''
        : hasPdf
          ? prksWorkThumbUrl(w.id, thumbPage)
          : isVideoKind && w.thumb_url
            ? String(w.thumb_url).trim()
            : '';

    let thumbHtml;
    if (thumbSrc) {
        thumbHtml =
            `<div class="work-card__thumb ${thumbKindClass} work-card__thumb--loading" data-prks-thumb-state="loading"` +
            ` data-prks-thumb-preview-src="${prksWorkCardsEscapeHtml(thumbSrc)}"` +
            ` data-prks-thumb-preview-kind="${isVideoKind ? 'video' : 'pdf'}">` +
            `<img loading="lazy" alt="" src="${PRKS_WORK_THUMB_PLACEHOLDER}" data-prks-thumb-src="${prksWorkCardsEscapeHtml(
                thumbSrc
            )}" />` +
            `</div>`;
    } else {
        const emptyLabel = suppressThumbnail
            ? 'empty'
            : 'empty';
        const emptyTitle = suppressThumbnail
            ? 'Preview not available offline'
            : isVideoKind
              ? 'No video preview'
              : 'No preview';
        thumbHtml =
            `<div class="work-card__thumb work-card__thumb--empty ${thumbKindClass}"` +
            ` data-prks-thumb-state="${emptyLabel}" title="${prksWorkCardsEscapeHtml(emptyTitle)}"` +
            ` aria-hidden="true"></div>`;
    }

    const fileSizeHtml = prksWorkFileSizeMbHtml(w);

    // Bibliographic identity only — stable Author/Editor + year. Route-specific
    // context (added/opened date, abstract excerpt, Person credit) is a
    // separate, lower-emphasis line so it never competes with who-wrote-it/when.
    const metaChunks = [];
    const credit = prksWorkCardCreditLine(w);
    if (credit) metaChunks.push(credit);
    const yearPlain = prksWorkCardYearPlain(w);
    if (yearPlain) metaChunks.push(yearPlain);
    const metaHtml = metaChunks.length
        ? `<div class="meta-row work-card__meta">${metaChunks.join(' · ')}</div>`
        : '';
    const contextHtml = subtitle ? `<div class="work-card__context">${subtitle}</div>` : '';

    return `
        <div class="project-card project-card--work-card" data-work-id="${wid}" data-prks-route="#/works/${wid}" data-prks-middleclick-nav="1" role="link" tabindex="0" aria-label="${title}">
            ${thumbHtml}
            <div class="work-card__body">
                <div class="card-title" title="${title}">${title}</div>
                ${metaHtml}
                ${contextHtml}
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

/* ---------- Quick preview (hover / keyboard; same thumb URL, no reader) ---------- */

function prksWorkThumbPreviewEl() {
    let el = document.getElementById('prks-work-thumb-preview');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'prks-work-thumb-preview';
    el.className = 'work-card-preview';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Work preview');
    el.hidden = true;
    el.innerHTML =
        '<div class="work-card-preview__frame work-card-preview__frame--pdf">' +
        '<img class="work-card-preview__img" alt="" />' +
        '</div>';
    document.body.appendChild(el);
    return el;
}

function prksHideWorkThumbPreview() {
    const el = document.getElementById('prks-work-thumb-preview');
    if (!el) return;
    el.hidden = true;
    el.classList.remove('work-card-preview--visible');
    const img = el.querySelector('.work-card-preview__img');
    if (img) {
        img.removeAttribute('src');
        img.removeAttribute('data-prks-preview-src');
    }
    window.__prksWorkThumbPreviewSource = null;
}

function prksPositionWorkThumbPreview(el, anchor) {
    if (!el || !anchor || typeof anchor.getBoundingClientRect !== 'function') return;
    const rect = anchor.getBoundingClientRect();
    const margin = 8;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    el.style.visibility = 'hidden';
    el.hidden = false;
    el.classList.add('work-card-preview--visible');
    const pw = el.offsetWidth || 320;
    const ph = el.offsetHeight || 240;
    let left = rect.right + margin;
    let top = rect.top;
    if (left + pw + margin > vw) {
        left = rect.left - pw - margin;
    }
    if (left < margin) left = Math.max(margin, (vw - pw) / 2);
    if (top + ph + margin > vh) {
        top = Math.max(margin, vh - ph - margin);
    }
    if (top < margin) top = margin;
    el.style.left = Math.round(left) + 'px';
    el.style.top = Math.round(top) + 'px';
    el.style.visibility = '';
}

/**
 * Show a larger preview of an existing thumb asset. Does not open the Work.
 * Uses the same URL already known for the card — no per-Work API.
 * @param {Element} thumbEl
 */
function prksShowWorkThumbPreview(thumbEl) {
    if (!thumbEl || !thumbEl.getAttribute) return;
    if (thumbEl.classList.contains('work-card__thumb--empty')) return;
    if (thumbEl.classList.contains('work-card__thumb--error')) return;
    const src =
        String(thumbEl.getAttribute('data-prks-thumb-preview-src') || '').trim() ||
        (() => {
            const img = thumbEl.querySelector('img');
            if (!img) return '';
            const lazy = String(img.getAttribute('data-prks-thumb-src') || '').trim();
            if (lazy) return lazy;
            const cur = String(img.getAttribute('src') || '').trim();
            return cur && cur !== PRKS_WORK_THUMB_PLACEHOLDER ? cur : '';
        })();
    if (!src) return;
    const kind = String(thumbEl.getAttribute('data-prks-thumb-preview-kind') || 'pdf');
    const el = prksWorkThumbPreviewEl();
    const frame = el.querySelector('.work-card-preview__frame');
    const img = el.querySelector('.work-card-preview__img');
    if (!img || !frame) return;
    frame.classList.toggle('work-card-preview__frame--pdf', kind !== 'video');
    frame.classList.toggle('work-card-preview__frame--video', kind === 'video');
    if (img.getAttribute('data-prks-preview-src') !== src) {
        img.setAttribute('data-prks-preview-src', src);
        img.setAttribute('src', src);
    }
    window.__prksWorkThumbPreviewSource = thumbEl;
    prksPositionWorkThumbPreview(el, thumbEl);
}

function prksWorkThumbFromCard(card) {
    if (!card || !card.querySelector) return null;
    return card.querySelector('.work-card__thumb[data-prks-thumb-preview-src]');
}

if (typeof document !== 'undefined' && !window.__prksWorkCardKeyNavBound) {
    window.__prksWorkCardKeyNavBound = true;
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            const preview = document.getElementById('prks-work-thumb-preview');
            if (preview && !preview.hidden) {
                e.preventDefault();
                prksHideWorkThumbPreview();
                return;
            }
        }
        /* Preview without navigating: P while a Work card is focused. */
        if (
            (e.key === 'p' || e.key === 'P') &&
            !e.metaKey &&
            !e.ctrlKey &&
            !e.altKey
        ) {
            const t = e.target;
            if (t && t.closest && t.closest('input, button, a, textarea, select, [contenteditable="true"]')) {
                return;
            }
            const card =
                t && t.closest
                    ? t.closest('.project-card--work-card[data-prks-route][role="link"]')
                    : null;
            if (card && t === card) {
                const thumb = prksWorkThumbFromCard(card);
                if (thumb) {
                    e.preventDefault();
                    prksShowWorkThumbPreview(thumb);
                    return;
                }
            }
        }
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const t = e.target;
        if (!t || !t.closest) return;
        if (t.closest('input, button, a, textarea, select, [contenteditable="true"]')) return;
        /* Bulk selection owns Enter/Space on Work cards (toggle, not navigate). */
        if (typeof window.prksWorkSelectionIsActive === 'function' && window.prksWorkSelectionIsActive()) {
            return;
        }
        if (document.body && document.body.classList.contains('prks-bulk-selection-active')) {
            return;
        }
        const card = t.closest('.project-card--work-card[data-prks-route][role="link"]');
        if (!card || t !== card) return;
        const hash = card.getAttribute('data-prks-route');
        if (!hash) return;
        e.preventDefault();
        prksHideWorkThumbPreview();
        if (typeof window.prksNavigate === 'function') window.prksNavigate(hash);
    });

    document.addEventListener(
        'pointerover',
        function (e) {
            const t = e.target;
            if (!t || !t.closest) return;
            const thumb = t.closest('.work-card__thumb[data-prks-thumb-preview-src]');
            if (!thumb) return;
            if (window.matchMedia && window.matchMedia('(hover: none)').matches) return;
            prksShowWorkThumbPreview(thumb);
        },
        true
    );

    document.addEventListener(
        'pointerout',
        function (e) {
            const t = e.target;
            if (!t || !t.closest) return;
            const thumb = t.closest('.work-card__thumb[data-prks-thumb-preview-src]');
            if (!thumb) return;
            const related = e.relatedTarget;
            if (related && thumb.contains(related)) return;
            const preview = document.getElementById('prks-work-thumb-preview');
            if (preview && related && preview.contains(related)) return;
            if (window.__prksWorkThumbPreviewSource === thumb) prksHideWorkThumbPreview();
        },
        true
    );

    document.addEventListener(
        'focusout',
        function () {
            /* Keep keyboard preview until Escape or another card action. */
        },
        true
    );

    window.addEventListener('scroll', function () {
        if (window.__prksWorkThumbPreviewSource) prksHideWorkThumbPreview();
    }, true);

    window.addEventListener('resize', function () {
        if (window.__prksWorkThumbPreviewSource) prksHideWorkThumbPreview();
    });
}

window.prksInitLazyWorkThumbs = prksInitLazyWorkThumbs;
window.prksWorkCardCreditText = prksWorkCardCreditText;
window.prksWorkCardCreditLine = prksWorkCardCreditLine;
window.prksGetWorkBrowseMode = prksGetWorkBrowseMode;
window.prksSetWorkBrowseMode = prksSetWorkBrowseMode;
window.prksWorkBrowseCollectionClass = prksWorkBrowseCollectionClass;
window.prksWorkBrowseModeToggleHtml = prksWorkBrowseModeToggleHtml;
window.prksBindWorkBrowseMode = prksBindWorkBrowseMode;
window.prksApplyWorkBrowseModeToDom = prksApplyWorkBrowseModeToDom;
window.prksShowWorkThumbPreview = prksShowWorkThumbPreview;
window.prksHideWorkThumbPreview = prksHideWorkThumbPreview;
