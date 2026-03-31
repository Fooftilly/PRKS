function escapeHtmlPublishersPage(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const prksPublishersPageCtx = {
    publishers: [],
    selectedId: null,
    containerEl: null,
};

function prksPublishersPageSelected() {
    return prksPublishersPageCtx.publishers.find((p) => p.id === prksPublishersPageCtx.selectedId) || null;
}

let prksPublishersAliasEscapeHandler = null;
let prksPublishersAliasLastFocus = null;

function prksClosePublishersAliasModal() {
    const bd = document.getElementById('publishers-page-alias-backdrop');
    const md = document.getElementById('publishers-page-alias-modal');
    if (bd) bd.classList.add('hidden');
    if (md) md.classList.add('hidden');
    if (prksPublishersAliasEscapeHandler) {
        document.removeEventListener('keydown', prksPublishersAliasEscapeHandler);
        prksPublishersAliasEscapeHandler = null;
    }
    prksPublishersPageCtx.selectedId = null;
    if (prksPublishersAliasLastFocus && typeof prksPublishersAliasLastFocus.focus === 'function') {
        try {
            prksPublishersAliasLastFocus.focus();
        } catch (_e) {}
    }
    prksPublishersAliasLastFocus = null;
}

function prksRenderPublishersAliasModal() {
    const nameEl = document.getElementById('publishers-page-alias-canonical');
    const ul = document.getElementById('publishers-page-alias-list');
    const input = document.getElementById('publishers-page-alias-input');
    const pub = prksPublishersPageSelected();
    if (!pub) return;
    if (nameEl) nameEl.textContent = pub.name || '';
    const aliases = Array.isArray(pub.aliases) ? pub.aliases : [];
    if (ul) {
        if (aliases.length === 0) {
            ul.innerHTML = '<li class="tags-page-alias-list__none">No aliases yet.</li>';
        } else {
            ul.innerHTML = aliases
                .map(
                    (a) =>
                        `<li class="tags-page-alias-list__item"><span class="tags-page-alias-list__text">${escapeHtmlPublishersPage(a)}</span>` +
                        `<button type="button" class="tags-page-alias-remove" data-publisher-alias-remove="${escapeHtmlPublishersPage(a)}" aria-label="Remove alias">×</button></li>`
                )
                .join('');
        }
    }
    if (input) input.value = '';
}

function prksOpenPublishersAliasModal(triggerEl) {
    const bd = document.getElementById('publishers-page-alias-backdrop');
    const md = document.getElementById('publishers-page-alias-modal');
    if (!bd || !md) return;
    prksPublishersAliasLastFocus = triggerEl && triggerEl instanceof Element ? triggerEl : null;
    bd.classList.remove('hidden');
    md.classList.remove('hidden');
    prksRenderPublishersAliasModal();
    const input = document.getElementById('publishers-page-alias-input');
    if (input) setTimeout(() => input.focus(), 0);
    prksPublishersAliasEscapeHandler = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksClosePublishersAliasModal();
        }
    };
    document.addEventListener('keydown', prksPublishersAliasEscapeHandler);
}

function wirePublisherCloudNavigation(root) {
    if (!root) return;
    root.onclick = (e) => {
        const el = e.target.closest('[data-publisher-nav]');
        if (!el) return;
        const enc = el.getAttribute('data-publisher-nav');
        if (enc != null) window.location.hash = '#/search?publisher=' + enc;
    };
    root.onkeydown = (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const el = e.target.closest('[data-publisher-nav]');
        if (!el) return;
        e.preventDefault();
        const enc = el.getAttribute('data-publisher-nav');
        if (enc != null) window.location.hash = '#/search?publisher=' + enc;
    };
}

