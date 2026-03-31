function escapeHtmlTagPage(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** Return a safe CSS color string from a user-supplied value. Only hex colors and a
 *  short allowlist of CSS keywords are passed through; everything else falls back to
 *  the default accent colour. This prevents CSS injection via tag/publisher colors. */
function prksSafeCssColor(raw, fallback) {
    const val = String(raw || '').trim();
    const fb = fallback || '#6d6cf7';
    if (/^#[0-9a-fA-F]{3}$/.test(val)) return val;
    if (/^#[0-9a-fA-F]{6}$/.test(val)) return val;
    if (/^#[0-9a-fA-F]{8}$/.test(val)) return val;
    const SAFE_KEYWORDS = new Set([
        'red','green','blue','yellow','orange','purple','pink','brown','black',
        'white','gray','grey','cyan','magenta','lime','maroon','navy','olive',
        'teal','aqua','fuchsia','silver',
    ]);
    if (SAFE_KEYWORDS.has(val.toLowerCase())) return val;
    return fb;
}

function wireTagCloudNavigation(root) {
    if (!root) return;
    root.onclick = (e) => {
        const el = e.target.closest('[data-tag-nav]');
        if (!el) return;
        const enc = el.getAttribute('data-tag-nav');
        if (enc != null) window.location.hash = '#/search?tag=' + enc;
    };
    root.onkeydown = (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const el = e.target.closest('[data-tag-nav]');
        if (!el) return;
        e.preventDefault();
        const enc = el.getAttribute('data-tag-nav');
        if (enc != null) window.location.hash = '#/search?tag=' + enc;
    };
}

const prksTagsPageCtx = { tags: [], selectedId: null, containerEl: null, mergeSourceId: null, mergeTargetId: null };

function prksTagsPageSelectedTag() {
    return prksTagsPageCtx.tags.find((t) => t.id === prksTagsPageCtx.selectedId) || null;
}

let prksTagsAliasEscapeHandler = null;
let prksTagsAliasLastFocus = null;

function prksCloseTagsAliasModal() {
    const bd = document.getElementById('tags-page-alias-backdrop');
    const md = document.getElementById('tags-page-alias-modal');
    if (bd) bd.classList.add('hidden');
    if (md) md.classList.add('hidden');
    if (prksTagsAliasEscapeHandler) {
        document.removeEventListener('keydown', prksTagsAliasEscapeHandler);
        prksTagsAliasEscapeHandler = null;
    }
    prksTagsPageCtx.selectedId = null;
    if (prksTagsAliasLastFocus && typeof prksTagsAliasLastFocus.focus === 'function') {
        try {
            prksTagsAliasLastFocus.focus();
        } catch (_e) {}
    }
    prksTagsAliasLastFocus = null;
}

function prksRenderTagsPageAliasModal() {
    const nameEl = document.getElementById('tags-page-alias-canonical');
    const ul = document.getElementById('tags-page-alias-list');
    const input = document.getElementById('tags-page-alias-input');
    const tag = prksTagsPageSelectedTag();
    if (!tag) return;
    if (nameEl) nameEl.textContent = tag.name || '';
    const aliases = Array.isArray(tag.aliases) ? tag.aliases : [];
    if (ul) {
        if (aliases.length === 0) {
            ul.innerHTML = '<li class="tags-page-alias-list__none">No aliases yet.</li>';
        } else {
            ul.innerHTML = aliases
                .map(
                    (a) =>
                        `<li class="tags-page-alias-list__item"><span class="tags-page-alias-list__text">${escapeHtmlTagPage(a)}</span>` +
                        `<button type="button" class="tags-page-alias-remove" data-alias-remove="${escapeHtmlTagPage(a)}" aria-label="Remove alias">×</button></li>`
                )
                .join('');
        }
    }
    if (input) input.value = '';
}

function prksOpenTagsAliasModal(triggerEl) {
    const bd = document.getElementById('tags-page-alias-backdrop');
    const md = document.getElementById('tags-page-alias-modal');
    if (!bd || !md) return;
    prksTagsAliasLastFocus = triggerEl && triggerEl instanceof Element ? triggerEl : null;
    bd.classList.remove('hidden');
    md.classList.remove('hidden');
    prksRenderTagsPageAliasModal();
    const input = document.getElementById('tags-page-alias-input');
    if (input) {
        setTimeout(() => input.focus(), 0);
    }
    prksTagsAliasEscapeHandler = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksCloseTagsAliasModal();
        }
    };
    document.addEventListener('keydown', prksTagsAliasEscapeHandler);
}

let prksTagsMergeEscapeHandler = null;
let prksTagsMergeLastFocus = null;

function prksTagMergeSource() {
    return prksTagsPageCtx.tags.find((t) => t.id === prksTagsPageCtx.mergeSourceId) || null;
}

function prksTagMergeTarget() {
    return prksTagsPageCtx.tags.find((t) => t.id === prksTagsPageCtx.mergeTargetId) || null;
}

function prksCloseTagsMergeModal() {
    const bd = document.getElementById('tags-page-merge-backdrop');
    const md = document.getElementById('tags-page-merge-modal');
    if (bd) bd.classList.add('hidden');
    if (md) md.classList.add('hidden');
    if (prksTagsMergeEscapeHandler) {
        document.removeEventListener('keydown', prksTagsMergeEscapeHandler);
        prksTagsMergeEscapeHandler = null;
    }
    prksTagsPageCtx.mergeSourceId = null;
    prksTagsPageCtx.mergeTargetId = null;
    if (prksTagsMergeLastFocus && typeof prksTagsMergeLastFocus.focus === 'function') {
        try {
            prksTagsMergeLastFocus.focus();
        } catch (_e) {}
    }
    prksTagsMergeLastFocus = null;
}

function prksTagsMergeFilteredCandidates(filter) {
    const src = prksTagsPageCtx.mergeSourceId;
    const q = (filter || '').trim().toLowerCase();
    return prksTagsPageCtx.tags.filter((t) => {
        if (t.id === src) return false;
        if (!q) return true;
        const n = (t.name || '').toLowerCase();
        return n.includes(q);
    });
}

function prksRenderTagsMergeModal() {
    const src = prksTagMergeSource();
    const tgt = prksTagMergeTarget();
    const filterEl = document.getElementById('tags-page-merge-filter');
    const listEl = document.getElementById('tags-page-merge-target-list');
    const pickBlock = document.getElementById('tags-page-merge-pick');
    const confirmBlock = document.getElementById('tags-page-merge-confirm');
    const confirmText = document.getElementById('tags-page-merge-confirm-text');
    const sourceLabel = document.getElementById('tags-page-merge-source-label');
    if (!src) return;
    if (sourceLabel) {
        sourceLabel.innerHTML = `Merging: <strong>${escapeHtmlTagPage(src.name || '')}</strong>`;
    }
    if (prksTagsPageCtx.mergeTargetId && !tgt) {
        prksTagsPageCtx.mergeTargetId = null;
    }
    if (prksTagsPageCtx.mergeTargetId && tgt) {
        if (pickBlock) pickBlock.classList.add('hidden');
        if (confirmBlock) confirmBlock.classList.remove('hidden');
        if (confirmText) {
            confirmText.innerHTML = `<strong>${escapeHtmlTagPage(src.name || '')}</strong> will become an alias of <strong>${escapeHtmlTagPage(
                tgt.name || ''
            )}</strong>. It will no longer appear as a separate tag; searches and links using that name will use the same files as the target tag.`;
        }
    } else {
        if (pickBlock) pickBlock.classList.remove('hidden');
        if (confirmBlock) confirmBlock.classList.add('hidden');
        const q = filterEl ? String(filterEl.value || '') : '';
        const candidates = prksTagsMergeFilteredCandidates(q);
        if (listEl) {
            if (candidates.length === 0) {
                listEl.innerHTML =
                    '<li class="tags-page-merge-list__none">No other tags match. Try another search.</li>';
            } else {
                listEl.innerHTML = candidates
                    .map((t) => {
                        const idEsc = escapeHtmlTagPage(t.id);
                        const col = prksSafeCssColor(t.color, '#6d6cf7');
                        return (
                            `<li class="tags-page-merge-list__item">` +
                            `<button type="button" class="tags-page-merge-pick-btn" data-tag-merge-pick="${idEsc}" style="border-left: 3px solid ${col};">` +
                            `${escapeHtmlTagPage(t.name || '')}</button></li>`
                        );
                    })
                    .join('');
            }
        }
    }
}

function prksOpenTagsMergeModal(triggerEl) {
    const bd = document.getElementById('tags-page-merge-backdrop');
    const md = document.getElementById('tags-page-merge-modal');
    if (!bd || !md) return;
    prksTagsMergeLastFocus = triggerEl && triggerEl instanceof Element ? triggerEl : null;
    prksTagsPageCtx.mergeTargetId = null;
    bd.classList.remove('hidden');
    md.classList.remove('hidden');
    const filterEl = document.getElementById('tags-page-merge-filter');
    if (filterEl) filterEl.value = '';
    prksRenderTagsMergeModal();
    if (filterEl) {
        setTimeout(() => filterEl.focus(), 0);
    }
    prksTagsMergeEscapeHandler = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksCloseTagsMergeModal();
        }
    };
    document.addEventListener('keydown', prksTagsMergeEscapeHandler);
}

