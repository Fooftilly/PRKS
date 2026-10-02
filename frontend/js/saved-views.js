/**
 * Saved Views: named search definitions over the current search engine.
 * Results are never stored. Opening a view re-runs fetchSearch() through the
 * coordinator's prksEffectiveSearchResults, the same read Search uses.
 *
 * This file owns the canonical search query codec (definition <-> route
 * params <-> hash <-> fetchSearch options) until the coordinator/global-window
 * remainder of #303 B1, and the shared Saved View modal until #303 B4. The
 * typed route identity already lives in frontend-app `PrksRouteInstance`.
 * Search, Saved View detail, and the Saved Views index are painted by frontend-app.
 */
(function (root) {
    'use strict';

    const UNSAVABLE_MSG = 'This search combination cannot be saved as a view.';

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

    /**
     * The Search surface passes its owner's canonical hash. The command
     * palette omits it; Search is Main-only, so the URL is that owner.
     */
    function prksOpenSavedViewModalFromCurrentSearch(searchHash) {
        const hash = typeof searchHash === 'string' && searchHash
            ? searchHash
            : (root.location ? root.location.hash : '');
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

    /** Saved View detail is Main-only. Its record is that TabContext's entity. */
    function prksOpenSavedViewModalForCurrentView() {
        const ctx = typeof root.prksGetMainTabContext === 'function' ? root.prksGetMainTabContext() : null;
        const view = ctx && typeof ctx.getEntity === 'function' ? ctx.getEntity('savedView') : null;
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

    function savedViewActionMessage(err, fallback) {
        const message = err && err.message != null ? String(err.message).trim() : '';
        return message || fallback;
    }

    /**
     * Confirm, recheck `still`, then delete. Success, cancel, a stale owner,
     * and a failed delete stay distinct so the index can show only the failure.
     */
    async function confirmAndDelete(id, still) {
        const ok =
            typeof root.prksConfirmDestructive === 'function'
                ? await root.prksConfirmDestructive({
                      title: 'Delete Saved View?',
                      message: 'Deleting this Saved View will not delete any files.',
                      confirmLabel: 'Delete Saved View',
                  })
                : true;
        if (!ok) return { ok: false, reason: 'cancelled' };
        if (typeof still === 'function' && !still()) return { ok: false, reason: 'stale' };
        try {
            await root.deleteSavedView(id);
        } catch (err) {
            if (typeof still === 'function' && !still()) return { ok: false, reason: 'stale' };
            return {
                ok: false,
                reason: 'failed',
                message: savedViewActionMessage(err, 'Could not delete Saved View.'),
            };
        }
        return { ok: true, reason: 'success' };
    }

    /**
     * Index delete. Confirms, rechecks `still`, deletes, rechecks `still`,
     * then navigates this tab to `#/views` so the index refetches. The
     * refresh is the index hash on `tabId`. The focused location stays put.
     * Cancel and a stale owner stay quiet. A failed delete returns its message.
     */
    async function prksDeleteSavedViewFromIndex(id, still, tabId) {
        const outcome = await confirmAndDelete(id, still);
        if (typeof still === 'function' && !still()) return { ok: false, reason: 'stale' };
        if (!outcome.ok) return outcome;
        if (typeof root.prksNavigate !== 'function') return { ok: false, reason: 'stale' };
        root.prksNavigate('#/views', tabId ? { replace: true, tabId: tabId } : { replace: true });
        return outcome;
    }

    /**
     * Saved View detail delete. `still` is the owning surface's fence: a
     * replaced owner does not delete after confirm, and a delete that already
     * started does not navigate a pane that moved on.
     */
    async function prksDeleteSavedViewFromDetail(id, still, tabId) {
        const outcome = await confirmAndDelete(id, still);
        if (!outcome.ok) return;
        if (typeof still === 'function' && !still()) return;
        if (typeof root.prksNavigate === 'function') {
            root.prksNavigate('#/views', tabId ? { replace: true, tabId: tabId } : { replace: true });
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
        prksOpenSavedViewModalFromCurrentSearch: prksOpenSavedViewModalFromCurrentSearch,
        prksOpenSavedViewModalForCurrentView: prksOpenSavedViewModalForCurrentView,
        prksOpenSavedViewModal: openModalWith,
        prksDeleteSavedViewFromDetail: prksDeleteSavedViewFromDetail,
        prksDeleteSavedViewFromIndex: prksDeleteSavedViewFromIndex,
        prksInitSavedViews: init,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
