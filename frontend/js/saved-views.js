/**
 * Saved Views: named search definitions over the current search engine.
 * Results are never stored. Opening a view re-runs fetchSearch() through the
 * coordinator's prksEffectiveSearchResults, the same read Search uses.
 *
 * Saved View records belong to frontend-app (typed client, shared query
 * cache, write invalidation). This modal saves through that one owner,
 * window.prksSavedViewRecords. Index and detail deletes are Vue intents.
 *
 * The search query codec lives in frontend-app/src/features/search/codec.ts.
 * This file does not own it. The shared Saved View modal reads a parsed route
 * through the one classic bridge, prksSearchQueryCodec, and stays here until
 * #303 B4. Search, Saved View detail, and the Saved Views index are painted
 * by frontend-app.
 */
(function (root) {
    'use strict';

    const UNSAVABLE_MSG = 'This search combination cannot be saved as a view.';

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
        const records = root.prksSavedViewRecords;
        try {
            if (modalState.mode === 'edit') {
                await records.update(modalState.viewId, { name: name, search: search });
                if (typeof root.requestModalClose === 'function') root.requestModalClose('save');
                else if (typeof root.closeModal === 'function') root.closeModal();
                navigateRefresh();
            } else {
                const created = await records.create({ name: name, search: search });
                if (typeof root.requestModalClose === 'function') root.requestModalClose('save');
                else if (typeof root.closeModal === 'function') root.closeModal();
                if (created && created.id && typeof root.prksNavigate === 'function') {
                    root.prksNavigate('#/views/' + encodeURIComponent(created.id));
                } else {
                    navigateRefresh();
                }
            }
        } catch (err) {
            const fallback = modalState.mode === 'edit' ? 'Could not update Saved View.' : 'Could not save view.';
            setModalError(records && typeof records.actionMessage === 'function'
                ? records.actionMessage(err, fallback)
                : fallback);
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
        const codec = root.prksSearchQueryCodec;
        const parsed =
            codec && typeof codec.definitionFromRoute === 'function'
                ? codec.definitionFromRoute(route)
                : { ok: false, unsavable: true, message: UNSAVABLE_MSG };
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

    function init() {
        bindModal();
    }

    const api = {
        prksOpenSavedViewModalFromCurrentSearch: prksOpenSavedViewModalFromCurrentSearch,
        prksOpenSavedViewModalForCurrentView: prksOpenSavedViewModalForCurrentView,
        prksOpenSavedViewModal: openModalWith,
        prksInitSavedViews: init,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