function prksWireTagsPageMergePanel() {
    const cloud = document.getElementById('tags-page-cloud');
    if (cloud) {
        cloud.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-tag-merge]');
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            const id = btn.getAttribute('data-tag-merge');
            prksTagsPageCtx.mergeSourceId = id;
            prksOpenTagsMergeModal(btn);
        });
    }

    const bd = document.getElementById('tags-page-merge-backdrop');
    if (bd) {
        bd.addEventListener('click', (e) => {
            if (e.target === bd) prksCloseTagsMergeModal();
        });
    }

    const closeBtn = document.getElementById('tags-page-merge-modal-close');
    if (closeBtn) {
        closeBtn.onclick = () => prksCloseTagsMergeModal();
    }

    const filterEl = document.getElementById('tags-page-merge-filter');
    if (filterEl) {
        filterEl.addEventListener('input', () => prksRenderTagsMergeModal());
    }

    const listEl = document.getElementById('tags-page-merge-target-list');
    if (listEl) {
        listEl.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-tag-merge-pick]');
            if (!btn) return;
            const id = btn.getAttribute('data-tag-merge-pick');
            prksTagsPageCtx.mergeTargetId = id;
            prksRenderTagsMergeModal();
        });
    }

    const backBtn = document.getElementById('tags-page-merge-back-btn');
    if (backBtn) {
        backBtn.onclick = () => {
            prksTagsPageCtx.mergeTargetId = null;
            prksRenderTagsMergeModal();
            const fe = document.getElementById('tags-page-merge-filter');
            if (fe) setTimeout(() => fe.focus(), 0);
        };
    }

    const confirmBtn = document.getElementById('tags-page-merge-confirm-btn');
    if (confirmBtn) {
        confirmBtn.onclick = async () => {
            const source = prksTagMergeSource();
            const target = prksTagMergeTarget();
            if (!source || !target) return;
            try {
                const res = await fetch('/api/tags/merge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        source_tag_id: source.id,
                        target_tag_id: target.id,
                    }),
                });
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Merge failed');
                window.__prksAllTagsCache = null;
                prksCloseTagsMergeModal();
                const wrap = prksTagsPageCtx.containerEl;
                if (wrap && typeof renderTagsPage === 'function') {
                    await renderTagsPage(wrap);
                }
                if (typeof refreshSidebarTags === 'function') void refreshSidebarTags();
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not merge tags.');
            }
        };
    }
}

