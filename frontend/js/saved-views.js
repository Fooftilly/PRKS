/**
 * Saved Views: named search definitions over the current search engine.
 * Results are never stored. Opening a view re-runs fetchSearch().
 */
(function (root) {
    'use strict';

    const UNSAVABLE_MSG = 'This search combination cannot be saved as a view.';
    const EMPTY_HINT = 'Run a search and choose “Save View” to keep it here.';

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function truthyAny(raw) {
        const anyRaw = raw == null ? '' : raw;
        return (
            anyRaw === '1' ||
            String(anyRaw).trim().toLowerCase() === 'true' ||
            String(anyRaw).trim().toLowerCase() === 'yes'
        );
    }

    function paramsFromRoute(route) {
        if (route && route.params) return route.params;
        return route || {};
    }

    function prksSearchDefinitionFromRoute(route) {
        const params = paramsFromRoute(route);
        const q = String(params.q || '').trim();
        const tag = String(params.tag || '').trim();
        const author = String(params.author || '').trim();
        const publisher = String(params.publisher || '').trim();
        const any = truthyAny(params.any);
        if (!q && !tag && !author && !publisher) {
            return { ok: false, empty: true, unsavable: true, message: UNSAVABLE_MSG };
        }
        if (any && (tag || author || publisher)) {
            return { ok: false, unsavable: true, message: UNSAVABLE_MSG };
        }
        if (tag && q) {
            return { ok: false, unsavable: true, message: UNSAVABLE_MSG };
        }
        if (any) {
            if (!q) return { ok: false, unsavable: true, message: UNSAVABLE_MSG };
            return {
                ok: true,
                definition: { mode: 'all', q: q, tag: '', author: '', publisher: '' },
            };
        }
        if (tag) {
            return {
                ok: true,
                definition: {
                    mode: 'tag',
                    q: '',
                    tag: tag,
                    author: author,
                    publisher: publisher,
                },
            };
        }
        return {
            ok: true,
            definition: {
                mode: 'advanced',
                q: q,
                tag: '',
                author: author,
                publisher: publisher,
            },
        };
    }

    function prksSearchHashFromDefinition(definition) {
        const d = definition || {};
        const p = new URLSearchParams();
        const mode = String(d.mode || '');
        const q = String(d.q || '').trim();
        const tag = String(d.tag || '').trim();
        const author = String(d.author || '').trim();
        const publisher = String(d.publisher || '').trim();
        if (mode === 'all') {
            p.set('any', '1');
            if (q) p.set('q', q);
        } else if (mode === 'tag') {
            if (tag) p.set('tag', tag);
            if (author) p.set('author', author);
            if (publisher) p.set('publisher', publisher);
        } else {
            if (q) p.set('q', q);
            if (author) p.set('author', author);
            if (publisher) p.set('publisher', publisher);
        }
        return '#/search?' + p.toString();
    }

    function prksSearchOptionsFromDefinition(definition) {
        const d = definition || {};
        const q = String(d.q || '').trim();
        const tag = String(d.tag || '').trim();
        const author = String(d.author || '').trim();
        const publisher = String(d.publisher || '').trim();
        if (d.mode === 'all') {
            return { q: q, tag: null, options: { any: '1' } };
        }
        if (d.mode === 'tag') {
            return { q: '', tag: tag, options: { author: author, publisher: publisher } };
        }
        return { q: q, tag: null, options: { author: author, publisher: publisher } };
    }

    function prksSearchSummaryText(definition) {
        const d = definition || {};
        const parts = [];
        if (d.mode === 'all') {
            return 'All: ' + String(d.q || '');
        }
        if (d.mode === 'tag') {
            parts.push('Tag: ' + String(d.tag || ''));
            if (d.author) parts.push('Author: ' + d.author);
            if (d.publisher) parts.push('Publisher: ' + d.publisher);
            return parts.join(' · ');
        }
        if (d.q) parts.push('Keywords: ' + d.q);
        if (d.author) parts.push('Author: ' + d.author);
        if (d.publisher) parts.push('Publisher: ' + d.publisher);
        return parts.join(' · ');
    }

    function prksSearchResultCardsHtml(results, emptyMsg) {
        /* Search and Saved View results come from the SERVER, so while a local
         * edit is pending they still carry the acknowledged value -- a card
         * here would show "Planned" seconds after the user set it to
         * "Completed" everywhere else. One overlay for every synchronized
         * field, applied where the cards are built, so this file never learns
         * to read the durable queue and the fix is not Status-specific. The
         * results array itself is never mutated. */
        const rows = typeof root.prksEffectiveWorksSync === 'function'
            ? root.prksEffectiveWorksSync(results) : results;
        const browseClass =
            typeof root.prksWorkBrowseCollectionClass === 'function'
                ? root.prksWorkBrowseCollectionClass()
                : 'card-grid';
        let html = '<div class="' + browseClass + '">';
        if (rows && rows.length > 0) {
            rows.forEach(function (w) {
                /* The server's own excerpt rule -- code points, not UTF-16
                 * units -- so a pending Abstract is cut exactly where the
                 * acknowledged one will be and the card does not shift at
                 * acknowledgement. */
                const subtitle = w.abstract
                    ? (typeof root.prksAbstractExcerpt === 'function'
                        ? root.prksAbstractExcerpt(w.abstract)
                        : String(w.abstract).substring(0, 100)) + '…'
                    : '';
                html +=
                    typeof root.prksWorkCardHtml === 'function'
                        ? root.prksWorkCardHtml(w, { subtitle: subtitle })
                        : '';
            });
        } else {
            html += '<p class="prks-inline-message">' + esc(emptyMsg || 'No results found matching your query.') + '</p>';
        }
        html += '</div>';
        return html;
    }

    const modalState = {
        mode: 'create',
        viewId: '',
        inited: false,
        saving: false,
    };

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    function modalEls() {
        const d = doc();
        if (!d) return {};
        return {
            root: d.getElementById('saved-view-modal'),
            title: d.getElementById('saved-view-modal-title'),
            helper: d.getElementById('saved-view-modal-helper'),
            error: d.getElementById('saved-view-modal-error'),
            name: d.getElementById('saved-view-name'),
            qWrap: d.getElementById('saved-view-field-q-wrap'),
            qLabel: d.getElementById('saved-view-q-label'),
            q: d.getElementById('saved-view-q'),
            tagWrap: d.getElementById('saved-view-field-tag-wrap'),
            tag: d.getElementById('saved-view-tag'),
            authorWrap: d.getElementById('saved-view-field-author-wrap'),
            author: d.getElementById('saved-view-author'),
            publisherWrap: d.getElementById('saved-view-field-publisher-wrap'),
            publisher: d.getElementById('saved-view-publisher'),
            save: d.getElementById('save-saved-view-btn'),
        };
    }

    function selectedMode() {
        const d = doc();
        const picked = d && d.querySelector('input[name="saved-view-mode"]:checked');
        return picked ? String(picked.value || '') : 'advanced';
    }

    function setMode(mode) {
        const d = doc();
        if (!d) return;
        const radios = d.querySelectorAll('input[name="saved-view-mode"]');
        for (let i = 0; i < radios.length; i++) {
            radios[i].checked = radios[i].value === mode;
        }
        syncModeFields();
    }

    function syncModeFields() {
        const els = modalEls();
        const mode = selectedMode();
        if (els.qWrap) els.qWrap.hidden = mode === 'tag';
        if (els.tagWrap) els.tagWrap.hidden = mode !== 'tag';
        if (els.authorWrap) els.authorWrap.hidden = mode === 'all';
        if (els.publisherWrap) els.publisherWrap.hidden = mode === 'all';
        if (els.qLabel) els.qLabel.textContent = mode === 'all' ? 'All fields' : 'Keywords';
    }

    function setModalError(msg) {
        const els = modalEls();
        if (!els.error) return;
        const text = String(msg || '');
        els.error.textContent = text;
        if (text) els.error.classList.remove('hidden');
        else els.error.classList.add('hidden');
    }

    function fillDefinition(def) {
        const d = def || {};
        const els = modalEls();
        setMode(d.mode || 'advanced');
        if (els.q) els.q.value = d.q || '';
        if (els.tag) els.tag.value = d.tag || '';
        if (els.author) els.author.value = d.author || '';
        if (els.publisher) els.publisher.value = d.publisher || '';
    }

    function readDefinition() {
        const els = modalEls();
        const mode = selectedMode();
        return {
            mode: mode,
            q: els.q ? String(els.q.value || '').trim() : '',
            tag: els.tag ? String(els.tag.value || '').trim() : '',
            author: els.author ? String(els.author.value || '').trim() : '',
            publisher: els.publisher ? String(els.publisher.value || '').trim() : '',
        };
    }

    function navigateRefresh() {
        if (typeof root.prksNavigate === 'function') {
            root.prksNavigate(root.location ? root.location.hash : '', { replace: true });
        } else if (typeof root.handleRoute === 'function') {
            root.handleRoute();
        }
    }

    async function submitModal() {
        if (modalState.saving) return;
        const els = modalEls();
        const name = els.name ? String(els.name.value || '').trim() : '';
        if (!name) {
            setModalError('Name is required.');
            if (els.name && typeof els.name.focus === 'function') els.name.focus();
            return;
        }
        const search = readDefinition();
        modalState.saving = true;
        if (els.save) els.save.disabled = true;
        setModalError('');
        try {
            if (modalState.mode === 'edit') {
                await root.updateSavedView(modalState.viewId, { name: name, search: search });
                if (typeof root.requestModalClose === 'function') root.requestModalClose('save');
                else if (typeof root.closeModal === 'function') root.closeModal();
                navigateRefresh();
            } else {
                const created = await root.createSavedView({ name: name, search: search });
                if (typeof root.requestModalClose === 'function') root.requestModalClose('save');
                else if (typeof root.closeModal === 'function') root.closeModal();
                if (created && created.id && typeof root.prksNavigate === 'function') {
                    root.prksNavigate('#/views/' + encodeURIComponent(created.id));
                } else {
                    navigateRefresh();
                }
            }
        } catch (err) {
            setModalError((err && err.message) || 'Could not save view.');
        } finally {
            modalState.saving = false;
            if (els.save) els.save.disabled = false;
        }
    }

    function openModalWith(options) {
        if (typeof root.prksOfflineGuardMutation === 'function' &&
            root.prksOfflineGuardMutation(
                'Saved Views require a connection to PRKS.')) {
            return;
        }
        const opts = options || {};
        const els = modalEls();
        modalState.mode = opts.viewId ? 'edit' : 'create';
        modalState.viewId = opts.viewId || '';
        if (els.title) els.title.textContent = modalState.mode === 'edit' ? 'Edit Saved View' : 'Save View';
        if (els.helper) {
            els.helper.textContent =
                modalState.mode === 'edit'
                    ? 'Rename this view or change the saved search.'
                    : 'Name this search to open it later from Saved Views.';
        }
        if (els.save) els.save.textContent = modalState.mode === 'edit' ? 'Save changes' : 'Save View';
        if (els.name) els.name.value = opts.name || '';
        fillDefinition(
            opts.definition || { mode: 'advanced', q: '', tag: '', author: '', publisher: '' }
        );
        setModalError('');
        if (typeof root.openModal === 'function') root.openModal('saved-view-modal');
        const focusName = function () {
            if (els.name && typeof els.name.focus === 'function') els.name.focus();
        };
        if (typeof root.requestAnimationFrame === 'function') root.requestAnimationFrame(focusName);
        else focusName();
    }

    function prksOpenSavedViewModalFromCurrentSearch() {
        const hash = root.location ? root.location.hash : '';
        const route = typeof root.prksParseRoute === 'function' ? root.prksParseRoute(hash) : null;
        const parsed = prksSearchDefinitionFromRoute(route);
        if (!parsed || !parsed.ok) {
            const msg = (parsed && parsed.message) || UNSAVABLE_MSG;
            if (typeof root.prksAlertDialog === 'function') {
                root.prksAlertDialog({ title: 'Cannot save view', message: msg });
            } else if (typeof root.prksConfirmDialog === 'function') {
                root.prksConfirmDialog({ title: 'Cannot save view', message: msg, alertOnly: true });
            }
            return;
        }
        openModalWith({ definition: parsed.definition });
    }

    function prksOpenSavedViewModalForCurrentView() {
        const view = root.__prksCurrentSavedView;
        if (!view || !view.id) return;
        openModalWith({
            viewId: view.id,
            name: view.name || '',
            definition: view.search || {},
        });
    }

    function bindModal() {
        if (modalState.inited) return;
        const els = modalEls();
        if (!els.root) return;
        modalState.inited = true;
        const d = doc();
        const radios = d ? d.querySelectorAll('input[name="saved-view-mode"]') : [];
        for (let i = 0; i < radios.length; i++) {
            radios[i].addEventListener('change', syncModeFields);
        }
        if (els.save) {
            els.save.addEventListener('click', function () {
                void submitModal();
            });
        }
        if (els.name) {
            els.name.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    void submitModal();
                }
            });
        }
    }

    function renderSavedViewsIndex(views, container) {
        const list = Array.isArray(views) ? views : [];
        const icon = typeof root.prksIcon === 'function' ? root.prksIcon('bookmark', { size: 'sm' }) : '';
        const rowsHtml = list.length
            ? list
                  .map(function (v) {
                      const id = String(v && v.id ? v.id : '').trim();
                      const path = '#/views/' + encodeURIComponent(id);
                      const name = esc(v.name || 'Saved View');
                      const summary = esc(prksSearchSummaryText(v.search || {}));
                      const idAttr = esc(id);
                      return `
                        <div class="project-card saved-views-page__list-item">
                            <a class="saved-views-page__list-main" href="${path}">
                                <span class="saved-views-page__badge">${icon}<span>${name}</span></span>
                                <p class="meta-row saved-views-page__summary">${summary}</p>
                            </a>
                            <div class="saved-views-page__row-actions">
                                <a class="prks-btn prks-btn--secondary prks-btn--sm" href="${path}">Open</a>
                                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-sv-edit="${idAttr}">Edit</button>
                                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-sv-delete="${idAttr}">Delete</button>
                            </div>
                        </div>`;
                  })
                  .join('')
            : `<p class="meta-row saved-views-page__empty">No Saved Views yet.</p>
               <p class="meta-row">${esc(EMPTY_HINT)}</p>
               <p><button type="button" class="prks-btn prks-btn--secondary" id="prks-saved-views-empty-search">Search or jump</button></p>`;
        container.innerHTML = `
            <div class="saved-views-page">
                <div class="prks-page-header page-header">
                    <h2 class="prks-page-title">${typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('bookmark') : ''} Saved Views</h2>
                </div>
                <div class="list-view saved-views-page__list">
                    ${rowsHtml}
                </div>
            </div>
        `;
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
        bindIndexActions(container);
    }

    function bindIndexActions(container) {
        const emptyLaunch = container.querySelector('#prks-saved-views-empty-search');
        if (emptyLaunch) {
            emptyLaunch.addEventListener('click', function () {
                if (typeof root.prksOpenCommandPalette === 'function') root.prksOpenCommandPalette();
            });
        }
        container.querySelectorAll('[data-sv-edit]').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                const id = btn.getAttribute('data-sv-edit');
                void openEditById(id);
            });
        });
        container.querySelectorAll('[data-sv-delete]').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                const id = btn.getAttribute('data-sv-delete');
                void confirmDelete(id, false);
            });
        });
    }

    function currentCanonicalHash() {
        if (typeof root.prksCurrentCanonicalHash === 'function') {
            return root.prksCurrentCanonicalHash();
        }
        if (typeof root.prksParseRoute === 'function' && root.location) {
            const parsed = root.prksParseRoute(root.location.hash || '');
            return (parsed && parsed.canonicalHash) || '';
        }
        return root.location ? String(root.location.hash || '') : '';
    }

    function editFetchStillCurrent(routeGen, canonicalHash) {
        if (typeof root.prksFocusedRouteGeneration === 'function' && routeGen !== root.prksFocusedRouteGeneration()) return false;
        return canonicalHash === currentCanonicalHash();
    }

    async function openEditById(id) {
        if (typeof root.fetchSavedView !== 'function') return;
        const routeGen = typeof root.prksFocusedRouteGeneration === 'function' ? root.prksFocusedRouteGeneration() : 0;
        const canonicalHash = currentCanonicalHash();
        let view;
        try {
            view = await root.fetchSavedView(id);
        } catch (_e) {
            if (!editFetchStillCurrent(routeGen, canonicalHash)) return;
            return;
        }
        if (!editFetchStillCurrent(routeGen, canonicalHash)) return;
        if (!view) return;
        openModalWith({
            viewId: view.id,
            name: view.name || '',
            definition: view.search || {},
        });
    }

    async function confirmDelete(id, fromDetail) {
        const ok =
            typeof root.prksConfirmDestructive === 'function'
                ? await root.prksConfirmDestructive({
                      title: 'Delete Saved View?',
                      message: 'Deleting this Saved View will not delete any files.',
                      confirmLabel: 'Delete',
                  })
                : true;
        if (!ok) return;
        try {
            await root.deleteSavedView(id);
        } catch (_e) {
            return;
        }
        if (fromDetail && typeof root.prksNavigate === 'function') {
            root.prksNavigate('#/views', { replace: true });
        } else {
            navigateRefresh();
        }
    }

    function renderSavedViewNotFound(container) {
        container.innerHTML = `
            <div class="prks-page-header page-header">
                <h2 class="prks-page-title">Saved View not found.</h2>
            </div>
            <p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/views">Back to Saved Views</a></p>
        `;
    }

    function renderSavedViewDetail(view, results, container) {
        const name = esc(view.name || 'Saved View');
        const idAttr = esc(view.id);
        const searchHash = prksSearchHashFromDefinition(view.search || {});
        const modeToggle =
            typeof root.prksWorkBrowseModeToggleHtml === 'function'
                ? root.prksWorkBrowseModeToggleHtml('prks-work-browse-mode-saved-view')
                : '';
        const cards =
            typeof root.prksSearchResultCardsHtml === 'function'
                ? root.prksSearchResultCardsHtml(results, 'No results found matching your query.')
                : prksSearchResultCardsHtml(results, 'No results found matching your query.');
        container.innerHTML = `
            <div class="saved-view-detail">
                <div class="prks-page-header page-header">
                    <div class="page-header__title-row">
                        <div>
                            <p class="saved-view-detail__kicker">Saved View</p>
                            <h2 class="prks-page-title">${name}</h2>
                        </div>
                        <div class="page-header__actions">
                            ${modeToggle}
                            <a class="prks-btn prks-btn--secondary" href="${esc(searchHash)}">Open as Search</a>
                            <button type="button" class="prks-btn prks-btn--secondary" id="prks-saved-view-edit">Edit</button>
                            <button type="button" class="prks-btn prks-btn--secondary" id="prks-saved-view-delete" data-sv-delete="${idAttr}">Delete</button>
                        </div>
                    </div>
                </div>
                ${cards}
            </div>
        `;
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
        if (typeof root.prksBindWorkBrowseMode === 'function') {
            root.prksBindWorkBrowseMode(container);
        }
        if (typeof root.prksInitLazyWorkThumbs === 'function') {
            root.prksInitLazyWorkThumbs(container);
        }
        const editBtn = container.querySelector('#prks-saved-view-edit');
        if (editBtn) {
            editBtn.addEventListener('click', function () {
                openModalWith({
                    viewId: view.id,
                    name: view.name || '',
                    definition: view.search || {},
                });
            });
        }
        const delBtn = container.querySelector('#prks-saved-view-delete');
        if (delBtn) {
            delBtn.addEventListener('click', function () {
                void confirmDelete(view.id, true);
            });
        }
    }

    function init() {
        bindModal();
    }

    const api = {
        prksSearchDefinitionFromRoute: prksSearchDefinitionFromRoute,
        prksSearchHashFromDefinition: prksSearchHashFromDefinition,
        prksSearchOptionsFromDefinition: prksSearchOptionsFromDefinition,
        prksSearchSummaryText: prksSearchSummaryText,
        prksSearchResultCardsHtml: prksSearchResultCardsHtml,
        prksOpenSavedViewModalFromCurrentSearch: prksOpenSavedViewModalFromCurrentSearch,
        prksOpenSavedViewModalForCurrentView: prksOpenSavedViewModalForCurrentView,
        prksOpenSavedViewIndexEdit: openEditById,
        prksOpenSavedViewModal: openModalWith,
        renderSavedViewsIndex: renderSavedViewsIndex,
        renderSavedViewDetail: renderSavedViewDetail,
        renderSavedViewNotFound: renderSavedViewNotFound,
        prksInitSavedViews: init,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