function prksEnsurePublishersPageDelegated(container) {
    if (!container || container._prksPublishersDelegated) return;
    container._prksPublishersDelegated = true;

    container.addEventListener('click', async (e) => {
        const bd = document.getElementById('publishers-page-alias-backdrop');
        if (e.target === bd) {
            prksClosePublishersAliasModal();
            return;
        }

        const closeBtn = e.target.closest('#publishers-page-alias-modal-close');
        if (closeBtn) {
            prksClosePublishersAliasModal();
            return;
        }

        const addBtn = e.target.closest('#publishers-page-add-btn');
        if (addBtn) {
            e.preventDefault();
            const input = document.getElementById('publishers-page-new-name');
            const name = input ? String(input.value || '').trim() : '';
            if (!name) return;
            try {
                const res = await fetch('/api/publishers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name }),
                });
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Failed to add publisher');
                if (input) input.value = '';
                if (typeof renderPublishersPage === 'function') {
                    await renderPublishersPage(container);
                }
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not add publisher.');
            }
            return;
        }

        const aliasBtn = e.target.closest('[data-publisher-alias-edit]');
        if (aliasBtn) {
            e.preventDefault();
            e.stopPropagation();
            const id = aliasBtn.getAttribute('data-publisher-alias-edit');
            prksPublishersPageCtx.selectedId = id;
            prksOpenPublishersAliasModal(aliasBtn);
            return;
        }

        const delBtn = e.target.closest('[data-publisher-delete]');
        if (delBtn) {
            e.preventDefault();
            e.stopPropagation();
            const id = delBtn.getAttribute('data-publisher-delete');
            const pub = prksPublishersPageCtx.publishers.find((p) => p.id === id);
            const label = pub ? pub.name || id : id;
            if (!confirm(`Delete publisher “${label}” and its aliases? Works are not changed.`)) return;
            try {
                const res = await fetch('/api/publishers/' + encodeURIComponent(id), { method: 'DELETE' });
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Delete failed');
                if (typeof renderPublishersPage === 'function') {
                    await renderPublishersPage(container);
                }
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not delete publisher.');
            }
            return;
        }

        const addAliasBtn = e.target.closest('#publishers-page-alias-add-btn');
        if (addAliasBtn) {
            const pub = prksPublishersPageSelected();
            const input = document.getElementById('publishers-page-alias-input');
            if (!pub || !input) return;
            const alias = String(input.value || '').trim();
            if (!alias) return;
            try {
                const res = await fetch(`/api/publishers/${encodeURIComponent(pub.id)}/aliases`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ alias }),
                });
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Failed to add alias');
                const list = await fetchPublishersInUse();
                prksPublishersPageCtx.publishers = list;
                prksRenderPublishersAliasModal();
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not add alias.');
            }
            return;
        }

        const rm = e.target.closest('[data-publisher-alias-remove]');
        if (rm) {
            const pub = prksPublishersPageSelected();
            if (!pub) return;
            const alias = rm.getAttribute('data-publisher-alias-remove');
            if (alias == null) return;
            try {
                const res = await fetch(
                    `/api/publishers/${encodeURIComponent(pub.id)}/aliases?alias=${encodeURIComponent(alias)}`,
                    { method: 'DELETE' }
                );
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Failed to remove alias');
                const list = await fetchPublishersInUse();
                prksPublishersPageCtx.publishers = list;
                prksRenderPublishersAliasModal();
            } catch (err) {
                console.error(err);
                alert(err.message || 'Could not remove alias.');
            }
        }
    });
}