function prksWireTagsPageAliasPanel(container) {
    const cloud = document.getElementById('tags-page-cloud');
    if (cloud) {
        cloud.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-tag-alias-edit]');
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            const id = btn.getAttribute('data-tag-alias-edit');
            prksTagsPageCtx.selectedId = id;
            prksOpenTagsAliasModal(btn);
        });
    }

    const bd = document.getElementById('tags-page-alias-backdrop');
    if (bd) {
        bd.addEventListener('click', (e) => {
            if (e.target === bd) prksCloseTagsAliasModal();
        });
    }

    const closeBtn = document.getElementById('tags-page-alias-modal-close');
    if (closeBtn) {
        closeBtn.onclick = () => prksCloseTagsAliasModal();
    }

    const addBtn = document.getElementById('tags-page-alias-add-btn');
    if (addBtn) {
        addBtn.onclick = async () => {
            const tag = prksTagsPageSelectedTag();
            const input = document.getElementById('tags-page-alias-input');
            if (!tag || !input) return;
            const alias = String(input.value || '').trim();
            if (!alias) return;
            try {
                const res = await fetch(`/api/tags/${encodeURIComponent(tag.id)}/aliases`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ alias }),
                });
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Failed to add alias');
                window.__prksAllTagsCache = null;
                const tags = await fetchTags({ used: true });
                prksTagsPageCtx.tags = tags;
                prksRenderTagsPageAliasModal();
                if (typeof refreshSidebarTags === 'function') void refreshSidebarTags();
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not add alias.');
            }
        };
    }

    const list = document.getElementById('tags-page-alias-list');
    if (list) {
        list.onclick = async (e) => {
            const btn = e.target.closest('[data-alias-remove]');
            if (!btn) return;
            const tag = prksTagsPageSelectedTag();
            if (!tag) return;
            const alias = btn.getAttribute('data-alias-remove');
            if (alias == null) return;
            try {
                const res = await fetch(
                    `/api/tags/${encodeURIComponent(tag.id)}/aliases?alias=${encodeURIComponent(alias)}`,
                    { method: 'DELETE' }
                );
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Failed to remove alias');
                window.__prksAllTagsCache = null;
                const tags = await fetchTags({ used: true });
                prksTagsPageCtx.tags = tags;
                prksRenderTagsPageAliasModal();
                if (typeof refreshSidebarTags === 'function') void refreshSidebarTags();
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not remove alias.');
            }
        };
    }
}

