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
                await prksAlertMessage(err.message || 'Could not add publisher.', 'Error');
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

        const deletePublisherBtn = e.target.closest('#publishers-page-delete-btn');
        if (deletePublisherBtn) {
            e.preventDefault();
            const pub = prksPublishersPageSelected();
            if (!pub) return;
            const label = pub.name || pub.id;
            const msg = 'Works are not changed. Alternate spellings (aliases) for this publisher group will be removed.';
            const confirmed = await prksConfirmDestructive({
                title: `Delete publisher “${label}”?`,
                message: msg,
                confirmLabel: 'Delete publisher',
            });
            if (!confirmed) return;
            try {
                const res = await fetch('/api/publishers/' + encodeURIComponent(pub.id), {
                    method: 'DELETE',
                });
                const errData = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(errData.error || 'Delete failed');
                prksClosePublishersAliasModal();
                if (typeof renderPublishersPage === 'function') {
                    await renderPublishersPage(container);
                }
            } catch (err) {
                console.error(err);
                await prksAlertMessage(err.message || 'Could not delete publisher.', 'Error');
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
                await prksAlertMessage(err.message || 'Could not add alias.', 'Error');
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
                await prksAlertMessage(err.message || 'Could not remove alias.', 'Error');
            }
        }
    });
}

async function renderPublishersPage(container) {
    prksPublishersPageCtx.containerEl = container;
    const publishers = await fetchPublishersInUse();
    prksPublishersPageCtx.publishers = publishers;
    prksPublishersPageCtx.selectedId = null;

    const rowsHtml =
        publishers.length === 0
            ? '<p class="tags-page__empty publishers-page__empty">No publisher groups yet. Add a canonical name below, then add alternate spellings that appear on your files (⋯).</p>'
            : publishers
                  .map((p) => {
                      const count = Number(p.work_count) || 0;
                      const idEsc = escapeHtmlPublishersPage(p.id);
                      const navEnc = encodeURIComponent(p.name || '');
                      const nm = escapeHtmlPublishersPage(p.name || '');
                      const aliases = Array.isArray(p.aliases) ? p.aliases.length : 0;
                      const icon = typeof prksIcon === 'function' ? prksIcon('building-2', { size: 'sm' }) : '';
                      return (
                          `<div class="project-card publishers-page__list-item" role="button" tabindex="0" data-publisher-nav="${navEnc}" aria-label="View files for publisher ${nm}">` +
                          `<div class="publishers-page__list-main">` +
                          `<span class="publishers-page__badge">${icon}<span>${nm}</span></span>` +
                          `<p class="meta-row publishers-page__list-stats">${count} file${count === 1 ? '' : 's'}${aliases ? ` · ${aliases} alias${aliases === 1 ? '' : 'es'}` : ''}</p>` +
                          `</div>` +
                          `<div class="publishers-page__list-actions">` +
                          `<button type="button" class="ribbon-btn ribbon-btn--sm publishers-page__alias-btn" data-publisher-alias-edit="${idEsc}" title="Aliases" aria-label="Edit aliases for ${nm}">⋯<span>Aliases</span></button>` +
                          `</div>` +
                          `</div>`
                      );
                  })
                  .join('');

    container.innerHTML = `
        <div class="tags-page publishers-page">
            <div class="page-header tags-page__header publishers-page__header">
                <div class="publishers-page__header-lede">
                    <h2>Publishers</h2>
                    <p class="tags-page__sub publishers-page__sub">Canonical names and alternate spellings for search. Files still store whatever publisher string each book has; search matches a substring on that field, or treats exact matches as the same publisher when you define aliases (e.g. “OUP” and “Oxford University Press”). Click row to view files; use <strong>Aliases</strong> to edit variants.</p>
                </div>
                <div class="publishers-page__add">
                    <label class="search-advanced__label" for="publishers-page-new-name">New canonical publisher</label>
                    <div class="publishers-page__add-row">
                        <div class="tag-add-shell combobox-container publishers-page__add-shell">
                            <div class="tag-add-shell__field">
                                ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : ''}
                                <input type="text" id="publishers-page-new-name" class="tag-add-shell__input" maxlength="200" placeholder="e.g. Oxford University Press" autocomplete="off" aria-label="New canonical publisher name">
                            </div>
                        </div>
                        <button type="button" id="publishers-page-add-btn" class="tags-page-alias-add__submit">Add</button>
                    </div>
                </div>
            </div>
            <div id="publishers-page-cloud" class="list-view publishers-page__list">${rowsHtml}</div>
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
                        <div class="tags-page-alias-delete">
                            <button type="button" id="publishers-page-delete-btn" class="btn-danger-outline">Delete publisher</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
    wirePublisherCloudNavigation(document.getElementById('publishers-page-cloud'));
    prksEnsurePublishersPageDelegated(container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

window.prksClosePublishersAliasModal = prksClosePublishersAliasModal;
