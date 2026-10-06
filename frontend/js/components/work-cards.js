function prksWorkCardsEscapeHtml(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
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

/** PDF managed thumbs: `/api/works/<id>/thumbnail?page=<n>` only. */

/**
 * Allowlist a Work thumb URL before assigning it to an <img src>.
 * DOM attributes are untrusted once in the page; reject javascript:/data:/etc.
 * Rebuilds a fresh string from validated parts so DOM text never flows into src.
 * @param {string} raw
 * @returns {string} safe URL or ''
 */
function prksSafeWorkThumbSrc(raw) {
    const src = String(raw == null ? '' : raw).trim();
    if (!src || src === PRKS_WORK_THUMB_PLACEHOLDER) return '';

    const pdfMatch = src.match(/^\/api\/works\/([^/?#]+)\/thumbnail\?page=(\d+)$/);
    if (pdfMatch) {
        let workId = pdfMatch[1];
        try {
            workId = decodeURIComponent(workId);
        } catch (_err) {
            return '';
        }
        if (!workId || /[/?#]/.test(workId)) return '';
        const page = String(parseInt(pdfMatch[2], 10));
        if (!/^\d+$/.test(page) || page === '0') return '';
        return '/api/works/' + encodeURIComponent(workId) + '/thumbnail?page=' + page;
    }

    // Relative non-PDF paths are never Work thumbs.
    if (src.charAt(0) === '/' || src.charAt(0) === '.') return '';

    try {
        const u = new URL(src);
        const proto = String(u.protocol || '').toLowerCase();
        if (proto !== 'http:' && proto !== 'https:') return '';
        if (u.username || u.password) return '';
        // Reconstruct so the returned value is not the tainted DOM string.
        return u.protocol + '//' + u.host + u.pathname + u.search + u.hash;
    } catch (_err) {
        return '';
    }
}

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
                    ` data-value="${prksWorkCardsEscapeHtml(l)}" aria-checked="${on ? 'true' : 'false'}" role="radio">${prksWorkCardsEscapeHtml(l)}</button>`
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
            btn.setAttribute('aria-checked', on ? 'true' : 'false');
        });
    });
}

/**
 * Bind Cards|List toggles under root. Mode change persists and reclasses every
 * mounted Work collection (document-wide), so split panes stay in sync.
 * Optional onModeChange for surfaces that need a full re-paint.
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
                // Global preference — sync every mounted browse surface, not only host.
                prksApplyWorkBrowseModeToDom(document, next);
                if (typeof opts.onModeChange === 'function') opts.onModeChange(next);
                if (typeof window.prksInitLazyWorkThumbs === 'function') {
                    window.prksInitLazyWorkThumbs(document);
                }
            });
        });
    });
}

function prksWorkThumbSrcMap() {
    if (!window.__prksWorkThumbSrcByEl && typeof WeakMap !== 'undefined') {
        window.__prksWorkThumbSrcByEl = new WeakMap();
    }
    return window.__prksWorkThumbSrcByEl || null;
}

/** Remember a rebuilt (untainted) thumb URL for preview/hydrate — never read URL attrs back. */
function prksRememberWorkThumbSrc(thumbEl, src) {
    const map = prksWorkThumbSrcMap();
    if (!map || !thumbEl || !src) return;
    map.set(thumbEl, src);
}

function prksRecallWorkThumbSrc(thumbEl) {
    const map = prksWorkThumbSrcMap();
    if (!map || !thumbEl) return '';
    const src = map.get(thumbEl);
    return src ? String(src) : '';
}

/**
 * Work-id → allowlisted thumb URL, registered at card HTML build from Work data
 * (never from DOM attributes). Video thumbs use this; PDFs rebuild from id+page.
 */
function prksWorkThumbUrlByWorkId() {
    if (!window.__prksWorkThumbUrlByWorkId) {
        window.__prksWorkThumbUrlByWorkId = new Map();
    }
    return window.__prksWorkThumbUrlByWorkId;
}

function prksRegisterWorkThumbUrl(workId, rawSrc) {
    const id = String(workId == null ? '' : workId).trim();
    if (!id) return '';
    const safe = prksSafeWorkThumbSrc(rawSrc);
    if (!safe) {
        prksWorkThumbUrlByWorkId().delete(id);
        return '';
    }
    prksWorkThumbUrlByWorkId().set(id, safe);
    return safe;
}

function prksLookupRegisteredWorkThumbUrl(workId) {
    const id = String(workId == null ? '' : workId).trim();
    if (!id) return '';
    const src = prksWorkThumbUrlByWorkId().get(id);
    return src ? String(src) : '';
}

function prksPreviewImgSrcMap() {
    if (!window.__prksPreviewSrcByImg && typeof WeakMap !== 'undefined') {
        window.__prksPreviewSrcByImg = new WeakMap();
    }
    return window.__prksPreviewSrcByImg || null;
}

function prksRememberPreviewImgSrc(img, src) {
    const map = prksPreviewImgSrcMap();
    if (!map || !img || !src) return;
    map.set(img, src);
}

function prksRecallPreviewImgSrc(img) {
    const map = prksPreviewImgSrcMap();
    if (!map || !img) return '';
    const src = map.get(img);
    return src ? String(src) : '';
}

/**
 * Resolve the thumb URL for an already-rendered thumb slot without reading a
 * URL-bearing attribute back into an HTML sink (CodeQL js/xss-through-dom).
 * PDF: rebuild from card data-work-id + digit-only data-prks-thumb-page.
 * Video/other: Map registered at card-build from allowlisted Work thumb_url.
 */
function prksResolveWorkThumbSrc(thumbEl) {
    if (!thumbEl || !thumbEl.getAttribute) return '';
    if (thumbEl.classList && thumbEl.classList.contains('work-card__thumb--empty')) return '';
    const remembered = prksRecallWorkThumbSrc(thumbEl);
    if (remembered) return remembered;

    const card =
        typeof thumbEl.closest === 'function'
            ? thumbEl.closest('.project-card--work-card[data-work-id]')
            : null;
    if (!card) return '';
    const workId = String(card.getAttribute('data-work-id') || '').trim();
    if (!workId) return '';

    const kind = String(thumbEl.getAttribute('data-prks-thumb-preview-kind') || '');
    if (kind !== 'video' && kind !== 'pdf') return '';
    if (kind === 'video') {
        const registered = prksLookupRegisteredWorkThumbUrl(workId);
        if (registered) prksRememberWorkThumbSrc(thumbEl, registered);
        return registered;
    }

    // PDF: page must be stated on the thumb; never invent page 1 from a bare slot.
    const pageRaw = String(thumbEl.getAttribute('data-prks-thumb-page') || '').trim();
    if (!/^\d+$/.test(pageRaw)) return '';
    const page = parseInt(pageRaw, 10);
    if (!Number.isFinite(page) || page < 1) return '';
    const src = prksWorkThumbUrl(workId, page);
    if (src) prksRememberWorkThumbSrc(thumbEl, src);
    return src;
}

function prksAssignImgSrc(img, src) {
    if (!img || !src) return;
    // Property assignment of a rebuilt/registered URL — never setAttribute with DOM text.
    img.src = src;
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
    const thumb = img.closest('.work-card__thumb');
    img.removeAttribute('data-prks-thumb-lazy');
    // Never read a URL from DOM attributes into src — resolve via rebuild/Map only.
    const src = thumb ? prksResolveWorkThumbSrc(thumb) : '';
    if (!src) {
        if (thumb) prksSetWorkThumbState(thumb, 'error');
        img.remove();
        return;
    }
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
    prksAssignImgSrc(img, src);
    img.dataset.prksThumbLoaded = '1';
}

/**
 * Tracked targets for the singleton lazy-thumb IntersectionObserver.
 * Needed because observer.unobserve only runs on intersect today would leave
 * detached (never-scrolled-into-view) card trees retained across route/tab
 * replacements in a long-running session.
 */
function prksObservedLazyWorkThumbs() {
    if (!window.__prksWorkThumbObserved) {
        window.__prksWorkThumbObserved = new Set();
    }
    return window.__prksWorkThumbObserved;
}

function prksUnobserveLazyWorkThumb(img, observer) {
    if (!img) return;
    const obs = observer || window.__prksWorkThumbObserver;
    if (obs) {
        try {
            obs.unobserve(img);
        } catch (_err) {
            /* Target may already be unknown to the observer. */
        }
    }
    prksObservedLazyWorkThumbs().delete(img);
    if (typeof img.removeAttribute === 'function') {
        img.removeAttribute('data-prks-thumb-observing');
    } else if (img.dataset) {
        delete img.dataset.prksThumbObserving;
    }
}

/** Drop observer targets that are no longer in the document. */
function prksPruneDisconnectedLazyWorkThumbs() {
    const obs = window.__prksWorkThumbObserver;
    if (!obs) return;
    for (const img of Array.from(prksObservedLazyWorkThumbs())) {
        if (!img || !img.isConnected) {
            prksUnobserveLazyWorkThumb(img, obs);
        }
    }
}

/**
 * Explicit teardown for a browse subtree that is about to be unmounted or
 * replaced. Pass a root to release only its descendants; omit to release all
 * tracked lazy thumbs.
 * @param {ParentNode|null} [root]
 */
function prksReleaseLazyWorkThumbs(root) {
    const obs = window.__prksWorkThumbObserver;
    if (!obs) return;
    const tracked = prksObservedLazyWorkThumbs();
    if (root && typeof root.querySelectorAll === 'function') {
        root.querySelectorAll('img[data-prks-thumb-observing], img[data-prks-thumb-lazy]').forEach((img) => {
            if (tracked.has(img) || (img.dataset && img.dataset.prksThumbObserving === '1')) {
                prksUnobserveLazyWorkThumb(img, obs);
            }
        });
        return;
    }
    for (const img of Array.from(tracked)) {
        prksUnobserveLazyWorkThumb(img, obs);
    }
}

function prksLazyThumbObserver() {
    if (!('IntersectionObserver' in window)) return null;
    if (!window.__prksWorkThumbObserver) {
        window.__prksWorkThumbObserver = new IntersectionObserver(
            (entries, obs) => {
                entries.forEach((entry) => {
                    if (!entry || !entry.target) return;
                    // Detached trees must not stay observed after route/tab replace.
                    if (!entry.target.isConnected) {
                        prksUnobserveLazyWorkThumb(entry.target, obs);
                        return;
                    }
                    if (!entry.isIntersecting) return;
                    prksHydrateLazyWorkThumb(entry.target);
                    prksUnobserveLazyWorkThumb(entry.target, obs);
                });
            },
            { root: null, rootMargin: '240px 0px', threshold: 0.01 }
        );
    }
    return window.__prksWorkThumbObserver;
}

function prksInitLazyWorkThumbs(root) {
    // Always prune first so a replace that already detached prior cards cannot
    // accumulate observed targets across navigations.
    prksPruneDisconnectedLazyWorkThumbs();
    const host = root && typeof root.querySelectorAll === 'function' ? root : document;
    const imgs = host.querySelectorAll('img[data-prks-thumb-lazy]');
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
        prksObservedLazyWorkThumbs().add(img);
    });
}