async function renderTagsPage(container) {
    prksTagsPageCtx.containerEl = container;
    const tags = await fetchTags({ used: true });
    prksTagsPageCtx.tags = tags;
    prksTagsPageCtx.selectedId = null;

    const totals = tags.map((t) => (Number(t.work_count) || 0) + (Number(t.folder_count) || 0));
    const minTotal = totals.length ? Math.min(...totals) : 0;
    const maxTotal = totals.length ? Math.max(...totals) : 0;

    const minScale = 0.85;
    const maxScale = 1.85;
    const logMin = Math.log10(minTotal + 1);
    const logMax = Math.log10(maxTotal + 1);
    const scaleForTotal = (total) => {
        const log = Math.log10((total || 0) + 1);
        if (logMax === logMin) return 1;
        const t = (log - logMin) / (logMax - logMin);
        return minScale + t * (maxScale - minScale);
    };

    let chips =
        tags.length === 0
            ? '<p class="tags-page__empty">No tags in use yet. Add tags to files or folders from the details panel.</p>'
            : tags
                  .map(
                      (t) =>
                          (() => {
                              const count = (Number(t.work_count) || 0) + (Number(t.folder_count) || 0);
                              const scale = scaleForTotal(count);
                              const borderLeftPx = 4 * scale;
                              const color = prksSafeCssColor(t.color, '#6d6cf7');
                              const idEsc = escapeHtmlTagPage(t.id);
                              return (
                                  `<span class="tag tag--page tag--page-with-actions" style="--tag-scale:${scale.toFixed(3)};border-left: ${borderLeftPx.toFixed(2)}px solid ${color};">` +
                                  `<span class="tag--page__nav" role="button" tabindex="0" data-tag-nav="${encodeURIComponent(t.name)}">${escapeHtmlTagPage(t.name)}</span>` +
                                  `<button type="button" class="tag-page-alias-btn" data-tag-alias-edit="${idEsc}" title="Aliases" aria-label="Edit aliases for ${escapeHtmlTagPage(t.name)}">⋯</button>` +
                                  `<button type="button" class="tag-page-merge-btn" data-tag-merge="${idEsc}" title="Merge into another tag" aria-label="Merge ${escapeHtmlTagPage(t.name)} into another tag">→</button>` +
                                  `</span>`
                              );
                          })()
                  )
                  .join('');

    container.innerHTML = `
        <div class="tags-page">
            <div class="page-header tags-page__header">
                <h2>All tags</h2>
                <p class="tags-page__sub">Tags currently used on at least one file or folder. Click a name to list matching files. Use ⋯ for alternate names (e.g. other languages). Use → to merge this tag into another; the merged name becomes an alias and no longer appears as its own tag.</p>
            </div>
            <div id="tags-page-cloud" class="tag-cloud tag-cloud--page">${chips}</div>
            <div id="tags-page-alias-backdrop" class="modal-backdrop hidden tags-page-alias-backdrop" role="presentation">
                <div id="tags-page-alias-modal" class="modal tags-page-alias-modal hidden" role="dialog" aria-modal="true" aria-labelledby="tags-page-alias-heading" tabindex="-1">
                    <div class="modal-header">
                        <h3 id="tags-page-alias-heading">Tag aliases</h3>
                        <button type="button" id="tags-page-alias-modal-close" class="close-btn" aria-label="Close">&times;</button>
                    </div>
                    <div class="modal-body tags-page-alias-modal__body">
                        <p class="modal-helper">Alternate names for this tag (same file set). Not shown as separate tags in the list.</p>
                        <p class="tags-page-alias-panel__for">Canonical name: <strong id="tags-page-alias-canonical"></strong></p>
                        <ul id="tags-page-alias-list" class="tags-page-alias-list"></ul>
                        <div class="tags-page-alias-add">
                            <input type="text" id="tags-page-alias-input" class="tags-page-alias-input" maxlength="120" placeholder="New alias…" autocomplete="off" aria-label="New alias">
                            <button type="button" id="tags-page-alias-add-btn" class="tags-page-alias-add__submit">Add alias</button>
                        </div>
                    </div>
                </div>
            </div>
            <div id="tags-page-merge-backdrop" class="modal-backdrop hidden tags-page-merge-backdrop" role="presentation">
                <div id="tags-page-merge-modal" class="modal tags-page-merge-modal hidden" role="dialog" aria-modal="true" aria-labelledby="tags-page-merge-heading" tabindex="-1">
                    <div class="modal-header">
                        <h3 id="tags-page-merge-heading">Merge tag</h3>
                        <button type="button" id="tags-page-merge-modal-close" class="close-btn" aria-label="Close">&times;</button>
                    </div>
                    <div class="modal-body tags-page-merge-modal__body">
                        <p class="modal-helper">Pick the tag that should stay. The tag you started from will be removed from the list; its name will resolve to the same files as the target.</p>
                        <p id="tags-page-merge-source-label" class="tags-page-merge-source-label"></p>
                        <div id="tags-page-merge-pick">
                            <label class="tags-page-merge-filter-label" for="tags-page-merge-filter">Merge into</label>
                            <input type="search" id="tags-page-merge-filter" class="tags-page-merge-filter" maxlength="120" placeholder="Search tags…" autocomplete="off" aria-label="Filter tags to merge into">
                            <ul id="tags-page-merge-target-list" class="tags-page-merge-list"></ul>
                        </div>
                        <div id="tags-page-merge-confirm" class="tags-page-merge-confirm hidden">
                            <p id="tags-page-merge-confirm-text" class="tags-page-merge-confirm-text"></p>
                            <div class="tags-page-merge-confirm-actions">
                                <button type="button" id="tags-page-merge-back-btn" class="tags-page-merge-back-btn">Back</button>
                                <button type="button" id="tags-page-merge-confirm-btn" class="tags-page-merge-confirm-btn">Merge</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
    wireTagCloudNavigation(document.getElementById('tags-page-cloud'));
    prksWireTagsPageAliasPanel(container);
    prksWireTagsPageMergePanel();
}

window.prksCloseTagsAliasModal = prksCloseTagsAliasModal;
window.prksCloseTagsMergeModal = prksCloseTagsMergeModal;