async function renderPublishersPage(container) {
    prksPublishersPageCtx.containerEl = container;
    const publishers = await fetchPublishersInUse();
    prksPublishersPageCtx.publishers = publishers;
    prksPublishersPageCtx.selectedId = null;

    const totals = publishers.map((p) => Number(p.work_count) || 0);
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
        publishers.length === 0
            ? '<p class="tags-page__empty publishers-page__empty">No publisher groups yet. Add a canonical name below, then add alternate spellings that appear on your files (⋯).</p>'
            : publishers
                  .map((p) => {
                      const count = Number(p.work_count) || 0;
                      const scale = scaleForTotal(count);
                      const borderLeftPx = 4 * scale;
                      const idEsc = escapeHtmlPublishersPage(p.id);
                      const navEnc = encodeURIComponent(p.name || '');
                      const nm = escapeHtmlPublishersPage(p.name || '');
                      return (
                          `<span class="tag tag--page tag--page-with-actions publishers-page__chip" style="--tag-scale:${scale.toFixed(3)};border-left: ${borderLeftPx.toFixed(2)}px solid var(--accent-muted, #6d6cf7);">` +
                          `<span class="tag--page__nav" role="button" tabindex="0" data-publisher-nav="${navEnc}">${nm}</span>` +
                          `<span class="publishers-page__count" aria-hidden="true">${count}</span>` +
                          `<button type="button" class="tag-page-alias-btn" data-publisher-alias-edit="${idEsc}" title="Aliases" aria-label="Edit aliases for ${nm}">⋯</button>` +
                          `<button type="button" class="publishers-page__delete-btn" data-publisher-delete="${idEsc}" title="Delete publisher group" aria-label="Delete ${nm}">×</button>` +
                          `</span>`
                      );
                  })
                  .join('');

    container.innerHTML = `
        <div class="tags-page publishers-page">
            <div class="page-header tags-page__header publishers-page__header">
                <div class="publishers-page__header-lede">
                    <h2>Publishers</h2>
                    <p class="tags-page__sub publishers-page__sub">Canonical names and alternate spellings for search. Files still store whatever publisher string each book has; search matches a substring on that field, or treats exact matches as the same publisher when you define aliases (e.g. “OUP” and “Oxford University Press”). Click a name to list matching files.</p>
                </div>
                <div class="publishers-page__add">
                    <label class="search-advanced__label" for="publishers-page-new-name">New canonical publisher</label>
                    <div class="publishers-page__add-row">
                        <div class="tag-add-shell combobox-container publishers-page__add-shell">
                            <div class="tag-add-shell__field">
                                <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
                                <input type="text" id="publishers-page-new-name" class="tag-add-shell__input" maxlength="200" placeholder="e.g. Oxford University Press" autocomplete="off" aria-label="New canonical publisher name">
                            </div>
                        </div>
                        <button type="button" id="publishers-page-add-btn" class="tags-page-alias-add__submit">Add</button>
                    </div>
                </div>
            </div>
            <div id="publishers-page-cloud" class="tag-cloud tag-cloud--page">${chips}</div>
            <div id="publishers-page-alias-backdrop" class="modal-backdrop hidden tags-page-alias-backdrop" role="presentation">
                <div id="publishers-page-alias-modal" class="modal tags-page-alias-modal hidden" role="dialog" aria-modal="true" aria-labelledby="publishers-page-alias-heading" tabindex="-1">
                    <div class="modal-header">
                        <h3 id="publishers-page-alias-heading">Publisher aliases</h3>
                        <button type="button" id="publishers-page-alias-modal-close" class="close-btn" aria-label="Close">&times;</button>
                    </div>
                    <div class="modal-body tags-page-alias-modal__body">
                        <p class="modal-helper">Alternate spellings that appear on some books. Search for any of these (or the canonical name) includes files whose publisher field exactly matches any label in this group (case-insensitive), or contains your search as a substring.</p>
                        <p class="tags-page-alias-panel__for">Canonical name: <strong id="publishers-page-alias-canonical"></strong></p>
                        <ul id="publishers-page-alias-list" class="tags-page-alias-list"></ul>
                        <div class="tags-page-alias-add">
                            <input type="text" id="publishers-page-alias-input" class="tags-page-alias-input" maxlength="200" placeholder="New alias…" autocomplete="off" aria-label="New alias">
                            <button type="button" id="publishers-page-alias-add-btn" class="tags-page-alias-add__submit">Add alias</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
    wirePublisherCloudNavigation(document.getElementById('publishers-page-cloud'));
    prksEnsurePublishersPageDelegated(container);
}

window.prksClosePublishersAliasModal = prksClosePublishersAliasModal;