/* ---------- Quick preview (hover / keyboard; same thumb URL, no reader) ---------- */

function prksWorkThumbPreviewEl() {
    let el = document.getElementById('prks-work-thumb-preview');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'prks-work-thumb-preview';
    el.className = 'work-card-preview';
    // Decorative enlarge of an existing thumb — not a dialog/AT landmark.
    el.setAttribute('aria-hidden', 'true');
    el.hidden = true;
    const frame = document.createElement('div');
    frame.className = 'work-card-preview__frame work-card-preview__frame--pdf';
    const img = document.createElement('img');
    img.className = 'work-card-preview__img';
    img.alt = '';
    frame.appendChild(img);
    el.appendChild(frame);
    document.body.appendChild(el);
    return el;
}

function prksForgetPreviewImgSrc(img) {
    const map = prksPreviewImgSrcMap();
    if (map && img) map.delete(img);
}

function prksHideWorkThumbPreview() {
    const el = document.getElementById('prks-work-thumb-preview');
    if (!el) return;
    el.hidden = true;
    el.classList.remove('work-card-preview--visible');
    const img = el.querySelector('.work-card-preview__img');
    if (img) {
        // Clear both the live src and the WeakMap recall so the next show of
        // the same URL re-enters the assign/createElement path (otherwise
        // recall === src skips assign and the frame stays blank).
        img.removeAttribute('src');
        prksForgetPreviewImgSrc(img);
    }
    window.__prksWorkThumbPreviewSource = null;
}

/**
 * Dismiss the quick preview when its owning browse subtree is about to be
 * replaced or unmounted. Scoped to `root` so another mounted pane's preview
 * is left alone. With no root, dismiss only if the source left the document.
 * @param {ParentNode|null} [root]
 */
function prksWorkThumbPreviewSourceConnected(src) {
    if (!src) return false;
    if (typeof src.isConnected === 'boolean') return src.isConnected;
    return !!(typeof document !== 'undefined' && document.contains && document.contains(src));
}

function prksWorkThumbPreviewSourceUnder(root, src) {
    if (!root || !src) return false;
    if (typeof root.contains === 'function') return !!root.contains(src);
    let n = src;
    while (n) {
        if (n === root) return true;
        n = n.parentNode;
    }
    return false;
}

function prksReleaseWorkThumbPreview(root) {
    const src = window.__prksWorkThumbPreviewSource;
    if (!src) return;
    // Folder→Folder preserve keeps the shell and may detach the previous
    // thumb before a contains(root) check. A disconnected source is this
    // pane's leftover body-mounted preview, not another tile's live one.
    if (root) {
        if (prksWorkThumbPreviewSourceUnder(root, src) || !prksWorkThumbPreviewSourceConnected(src)) {
            prksHideWorkThumbPreview();
        }
        return;
    }
    if (!prksWorkThumbPreviewSourceConnected(src)) prksHideWorkThumbPreview();
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
 * Resolves the URL without reading URL-bearing DOM attributes into an HTML sink.
 * @param {Element} thumbEl
 */
function prksShowWorkThumbPreview(thumbEl) {
    if (!thumbEl || !thumbEl.getAttribute) return;
    if (thumbEl.classList.contains('work-card__thumb--empty')) return;
    if (thumbEl.classList.contains('work-card__thumb--error')) return;
    const src = prksResolveWorkThumbSrc(thumbEl);
    if (!src) return;
    const kindRaw = String(thumbEl.getAttribute('data-prks-thumb-preview-kind') || 'pdf');
    const kind = kindRaw === 'video' ? 'video' : 'pdf';
    const el = prksWorkThumbPreviewEl();
    const frame = el.querySelector('.work-card-preview__frame');
    let img = el.querySelector('.work-card-preview__img');
    if (!frame) return;
    frame.classList.toggle('work-card-preview__frame--pdf', kind !== 'video');
    frame.classList.toggle('work-card-preview__frame--video', kind === 'video');
    // Fresh <img> each distinct src: createElement + property assign; never
    // setAttribute('src', …) with anything that touched the DOM as text.
    // Same-URL reopen after hide: WeakMap was cleared, so recall !== src and
    // we recreate; if an img is reused with matching recall but empty src,
    // re-assign so the frame is never blank.
    if (!img || prksRecallPreviewImgSrc(img) !== src) {
        const fresh = document.createElement('img');
        fresh.className = 'work-card-preview__img';
        fresh.alt = '';
        prksAssignImgSrc(fresh, src);
        prksRememberPreviewImgSrc(fresh, src);
        if (img) frame.replaceChild(fresh, img);
        else frame.appendChild(fresh);
        img = fresh;
    } else {
        prksAssignImgSrc(img, src);
    }
    window.__prksWorkThumbPreviewSource = thumbEl;
    prksPositionWorkThumbPreview(el, thumbEl);
}

function prksWorkThumbFromCard(card) {
    if (!card || !card.querySelector) return null;
    return card.querySelector('.work-card__thumb[data-prks-thumb-preview-kind]');
}

function prksWorkCardFromNode(t) {
    if (!t || !t.closest) return null;
    return t.closest('.project-card--work-card[data-work-id]');
}

function prksWorkCardKeyBlocked(t) {
    if (!t || !t.closest) return true;
    if (t.closest('input, button, textarea, select, [contenteditable="true"]')) return true;
    const a = t.closest('a');
    if (!a) return false;
    return !a.classList.contains('work-card__link');
}

function prksWorkCardFromEventTarget(t) {
    if (prksWorkCardKeyBlocked(t)) return null;
    return prksWorkCardFromNode(t);
}

/**
 * Hover open/close is the thumbnail slot only (pre-#451 Work-card contract).
 * Keyboard P still opens from the native work-card__link.
 */
function prksWorkThumbFromHoverTarget(t) {
    if (!t || !t.closest) return null;
    if (t.closest('.work-card__select, input, button, textarea, select')) return null;
    return t.closest('.work-card__thumb[data-prks-thumb-preview-kind]');
}

function prksWorkThumbPointerStillInside(thumb, related) {
    if (!thumb || !related) return false;
    if (typeof thumb.contains === 'function' && thumb.contains(related)) return true;
    const preview = document.getElementById('prks-work-thumb-preview');
    return !!(preview && typeof preview.contains === 'function' && preview.contains(related));
}

function prksWorkCardBulkOwnsKeys() {
    if (typeof window.prksWorkSelectionIsActive === 'function' && window.prksWorkSelectionIsActive()) {
        return true;
    }
    return !!(document.body && document.body.classList.contains('prks-bulk-selection-active'));
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
        /* Preview without navigating: P while the Work card or its native link is focused. */
        if (
            (e.key === 'p' || e.key === 'P') &&
            !e.metaKey &&
            !e.ctrlKey &&
            !e.altKey
        ) {
            const card = prksWorkCardFromEventTarget(e.target);
            if (!card) return;
            const thumb = prksWorkThumbFromCard(card);
            if (thumb) {
                e.preventDefault();
                prksShowWorkThumbPreview(thumb);
            }
            return;
        }
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const card = prksWorkCardFromEventTarget(e.target);
        if (!card) return;
        /* Bulk selection owns Enter/Space on Work cards (toggle, not navigate). */
        if (prksWorkCardBulkOwnsKeys()) return;
        /* Enter on the native <a href> follows the hash; Space still opens the Work. */
        if (e.key === 'Enter') return;
        const hash =
            card.getAttribute('data-prks-route') ||
            (card.querySelector('a.work-card__link') || {}).getAttribute('href') ||
            '';
        if (!hash) return;
        e.preventDefault();
        prksHideWorkThumbPreview();
        if (typeof window.prksNavigate === 'function') window.prksNavigate(hash);
    });

    document.addEventListener(
        'pointerover',
        function (e) {
            const t = e.target;
            const thumb = prksWorkThumbFromHoverTarget(t);
            if (!thumb) return;
            if (t && t.closest && t.closest('[aria-busy="true"]')) return;
            if (window.matchMedia && window.matchMedia('(hover: none)').matches) return;
            prksShowWorkThumbPreview(thumb);
        },
        true
    );

    document.addEventListener(
        'pointerout',
        function (e) {
            const thumb = prksWorkThumbFromHoverTarget(e.target);
            if (!thumb) return;
            if (prksWorkThumbPointerStillInside(thumb, e.relatedTarget)) return;
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

    if (typeof window.addEventListener === 'function') {
        window.addEventListener(
            'scroll',
            function () {
                if (window.__prksWorkThumbPreviewSource) prksHideWorkThumbPreview();
            },
            true
        );

        window.addEventListener('resize', function () {
            if (window.__prksWorkThumbPreviewSource) prksHideWorkThumbPreview();
        });
    }
}

window.prksInitLazyWorkThumbs = prksInitLazyWorkThumbs;
window.prksReleaseLazyWorkThumbs = prksReleaseLazyWorkThumbs;
window.prksSafeWorkThumbSrc = prksSafeWorkThumbSrc;
window.prksRegisterWorkThumbUrl = prksRegisterWorkThumbUrl;
window.prksLookupRegisteredWorkThumbUrl = prksLookupRegisteredWorkThumbUrl;
window.prksResolveWorkThumbSrc = prksResolveWorkThumbSrc;
window.prksGetWorkBrowseMode = prksGetWorkBrowseMode;
window.prksSetWorkBrowseMode = prksSetWorkBrowseMode;
window.prksWorkBrowseCollectionClass = prksWorkBrowseCollectionClass;
window.prksWorkBrowseModeToggleHtml = prksWorkBrowseModeToggleHtml;
window.prksBindWorkBrowseMode = prksBindWorkBrowseMode;
window.prksApplyWorkBrowseModeToDom = prksApplyWorkBrowseModeToDom;
window.prksShowWorkThumbPreview = prksShowWorkThumbPreview;
window.prksHideWorkThumbPreview = prksHideWorkThumbPreview;
window.prksReleaseWorkThumbPreview = prksReleaseWorkThumbPreview;
