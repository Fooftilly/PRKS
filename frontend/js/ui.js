const PRKS_AUTOSIZE_TEXTAREA_SELECTOR = [
    '#pd-about',
    '#pd-links-other',
    '#person-template-json',
    '#person-about',
    '#person-links-other',
    '#folder-description',
    '#playlist-description',
    '#group-description',
    '#work-private-notes',
    '#work-abstract',
    '#prks-playlist-edit-desc',
    '#gd-description',
    '#meta-abstract',
    'textarea.prks-private-notes-input'
].join(', ');

const PRKS_MODAL_UNSAVED_CONFIRM_IDS = new Set([
    'work-modal',
    'folder-modal',
    'person-modal',
    'group-modal',
    'role-modal',
]);

function prksGetModalBaselineStore() {
    if (!window.__prksModalFormBaseline || typeof window.__prksModalFormBaseline !== 'object') {
        window.__prksModalFormBaseline = {};
    }
    return window.__prksModalFormBaseline;
}

function prksSerializeModalFormState(modalId) {
    const modalEl = document.getElementById(modalId);
    if (!modalEl) return '';
    const fields = modalEl.querySelectorAll('input, textarea, select');
    const parts = [];
    fields.forEach((el, idx) => {
        const tag = (el.tagName || '').toLowerCase();
        const type = (el.type || '').toLowerCase();
        const key = `${tag}:${type}:${el.id || el.name || idx}`;
        if (tag === 'select') {
            const opts = Array.from(el.options || []).map((opt) => `${opt.value}:${opt.selected ? '1' : '0'}`);
            parts.push([key, opts.join('|')]);
            return;
        }
        if (type === 'checkbox' || type === 'radio') {
            parts.push([key, el.checked ? '1' : '0']);
            return;
        }
        if (type === 'file') {
            const files = Array.from(el.files || []).map((f) => `${f.name}:${f.size}:${f.lastModified}`);
            parts.push([key, files.join('|')]);
            return;
        }
        parts.push([key, String(el.value || '')]);
    });
    return JSON.stringify(parts);
}

function prksCaptureModalBaseline(modalId) {
    if (!modalId) return;
    const store = prksGetModalBaselineStore();
    store[modalId] = prksSerializeModalFormState(modalId);
}

function prksScheduleModalBaselineCapture(modalId) {
    if (!modalId) return;
    requestAnimationFrame(() => {
        if (document.getElementById(modalId)?.classList.contains('hidden')) return;
        prksCaptureModalBaseline(modalId);
    });
    window.setTimeout(() => {
        if (document.getElementById(modalId)?.classList.contains('hidden')) return;
        prksCaptureModalBaseline(modalId);
    }, 250);
}

function prksGetActiveModalId() {
    const modal = document.querySelector('.modal:not(.hidden)');
    return modal ? modal.id : '';
}

function prksModalNeedsUnsavedConfirm(modalId) {
    return PRKS_MODAL_UNSAVED_CONFIRM_IDS.has(String(modalId || ''));
}

function prksModalHasUnsavedChanges(modalId) {
    if (!modalId || !prksModalNeedsUnsavedConfirm(modalId)) return false;
    const store = prksGetModalBaselineStore();
    const baseline = Object.prototype.hasOwnProperty.call(store, modalId) ? store[modalId] : null;
    if (baseline == null) return false;
    const now = prksSerializeModalFormState(modalId);
    return baseline !== now;
}

function prksResetModalBaselines() {
    window.__prksModalFormBaseline = {};
}

function prksIsModalUnsavedConfirmOpen() {
    const root = document.getElementById('prks-modal-unsaved-confirm');
    return !!(root && !root.classList.contains('hidden'));
}

function prksHideModalUnsavedConfirm() {
    const root = document.getElementById('prks-modal-unsaved-confirm');
    if (root) {
        root.classList.add('hidden');
        root.setAttribute('aria-hidden', 'true');
    }
    window.__prksModalUnsavedConfirmOnDiscard = null;
}

function prksOpenModalUnsavedConfirm(onDiscard) {
    window.__prksModalUnsavedConfirmOnDiscard = typeof onDiscard === 'function' ? onDiscard : null;
    const root = document.getElementById('prks-modal-unsaved-confirm');
    if (!root) {
        const fn = window.__prksModalUnsavedConfirmOnDiscard;
        prksHideModalUnsavedConfirm();
        if (fn) fn();
        return;
    }
    root.classList.remove('hidden');
    root.setAttribute('aria-hidden', 'false');
    const discard = document.getElementById('prks-modal-unsaved-confirm-discard');
    if (discard && typeof discard.focus === 'function') {
        requestAnimationFrame(() => discard.focus());
    }
}

function prksDiscardConfirmedClose() {
    const fn = window.__prksModalUnsavedConfirmOnDiscard;
    prksHideModalUnsavedConfirm();
    if (fn) fn();
}

let prksModalConfirmResolve = null;
let prksModalConfirmAlertOnly = false;
let prksModalConfirmOpener = null;

function prksIsModalConfirmOpen() {
    const root = document.getElementById('prks-modal-confirm');
    return !!(root && !root.classList.contains('hidden'));
}

function prksRestoreModalConfirmCancel() {
    const cancelBtn = document.getElementById('prks-modal-confirm-cancel');
    if (cancelBtn) {
        cancelBtn.classList.remove('hidden');
        cancelBtn.setAttribute('aria-hidden', 'false');
    }
    const actions = document.querySelector('.prks-modal-confirm__actions');
    if (actions) actions.classList.remove('prks-modal-confirm__actions--alertOnly');
    prksModalConfirmAlertOnly = false;
}

function prksHideModalConfirm() {
    const root = document.getElementById('prks-modal-confirm');
    if (root) {
        root.classList.add('hidden');
        root.setAttribute('aria-hidden', 'true');
    }
    prksRestoreModalConfirmCancel();
    prksModalConfirmResolve = null;
    const opener = prksModalConfirmOpener;
    prksModalConfirmOpener = null;
    if (opener && typeof opener.focus === 'function' && document.contains(opener)) {
        opener.focus();
    }
}

function prksFinishModalConfirm(confirmed) {
    const resolve = prksModalConfirmResolve;
    prksHideModalConfirm();
    if (typeof resolve === 'function') resolve(!!confirmed);
}

/**
 * In-app confirm dialog (replaces window.confirm for destructive flows).
 * @returns {Promise<boolean>}
 */
function prksConfirmDialog(options = {}) {
    return new Promise((resolve) => {
        const root = document.getElementById('prks-modal-confirm');
        if (!root) {
            resolve(false);
            return;
        }
        const titleEl = document.getElementById('prks-modal-confirm-title');
        const descEl = document.getElementById('prks-modal-confirm-desc');
        const cancelBtn = document.getElementById('prks-modal-confirm-cancel');
        const okBtn = document.getElementById('prks-modal-confirm-ok');
        const title = options.title != null ? String(options.title) : 'Confirm';
        const message = options.message != null ? String(options.message) : '';
        const messageHtml = options.messageHtml != null ? String(options.messageHtml) : '';
        const confirmLabel =
            options.confirmLabel != null ? String(options.confirmLabel) : 'OK';
        const cancelLabel =
            options.cancelLabel != null ? String(options.cancelLabel) : 'Cancel';
        const danger = options.danger === true;
        const alertOnly = options.alertOnly === true;
        prksModalConfirmAlertOnly = alertOnly;
        const actions = document.querySelector('.prks-modal-confirm__actions');

        if (titleEl) titleEl.textContent = title;
        if (descEl) {
            if (messageHtml) {
                descEl.classList.add('prks-modal-confirm__desc--rich');
                descEl.innerHTML = messageHtml;
            } else {
                descEl.classList.remove('prks-modal-confirm__desc--rich');
                descEl.textContent = message;
            }
        }
        if (cancelBtn) {
            if (alertOnly) {
                cancelBtn.classList.add('hidden');
                cancelBtn.setAttribute('aria-hidden', 'true');
            } else {
                cancelBtn.classList.remove('hidden');
                cancelBtn.setAttribute('aria-hidden', 'false');
                cancelBtn.textContent = cancelLabel;
            }
        }
        if (actions) {
            actions.classList.toggle('prks-modal-confirm__actions--alertOnly', alertOnly);
        }
        if (okBtn) {
            okBtn.textContent = confirmLabel;
            okBtn.classList.remove('prks-btn--primary', 'prks-btn--danger');
            okBtn.classList.add(danger ? 'prks-btn--danger' : 'prks-btn--primary');
        }

        prksModalConfirmResolve = resolve;
        const active = document.activeElement;
        prksModalConfirmOpener = active && active !== document.body ? active : null;
        root.classList.remove('hidden');
        root.setAttribute('aria-hidden', 'false');
        const focusEl =
            alertOnly || !(danger && cancelBtn && !cancelBtn.classList.contains('hidden'))
                ? okBtn || cancelBtn
                : cancelBtn;
        if (focusEl && typeof focusEl.focus === 'function') {
            requestAnimationFrame(() => focusEl.focus());
        }
    });
}

/**
 * Single-button in-app alert (reuses #prks-modal-confirm).
 * @returns {Promise<void>}
 */
function prksAlertDialog(options = {}) {
    return prksConfirmDialog({
        title: options.title != null ? String(options.title) : 'Notice',
        message: options.message != null ? String(options.message) : '',
        messageHtml: options.messageHtml != null ? String(options.messageHtml) : '',
        confirmLabel: options.okLabel != null ? String(options.okLabel) : 'OK',
        alertOnly: true,
    }).then(() => {});
}

const PRKS_INLINE_COPY_FLASH_MS = 1500;
const _prksInlineCopyFlashTimers = new WeakMap();

/**
 * Brief check/error flash on small inline copy icon buttons.
 * @param {HTMLElement|null|undefined} btn
 * @param {boolean} ok
 */
function prksFlashInlineCopyButton(btn, ok = true) {
    if (!btn || !(btn instanceof HTMLElement)) return;
    const prev = _prksInlineCopyFlashTimers.get(btn);
    if (prev) clearTimeout(prev);
    if (!btn.dataset.prksFlashRestore) {
        btn.dataset.prksFlashRestore = btn.innerHTML;
        btn.dataset.prksFlashTitle = btn.getAttribute('title') || '';
        btn.dataset.prksFlashAria = btn.getAttribute('aria-label') || '';
    }
    const label = ok ? 'Copied' : 'Copy failed';
    btn.classList.remove('inline-action-btn--copied', 'inline-action-btn--error');
    btn.classList.add(ok ? 'inline-action-btn--copied' : 'inline-action-btn--error');
    btn.setAttribute('title', label);
    btn.setAttribute('aria-label', label);
    const iconName = ok ? 'check' : 'x';
    btn.innerHTML =
        typeof prksIcon === 'function' ? prksIcon(iconName, { size: 'sm' }) : label;
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
    const t = setTimeout(() => {
        btn.innerHTML = btn.dataset.prksFlashRestore || '';
        btn.setAttribute('title', btn.dataset.prksFlashTitle || '');
        btn.setAttribute('aria-label', btn.dataset.prksFlashAria || '');
        delete btn.dataset.prksFlashRestore;
        delete btn.dataset.prksFlashTitle;
        delete btn.dataset.prksFlashAria;
        btn.classList.remove('inline-action-btn--copied', 'inline-action-btn--error');
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
        _prksInlineCopyFlashTimers.delete(btn);
    }, PRKS_INLINE_COPY_FLASH_MS);
    _prksInlineCopyFlashTimers.set(btn, t);
}

function prksIsDuplicateRoleLinkError(msg) {
    return typeof msg === 'string' && /already linked/i.test(msg);
}

function prksShowDuplicateRoleLinkAlert(roleType) {
    const rt = String(roleType || 'Linked').trim() || 'Linked';
    return prksAlertDialog({
        title: 'Already linked',
        message: `This person is already linked as ${rt}.`,
    });
}

async function prksNotifyRoleLinkFailure(errorMsg, roleType) {
    if (prksIsDuplicateRoleLinkError(errorMsg)) {
        await prksShowDuplicateRoleLinkAlert(roleType);
        return;
    }
    await prksAlertDialog({
        title: 'Could not link',
        message: errorMsg || 'Could not create link.',
    });
}

function prksAlertMessage(message, title = 'Notice') {
    return prksAlertDialog({ title, message: String(message ?? '') });
}

const _prksButtonBusySnapshots = new WeakMap();

/**
 * Shared busy-button helper: disable, set aria-busy, swap in a busy label, and
 * later restore the exact original contents. Safe to call repeatedly with the
 * same busy state -- only the first busy(true) snapshots the idle contents.
 * busy(false) without an owning snapshot is a true no-op: the helper must
 * never enable, re-content, or otherwise touch a control whose busy state it
 * did not itself create.
 * @param {HTMLElement|null|undefined} button
 * @param {boolean} busy
 * @param {{busyLabel?: string}} [options]
 */
function prksSetButtonBusy(button, busy, options = {}) {
    if (!button) return;
    if (busy) {
        if (!_prksButtonBusySnapshots.has(button)) {
            _prksButtonBusySnapshots.set(button, {
                html: button.innerHTML,
                disabled: !!button.disabled,
                ariaBusy: button.getAttribute('aria-busy'),
            });
        }
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        if (options.busyLabel != null) button.textContent = String(options.busyLabel);
        return;
    }
    const snap = _prksButtonBusySnapshots.get(button);
    if (!snap) return;
    button.innerHTML = snap.html;
    button.disabled = snap.disabled;
    if (snap.ariaBusy == null) {
        button.removeAttribute('aria-busy');
    } else {
        button.setAttribute('aria-busy', snap.ariaBusy);
    }
    _prksButtonBusySnapshots.delete(button);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(button);
}

const PRKS_BUTTON_LABEL_FLASH_MS = 1500;
const _prksButtonLabelFlashTimers = new WeakMap();

/**
 * Brief success/error text flash for textual (non-icon-only) buttons, e.g. the
 * annotation-list "Copy link" action. Mirrors prksFlashInlineCopyButton's
 * icon-flash contract but swaps textContent instead of innerHTML/icon.
 * @param {HTMLElement|null|undefined} btn
 * @param {boolean} ok
 * @param {{successLabel?: string, errorLabel?: string, restoreMs?: number}} [options]
 */
function prksFlashButtonLabel(btn, ok = true, options = {}) {
    if (!btn || !(btn instanceof HTMLElement)) return;
    const prev = _prksButtonLabelFlashTimers.get(btn);
    if (prev) clearTimeout(prev);
    if (btn.dataset.prksLabelRestore == null) {
        btn.dataset.prksLabelRestore = btn.textContent || '';
    }
    const successLabel = options.successLabel != null ? String(options.successLabel) : 'Copied';
    const errorLabel = options.errorLabel != null ? String(options.errorLabel) : 'Copy failed';
    const restoreMs = typeof options.restoreMs === 'number' ? options.restoreMs : PRKS_BUTTON_LABEL_FLASH_MS;
    btn.textContent = ok ? successLabel : errorLabel;
    const t = setTimeout(() => {
        btn.textContent = btn.dataset.prksLabelRestore || '';
        delete btn.dataset.prksLabelRestore;
        _prksButtonLabelFlashTimers.delete(btn);
    }, restoreMs);
    _prksButtonLabelFlashTimers.set(btn, t);
}

/**
 * Shared destructive-confirm for both PDF annotation-delete entry points
 * (annotation editor Delete, annotation-list row Delete) so the copy and
 * behavior can't drift between the two call sites.
 * @returns {Promise<boolean>}
 */
function prksConfirmDeletePdfAnnotation() {
    if (typeof prksConfirmDestructive === 'function') {
        return prksConfirmDestructive({
            title: 'Delete annotation?',
            message: 'This annotation will be removed from the PDF.',
            confirmLabel: 'Delete',
        });
    }
    return Promise.resolve(window.confirm('Delete this annotation from the PDF?'));
}

function prksConfirmDestructive(options) {
    const o = options && typeof options === 'object' ? options : {};
    return prksConfirmDialog({
        title: o.title ?? 'Confirm',
        message: o.message ?? '',
        confirmLabel: o.confirmLabel ?? 'Confirm',
        cancelLabel: o.cancelLabel ?? 'Cancel',
        danger: o.danger !== false,
    });
}

function prksConfirmUnsavedRouteLeave(options) {
    const o = options && typeof options === 'object' ? options : {};
    return prksConfirmDialog({
        title: o.title ?? 'Discard unsaved changes?',
        message: o.message ?? 'Your unsaved changes will be discarded.',
        confirmLabel: 'Discard changes',
        cancelLabel: 'Keep editing',
        danger: true,
    });
}

function prksBindModalConfirmOnce() {
    const root = document.getElementById('prks-modal-confirm');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    const cancel = document.getElementById('prks-modal-confirm-cancel');
    const ok = document.getElementById('prks-modal-confirm-ok');
    const scrim = document.getElementById('prks-modal-confirm-scrim');
    if (cancel) {
        cancel.addEventListener('click', () =>
            prksFinishModalConfirm(prksModalConfirmAlertOnly)
        );
    }
    if (ok) {
        ok.addEventListener('click', () => prksFinishModalConfirm(true));
    }
    if (scrim) {
        scrim.addEventListener('click', () =>
            prksFinishModalConfirm(prksModalConfirmAlertOnly)
        );
    }
    if (!window.__prksModalConfirmKeyBound) {
        window.__prksModalConfirmKeyBound = true;
        document.addEventListener(
            'keydown',
            (e) => {
                if (e.key !== 'Escape') return;
                if (!prksIsModalConfirmOpen()) return;
                e.preventDefault();
                e.stopPropagation();
                prksFinishModalConfirm(prksModalConfirmAlertOnly);
            },
            true
        );
    }
}

function prksBindModalUnsavedConfirmOnce() {
    const root = document.getElementById('prks-modal-unsaved-confirm');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    const cancel = document.getElementById('prks-modal-unsaved-confirm-cancel');
    const discard = document.getElementById('prks-modal-unsaved-confirm-discard');
    const scrim = document.getElementById('prks-modal-unsaved-confirm-scrim');
    if (cancel) {
        cancel.addEventListener('click', () => prksHideModalUnsavedConfirm());
    }
    if (discard) {
        discard.addEventListener('click', () => prksDiscardConfirmedClose());
    }
    if (scrim) {
        scrim.addEventListener('click', () => prksHideModalUnsavedConfirm());
    }
    if (!window.__prksModalUnsavedConfirmKeyBound) {
        window.__prksModalUnsavedConfirmKeyBound = true;
        document.addEventListener(
            'keydown',
            (e) => {
                if (e.key !== 'Escape') return;
                if (!prksIsModalUnsavedConfirmOpen()) return;
                e.preventDefault();
                e.stopPropagation();
                prksHideModalUnsavedConfirm();
            },
            true
        );
    }
}

function requestModalClose(reason) {
    const activeModalId = prksGetActiveModalId();
    if (activeModalId && prksModalHasUnsavedChanges(activeModalId)) {
        prksOpenModalUnsavedConfirm(() => {
            closeModals();
        });
        return false;
    }
    closeModals();
    return true;
}

function prksAutosizeTextarea(el) {
    if (!el || el.tagName !== 'TEXTAREA') return;
    if ((el.getAttribute && el.getAttribute('data-prks-role') === 'research-notes-editor') || el.id === 'research-notes-editor' || el.id === 'pdf-annotation-editor-text') return;
    const cs = window.getComputedStyle(el);
    const minH = parseFloat(cs.minHeight || '0');
    el.style.height = 'auto';
    const next = Math.max(el.scrollHeight || 0, Number.isFinite(minH) ? minH : 0);
    if (next > 0) {
        el.style.height = `${Math.ceil(next)}px`;
        el.dataset.prksAutosize = '1';
    }
}

function prksBindAutosizeTextareas(root = document) {
    const scope = root && typeof root.querySelectorAll === 'function' ? root : document;
    const textareas = scope.querySelectorAll(PRKS_AUTOSIZE_TEXTAREA_SELECTOR);
    textareas.forEach((el) => {
        if (!el || el.tagName !== 'TEXTAREA') return;
        if ((el.getAttribute && el.getAttribute('data-prks-role') === 'research-notes-editor') || el.id === 'research-notes-editor' || el.id === 'pdf-annotation-editor-text') return;
        if (el.dataset.prksAutosizeBound !== '1') {
            el.dataset.prksAutosizeBound = '1';
            el.addEventListener('input', () => prksAutosizeTextarea(el));
        }
        prksAutosizeTextarea(el);
    });
}

window.prksAutosizeTextarea = prksAutosizeTextarea;
window.prksBindAutosizeTextareas = prksBindAutosizeTextareas;

// Modal Logic
function openModal(id) {
    if (id === 'group-modal' && typeof prksOfflineGuardMutation === 'function') {
        if (prksOfflineGuardMutation('Creating a Person Group requires a connection to PRKS.')) return;
    }
    // `playlist-modal` is creation-only, so guarding here covers every caller at
    // once -- the Playlists page, the Work detail panel, the New File flow and
    // anything added later -- instead of relying on each surface to remember.
    if (id === 'playlist-modal' && typeof prksOfflineGuardMutation === 'function') {
        if (prksOfflineGuardMutation('Creating a Playlist requires a connection to PRKS.')) return;
    }
    // `person-modal` is creation-only, so guarding here covers every caller at
    // once -- the People page, the ribbon, the command palette and anything
    // added later -- instead of relying on each surface to remember.
    // `person-template-modal` is deliberately exempt: it only edits an unsaved
    // local draft and performs no canonical mutation of its own.
    if (id === 'person-modal' && typeof prksOfflineGuardMutation === 'function') {
        if (prksOfflineGuardMutation('Creating a Person requires a connection to PRKS.')) return;
    }
    if (typeof window.prksCloseTagsAliasModal === 'function') {
        window.prksCloseTagsAliasModal();
    }
    prksHideModalUnsavedConfirm();
    const backdrop = document.getElementById('modal-backdrop');
    if (backdrop && backdrop.classList.contains('hidden')) {
        const ae = document.activeElement;
        window.__prksModalFocusRestore = ae && ae !== document.body ? ae : window.__prksModalFocusRestore;
    }
    document.getElementById('modal-backdrop').classList.remove('hidden');
    document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));
    const modalEl = document.getElementById(id);
    modalEl.classList.remove('hidden');

    if (id === 'role-modal') {
        prepareRoleModal();
    } else if (id === 'work-modal') {
        resetUploadModal();
        const after = () => {
            if (typeof window.prksSetWorkModalFolderFromId === 'function') {
                window.prksSetWorkModalFolderFromId(
                    typeof window.prksFolderIdFromFocusedContext === 'function'
                        ? window.prksFolderIdFromFocusedContext()
                        : window.prksFolderIdFromLocation
                          ? window.prksFolderIdFromLocation()
                          : ''
                );
            }
            if (typeof window.prksSyncWorkModalDisclosureInert === 'function') {
                window.prksSyncWorkModalDisclosureInert();
            }
            if (typeof window.prksClearWorkModalErrors === 'function') {
                window.prksClearWorkModalErrors();
            }
            if (typeof window.prksSetWorkModalCreateBusy === 'function') {
                window.prksSetWorkModalCreateBusy(false);
            }
            if (typeof window.prksFocusWorkModalInitial === 'function') {
                window.prksFocusWorkModalInitial();
            }
            prksScheduleModalBaselineCapture('work-modal');
        };
        const p = populateUploadComboboxes();
        if (p && typeof p.then === 'function') {
            p.then(after).catch(after);
        } else {
            after();
        }
    } else if (id === 'person-modal') {
        resetPersonAliasAutoSyncState();
        syncPersonAliasesFromNames();
    } else if (id === 'folder-modal' && typeof window.prksRefreshFolderModalValidation === 'function') {
        const parentSearch = document.getElementById('folder-parent-search');
        const parentId = document.getElementById('folder-parent-id');
        if (parentSearch && !window.__prksPendingWorkFolderAttach) parentSearch.value = '';
        if (parentId && !window.__prksPendingWorkFolderAttach) parentId.value = '';
        window.prksRefreshFolderModalValidation();
    } else if (id === 'settings-modal' && typeof window.prksOpenSettingsToLastCategory === 'function') {
        window.prksOpenSettingsToLastCategory();
    } else if (id === 'group-modal' && typeof window.prksInitNewGroupModal === 'function') {
        void window.prksInitNewGroupModal();
    }
    requestAnimationFrame(() => prksBindAutosizeTextareas(modalEl));
    prksScheduleModalBaselineCapture(id);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(modalEl);
}

// —— In-app help hints (popover + Settings toggle) ——
const PRKS_LS_HINTS = 'prks.ui.hints';

function prksGetHintsEnabled() {
    try {
        const raw = localStorage.getItem(PRKS_LS_HINTS);
        if (raw == null) return true;
        if (raw === '1' || raw === 'true') return true;
        if (raw === '0' || raw === 'false') return false;
        return true;
    } catch (_e) {
        return true;
    }
}

function prksSetHintsEnabled(enabled) {
    try {
        localStorage.setItem(PRKS_LS_HINTS, enabled ? '1' : '0');
    } catch (_e) {}
}

function prksApplyHintsPreferenceToDocument() {
    document.documentElement.dataset.prksHints = prksGetHintsEnabled() ? 'on' : 'off';
}

/** Trusted HTML per hint key (shown inside the shared popover). */
const PRKS_HINT_HTML = {
    'ann-pdf':
        '<p>Highlights and comments from the file. Choose a row to jump. Use Edit/Add comment for notes. To link people to this file, use <strong>Link Person to Work</strong> on the work details tab.</p>',
    'route-new-folder':
        '<p>Use <strong>New Folder</strong> in the top ribbon to add a folder.</p>',
    'route-new-playlist':
        '<p>Use <strong>New playlist</strong> in the right-hand panel on this page.</p>',
    'route-new-person':
        '<p>Use <strong>New Person</strong> in the ribbon to add someone.</p>',
    'route-new-group':
        '<p>Use <strong>New group</strong> on this page or in the ribbon.</p>',
    'route-progress-filters':
        '<p>Use the sidebar progress links to switch status filters.</p>',
    'notes-private-file': '<p>Notes for this file (saved with your library).</p>',
    'notes-private-folder': '<p>Notes for this folder (saved with your library).</p>',
    'group-edit-parent':
        '<p>Search or type a new top-level parent name (created on save).</p>',
    'upload-biblio-details':
        '<p>Publisher, location (place of publication), journal, DOI, thumbnail page, and related bibliographic fields.</p>',
};

let __prksHintAnchor = null;

function prksEnsureHintPopoverEl() {
    let el = document.getElementById('prks-hint-popover');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'prks-hint-popover';
    el.className = 'prks-hint-popover hidden';
    el.setAttribute('role', 'region');
    el.setAttribute('aria-label', 'Help');
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<div class="prks-hint-popover__inner"></div>';
    document.body.appendChild(el);
    return el;
}

function prksCloseHintPopover() {
    const pop = document.getElementById('prks-hint-popover');
    if (!pop) return;
    pop.classList.add('hidden');
    pop.setAttribute('aria-hidden', 'true');
    const inner = pop.querySelector('.prks-hint-popover__inner');
    if (inner) inner.innerHTML = '';
    document.querySelectorAll('.prks-hint-btn[aria-expanded="true"]').forEach((b) => {
        b.setAttribute('aria-expanded', 'false');
    });
    __prksHintAnchor = null;
}

function prksPositionHintPopover(anchor) {
    const pop = document.getElementById('prks-hint-popover');
    if (!pop || !anchor) return;
    const margin = 8;
    const gap = 6;
    pop.classList.remove('hidden');
    const rect = anchor.getBoundingClientRect();
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;
    let top = rect.bottom + gap;
    let left = rect.left + rect.width / 2 - pw / 2;
    if (top + ph > window.innerHeight - margin) {
        top = Math.max(margin, rect.top - gap - ph);
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - margin - pw));
    top = Math.max(margin, Math.min(top, window.innerHeight - margin - ph));
    pop.style.top = `${Math.round(top)}px`;
    pop.style.left = `${Math.round(left)}px`;
}

function prksOpenHintPopover(anchor, hintType) {
    if (!prksGetHintsEnabled()) return;
    const html = PRKS_HINT_HTML[hintType];
    if (!html || !anchor) return;
    prksCloseHintPopover();
    const pop = prksEnsureHintPopoverEl();
    const inner = pop.querySelector('.prks-hint-popover__inner');
    if (inner) inner.innerHTML = html;
    __prksHintAnchor = anchor;
    anchor.setAttribute('aria-expanded', 'true');
    pop.classList.remove('hidden');
    pop.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
        prksPositionHintPopover(anchor);
    });
}

function initPrksHintUi() {
    prksApplyHintsPreferenceToDocument();
    if (window.__prksHintUiBound) return;
    window.__prksHintUiBound = true;
    prksEnsureHintPopoverEl();

    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('.prks-hint-btn[data-prks-hint-type]') : null;
        if (btn) {
            if (!prksGetHintsEnabled()) return;
            e.preventDefault();
            e.stopPropagation();
            const t = btn.getAttribute('data-prks-hint-type');
            const pop = document.getElementById('prks-hint-popover');
            const open = pop && !pop.classList.contains('hidden') && __prksHintAnchor === btn;
            if (open) {
                prksCloseHintPopover();
            } else {
                prksOpenHintPopover(btn, t);
            }
            return;
        }
        const pop = document.getElementById('prks-hint-popover');
        if (pop && !pop.classList.contains('hidden')) {
            if (pop.contains(e.target)) return;
            prksCloseHintPopover();
        }
    });

    document.addEventListener(
        'keydown',
        (e) => {
            if (e.key !== 'Escape') return;
            const pop = document.getElementById('prks-hint-popover');
            if (!pop || pop.classList.contains('hidden')) return;
            if (typeof prksAnyModalOpen === 'function' && prksAnyModalOpen()) return;
            e.preventDefault();
            prksCloseHintPopover();
        },
        true
    );

    window.addEventListener('resize', () => {
        if (__prksHintAnchor && document.getElementById('prks-hint-popover')?.classList.contains('hidden') === false) {
            prksPositionHintPopover(__prksHintAnchor);
        }
    });
}

window.prksApplyHintsPreferenceToDocument = prksApplyHintsPreferenceToDocument;
window.prksGetHintsEnabled = prksGetHintsEnabled;
window.prksSetHintsEnabled = prksSetHintsEnabled;
window.prksCloseHintPopover = prksCloseHintPopover;
window.initPrksHintUi = initPrksHintUi;

function prksIsSmallScreen() {
    try {
        if (document.documentElement.classList.contains('prks-force-mobile')) return true;
        return !!(window.matchMedia && window.matchMedia('(max-width: 900px)').matches);
    } catch (_e) {
        return false;
    }
}

function prksAnyModalOpen() {
    const any = document.querySelector('.modal:not(.hidden)');
    return !!any;
}

function initModalCloseUi() {
    prksBindModalUnsavedConfirmOnce();
    prksBindModalConfirmOnce();
    const backdrop = document.getElementById('modal-backdrop');
    if (!backdrop || backdrop.dataset.boundClose !== '1') {
        if (!backdrop) return;
        backdrop.dataset.boundClose = '1';
        backdrop.addEventListener('click', (e) => {
            if (e.target !== backdrop) return;
            requestModalClose('backdrop');
        });
    }
}

function prksSetOverlayBackdropVisible(visible) {
    const el = document.getElementById('prks-overlay-backdrop');
    if (!el) return;
    el.classList.toggle('hidden', !visible);
    el.setAttribute('aria-hidden', visible ? 'false' : 'true');
}

function prksSyncMobileToggleButtons() {
    const navBtn = document.getElementById('prks-mobile-nav-btn');
    const detBtn = document.getElementById('prks-mobile-details-btn');
    if (!navBtn && !detBtn) return;
    const sbOpen = document.body.classList.contains('prks-sidebar-open');
    const rpOpen = document.body.classList.contains('prks-right-panel-open');
    if (navBtn) navBtn.setAttribute('aria-expanded', sbOpen ? 'true' : 'false');
    if (detBtn) detBtn.setAttribute('aria-expanded', rpOpen ? 'true' : 'false');
}

function prksIsTiledWorkspace() {
    const app = document.getElementById('app-container');
    return !!(app && app.classList.contains('app-container--tiled'));
}

function prksPrepareSidebarRailTooltips() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    const controls = sidebar.querySelectorAll(
        '.nav-menu > li > .nav-link, .nav-menu > li > .nav-disclosure__row > .nav-link, .nav-menu > li > .nav-disclosure__row > .nav-disclosure__toggle--full'
    );
    for (let i = 0; i < controls.length; i++) {
        const label = controls[i].querySelector('.nav-link__label');
        if (label && label.textContent) controls[i].title = label.textContent.trim();
    }
}

function prksSyncDenseWorkspaceShell(tiled) {
    const app = document.getElementById('app-container');
    if (!app) return;
    const wasTiled = app.classList.contains('app-container--tiled');
    const dense = !!tiled && !prksIsSmallScreen();
    app.classList.toggle('app-container--tiled', dense);
    if (wasTiled && !dense) prksCloseOverlays();
    prksSyncMobileToggleButtons();
}

function prksCloseOverlays() {
    if (typeof window.prksCloseTagsAliasModal === 'function') {
        window.prksCloseTagsAliasModal();
    }
    document.body.classList.remove('prks-sidebar-open', 'prks-right-panel-open', 'prks-overlay-open');
    prksSetOverlayBackdropVisible(false);
    prksSyncMobileToggleButtons();
}

function prksOpenSidebarDrawer() {
    document.body.classList.add('prks-sidebar-open', 'prks-overlay-open');
    document.body.classList.remove('prks-right-panel-open');
    prksSetOverlayBackdropVisible(true);
    prksSyncMobileToggleButtons();
}

function prksOpenRightPanelOverlay() {
    const app = document.getElementById('app-container');
    if (app && app.classList.contains('app-container--hide-right-panel')) return;
    document.body.classList.add('prks-right-panel-open');
    document.body.classList.remove('prks-sidebar-open');
    if (prksIsSmallScreen()) {
        document.body.classList.add('prks-overlay-open');
        prksSetOverlayBackdropVisible(true);
    } else {
        document.body.classList.remove('prks-overlay-open');
        prksSetOverlayBackdropVisible(false);
    }
    prksSyncMobileToggleButtons();
}

function prksToggleSidebarDrawer(forceOpen) {
    if (!prksIsSmallScreen() && !prksIsTiledWorkspace()) return;
    const open = document.body.classList.contains('prks-sidebar-open');
    const want = forceOpen === undefined ? !open : !!forceOpen;
    if (want) prksOpenSidebarDrawer();
    else prksCloseOverlays();
}

function prksToggleRightPanelOverlay(forceOpen) {
    if (!prksIsSmallScreen() && !prksIsTiledWorkspace()) return;
    const open = document.body.classList.contains('prks-right-panel-open');
    const want = forceOpen === undefined ? !open : !!forceOpen;
    if (want) prksOpenRightPanelOverlay();
    else prksCloseOverlays();
}

function initMobileShell() {
    const navBtn = document.getElementById('prks-mobile-nav-btn');
    const detBtn = document.getElementById('prks-mobile-details-btn');
    const closeBtn = document.getElementById('prks-right-panel-close');
    const overlayBackdrop = document.getElementById('prks-overlay-backdrop');
    const expandBtn = document.getElementById('prks-sidebar-expand-btn');
    const collapseBtn = document.getElementById('prks-sidebar-collapse-btn');

    prksPrepareSidebarRailTooltips();

    if (navBtn && navBtn.dataset.bound !== '1') {
        navBtn.dataset.bound = '1';
        navBtn.addEventListener('click', () => prksToggleSidebarDrawer());
    }
    if (detBtn && detBtn.dataset.bound !== '1') {
        detBtn.dataset.bound = '1';
        detBtn.addEventListener('click', () => prksToggleRightPanelOverlay());
    }
    if (closeBtn && closeBtn.dataset.bound !== '1') {
        closeBtn.dataset.bound = '1';
        closeBtn.addEventListener('click', () => {
            prksToggleRightPanelOverlay(false);
            if (detBtn && typeof detBtn.focus === 'function') detBtn.focus();
        });
    }
    if (expandBtn && expandBtn.dataset.bound !== '1') {
        expandBtn.dataset.bound = '1';
        expandBtn.addEventListener('click', () => prksToggleSidebarDrawer(true));
    }
    if (collapseBtn && collapseBtn.dataset.bound !== '1') {
        collapseBtn.dataset.bound = '1';
        collapseBtn.addEventListener('click', () => prksToggleSidebarDrawer(false));
    }
    if (overlayBackdrop && overlayBackdrop.dataset.bound !== '1') {
        overlayBackdrop.dataset.bound = '1';
        overlayBackdrop.addEventListener('click', () => {
            if (prksAnyModalOpen()) return;
            prksCloseOverlays();
        });
    }

    if (!window.__prksMobileShellKeyBound) {
        window.__prksMobileShellKeyBound = true;
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (prksAnyModalOpen()) return;
            const anyOverlay =
                document.body.classList.contains('prks-sidebar-open') ||
                document.body.classList.contains('prks-right-panel-open');
            if (anyOverlay) {
                e.preventDefault();
                prksCloseOverlays();
            }
        });
    }

    // If a user rotates or resizes to desktop, ensure drawers are not stuck open.
    if (!window.__prksMobileShellResizeBound) {
        window.__prksMobileShellResizeBound = true;
        window.addEventListener('resize', () => {
            if (!prksIsSmallScreen()) {
                prksCloseOverlays();
            }
            if (typeof window.prksWorkspaceVisualTiled === 'function') {
                prksSyncDenseWorkspaceShell(window.prksWorkspaceVisualTiled());
            }
        });
    }

    prksSyncMobileToggleButtons();
}

function isNameInitialToken(token) {
    const t = token.trim();
    if (!t) return false;
    if (t.length === 1) return /^[A-Za-z]$/.test(t);
    if (t.length === 2 && t.endsWith('.')) return /^[A-Za-z]\.$/.test(t);
    return false;
}

function stripInitialsFromFirstName(firstName) {
    const parts = firstName.trim().split(/\s+/).filter(Boolean);
    return parts.filter(p => !isNameInitialToken(p)).join(' ');
}

function normalizePersonNameKey(s) {
    return s.trim().replace(/\s+/g, ' ').toLowerCase();
}

/** Comma-separated alias suggestions from first + last name (e.g. Theodor W. + Adorno → Theodor Adorno, Adorno). Omits a “full name” alias when it matches first+last as entered (John + Smith → Smith only). */
function buildPersonAliasSuggestions(firstName, lastName) {
    const fn = firstName.trim();
    const ln = lastName.trim();
    if (!ln && !fn) return '';

    const suggestions = [];
    const stripped = stripInitialsFromFirstName(fn);
    const coreFirst = stripped || fn;
    const literalFullKey =
        fn && ln ? normalizePersonNameKey(`${fn} ${ln}`) : '';

    if (ln) {
        if (coreFirst) {
            const full = `${coreFirst} ${ln}`.trim().replace(/\s+/g, ' ');
            if (!literalFullKey || normalizePersonNameKey(full) !== literalFullKey) {
                suggestions.push(full);
            }
        }
        suggestions.push(ln);
    } else if (fn) {
        suggestions.push(fn);
    }

    return [...new Set(suggestions)].join(', ');
}

function resetPersonAliasAutoSyncState() {
    window._personAliasesManual = false;
}

function syncPersonAliasesFromNames() {
    if (window._personAliasesManual) return;
    const fname = document.getElementById('person-fname');
    const lname = document.getElementById('person-lname');
    const aliases = document.getElementById('person-aliases');
    if (!fname || !lname || !aliases) return;
    aliases.value = buildPersonAliasSuggestions(fname.value, lname.value);
}

async function populateFolderDropdown() {
    const folders = await fetchFolders();
    const select = document.getElementById('work-folder-id');
    if (!select) return;
    const existingVal = select.value;
    select.innerHTML = `<option value="">(No Folder)</option>` +
        folders.map(f => `<option value="${f.id}">${f.title}</option>`).join('');

    if (existingVal && folders.find(f => f.id === existingVal)) {
        select.value = existingVal;
    } else if (typeof prksParseRoute === 'function' && prksParseRoute(window.location.hash).name === 'folder-detail') {
        select.value = prksParseRoute(window.location.hash).params.folderId || '';
    } else if (window.location.hash.startsWith('#/folders/')) {
        select.value = window.location.hash.split('/')[2];
    }
}

function closeModals() {
    prksHideModalUnsavedConfirm();
    const playlistModal = document.getElementById('playlist-modal');
    const playlistWasOpen = playlistModal && !playlistModal.classList.contains('hidden');
    document.getElementById('modal-backdrop').classList.add('hidden');
    document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));

    // If playlist modal was opened from "New File" flow, return to it.
    if (playlistWasOpen && window.__prksReturnToWorkModalAfterPlaylist === true) {
        window.__prksReturnToWorkModalAfterPlaylist = false;
        document.getElementById('modal-backdrop').classList.remove('hidden');
        const workModal = document.getElementById('work-modal');
        if (workModal) workModal.classList.remove('hidden');
        if (typeof window.prksSyncUploadModalKindUi === 'function') {
            window.prksSyncUploadModalKindUi();
        }
        if (typeof window.__prksRefreshAllPlaylistSelects === 'function') {
            void window.__prksRefreshAllPlaylistSelects();
        }
        return;
    }
    const restore = window.__prksModalFocusRestore;
    window.__prksModalFocusRestore = null;
    if (restore && typeof restore.focus === 'function') {
        try {
            if (!document.contains(restore) && restore !== document.body) {
                /* skip */
            } else {
                restore.focus({ preventScroll: true });
            }
        } catch (_e) {}
    }
    prksResetModalBaselines();
}

window.requestModalClose = requestModalClose;
window.initModalCloseUi = initModalCloseUi;
window.prksConfirmDialog = prksConfirmDialog;
window.prksAlertDialog = prksAlertDialog;
window.prksShowDuplicateRoleLinkAlert = prksShowDuplicateRoleLinkAlert;
window.prksIsDuplicateRoleLinkError = prksIsDuplicateRoleLinkError;
window.prksNotifyRoleLinkFailure = prksNotifyRoleLinkFailure;
window.prksAlertMessage = prksAlertMessage;
window.prksConfirmDestructive = prksConfirmDestructive;
window.prksConfirmUnsavedRouteLeave = prksConfirmUnsavedRouteLeave;
window.prksPromptTextDialog = prksPromptTextDialog;
window.prksSetButtonBusy = prksSetButtonBusy;
window.prksFlashButtonLabel = prksFlashButtonLabel;
window.prksConfirmDeletePdfAnnotation = prksConfirmDeletePdfAnnotation;

function personDisplayName(p) {
    return `${(p.first_name || '').trim()} ${p.last_name || ''}`.trim();
}

function prksPersonCanonicalName(p) {
    return personDisplayName(p);
}

function prksParsePersonAliases(p) {
    const raw = p && p.aliases != null ? String(p.aliases) : '';
    return raw
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
}

/** Display name for a role row: per-file credit_name override or profile name. */
function prksRoleDisplayName(role) {
    const credit = role && role.credit_name != null ? String(role.credit_name).trim() : '';
    if (credit) return credit;
    return personDisplayName(role);
}

function prksRoleCreditPickerHtml(prefix) {
    const p = String(prefix || 'role');
    return `
        <div class="prks-role-credit-picker" id="${p}-credit-wrap" hidden>
            <label class="prks-role-credit-picker__heading" for="${p}-credit-input">Name on this file</label>
            <p id="${p}-credit-profile" class="prks-role-credit-picker__profile meta-row meta-row--hint"></p>
            <div id="${p}-credit-shell" class="tag-add-shell tag-add-shell--flush prks-role-credit-picker__shell prks-role-credit-picker__shell--off">
                <div class="tag-add-shell__field prks-role-credit-picker__field">
                    <label class="prks-role-credit-picker__check" for="${p}-credit-enable" title="Use a different name on this file">
                        <input type="checkbox" id="${p}-credit-enable" class="prks-role-credit-picker__enable-input" aria-label="Use a different name on this file">
                    </label>
                    <input type="text" id="${p}-credit-input" class="tag-add-shell__input prks-role-credit-picker__input" disabled placeholder="Uses profile name" autocomplete="off" aria-label="Name shown on this file">
                </div>
            </div>
            <div id="${p}-credit-aliases" class="prks-role-credit-picker__aliases" hidden role="group" aria-label="Known aliases"></div>
            <p class="meta-row meta-row--hint meta-row--compact">Check the box to override; pick an alias or type a name.</p>
        </div>`;
}

function prksSyncRoleCreditPickerEnabled(prefix) {
    const enable = document.getElementById(`${prefix}-credit-enable`);
    const input = document.getElementById(`${prefix}-credit-input`);
    const shell = document.getElementById(`${prefix}-credit-shell`);
    const on = !!(enable && enable.checked);
    if (shell) {
        shell.classList.toggle('prks-role-credit-picker__shell--off', !on);
    }
    if (input) {
        input.disabled = !on;
        input.placeholder = on ? 'Name on this file' : 'Uses profile name';
        if (!on) input.value = '';
    }
}

function prksResetRoleCreditPicker(prefix) {
    const enable = document.getElementById(`${prefix}-credit-enable`);
    const aliasesWrap = document.getElementById(`${prefix}-credit-aliases`);
    const profileEl = document.getElementById(`${prefix}-credit-profile`);
    if (enable) enable.checked = false;
    prksSyncRoleCreditPickerEnabled(prefix);
    if (profileEl) profileEl.textContent = '';
    if (aliasesWrap) {
        aliasesWrap.hidden = true;
        aliasesWrap.innerHTML = '';
    }
}

function prksSyncRoleCreditAliasChips(prefix, value) {
    const aliasesWrap = document.getElementById(`${prefix}-credit-aliases`);
    if (!aliasesWrap) return;
    const v = String(value || '').trim().toLowerCase();
    aliasesWrap.querySelectorAll('.prks-role-credit-picker__alias-chip').forEach((btn) => {
        const alias = String(btn.getAttribute('data-alias') || '').trim().toLowerCase();
        btn.classList.toggle('prks-role-credit-picker__alias-chip--active', !!v && alias === v);
    });
}

function prksClearRoleCreditAliasChips(prefix) {
    prksSyncRoleCreditAliasChips(prefix, '');
}

function prksSetRoleCreditPickerValue(prefix, creditName) {
    const enable = document.getElementById(`${prefix}-credit-enable`);
    const input = document.getElementById(`${prefix}-credit-input`);
    const credit = String(creditName || '').trim();
    if (!enable || !input) return;
    if (!credit) {
        enable.checked = false;
        input.value = '';
        input.disabled = true;
        prksClearRoleCreditAliasChips(prefix);
        prksSyncRoleCreditPickerEnabled(prefix);
        return;
    }
    enable.checked = true;
    input.value = credit;
    prksSyncRoleCreditPickerEnabled(prefix);
    prksSyncRoleCreditAliasChips(prefix, credit);
}

function prksRefreshRoleCreditPicker(prefix, person) {
    const wrap = document.getElementById(`${prefix}-credit-wrap`);
    if (!wrap) return;
    if (!person || !person.id) {
        wrap.hidden = true;
        prksResetRoleCreditPicker(prefix);
        return;
    }
    wrap.hidden = false;
    const profileEl = document.getElementById(`${prefix}-credit-profile`);
    const aliasesWrap = document.getElementById(`${prefix}-credit-aliases`);
    const canonical = prksPersonCanonicalName(person);
    if (profileEl) {
        profileEl.textContent = canonical ? `Profile name: ${canonical}` : '';
    }
    prksResetRoleCreditPicker(prefix);

    const aliases = prksParsePersonAliases(person);
    if (aliasesWrap) {
        if (aliases.length) {
            aliasesWrap.hidden = false;
            aliasesWrap.innerHTML = `<span class="prks-role-credit-picker__aliases-label">Aliases</span><div class="prks-role-credit-picker__alias-chips">${aliases
                .map(
                    (alias) =>
                        `<button type="button" class="prks-role-credit-picker__alias-chip" data-alias="${prksEscapeAttr(alias)}">${escapeHtml(alias)}</button>`
                )
                .join('')}</div>`;
        } else {
            aliasesWrap.hidden = true;
            aliasesWrap.innerHTML = '';
        }
    }
    prksBindRoleCreditPicker(prefix);
}

function prksBindRoleCreditPicker(prefix) {
    const wrap = document.getElementById(`${prefix}-credit-wrap`);
    if (!wrap || wrap.dataset.bound === '1') return;
    wrap.dataset.bound = '1';
    wrap.addEventListener('change', (e) => {
        const enable = document.getElementById(`${prefix}-credit-enable`);
        const input = document.getElementById(`${prefix}-credit-input`);
        const t = e.target;
        if (!enable || !input) return;

        if (t.id === `${prefix}-credit-enable`) {
            prksSyncRoleCreditPickerEnabled(prefix);
            if (enable.checked) {
                input.focus();
            } else {
                prksClearRoleCreditAliasChips(prefix);
            }
            return;
        }
    });
    wrap.addEventListener('click', (e) => {
        const chip = e.target.closest('.prks-role-credit-picker__alias-chip');
        if (!chip || !wrap.contains(chip)) return;
        const enable = document.getElementById(`${prefix}-credit-enable`);
        const input = document.getElementById(`${prefix}-credit-input`);
        const alias = String(chip.getAttribute('data-alias') || '').trim();
        if (!alias || !enable || !input) return;
        enable.checked = true;
        input.value = alias;
        prksSyncRoleCreditPickerEnabled(prefix);
        prksSyncRoleCreditAliasChips(prefix, alias);
        input.focus();
    });
    wrap.addEventListener('input', (e) => {
        if (e.target.id !== `${prefix}-credit-input`) return;
        const enable = document.getElementById(`${prefix}-credit-enable`);
        const input = e.target;
        const val = String(input.value || '').trim();
        if (val) {
            if (enable) enable.checked = true;
            prksSyncRoleCreditPickerEnabled(prefix);
            prksSyncRoleCreditAliasChips(prefix, val);
        } else {
            prksClearRoleCreditAliasChips(prefix);
        }
    });
}

function prksCreditPickerPrefixForHiddenId(hiddenId) {
    const map = {
        'meta-role-person-id': 'meta-role',
        'role-person-id': 'role-link',
        'upload-person-id': 'upload-role',
    };
    return map[String(hiddenId || '').trim()] || '';
}

/** Credit from picker; optional fallback when search label differs from profile name. */
function prksResolveRoleCreditNameForLink(prefix, personId, searchInputId) {
    const credit = prksReadRoleCreditName(prefix);
    if (credit) return credit;
    const person = prksFindPersonInCache(personId);
    const canonical = person ? prksPersonCanonicalName(person) : '';
    const searchEl =
        typeof searchInputId === 'string' ? document.getElementById(searchInputId) : searchInputId;
    const searchLabel = searchEl ? String(searchEl.value || '').trim() : '';
    if (searchLabel && canonical && searchLabel.toLowerCase() !== canonical.toLowerCase()) {
        return searchLabel;
    }
    return '';
}

function prksReadRoleCreditName(prefix) {
    const enable = document.getElementById(`${prefix}-credit-enable`);
    const input = document.getElementById(`${prefix}-credit-input`);
    if (!enable || !enable.checked || !input) return '';
    return String(input.value || '').trim();
}

function prksFindPersonInCache(personId) {
    const id = String(personId || '').trim();
    if (!id) return null;
    const lists = [
        window.allPersons,
        typeof allPersons !== 'undefined' ? allPersons : null,
    ];
    for (const list of lists) {
        if (!Array.isArray(list)) continue;
        const hit = list.find((p) => String(p.id) === id);
        if (hit) return hit;
    }
    return null;
}

/** Text prompt using confirm shell. Resolves trimmed string on OK, null on cancel. */
function prksPromptTextDialog(options = {}) {
    return new Promise((resolve) => {
        const root = document.getElementById('prks-modal-confirm');
        const titleEl = document.getElementById('prks-modal-confirm-title');
        const descEl = document.getElementById('prks-modal-confirm-desc');
        const cancelBtn = document.getElementById('prks-modal-confirm-cancel');
        const okBtn = document.getElementById('prks-modal-confirm-ok');
        const actions = document.querySelector('.prks-modal-confirm__actions');
        if (!root || !descEl || !okBtn || !cancelBtn) {
            resolve(null);
            return;
        }
        const title = options.title != null ? String(options.title) : 'Edit';
        const message = options.message != null ? String(options.message) : '';
        const defaultValue = options.defaultValue != null ? String(options.defaultValue) : '';
        const okLabel = options.okLabel != null ? String(options.okLabel) : 'Save';
        const cancelLabel = options.cancelLabel != null ? String(options.cancelLabel) : 'Cancel';
        const multiline = options.multiline === true;

        if (titleEl) titleEl.textContent = title;
        descEl.textContent = '';
        descEl.classList.toggle('prks-modal-confirm__desc--prompt', true);
        if (message) {
            const p = document.createElement('span');
            p.className = 'prks-modal-prompt__hint';
            p.textContent = message;
            descEl.appendChild(p);
        }
        const input = document.createElement(multiline ? 'textarea' : 'input');
        if (!multiline) input.type = 'text';
        else input.rows = options.rows != null ? Number(options.rows) || 8 : 8;
        input.className =
            'prks-modal-prompt__input' + (multiline ? ' prks-modal-prompt__input--multiline' : '');
        input.value = defaultValue;
        input.setAttribute('aria-label', title);
        input.setAttribute('autocomplete', 'off');
        descEl.appendChild(input);

        cancelBtn.classList.remove('hidden');
        cancelBtn.setAttribute('aria-hidden', 'false');
        cancelBtn.textContent = cancelLabel;
        if (actions) actions.classList.remove('prks-modal-confirm__actions--alertOnly');
        okBtn.textContent = okLabel;
        okBtn.classList.remove('prks-btn--danger');
        okBtn.classList.add('prks-btn--primary');
        prksModalConfirmAlertOnly = false;

        if (!multiline) {
            input.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter' || e.shiftKey) return;
                e.preventDefault();
                prksFinishModalConfirm(true);
            });
        }

        prksModalConfirmResolve = (confirmed) => {
            const val = confirmed ? String(input.value || '').trim() : null;
            descEl.classList.remove('prks-modal-confirm__desc--prompt');
            descEl.textContent = '';
            resolve(val);
        };
        root.classList.remove('hidden');
        root.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => {
            input.focus();
            if (typeof input.select === 'function' && !multiline) input.select();
        });
    });
}

async function prksPatchRoleCreditName(workId, personId, roleType, orderIndex, creditName) {
    const res = await prksRequest(`/api/works/${encodeURIComponent(workId)}/roles`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            person_id: personId,
            role_type: roleType,
            order_index: orderIndex,
            credit_name: creditName || '',
        }),
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, data };
}

async function prksEditRoleCreditOnWork(btn) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (!btn) return;
    const workId = (btn.getAttribute('data-work-id') || '').trim();
    const personId = (btn.getAttribute('data-person-id') || '').trim();
    const roleType = (btn.getAttribute('data-role-type') || '').trim();
    const orderIndex = (btn.getAttribute('data-order-index') || '0').trim();
    const canonical = (btn.getAttribute('data-canonical-name') || '').trim();
    const currentDisplay = (btn.getAttribute('data-display-name') || '').trim();
    if (!workId || !personId || !roleType) return;

    let hint = canonical ? `Profile name: ${canonical}. Leave empty to use profile name.` : 'Leave empty to use profile name.';
    const person = prksFindPersonInCache(personId);
    const aliases = person ? prksParsePersonAliases(person) : [];
    if (aliases.length) {
        hint += ` Aliases: ${aliases.join(', ')}.`;
    }
    const initial =
        currentDisplay && canonical && currentDisplay === canonical ? '' : currentDisplay;

    const next = await prksPromptTextDialog({
        title: 'Name on this file',
        message: hint,
        defaultValue: initial,
        okLabel: 'Save',
    });
    if (next === null) return;

    const { ok, data } = await prksPatchRoleCreditName(
        workId,
        personId,
        roleType,
        parseInt(orderIndex, 10) || 0,
        next
    );
    if (!ok) {
        await prksAlertMessage(data.error || 'Could not update name on file.', 'Could not save');
        return;
    }
    // Every role type stales People; Author additionally stales cached
    // Argument source authors. One helper owns both dependencies.
    const coherenceToken =
        typeof prksMarkWorkRoleChanged === 'function'
            ? prksMarkWorkRoleChanged(workId, roleType)
            : typeof prksOfflineMarkEntityChanged === 'function'
              ? prksOfflineMarkEntityChanged('work', workId)
              : null;
    await prksRefreshUiAfterWorkRoleRemoved(workId, ownerCtx, coherenceToken);
}

window.prksRoleDisplayName = prksRoleDisplayName;
window.prksEditRoleCreditOnWork = prksEditRoleCreditOnWork;

/** Split typed display name: mononym->last, otherwise all-but-last->first and last token->last. */
function prksSplitTypedPersonName(name) {
    const parts = String(name || '')
        .trim()
        .split(/\s+/);
    if (parts.length === 0 || (parts.length === 1 && !parts[0])) {
        return { first_name: '', last_name: '' };
    }
    if (parts.length === 1) {
        return { first_name: '', last_name: parts[0] };
    }
    return {
        first_name: parts.slice(0, -1).join(' '),
        last_name: parts[parts.length - 1],
    };
}

/** Create a person from the Link Person to Work modal and select them for linking. */
async function prksQuickCreatePersonForSearchField(typedName, searchInputRef, hiddenInputRef, aboutText) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const trimmed = String(typedName || '').trim();
    if (!trimmed) {
        await prksAlertMessage('Type a name in the Person field first.', 'Validation');
        return;
    }
    const { first_name, last_name } = prksSplitTypedPersonName(trimmed);
    // Connectivity can change between the guard above and the request.
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    try {
        const res = await prksRequest('/api/persons', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                first_name,
                last_name,
                aliases: '',
                about: aboutText || 'Quick-created person',
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            await prksAlertMessage(data.error || 'Could not create person.', 'Could not save');
            return;
        }
        // Canonical success owns coherence -- never the fetchPersons() refresh
        // below, which is only UI state.
        if (typeof prksMarkPeopleDomainChanged === 'function') prksMarkPeopleDomainChanged();
        allPersons = await fetchPersons();
        window.allPersons = allPersons;
        window.__prksProcessingPeople = allPersons;
        const newPerson = allPersons.find((p) => String(p.id) === String(data.id));
        const personSearch =
            typeof searchInputRef === 'string' ? document.getElementById(searchInputRef) : searchInputRef;
        const personHidden =
            typeof hiddenInputRef === 'string' ? document.getElementById(hiddenInputRef) : hiddenInputRef;
        if (personHidden) personHidden.value = data.id;
        if (personSearch) {
            personSearch.value = newPerson ? personDisplayName(newPerson) : trimmed;
        }
        const hiddenId =
            typeof hiddenInputRef === 'string' ? hiddenInputRef : hiddenInputRef && hiddenInputRef.id;
        const prefix = prksCreditPickerPrefixForHiddenId(hiddenId);
        if (prefix && newPerson) {
            prksRefreshRoleCreditPicker(prefix, newPerson);
            const canonical = prksPersonCanonicalName(newPerson);
            if (trimmed && canonical && trimmed.toLowerCase() !== canonical.toLowerCase()) {
                prksSetRoleCreditPickerValue(prefix, trimmed);
            }
        }
    } catch (e) {
        console.error(e);
        await prksAlertMessage('Could not create person.', 'Error');
    }
}

async function prksQuickCreatePersonForRoleLink(typedName) {
    await prksQuickCreatePersonForSearchField(
        typedName,
        'role-person-search',
        'role-person-id',
        'Quick-created from Link Person to Work'
    );
}

async function initWorkMetaRoleLinker(workId) {
    if (!workId) return;
    if (!Array.isArray(allPersons) || allPersons.length === 0) {
        allPersons = await fetchPersons();
        window.allPersons = allPersons;
    }
    initSearchableCombobox('meta-role-person-search', 'meta-role-person-results', 'meta-role-person-id', 'person', {
        onQuickCreate: (typedName) => {
            void prksQuickCreatePersonForSearchField(
                typedName,
                'meta-role-person-search',
                'meta-role-person-id',
                'Quick-created from Edit Metadata'
            );
        },
        onPersonPick: (person) => prksRefreshRoleCreditPicker('meta-role', person),
    });
    prksBindRoleCreditPicker('meta-role');
    const workHidden = document.getElementById('meta-role-work-id');
    if (workHidden) workHidden.value = String(workId);
    const roleHidden = document.getElementById('meta-role-type');
    const roleSel = roleHidden && roleHidden.value ? roleHidden.value : 'Author';
    if (typeof prksMountMetaRoleSegmented === 'function') {
        prksMountMetaRoleSegmented(roleSel);
    }
}

async function addRoleToWorkFromMetaEditor(workId) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const personHidden = document.getElementById('meta-role-person-id');
    const personSearch = document.getElementById('meta-role-person-search');
    const roleHidden = document.getElementById('meta-role-type');
    const list = document.getElementById('meta-linked-persons-list');
    const addBtn = document.getElementById('meta-role-add-btn');

    const resolvedWorkId = String(workId || '').trim();
    const personId = personHidden ? String(personHidden.value || '').trim() : '';
    const roleType = roleHidden ? String(roleHidden.value || '').trim() : '';
    if (!resolvedWorkId || !personId || !roleType) {
        await prksAlertMessage('Select a person and role first.', 'Validation');
        return;
    }
    const _cw = typeof prksFocusedEntity === 'function' ? prksFocusedEntity('work') : null;
    const existingRoles =
        _cw && String(_cw.id) === resolvedWorkId
            ? _cw.roles
            : [];
    if (prksWorkHasRoleLink(existingRoles, personId, roleType)) {
        await prksShowDuplicateRoleLinkAlert(roleType);
        return;
    }

    if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = 'Linking...';
    }
    try {
        const creditName = prksResolveRoleCreditNameForLink(
            'meta-role',
            personId,
            'meta-role-person-search'
        );
        const res = await prksRequest('/api/roles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                person_id: personId,
                work_id: resolvedWorkId,
                role_type: roleType,
                credit_name: creditName,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            await prksNotifyRoleLinkFailure(data.error, roleType);
            return;
        }
        // Every role type stales People (assigned_roles, the Person's linked
        // Work rows, and aliases via credit names); Author additionally stales
        // cached Argument source authors.
        const coherenceToken =
            typeof prksMarkWorkRoleChanged === 'function'
                ? prksMarkWorkRoleChanged(resolvedWorkId, roleType)
                : typeof prksOfflineMarkEntityChanged === 'function'
                  ? prksOfflineMarkEntityChanged('work', resolvedWorkId)
                  : null;
        if (typeof fetchWorkDetails === 'function') {
            const _refreshed = await fetchWorkDetails(resolvedWorkId);
            if (_refreshed && typeof prksOfflineCacheEntityIfCurrent === 'function' && coherenceToken != null) {
                void prksOfflineCacheEntityIfCurrent('work', resolvedWorkId, _refreshed, coherenceToken);
            }
            const applied =
                typeof prksApplyOwnedWorkEntity === 'function'
                    ? prksApplyOwnedWorkEntity(ownerCtx, resolvedWorkId, _refreshed)
                    : false;
            if (applied && prksOwnerTabIsFocused(ownerCtx) && list && _refreshed) {
                list.innerHTML = buildWorkLinkedPersonsHtml(_refreshed);
            }
            if (applied && prksOwnerTabIsFocused(ownerCtx)) {
                if (personHidden) personHidden.value = '';
                if (personSearch) personSearch.value = '';
                prksRefreshRoleCreditPicker('meta-role', null);
            }
        } else if (prksOwnerTabIsFocused(ownerCtx)) {
            if (personHidden) personHidden.value = '';
            if (personSearch) personSearch.value = '';
            prksRefreshRoleCreditPicker('meta-role', null);
        }
    } catch (e) {
        console.error(e);
        await prksAlertDialog({
            title: 'Could not link',
            message: 'Could not create link.',
        });
    } finally {
        if (addBtn) {
            addBtn.disabled = false;
            addBtn.textContent = '+ Link';
        }
    }
}

async function prepareRoleModal() {
    allPersons = await fetchPersons();
    allWorks = await fetchWorks();
    window.allPersons = allPersons;

    initSearchableCombobox('role-person-search', 'role-person-results', 'role-person-id', 'person', {
        onQuickCreate: (typedName) => {
            void prksQuickCreatePersonForRoleLink(typedName);
        },
        onPersonPick: (person) => prksRefreshRoleCreditPicker('role-link', person),
    });
    initSearchableCombobox('role-work-search', 'role-work-results', 'role-work-id', 'work');
    prksBindRoleCreditPicker('role-link');
    if (typeof prksMountLinkRoleSegmented === 'function') {
        const roleHidden = document.getElementById('role-type');
        prksMountLinkRoleSegmented(roleHidden ? roleHidden.value : 'Author');
    }

    const hash = window.location.hash || '';
    const personSearch = document.getElementById('role-person-search');
    const personHidden = document.getElementById('role-person-id');
    const workSearch = document.getElementById('role-work-search');
    const workHidden = document.getElementById('role-work-id');

    personSearch.value = '';
    personHidden.value = '';
    prksRefreshRoleCreditPicker('role-link', null);
    workSearch.value = '';
    workHidden.value = '';

    if (
        typeof prksParseRoute === 'function'
            ? prksParseRoute(hash).name === 'person'
            : hash.startsWith('#/people/') &&
              !hash.startsWith('#/people/role/') &&
              !hash.startsWith('#/people/groups')
    ) {
        const pid = typeof prksParseRoute === 'function' ? prksParseRoute(hash).params.personId : hash.split('/')[2];
        const p = allPersons.find(x => String(x.id) === String(pid));
        if (p) {
            personHidden.value = p.id;
            personSearch.value = personDisplayName(p);
            prksRefreshRoleCreditPicker('role-link', p);
        }
    }

    let workId = null;
    if (typeof prksParseRoute === 'function') {
        const wr = prksParseRoute(hash);
        if (wr.name === 'work') workId = wr.params.workId || null;
    } else if (hash.startsWith('#/works/')) {
        workId = hash.split('/')[2];
    }
    const _cwModal = typeof prksFocusedEntity === 'function' ? prksFocusedEntity('work') : null;
    if (!workId && _cwModal && _cwModal.id) {
        workId = _cwModal.id;
    }
    if (workId) {
        const w = allWorks.find(x => String(x.id) === String(workId));
        if (w) {
            workHidden.value = w.id;
            workSearch.value = w.title || '';
        }
    }
}

function prksSetTabActive(btn, on) {
    if (!btn || !btn.classList) return;
    btn.classList.toggle('active', !!on);
    btn.classList.toggle('is-active', !!on);
}

function initTabs() {
    const btns = document.querySelectorAll('#right-panel .tabs .tab-btn');
    btns.forEach((btn) => {
        btn.addEventListener('click', (e) => {
            const t = e.target;
            if (!t || !t.classList || !t.classList.contains('tab-btn')) return;
            const target = t.getAttribute('data-target') || 'details';
            const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
            if (ctx && ctx.ui) {
                ctx.ui.rightPanelTab = target;
            }
            if (typeof prksSyncRightPanelTabStrip === 'function') prksSyncRightPanelTabStrip(target);
            updatePanelContent(target);
        });
    });
}

function prksSidebarEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function activateRightPanelDetailsTab() {
    const rp = document.getElementById('right-panel');
    if (!rp) return;
    rp.querySelectorAll('.tab-btn').forEach((b) => prksSetTabActive(b, false));
    prksSetTabActive(rp.querySelector('.tab-btn[data-target="details"]'), true);
}

/** Match tab button selection to the panel content. */
function prksSyncRightPanelTabStrip(tabId) {
    const rp = document.getElementById('right-panel');
    if (!rp) return;
    let want = tabId || 'details';
    const buttons = [...rp.querySelectorAll('.tabs .tab-btn')];
    const visible = buttons.filter((b) => !b.hidden);
    const allowed = new Set(visible.map((b) => b.getAttribute('data-target')));
    if (visible.length && !allowed.has(want)) want = 'details';
    buttons.forEach((btn) => {
        prksSetTabActive(btn, btn.getAttribute('data-target') === want);
    });
}

function prksRefreshFocusedRightPanel() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (!ctx) return;
    let tab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';
    const route = ctx.lastResolvedRoute || ctx.route;
    const onWorkDetailPage = !!(route && route.name === 'work' && ctx.getEntity && ctx.getEntity('work'));
    if (!onWorkDetailPage && tab === 'annotations') {
        tab = 'details';
        if (ctx.ui) ctx.ui.rightPanelTab = 'details';
    }
    prksSyncRightPanelTabStrip(tab);
    updatePanelContent(tab);
}

/**
 * Right column layout: full tabs (work), two tabs (folder, no annotations), or single contextual pane (everything else).
 */
function setRightPanelRouteContext(hash) {
    const h = hash || '';
    const rp = document.getElementById('right-panel');
    const tabs = rp?.querySelector('.tabs');
    const annBtn = rp?.querySelector('.tab-btn[data-target="annotations"]');
    if (!rp || !tabs) {
        prksApplyRightPanelVisibility(h);
        return;
    }

    rp.classList.remove(
        'right-panel--single-pane',
        'right-panel--mode-work',
        'right-panel--mode-folder',
        'right-panel--mode-person',
        'right-panel--mode-person-group'
    );
    rp.removeAttribute('data-right-panel-mode');

    if (annBtn) annBtn.hidden = false;

    function hideTabStripIfAtMostOneVisible() {
        const visibleTabs = [...tabs.querySelectorAll('.tab-btn')].filter((b) => !b.hidden);
        if (visibleTabs.length <= 1) {
            tabs.hidden = true;
            tabs.setAttribute('aria-hidden', 'true');
        }
    }

    const _fe = typeof prksFocusedEntity === 'function' ? prksFocusedEntity : function () { return null; };
    if (_fe('work')) {
        rp.classList.add('right-panel--mode-work');
        rp.setAttribute('data-right-panel-mode', 'work');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        prksApplyRightPanelVisibility(h);
        return;
    }

    if (_fe('folder')) {
        rp.classList.add('right-panel--mode-folder');
        rp.setAttribute('data-right-panel-mode', 'folder');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        if (annBtn) annBtn.hidden = true;
        const activeTarget = rp.querySelector('.tab-btn.active')?.getAttribute('data-target');
        if (activeTarget === 'annotations') activateRightPanelDetailsTab();
        hideTabStripIfAtMostOneVisible();
        prksApplyRightPanelVisibility(h);
        return;
    }

    if (_fe('person') && isPersonDetailHash(h)) {
        rp.classList.add('right-panel--mode-person');
        rp.setAttribute('data-right-panel-mode', 'person');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        if (annBtn) annBtn.hidden = true;
        const activeTarget = rp.querySelector('.tab-btn.active')?.getAttribute('data-target');
        if (activeTarget === 'annotations') activateRightPanelDetailsTab();
        hideTabStripIfAtMostOneVisible();
        prksApplyRightPanelVisibility(h);
        return;
    }

    if (_fe('personGroup') && isPersonGroupDetailHash(h)) {
        rp.classList.add('right-panel--mode-person-group');
        rp.setAttribute('data-right-panel-mode', 'person-group');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        if (annBtn) annBtn.hidden = true;
        const activeTarget = rp.querySelector('.tab-btn.active')?.getAttribute('data-target');
        if (activeTarget === 'annotations') activateRightPanelDetailsTab();
        hideTabStripIfAtMostOneVisible();
        prksApplyRightPanelVisibility(h);
        return;
    }

    if (_fe('playlist') && (h.startsWith('#/playlists/') || h === '#/playlists')) {
        rp.classList.add('right-panel--single-pane');
        rp.setAttribute('data-right-panel-mode', h.startsWith('#/playlists/') ? 'playlist' : 'playlists');
        tabs.hidden = true;
        tabs.setAttribute('aria-hidden', 'true');
        activateRightPanelDetailsTab();
        prksApplyRightPanelVisibility(h);
        return;
    }

    const mode = inferRightPanelListMode(h);
    rp.classList.add('right-panel--single-pane');
    rp.setAttribute('data-right-panel-mode', mode);
    tabs.hidden = true;
    tabs.setAttribute('aria-hidden', 'true');
    activateRightPanelDetailsTab();
    prksApplyRightPanelVisibility(h);
}

function isPersonGroupDetailHash(h) {
    if (typeof prksParseRoute === 'function') return prksParseRoute(h).name === 'person-group-detail';
    if (!h || h === '#/people/groups') return false;
    return /^#\/people\/groups\/.+/.test(h);
}

function isPersonDetailHash(h) {
    if (typeof prksParseRoute === 'function') return prksParseRoute(h).name === 'person';
    if (!h || !h.startsWith('#/people/')) return false;
    if (h.startsWith('#/people/role/')) return false;
    if (h === '#/people/groups' || h.startsWith('#/people/groups/')) return false;
    const path = h.replace(/^#\/?/, '').split('/').filter(Boolean);
    if (path.length < 2 || path[0] !== 'people') return false;
    if (path[1] === 'groups' || path[1] === 'role') return false;
    return true;
}

function inferRightPanelListMode(h) {
    if (typeof prksParseRoute === 'function') {
        const route = prksParseRoute(h);
        switch (route.name) {
            case 'folders':
                return 'library';
            case 'playlists':
                return 'playlists';
            case 'playlist-detail':
                return 'playlist';
            case 'people':
                return 'people';
            case 'people-role':
                return 'people-role';
            case 'people-groups':
                return 'people-groups';
            case 'person-group-detail':
                return 'people-group';
            case 'person':
                return 'person';
            case 'research-graph':
                return 'graph';
            case 'recent':
                return 'recent';
            case 'progress':
                return 'progress';
            case 'processing-files':
                return 'processing-files';
            case 'search':
                return 'search';
            case 'tags':
                return 'tags';
            case 'publishers':
                return 'publishers';
            case 'types':
            case 'type-detail':
                return 'types';
            default:
                return 'default';
        }
    }
    if (h === '#/folders') return 'library';
    if (h === '#/playlists') return 'playlists';
    if (h.startsWith('#/playlists/')) return 'playlist';
    if (h === '#/people') return 'people';
    if (h.startsWith('#/people/role/')) return 'people-role';
    if (h === '#/people/groups') return 'people-groups';
    if (h.startsWith('#/people/groups/')) return 'people-group';
    if (h.startsWith('#/people/')) return 'person';
    if (h === '#/graph' || h.startsWith('#/graph?')) return 'graph';
    if (h === '#/recent') return 'recent';
    if (h.startsWith('#/progress')) return 'progress';
    if (h === '#/processing-files') return 'processing-files';
    if (h.startsWith('#/search')) return 'search';
    if (h === '#/tags') return 'tags';
    if (h === '#/publishers') return 'publishers';
    if (h === '#/types' || h.startsWith('#/types/')) return 'types';
    return 'default';
}

/** List-route sidebar modes where #panel-content is static copy only (hide right column). */
const PRKS_RIGHT_PANEL_HIDE_LIST_MODES = new Set([
    'library',
    'recent',
    'progress',
    'search',
    'tags',
    'publishers',
    'types',
    'people',
    'people-role',
    'people-groups',
    'default'
]);

function isResearchGraphHash(h) {
    if (typeof prksParseRoute === 'function') return prksParseRoute(h).name === 'research-graph';
    return !!(h && (h === '#/graph' || h.startsWith('#/graph?')));
}

function prksRightPanelHasActionableContent(hash) {
    const h = hash || '';
    const _fe = typeof prksFocusedEntity === 'function' ? prksFocusedEntity : function () { return null; };
    if (_fe('work')) return true;
    if (_fe('folder')) return true;
    if (_fe('person') && isPersonDetailHash(h)) return true;
    if (_fe('personGroup') && isPersonGroupDetailHash(h)) return true;
    if (_fe('playlist') && (h.startsWith('#/playlists/') || h === '#/playlists')) return true;
    if (isResearchGraphHash(h)) {
        return !!(
            typeof window.prksResearchGraphHasInspectorSelection === 'function' &&
            window.prksResearchGraphHasInspectorSelection()
        );
    }
    return false;
}

function prksShouldHideRightPanel(hash) {
    const h = hash || '';
    if (h === '#/processing-files') return true;
    if (isResearchGraphHash(h)) return !prksRightPanelHasActionableContent(h);
    if (prksRightPanelHasActionableContent(h)) return false;
    const mode = inferRightPanelListMode(h);
    return PRKS_RIGHT_PANEL_HIDE_LIST_MODES.has(mode);
}

function prksApplyRightPanelVisibility(hash) {
    const app = document.getElementById('app-container');
    if (!app) return;
    const hide = prksShouldHideRightPanel(hash);
    app.classList.toggle('app-container--hide-right-panel', hide);
    app.classList.remove('app-container--processing-inline-preview');
    if (hide && document.body.classList.contains('prks-right-panel-open')) {
        prksCloseOverlays();
    }
}

/** Focused Graph runtime alone controls Graph inspector visibility; refresh after selection changes. */
function prksRefreshFocusedRightPanelVisibility() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const route = ctx && (ctx.lastResolvedRoute || ctx.route);
    const hash = (route && (route.hash || route.canonicalHash)) || window.location.hash || '';
    prksApplyRightPanelVisibility(hash);
}

function renderRouteContextSidebar(mode) {
    const ctx = (typeof prksFocusedRouteSidebar === 'function' ? prksFocusedRouteSidebar() : null) || {};
    const link = (href, label) =>
        `<p class="route-sidebar__action"><a href="${href}" class="route-sidebar__link">${label}</a></p>`;

    if (mode === 'graph') {
        return '<div id="prks-graph-inspector" class="right-panel-stack research-graph__inspector" aria-live="polite"></div>';
    }

    if (mode === 'library') {
        const n = ctx.folderCount != null ? Number(ctx.folderCount) : null;
        const extra = n != null && !Number.isNaN(n) ? `<p class="route-sidebar__meta">${n} folder${n === 1 ? '' : 's'} in the library.</p>` : '';
        return `
            <div class="route-sidebar">
                ${prksRouteSidebarTitleRow('Folder library', 'route-new-folder', 'How to add a folder')}
                <p class="route-sidebar__lede">Organize files into folders. Open a folder to see its works, tags, and linked people.</p>
                ${extra}
            </div>`;
    }
    if (mode === 'types') {
        const isDetail = !!(ctx.docTypeLabel || ctx.docType);
        if (!isDetail) {
            const typeCount = ctx.typeCount != null ? Number(ctx.typeCount) : null;
            const fileCount = ctx.totalFiles != null ? Number(ctx.totalFiles) : null;
            const typeCountLine =
                typeCount != null && !Number.isNaN(typeCount)
                    ? `<p class="route-sidebar__meta">${typeCount} type${typeCount === 1 ? '' : 's'} in use.</p>`
                    : '';
            const fileCountLine =
                fileCount != null && !Number.isNaN(fileCount)
                    ? `<p class="route-sidebar__meta">${fileCount} file${fileCount === 1 ? '' : 's'} grouped by type.</p>`
                    : '';
            return `
                <div class="route-sidebar">
                    <h2 class="route-sidebar__title">File types</h2>
                    <p class="route-sidebar__lede">Open any type row to list matching files.</p>
                    ${typeCountLine}
                    ${fileCountLine}
                    ${link('#/folders', 'Folder library')}
                    ${link('#/tags', 'All tags')}
                </div>`;
        }
        const dtLabel = prksSidebarEsc(ctx.docTypeLabel || ctx.docType || 'File type');
        const n = ctx.workCount != null ? Number(ctx.workCount) : null;
        const extra = n != null && !Number.isNaN(n) ? `<p class="route-sidebar__meta">${n} file${n === 1 ? '' : 's'} in this type.</p>` : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">${dtLabel}</h2>
                <p class="route-sidebar__lede">Files classified as this BibTeX type.</p>
                ${extra}
                ${link('#/types', 'All file types')}
                ${link('#/folders', 'Folder library')}
            </div>`;
    }
    if (mode === 'playlists') {
        return `
            <div class="route-sidebar">
                ${prksRouteSidebarTitleRow('Playlists', 'route-new-playlist', 'How to create a playlist')}
                <p class="route-sidebar__lede">Ordered collections of videos (courses, lecture series). Open a playlist to reorder items or add new videos.</p>
                <p class="route-sidebar__action route-sidebar__action--block">
                    <button type="button" class="prks-btn prks-btn--primary route-sidebar__new-playlist-btn" id="prks-create-playlist-btn">${typeof prksIcon === 'function' ? prksIcon('plus', { size: 'sm' }) : ''} New playlist</button>
                </p>
            </div>`;
    }
    if (mode === 'playlist') {
        const name = prksSidebarEsc(ctx.playlistTitle || 'Playlist');
        const n = ctx.itemCount != null ? Number(ctx.itemCount) : null;
        const extra = n != null && !Number.isNaN(n) ? `<p class="route-sidebar__meta">${n} item${n === 1 ? '' : 's'}.</p>` : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">${name}</h2>
                <p class="route-sidebar__lede">Edit title/description and add videos from the Details panel.</p>
                ${extra}
                ${link('#/playlists', 'All playlists')}
            </div>`;
    }
    if (mode === 'recent') {
        const n = ctx.workCount != null ? Number(ctx.workCount) : null;
        const extra = n != null && !Number.isNaN(n) ? `<p class="route-sidebar__meta">${n} file${n === 1 ? '' : 's'} with a recent open time.</p>` : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">Recently opened</h2>
                <p class="route-sidebar__lede">Sorted by last opened. Status and document-type badges match the rest of the app.</p>
                ${extra}
                ${link('#/folders', 'Browse all folders')}
            </div>`;
    }
    if (mode === 'people') {
        return `
            <div class="route-sidebar">
                ${prksRouteSidebarTitleRow('People', 'route-new-person', 'How to add a person')}
                <p class="route-sidebar__lede">Authors, editors, and other roles linked to your files. Open someone to edit their profile and see linked works.</p>
                ${link('#/people/groups', 'People groups')}
                ${link('#/people/role/Author', 'Filter: Authors')}
                ${link('#/people/role/Editor', 'Filter: Editors')}
                ${link('#/people/role/Translator', 'Filter: Translators')}
                ${link('#/people/role/Foreword', 'Filter: Foreword writers')}
            </div>`;
    }
    if (mode === 'people-groups') {
        const n = ctx.groupCount != null ? Number(ctx.groupCount) : null;
        const extra =
            n != null && !Number.isNaN(n)
                ? `<p class="route-sidebar__meta">${n} group${n === 1 ? '' : 's'}.</p>`
                : '';
        return `
            <div class="route-sidebar">
                ${prksRouteSidebarTitleRow('People groups', 'route-new-group', 'How to add a group')}
                <p class="route-sidebar__lede">Hierarchical labels for people (e.g. Frankfurt School → Philosophy). Membership is many-to-many.</p>
                ${extra}
                ${link('#/people', 'All people')}
            </div>`;
    }
    if (mode === 'people-group') {
        const name = prksSidebarEsc(ctx.groupName || 'Group');
        const mn = ctx.memberCount != null ? Number(ctx.memberCount) : null;
        const sn = ctx.subgroupCount != null ? Number(ctx.subgroupCount) : null;
        const bits = [];
        if (mn != null && !Number.isNaN(mn)) bits.push(`${mn} member${mn === 1 ? '' : 's'}`);
        if (sn != null && !Number.isNaN(sn)) bits.push(`${sn} subgroup${sn === 1 ? '' : 's'}`);
        const extra = bits.length ? `<p class="route-sidebar__meta">${bits.join(' · ')}.</p>` : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">${name}</h2>
                <p class="route-sidebar__lede">Members are in the main column; edit the group and open subgroups from the Details panel.</p>
                ${extra}
                ${link('#/people/groups', 'All groups')}
                ${link('#/people', 'All people')}
            </div>`;
    }
    if (mode === 'people-role') {
        const role = prksSidebarEsc(ctx.role || 'this role');
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">${role}</h2>
                <p class="route-sidebar__lede">People with the <strong>${role}</strong> role on at least one file.</p>
                ${link('#/people', 'All people')}
            </div>`;
    }
    if (mode === 'person') {
        const name = prksSidebarEsc(ctx.personDisplayName || 'Person');
        const wn = ctx.linkedWorks != null ? Number(ctx.linkedWorks) : null;
        const extra =
            wn != null && !Number.isNaN(wn)
                ? `<p class="route-sidebar__meta">${wn} linked file${wn === 1 ? '' : 's'} (all roles).</p>`
                : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">${name}</h2>
                <p class="route-sidebar__lede">Profile and external links are in the main column. Below are files linked to this person.</p>
                ${extra}
                ${link('#/people', 'Back to all people')}
            </div>`;
    }
    if (mode === 'progress') {
        const st = prksSidebarEsc(ctx.status || '—');
        return `
            <div class="route-sidebar">
                ${prksRouteSidebarTitleRow(`Progress · ${st}`, 'route-progress-filters', 'How to switch progress filters')}
                <p class="route-sidebar__lede">Files whose status is <strong>${st}</strong>. Change status from a file’s metadata panel.</p>
                ${link('#/folders', 'Folder library')}
            </div>`;
    }
    if (mode === 'processing-files') {
        const n = ctx.pendingCount != null ? Number(ctx.pendingCount) : null;
        const extra =
            n != null && !Number.isNaN(n)
                ? `<p class="route-sidebar__meta">${n} file${n === 1 ? '' : 's'} currently in inbox.</p>`
                : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">Files for Processing</h2>
                <p class="route-sidebar__lede">Scans <code>/data/for_processing</code> recursively for PDFs. Items stay isolated from search and graph until imported.</p>
                ${extra}
                ${link('#/folders', 'Folder library')}
            </div>`;
    }
    if (mode === 'search') {
        const q = (ctx.query || '').trim();
        const tag = (ctx.tag || '').trim();
        const author = (ctx.author || '').trim();
        const publisher = (ctx.publisher || '').trim();
        let line = '';
        if (tag) {
            line = `<p class="route-sidebar__lede">Files tagged <strong>${prksSidebarEsc(tag)}</strong>.</p>`;
            if (author) {
                line += `<p class="route-sidebar__meta">Filtered by author <strong>${prksSidebarEsc(author)}</strong>.</p>`;
            }
            if (publisher) {
                line += `<p class="route-sidebar__meta">Filtered by publisher <strong>${prksSidebarEsc(publisher)}</strong>.</p>`;
            }
        } else if (q && author && publisher) {
            line = `<p class="route-sidebar__lede">Keywords <strong>${prksSidebarEsc(q)}</strong>, author <strong>${prksSidebarEsc(author)}</strong>, and publisher <strong>${prksSidebarEsc(publisher)}</strong>.</p>`;
        } else if (q && author) {
            line = `<p class="route-sidebar__lede">Keywords <strong>${prksSidebarEsc(q)}</strong> and author <strong>${prksSidebarEsc(author)}</strong>.</p>`;
        } else if (q && publisher) {
            line = `<p class="route-sidebar__lede">Keywords <strong>${prksSidebarEsc(q)}</strong> and publisher <strong>${prksSidebarEsc(publisher)}</strong>.</p>`;
        } else if (author && publisher) {
            line = `<p class="route-sidebar__lede">Author <strong>${prksSidebarEsc(author)}</strong> and publisher <strong>${prksSidebarEsc(publisher)}</strong>.</p>`;
        } else if (q) {
            line = `<p class="route-sidebar__lede">Search across title, notes, abstract, free-text authors, and linked people for <strong>${prksSidebarEsc(q)}</strong>.</p>`;
        } else if (author) {
            line = `<p class="route-sidebar__lede">Files whose authors match <strong>${prksSidebarEsc(author)}</strong> (metadata or linked people).</p>`;
        } else if (publisher) {
            line = `<p class="route-sidebar__lede">Files whose publisher metadata matches <strong>${prksSidebarEsc(publisher)}</strong> (substring or alias groups from the Publishers page).</p>`;
        } else {
            line = `<p class="route-sidebar__lede">Search results from the library search box or a tag.</p>`;
        }
        const rc = ctx.resultCount != null ? Number(ctx.resultCount) : null;
        const extra =
            rc != null && !Number.isNaN(rc) ? `<p class="route-sidebar__meta">${rc} result${rc === 1 ? '' : 's'}.</p>` : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">Search</h2>
                ${line}
                ${extra}
                ${link('#/tags', 'Browse all tags')}
                ${link('#/publishers', 'Browse publishers')}
            </div>`;
    }
    if (mode === 'tags') {
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">Tags</h2>
                <p class="route-sidebar__lede">Tags in use on files or folders. Select one to list matching files.</p>
                ${link('#/publishers', 'Browse publishers')}
                ${link('#/folders', 'Folder library')}
            </div>`;
    }
    if (mode === 'publishers') {
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">Publishers</h2>
                <p class="route-sidebar__lede">Canonical publisher names and alternate spellings. Search matches substring on each file’s publisher field, or exact labels in a group.</p>
                ${link('#/tags', 'Browse all tags')}
                ${link('#/folders', 'Folder library')}
            </div>`;
    }
    return `
        <div class="route-sidebar">
            <h2 class="route-sidebar__title">PRKS</h2>
            <p class="route-sidebar__lede">Personal Research Knowledge System — library, people, and progress in one place.</p>
            ${link('#/folders', 'Folder library')}
        </div>`;
}

function getActiveRightPanelTab() {
    const focusedCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (focusedCtx && focusedCtx.ui && focusedCtx.ui.rightPanelTab) {
        return focusedCtx.ui.rightPanelTab;
    }
    const active = document.querySelector('#right-panel .tab-btn.active');
    return (active && active.getAttribute('data-target')) || 'details';
}

function prksOwnerTabIsFocused(ctx) {
    if (typeof prksTabContextIsFocused === 'function') return prksTabContextIsFocused(ctx);
    if (!ctx) return false;
    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    return !!(focused && focused.tabId === ctx.tabId);
}

function prksRightPanelOwnedBy(ctx, node) {
    if (!ctx || ctx.destroyed || !ctx.mounted || !prksOwnerTabIsFocused(ctx)) return false;
    const panel = document.getElementById('panel-content');
    if (!panel) return false;
    if (node && node !== panel && !panel.contains(node)) return false;
    const ownerTabId = panel.dataset.prksOwnerTabId || '';
    const ownerGeneration = panel.dataset.prksOwnerGeneration || '';
    return (
        (!ownerTabId || ownerTabId === String(ctx.tabId)) &&
        (!ownerGeneration || ownerGeneration === String(ctx.generation))
    );
}

function prksMarkRightPanelOwner(ctx) {
    const panel = document.getElementById('panel-content');
    if (!panel || !ctx) return panel;
    panel.dataset.prksOwnerTabId = String(ctx.tabId);
    panel.dataset.prksOwnerGeneration = String(ctx.generation);
    return panel;
}

function prksPrepareRightPanelReplace(ctx) {
    if (typeof prksCaptureWorkMetaDraft === 'function') prksCaptureWorkMetaDraft(ctx);
    if (ctx && typeof ctx.getResource === 'function' && ctx.getResource('privateNotesEditor')) {
        if (typeof prksFlushPendingPrivateNotes === 'function') prksFlushPendingPrivateNotes(ctx);
        ctx.clearResource('privateNotesEditor');
    }
    return prksMarkRightPanelOwner(ctx);
}

window.prksRightPanelOwnedBy = prksRightPanelOwnedBy;

function prksReplaceFocusedWorkDetailsPanel(ctx, work) {
    if (!prksOwnerTabIsFocused(ctx) || !work) return false;
    const live = ctx.getEntity && ctx.getEntity('work');
    if (!live || String(live.id) !== String(work.id)) return false;
    const tab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';
    if (tab !== 'details') return false;
    const panel = prksPrepareRightPanelReplace(ctx);
    if (!panel || typeof prksWorkRightPanelStackHtml !== 'function') return false;
    const mode = prksWorkDetailsMode(ctx, work);
    panel.innerHTML = prksWorkRightPanelStackHtml(work, mode, ctx);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(panel);
    if (typeof initPrksPrivateNotesEditor === 'function') initPrksPrivateNotesEditor('work', work.id, ctx);
    if (typeof initWorkTagCombobox === 'function') initWorkTagCombobox(work.id, ctx);
    if (typeof prksMountWorkMetadataEditor === 'function') prksMountWorkMetadataEditor(ctx, work.id);
    if (typeof prksMountWorkSourceEditor === 'function') prksMountWorkSourceEditor(ctx, work.id);
    if (mode !== 'metadata' && typeof mountPlaylistAttachControls === 'function') {
        void mountPlaylistAttachControls(work, ctx);
    }
    if (mode !== 'metadata' && typeof mountFolderAttachControlsForWork === 'function') {
        void mountFolderAttachControlsForWork(work, ctx);
    }
    if (typeof initWorkDetailRightPanelActions === 'function') {
        initWorkDetailRightPanelActions(work, ctx);
    }
    if (mode === 'metadata') prksMountWorkMetaEditor(ctx, work);
    return true;
}

function renderPrksPrivateNotesCard(entityType, entityId, initialText) {
    const text = prksPrivateNotesTextForEntity(entityType, entityId, initialText);
    const hintKey = entityType === 'work' ? 'notes-private-file' : 'notes-private-folder';
    const hintBtn = prksHintBtnHtml(hintKey, 'About reminders', 'prks-private-notes-card__hint-btn');
    return `
        <div class="doc-meta-card prks-private-notes-card">
            <h3 class="prks-private-notes-card__head"><span class="prks-private-notes-card__head-text">Reminders</span>${hintBtn}</h3>
            <textarea
                class="prks-private-notes-input"
                id="prks-private-notes-${entityType}-${entityId}"
                rows="4"
                maxlength="8000"
                spellcheck="true"
                data-prks-notes-entity="${entityType}"
                data-prks-notes-id="${entityId}"
                placeholder="e.g. Need this for…">${escapeHtml(text)}</textarea>
            <p class="meta-row prks-private-notes-status" id="prks-private-notes-status-${entityType}-${entityId}" aria-live="polite"></p>
        </div>`;
}

const PRKS_PRIVATE_DRAFT_MAX_COMMITTED = 64;
const prksPrivateNoteDrafts = new Map();

function prksPrivateNoteKey(entityType, entityId) {
    return String(entityType || '') + ':' + String(entityId == null ? '' : entityId);
}

function prksPrivateNoteDraft(entityType, entityId, text) {
    const key = prksPrivateNoteKey(entityType, entityId);
    let entry = prksPrivateNoteDrafts.get(key);
    if (!entry) {
        entry = {
            key: key,
            entityType: String(entityType),
            entityId: String(entityId),
            draftText: String(text == null ? '' : text),
            editGeneration: 0,
            saveSequence: 0,
            latestSaveToken: 0,
            latestSaveEditGeneration: 0,
            settledSaveToken: 0,
            state: 'committed',
            saveError: false,
            promise: null,
            updatedAt: Date.now(),
        };
        prksPrivateNoteDrafts.set(key, entry);
    }
    return entry;
}

function prksPrunePrivateNoteDrafts() {
    const committed = Array.from(prksPrivateNoteDrafts.values())
        .filter((entry) => entry.state === 'committed' && !entry.promise)
        .sort((a, b) => a.updatedAt - b.updatedAt);
    while (committed.length > PRKS_PRIVATE_DRAFT_MAX_COMMITTED) {
        const old = committed.shift();
        if (old) prksPrivateNoteDrafts.delete(old.key);
    }
}

function prksPrivateNotesTextForEntity(entityType, entityId, serverText) {
    const key = prksPrivateNoteKey(entityType, entityId);
    const server = String(serverText == null ? '' : serverText);
    const entry = prksPrivateNoteDrafts.get(key);
    if (!entry) return server;
    if (entry.state === 'committed' && !entry.promise && entry.draftText === server) {
        prksPrivateNoteDrafts.delete(key);
        return server;
    }
    return entry.draftText;
}

function prksPrivateNotesOwnerCurrent(editor) {
    if (!editor || !editor.ctx || !editor.ctx.isCurrent(editor.generation)) return false;
    const live = editor.ctx.getEntity ? editor.ctx.getEntity(editor.entityType) : null;
    return !!(live && String(live.id) === editor.entityId);
}

function prksPrivateNotesSetStatus(editor, text) {
    if (!editor || !editor.statusEl || !prksRightPanelOwnedBy(editor.ctx, editor.statusEl)) return;
    editor.statusEl.textContent = text;
}

function prksEnqueuePrivateNotesSave(editor) {
    if (!editor) return null;
    if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') {
        // No offline mutation outbox in Phase 1: keep the typed draft local only.
        prksPrivateNotesSetStatus(editor, 'Offline — notes are read-only');
        return null;
    }
    const entry = prksPrivateNoteDraft(editor.entityType, editor.entityId, editor.textarea.value);
    const content = String(entry.draftText);
    editor.dirty = false;
    entry.saveSequence += 1;
    const token = entry.saveSequence;
    entry.latestSaveToken = token;
    entry.latestSaveEditGeneration = entry.editGeneration;
    entry.state = 'saving';
    entry.saveError = false;
    entry.updatedAt = Date.now();
    prksPrivateNotesSetStatus(editor, 'Saving…');
    // Documented exception to the canonical-Folder-wrapper rule: this is a
    // coalesced autosave with its own draft lifecycle, and it is already gated
    // by the runtime check at the top of this function. Its success branch
    // publishes the same Folder coherence a wrapper would.
    const url = editor.entityType === 'work' ? `/api/works/${editor.entityId}` : `/api/folders/${editor.entityId}`;
    const promise = prksRequest(
        url,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ private_notes: content }),
        },
        { coalesceKey: 'private-notes:' + editor.key }
    );
    entry.promise = promise;
    void promise
        .then(async function (res) {
            const ok = !!(res && res.ok);
            if (ok && editor.entityType === 'work' && typeof prksOfflineMarkEntityChanged === 'function') {
                // Canonical success matters even when this UI save is stale.
                prksOfflineMarkEntityChanged('work', editor.entityId);
            }
            if (ok && editor.entityType === 'folder' && typeof prksMarkFoldersDomainChanged === 'function') {
                // private_notes is part of the cached Folder detail payload.
                prksMarkFoldersDomainChanged();
            }
            if (token !== entry.latestSaveToken) return;
            const hasNewerDraft = entry.editGeneration > entry.latestSaveEditGeneration;
            entry.settledSaveToken = token;
            entry.promise = null;
            entry.saveError = !ok && !hasNewerDraft;
            entry.state = hasNewerDraft ? 'drafting' : !ok ? 'error' : 'committed';
            entry.updatedAt = Date.now();
            prksPrunePrivateNoteDrafts();
            if (!prksPrivateNotesOwnerCurrent(editor)) return;
            const liveEditor = editor.ctx.getResource ? editor.ctx.getResource('privateNotesEditor') : null;
            if (liveEditor !== editor) return;
            if (hasNewerDraft) {
                prksPrivateNotesSetStatus(editor, 'Drafting…');
                return;
            }
            if (!ok) {
                prksPrivateNotesSetStatus(editor, 'Could not save');
                return;
            }
            const live = editor.ctx.getEntity(editor.entityType);
            if (live) live.private_notes = content;
            prksPrivateNotesSetStatus(editor, 'Saved');
            const timer = window.setTimeout(function () {
                if (editor.ctx && editor.ctx.timers && editor.ctx.timers.get('privateNotesStatus') === timer) {
                    editor.ctx.clearTimer('privateNotesStatus');
                }
                if (editor.statusEl && editor.statusEl.textContent === 'Saved') editor.statusEl.textContent = '';
            }, 1800);
            editor.ctx.setTimer('privateNotesStatus', timer);
        })
        .catch(function () {
            if (token !== entry.latestSaveToken) return;
            const hasNewerDraft = entry.editGeneration > entry.latestSaveEditGeneration;
            entry.settledSaveToken = token;
            entry.promise = null;
            entry.saveError = !hasNewerDraft;
            entry.state = hasNewerDraft ? 'drafting' : 'error';
            entry.updatedAt = Date.now();
            if (prksPrivateNotesOwnerCurrent(editor)) {
                prksPrivateNotesSetStatus(editor, hasNewerDraft ? 'Drafting…' : 'Could not save');
            }
        });
    return promise;
}

function prksFlushPendingPrivateNotes(ctx) {
    if (!ctx || typeof ctx.getResource !== 'function') return;
    const editor = ctx.getResource('privateNotesEditor');
    if (!editor || !editor.dirty) return;
    ctx.clearTimer(editor.timerKey);
    prksEnqueuePrivateNotesSave(editor);
}

/** Private notes stay explicitly read-only while offline -- same Phase 1 rule as Research Notes. */
if (typeof prksOfflineRuntimeSubscribe === 'function') {
    prksOfflineRuntimeSubscribe(function (state) {
        if (typeof prksForEachLiveTabContext !== 'function') return;
        const offline = state !== 'online';
        prksForEachLiveTabContext(function (ctx) {
            const editor = ctx && ctx.getResource ? ctx.getResource('privateNotesEditor') : null;
            if (!editor || !editor.textarea) return;
            editor.textarea.readOnly = offline;
            if (offline) {
                prksPrivateNotesSetStatus(editor, 'Offline — notes are read-only');
            } else if (editor.dirty) {
                prksEnqueuePrivateNotesSave(editor);
            } else {
                prksPrivateNotesSetStatus(editor, '');
            }
        });
    });
}

function initPrksPrivateNotesEditor(entityType, entityId, ownerCtx) {
    const idSuffix = `${entityType}-${entityId}`;
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (!ctx || !prksRightPanelOwnedBy(ctx)) return;
    const panel = document.getElementById('panel-content');
    const ta = panel && panel.querySelector(`#prks-private-notes-${idSuffix}`);
    if (!ta || ta.dataset.prksNotesBound === '1') return;
    ta.dataset.prksNotesBound = '1';
    const statusEl = panel.querySelector(`#prks-private-notes-status-${idSuffix}`);
    const entry = prksPrivateNoteDrafts.get(prksPrivateNoteKey(entityType, entityId));
    if (entry) ta.value = entry.draftText;
    const editor = {
        key: prksPrivateNoteKey(entityType, entityId),
        entityType: String(entityType),
        entityId: String(entityId),
        ctx: ctx,
        generation: ctx.generation,
        textarea: ta,
        statusEl: statusEl,
        timerKey: 'privateNotesDebounce:' + prksPrivateNoteKey(entityType, entityId),
        dirty: !!(entry && entry.state === 'drafting'),
    };
    const schedule = function () {
        // The textarea's readOnly flag blocks ordinary user typing, but this is
        // a defensive belt-and-suspenders check: offline must never enter
        // drafting state or arm a save debounce, no matter how input fired.
        if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') return;
        const draft = prksPrivateNoteDraft(entityType, entityId, ta.value);
        draft.draftText = ta.value;
        draft.editGeneration += 1;
        draft.state = 'drafting';
        draft.saveError = false;
        draft.updatedAt = Date.now();
        editor.dirty = true;
        prksPrivateNotesSetStatus(editor, 'Drafting…');
        const timer = window.setTimeout(function () {
            if (ctx.timers && ctx.timers.get(editor.timerKey) === timer) ctx.clearTimer(editor.timerKey);
            if (editor.dirty) prksEnqueuePrivateNotesSave(editor);
        }, 850);
        ctx.setTimer(editor.timerKey, timer);
    };
    const blur = function () {
        if (!editor.dirty) return;
        ctx.clearTimer(editor.timerKey);
        prksEnqueuePrivateNotesSave(editor);
    };
    ta.addEventListener('input', schedule);
    ta.addEventListener('blur', blur);
    ctx.setResource('privateNotesEditor', editor, function () {
        ctx.clearTimer(editor.timerKey);
        ta.removeEventListener('input', schedule);
        ta.removeEventListener('blur', blur);
    });
    // Immediately reflect the current offline state -- an editor constructed
    // AFTER the runtime already left 'online' must never wait for a future
    // prksOfflineRuntimeSubscribe callback to become read-only.
    if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') {
        ta.readOnly = true;
        prksPrivateNotesSetStatus(editor, 'Offline — notes are read-only');
    }
}

window.prksFlushPendingPrivateNotes = prksFlushPendingPrivateNotes;
window.prksPrivateNotesTextForEntity = prksPrivateNotesTextForEntity;
window.prksResetPrivateNoteDraftsForTest = function () {
    prksPrivateNoteDrafts.clear();
};

function prksWorkDetailsMode(ownerCtx, work) {
    if (!ownerCtx || !ownerCtx.ui || !work) return 'view';
    const live = ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
    if (!live || String(live.id) !== String(work.id)) return 'view';
    const mode = String(ownerCtx.ui.workDetailsMode || 'view');
    return ['view', 'metadata', 'people', 'tags'].includes(mode) ? mode : 'view';
}

function prksWorkRightPanelStackHtml(work, mode = 'view', ownerCtx) {
    const detailsMode = typeof mode === 'boolean' ? (mode ? 'metadata' : 'view') : mode;
    if (detailsMode === 'metadata') {
        return '<div class="right-panel-stack work-details-panel work-details-panel--metadata">' +
            renderWorkMetaEditTab(work, ownerCtx && ownerCtx.ui ? ownerCtx.ui.workMetaDraft : null) +
            '</div>';
    }
    const notes = renderPrksPrivateNotesCard('work', work.id, work.private_notes);
    const inferred = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
    const playlistCard =
        inferred === 'video' && typeof renderPlaylistAttachControlsHtml === 'function'
            ? renderPlaylistAttachControlsHtml(work, ownerCtx)
            : '';
    const folderCard =
        typeof renderFolderAttachControlsHtml === 'function' ? renderFolderAttachControlsHtml(work, ownerCtx) : '';
    return (
        '<div class="right-panel-stack">' +
        notes +
        playlistCard +
        folderCard +
        renderWorkMetaTab(work, detailsMode) +
        '</div>'
    );
}

function prksFolderRightPanelStackHtml(folder) {
    const notes = renderPrksPrivateNotesCard('folder', folder.id, folder.private_notes);
    return (
        '<div class="right-panel-stack">' +
        notes +
        renderFolderDetailsPanel(folder) +
        '</div>'
    );
}

function updatePanelContent(tabId) {
    const panel = document.getElementById('panel-content');
    if (!panel) return;

    const focusedCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const previousOwnerId = panel.dataset.prksOwnerTabId || '';
    if (previousOwnerId && (!focusedCtx || previousOwnerId !== String(focusedCtx.tabId))) {
        const previousOwner =
            typeof prksGetTabContext === 'function' ? prksGetTabContext(previousOwnerId) : null;
        if (previousOwner) {
            if (typeof prksCaptureWorkMetaDraft === 'function') prksCaptureWorkMetaDraft(previousOwner);
            if (typeof prksFlushPendingPrivateNotes === 'function') prksFlushPendingPrivateNotes(previousOwner);
            if (typeof previousOwner.clearResource === 'function') previousOwner.clearResource('privateNotesEditor');
        }
    }
    prksPrepareRightPanelReplace(focusedCtx);
    const focusedRoute = focusedCtx && (focusedCtx.lastResolvedRoute || focusedCtx.route);
    const focusedHash =
        (focusedRoute && (focusedRoute.hash || focusedRoute.canonicalHash)) || (window.location.hash || '');
    const routeName = focusedRoute && focusedRoute.name;

    setRightPanelRouteContext(focusedHash);

    const _fe = typeof prksFocusedEntity === 'function' ? prksFocusedEntity : function () { return null; };
    const _cw = _fe('work');
    const _cf = _fe('folder');
    const _cp = _fe('person');
    const _cpg = _fe('personGroup');
    const _cpl = _fe('playlist');

    if (_cw) {
        if (tabId === 'details') {
            const mode = prksWorkDetailsMode(focusedCtx, _cw);
            panel.innerHTML = prksWorkRightPanelStackHtml(_cw, mode, focusedCtx);
            if (mode !== 'metadata') initPrksPrivateNotesEditor('work', _cw.id, focusedCtx);
            if (mode !== 'metadata' && typeof mountPlaylistAttachControls === 'function') {
                void mountPlaylistAttachControls(_cw, focusedCtx);
            }
            if (mode !== 'metadata' && typeof mountFolderAttachControlsForWork === 'function') {
                void mountFolderAttachControlsForWork(_cw, focusedCtx);
            }
            initWorkTagCombobox(_cw.id, focusedCtx);
            if (typeof prksMountWorkMetadataEditor === 'function') prksMountWorkMetadataEditor(focusedCtx, _cw.id);
            if (typeof prksMountWorkSourceEditor === 'function') prksMountWorkSourceEditor(focusedCtx, _cw.id);
            if (typeof initWorkDetailRightPanelActions === 'function') {
                initWorkDetailRightPanelActions(_cw, focusedCtx);
            }
            if (mode === 'metadata') prksMountWorkMetaEditor(focusedCtx, _cw);
        } else if (tabId === 'annotations') {
            panel.innerHTML = renderWorkAnnotationsTab(_cw);
            if (typeof window.applyCachedAnnotationListToPanel === 'function') {
                window.applyCachedAnnotationListToPanel();
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Use Details or Annotations.</p>';
        }
    } else if (_cpl && routeName === 'playlist-detail') {
        const editing = !!(focusedCtx && focusedCtx.ui && focusedCtx.ui.playlistEditing);
        panel.innerHTML = editing ? renderPlaylistEditSidebarHtml(_cpl) : renderPlaylistSummarySidebarHtml(_cpl);
        if (editing) {
            void mountPlaylistEditSidebar(_cpl, focusedCtx);
        } else {
            const btn = document.getElementById('prks-playlist-edit-btn');
            if (btn && btn.dataset.bound !== '1') {
                btn.dataset.bound = '1';
                btn.onclick = () => {
                    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                    if (ctx && ctx.ui) ctx.ui.playlistEditing = true;
                    updatePanelContent('details');
                    if (typeof prksRefreshPlaylistDetailMain === 'function') prksRefreshPlaylistDetailMain(ctx);
                };
            }
        }
        // The panel was just repainted, so re-settle its controls: a Playlist
        // page mounted while online must not expose live mutation controls
        // after the runtime has already left 'online'.
        if (typeof prksApplyPlaylistPanelOfflineState === 'function') {
            prksApplyPlaylistPanelOfflineState(focusedCtx);
        }
    } else if (_cf) {
        if (tabId === 'details') {
            panel.innerHTML = prksFolderRightPanelStackHtml(_cf);
            initPrksPrivateNotesEditor('folder', _cf.id, focusedCtx);
            initFolderTagCombobox(_cf.id);
            if (typeof mountFolderHierarchyControls === 'function') {
                void mountFolderHierarchyControls(_cf);
            }
            if (typeof mountFolderLibraryAttachControls === 'function') {
                void mountFolderLibraryAttachControls(_cf);
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Folder details and tags are above.</p>';
        }
    } else if (_cp && (routeName === 'person' || isPersonDetailHash(focusedHash))) {
        if (tabId === 'details') {
            let topHtml;
            const editing = !!(focusedCtx && focusedCtx.ui && focusedCtx.ui.personDetailEditing);
            if (editing && typeof renderPersonProfileEditFormHtml === 'function') {
                const draft = typeof prksEnsurePersonProfileDraft === 'function'
                    ? prksEnsurePersonProfileDraft(focusedCtx, _cp)
                    : null;
                topHtml = renderPersonProfileEditFormHtml(_cp, draft);
            } else if (typeof renderPersonProfileDetailsSidebarHtml === 'function') {
                topHtml = renderPersonProfileDetailsSidebarHtml(_cp);
            } else {
                topHtml = '<p class="meta-row">Person panel unavailable.</p>';
            }
            panel.innerHTML = '<div class="right-panel-stack">' + topHtml + '</div>';
            if (editing && typeof prksMountPersonProfileEditor === 'function') {
                void prksMountPersonProfileEditor(focusedCtx, _cp);
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Use the Details tab.</p>';
        }
    } else if (
        _cpg &&
        (routeName === 'person-group-detail' || isPersonGroupDetailHash(focusedHash))
    ) {
        if (tabId === 'details') {
            const g = _cpg;
            let topHtml;
            if (
                focusedCtx &&
                focusedCtx.ui &&
                focusedCtx.ui.personGroupEditing &&
                typeof renderPersonGroupEditSidebarHtml === 'function'
            ) {
                topHtml = renderPersonGroupEditSidebarHtml(g);
            } else if (typeof renderPersonGroupSummarySidebarHtml === 'function') {
                topHtml = renderPersonGroupSummarySidebarHtml(g);
            } else {
                topHtml = '<p class="meta-row">Group panel unavailable.</p>';
            }
            panel.innerHTML = '<div class="right-panel-stack">' + topHtml + '</div>';
            if (
                focusedCtx &&
                focusedCtx.ui &&
                focusedCtx.ui.personGroupEditing &&
                typeof mountPersonGroupEditPanel === 'function'
            ) {
                void mountPersonGroupEditPanel(g, focusedCtx);
            }
            if (typeof prksApplyPersonGroupPanelOfflineState === 'function') prksApplyPersonGroupPanelOfflineState(focusedCtx);
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Use the Details tab.</p>';
        }
    } else {
        if (_cpl && routeName === 'playlist-detail') {
            const pl = _cpl;
            const editing = !!(focusedCtx && focusedCtx.ui && focusedCtx.ui.playlistEditing);
            const panel = document.getElementById('panel-content');
            if (!panel) return;
            panel.innerHTML = editing ? renderPlaylistEditSidebarHtml(pl) : renderPlaylistSummarySidebarHtml(pl);
            prksBindAutosizeTextareas(panel);
            if (editing) {
                void mountPlaylistEditSidebar(pl, focusedCtx);
            }
            if (typeof prksApplyPlaylistPanelOfflineState === 'function') {
                prksApplyPlaylistPanelOfflineState(focusedCtx);
            }
            return;
        }
        const mode = inferRightPanelListMode(focusedHash);
        panel.innerHTML = renderRouteContextSidebar(mode);
        if (mode === 'graph' && typeof window.renderGraphInspector === 'function') {
            window.renderGraphInspector();
        }
        if (mode === 'playlists' && typeof window.prksBindPlaylistsIndexCreateBtn === 'function') {
            window.prksBindPlaylistsIndexCreateBtn();
            if (typeof prksApplyPlaylistPanelOfflineState === 'function') {
                prksApplyPlaylistPanelOfflineState(focusedCtx);
            }
        }
    }

    prksBindAutosizeTextareas(panel);
    prksSyncRightPanelTabStrip(tabId || 'details');
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(panel);
}

function renderPlaylistSummarySidebarHtml(pl) {
    if (!pl) return '<p class="meta-row">Playlist not found.</p>';
    const title = escapeHtml(pl.title || 'Playlist');
    const desc = escapeHtml(pl.description || '').trim();
    const originalUrl = String(pl.original_url || '').trim();
    const originalUrlHtml = originalUrl
        ? `<p class="meta-row meta-row--compact"><strong>Original playlist URL:</strong> <a href="${escapeHtml(originalUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(originalUrl)}</a></p>`
        : '<p class="meta-row meta-row--compact meta-row--muted-italic">No original playlist URL.</p>';
    const count = Array.isArray(pl.items) ? pl.items.length : 0;
    return `
        <div class="right-panel-stack">
            <div class="doc-meta-card">
                <div class="card-heading-row">
                    <h3>Playlist</h3>
                    <button type="button" class="prks-btn prks-btn--secondary" id="prks-playlist-edit-btn">Edit</button>
                </div>
                <p class="card-title">${title}</p>
                ${desc ? `<p class="meta-row meta-row--compact">${desc}</p>` : '<p class="meta-row meta-row--compact meta-row--muted-italic">No description.</p>'}
                ${originalUrlHtml}
                <p class="meta-row meta-row--spaced">${count} item${count === 1 ? '' : 's'}</p>
            </div>
        </div>
    `;
}

function renderPlaylistEditSidebarHtml(pl) {
    if (!pl) return '<p class="meta-row">Playlist not found.</p>';
    const title = escapeHtml(pl.title || '');
    const desc = escapeHtml(pl.description || '');
    const originalUrl = escapeHtml(pl.original_url || '');
    return `
        <div class="right-panel-stack">
            <div class="doc-meta-card form-pane doc-meta-card--editing">
                <div class="card-heading-row">
                    <h3 class="doc-meta-card__accent-title">Edit playlist</h3>
                    <button type="button" class="prks-icon-btn close-btn" id="prks-playlist-edit-close" aria-label="Close">&times;</button>
                </div>
                <label for="prks-playlist-edit-title">Title</label>
                <input type="text" id="prks-playlist-edit-title" value="${title}" autocomplete="off">
                <label for="prks-playlist-edit-desc">Description</label>
                <textarea id="prks-playlist-edit-desc" class="textarea-sm">${desc}</textarea>
                <label for="prks-playlist-edit-original-url">Original playlist URL</label>
                <input type="url" id="prks-playlist-edit-original-url" value="${originalUrl}" placeholder="https://..." autocomplete="off">
                <div class="prks-form-actions prks-form-actions--split form-actions">
                    <button type="button" class="prks-btn prks-btn--secondary" id="prks-playlist-edit-cancel">Cancel</button>
                    <button type="button" class="prks-btn prks-btn--primary" id="prks-playlist-edit-save">Save</button>
                </div>
                <p class="meta-row meta-row--spaced" id="prks-playlist-edit-status" aria-live="polite"></p>
            </div>

            <div class="doc-meta-card">
                <h3>Add video</h3>
                <p class="meta-row meta-row--compact">Search for a video and click Add.</p>
                <div class="tag-add-shell combobox-container tag-add-shell--flush">
                    <div class="tag-add-shell__field">
                        ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : '<span class="tag-add-shell__icon"></span>'}
                        <input type="text" id="prks-playlist-add-search" class="tag-add-shell__input" placeholder="Search videos…" maxlength="300" autocomplete="off" aria-label="Search videos to add">
                    </div>
                    <div id="prks-playlist-add-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                </div>
                <p class="meta-row meta-row--spaced" id="prks-playlist-add-status" aria-live="polite"></p>
            </div>
        </div>
    `;
}

async function mountPlaylistEditSidebar(pl, ownerCtx) {
    if (!pl || !pl.id) return;
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const generation = ctx && ctx.generation;
    const panel = document.getElementById('panel-content');
    const ownsPlaylistPanel = function (node) {
        return !!(
            typeof prksTabContextOwnsEntityRoute === 'function' &&
            prksTabContextOwnsEntityRoute(ctx, generation, 'playlist', pl.id, 'playlist-detail') &&
            typeof prksRightPanelOwnedBy === 'function' &&
            prksRightPanelOwnedBy(ctx, node || panel)
        );
    };
    if (!panel || !ownsPlaylistPanel(panel)) return;
    prksBindAutosizeTextareas(panel);
    if (typeof prksApplyPlaylistPanelOfflineState === 'function') prksApplyPlaylistPanelOfflineState(ctx);
    const editBtn = panel.querySelector('#prks-playlist-edit-btn');
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : ctx;
            if (focused && focused.ui) focused.ui.playlistEditing = true;
            updatePanelContent('details');
            if (typeof prksRefreshPlaylistDetailMain === 'function') prksRefreshPlaylistDetailMain(focused);
        };
    }
    const close = () => {
        if (ctx && ctx.ui) ctx.ui.playlistEditing = false;
        if (typeof prksClearPlaylistRenameState === 'function') prksClearPlaylistRenameState(ctx);
        updatePanelContent('details');
        if (typeof prksRefreshPlaylistDetailMain === 'function') prksRefreshPlaylistDetailMain(ctx);
    };

    panel.querySelector('#prks-playlist-edit-close')?.addEventListener('click', close);
    panel.querySelector('#prks-playlist-edit-cancel')?.addEventListener('click', close);

    const saveBtn = panel.querySelector('#prks-playlist-edit-save');
    const statusEl = panel.querySelector('#prks-playlist-edit-status');
    if (saveBtn && saveBtn.dataset.bound !== '1') {
        saveBtn.dataset.bound = '1';
        saveBtn.onclick = async () => {
            const title = String(panel.querySelector('#prks-playlist-edit-title')?.value || '').trim();
            const description = String(panel.querySelector('#prks-playlist-edit-desc')?.value || '').trim();
            const originalUrl = String(panel.querySelector('#prks-playlist-edit-original-url')?.value || '').trim();
            if (!title) {
                if (statusEl) statusEl.textContent = 'Title is required.';
                return;
            }
            try {
                // Canonical success controls coherence, so the wrapper runs
                // before any panel-ownership test. Renaming a Playlist stales
                // the cached Work entity of every current member, because
                // get_work() embeds playlist_title -- the wrapper diffs the
                // title itself so this call site cannot forget.
                await updatePlaylist(
                    pl.id,
                    { title, description, original_url: originalUrl },
                    {
                        previousTitle: pl.title || '',
                        memberWorkIds: (Array.isArray(pl.items) ? pl.items : [])
                            .map((w) => w && w.id)
                            .filter(Boolean),
                    }
                );
                if (!ownsPlaylistPanel(panel)) return;
                const fresh =
                    typeof fetchPlaylistDetails === 'function'
                        ? await fetchPlaylistDetails(pl.id, {
                              signal: ctx && ctx.abortController && ctx.abortController.signal,
                          })
                        : null;
                if (!ownsPlaylistPanel(panel)) return;
                if (fresh) {
                    if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('playlist', fresh);
                    if (ctx) ctx.routeSidebar = { playlistTitle: fresh.title || 'Playlist', itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0 };
                }
                if (ctx && ctx.ui) ctx.ui.playlistEditing = false;
                if (typeof prksClearPlaylistRenameState === 'function') prksClearPlaylistRenameState(ctx);
                updatePanelContent('details');
                if (typeof prksRefreshPlaylistDetailMain === 'function') prksRefreshPlaylistDetailMain(ctx);
            } catch (_e) {
                if (typeof prksPlaylistWasBlocked === 'function' && prksPlaylistWasBlocked(_e)) return;
                if (statusEl && ownsPlaylistPanel(statusEl)) statusEl.textContent = 'Could not save.';
            }
        };
    }

    const input = panel.querySelector('#prks-playlist-add-search');
    const results = panel.querySelector('#prks-playlist-add-results');
    const addStatus = panel.querySelector('#prks-playlist-add-status');
    if (!input || !results) return;

    const present = new Set((Array.isArray(pl.items) ? pl.items : []).map((w) => String(w.id || '')).filter(Boolean));
    const works =
        typeof fetchWorks === 'function'
            ? await fetchWorks({ signal: ctx && ctx.abortController && ctx.abortController.signal })
            : [];
    if (!ownsPlaylistPanel(panel)) return;
    if (typeof prksApplyPlaylistPanelOfflineState === 'function') prksApplyPlaylistPanelOfflineState(ctx);
    const isVideo = (w) => {
        if (!w) return false;
        return typeof prksInferWorkSourceKind === 'function' && prksInferWorkSourceKind(w) === 'video';
    };
    const choices = (Array.isArray(works) ? works : [])
        .filter(isVideo)
        .filter((w) => !present.has(String(w.id)))
        .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }));

    function renderDropdown() {
        const q = String(input.value || '').trim().toLowerCase();
        const filtered = !q ? choices.slice(0, 30) : choices.filter((w) => String(w.title || '').toLowerCase().includes(q)).slice(0, 30);
        results.innerHTML = '';
        if (filtered.length === 0) {
            results.innerHTML = `<div class="result-item no-results">No videos found</div>`;
        } else {
            for (const w of filtered) {
                const row = document.createElement('div');
                row.className = 'result-item';
                row.style.display = 'flex';
                row.style.alignItems = 'center';
                row.style.justifyContent = 'space-between';
                row.style.gap = '10px';

                const label = document.createElement('div');
                label.style.flex = '1 1 auto';
                label.style.minWidth = '0';
                label.textContent = w.title || 'Untitled';
                row.appendChild(label);

                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'prks-btn prks-btn--secondary prks-btn--sm';
                btn.textContent = 'Add';
                btn.style.flex = '0 0 auto';
                btn.onmousedown = (ev) => ev.preventDefault();
                btn.onclick = async (ev) => {
                    ev.preventDefault();
                    try {
                        if (typeof addWorkToPlaylist !== 'function') throw new Error('no api');
                        await addWorkToPlaylist(pl.id, w.id);
                        if (!ownsPlaylistPanel(addStatus)) return;
                        if (addStatus) addStatus.textContent = 'Added.';
                        const fresh =
                            typeof fetchPlaylistDetails === 'function'
                                ? await fetchPlaylistDetails(pl.id, {
                                      signal: ctx && ctx.abortController && ctx.abortController.signal,
                                  })
                                : null;
                        if (fresh && ownsPlaylistPanel(panel)) {
                            if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('playlist', fresh);
                            if (ctx) ctx.routeSidebar = {
                                playlistTitle: fresh.title || 'Playlist',
                                itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
                            };
                            if (ctx && ctx.root && typeof renderPlaylistDetail === 'function') {
                                renderPlaylistDetail(ctx, fresh, ctx.root);
                            }
                            if (ctx && ctx.ui) ctx.ui.playlistEditing = true;
                            updatePanelContent('details');
                        }
                    } catch (_e) {
                        if (typeof prksPlaylistWasBlocked === 'function' && prksPlaylistWasBlocked(_e)) return;
                        if (addStatus && ownsPlaylistPanel(addStatus)) addStatus.textContent = 'Could not add.';
                    }
                };
                row.appendChild(btn);
                results.appendChild(row);
            }
        }
        prksShowInlineComboboxResults(input, results);
    }

    input.onfocus = () => renderDropdown();
    input.oninput = () => renderDropdown();
    input.onblur = () => setTimeout(() => prksHideInlineComboboxResults(results), 200);
}

function prksWorkMetaDraftFromWork(rawWork) {
    /* EFFECTIVE, not acknowledged. A synchronized field with a pending durable
     * edit is already saved as far as the user is concerned, so comparing
     * against the acknowledged value would ask them whether to discard changes
     * they had just saved -- and re-opening the form would show them the old
     * text. The draft guard protects unsaved typing, not saved work. */
    const work = typeof prksEffectiveWorkSync === 'function' ? prksEffectiveWorkSync(rawWork) : rawWork;
    const inferredKind = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
    const published = typeof prksWorkFieldToDisplay === 'function'
        ? prksWorkFieldToDisplay('published_date', work.published_date)
        : work.published_date;
    return {
        title: work.title || '', status: work.status || '', doc_type: work.doc_type || 'article',
        year: work.year || '', published_date: published || '', publisher: work.publisher || '',
        location: work.location || '', edition: work.edition || '', journal: work.journal || '',
        volume: work.volume || '', issue: work.issue || '', pages: work.pages || '', isbn: work.isbn || '',
        doi: work.doi || '', source_url: work.source_url || '', abstract: work.abstract || '',
        thumb_page: work.thumb_page == null ? '' : String(work.thumb_page),
        /* Every Work carries `author_text`, and now every Work has a control
         * for it -- "Channel name" for a video, the textual Author otherwise.
         * Restricting the draft to videos would make a non-video edit read as
         * unchanged, and the leave guard would let it be discarded silently. */
        author_text: work.author_text || '',
    };
}

function prksWorkMetaDraftIsDirty(ctx, work) {
    if (!ctx || !ctx.ui || ctx.ui.workMetaDraftWorkId !== String(work.id) || !ctx.ui.workMetaDraft) return false;
    const original = prksWorkMetaDraftFromWork(work);
    const draft = ctx.ui.workMetaDraft;
    return Object.keys(original).some((key) => String(original[key] || '') !== String(draft[key] || ''));
}

function prksCaptureWorkMetaDraft(ownerCtx) {
    if (!ownerCtx || !ownerCtx.ui || ownerCtx.ui.workDetailsMode !== 'metadata') return;
    const work = ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
    if (!work || ownerCtx.ui.workMetaDraftWorkId !== String(work.id)) return;
    const panel = document.getElementById('panel-content');
    if (!prksRightPanelOwnedBy(ownerCtx, panel)) return;
    const draft = ownerCtx.ui.workMetaDraft || prksWorkMetaDraftFromWork(work);
    const fields = {
        title: 'meta-title', status: 'meta-status', doc_type: 'meta-doc-type', year: 'meta-year',
        published_date: 'meta-date', publisher: 'meta-publisher', location: 'meta-location',
        edition: 'meta-edition', journal: 'meta-journal', volume: 'meta-volume', issue: 'meta-issue',
        pages: 'meta-pages', isbn: 'meta-isbn', doi: 'meta-doi', source_url: 'meta-source-url',
        abstract: 'meta-abstract', thumb_page: 'meta-thumb-page', author_text: 'meta-author-text',
    };
    Object.keys(fields).forEach((key) => {
        const el = panel.querySelector('#' + fields[key]);
        if (el) draft[key] = el.value;
    });
    ownerCtx.ui.workMetaDraft = draft;
}

function prksBindWorkMetaDraftEditor(ownerCtx, work) {
    if (!ownerCtx || !ownerCtx.ui || ownerCtx.ui.workDetailsMode !== 'metadata') return;
    const panel = document.getElementById('panel-content');
    if (!prksRightPanelOwnedBy(ownerCtx, panel)) return;
    const editorRoot = panel.querySelector('.work-meta-editor');
    const capture = () => prksCaptureWorkMetaDraft(ownerCtx);
    panel.querySelectorAll('.work-meta-editor input, .work-meta-editor textarea').forEach((el) => {
        el.addEventListener('input', capture);
        el.addEventListener('change', capture);
    });
    const date = panel.querySelector('#meta-date');
    const dateError = panel.querySelector('#meta-date-error');
    if (date && dateError) {
        const clearDateError = () => {
            date.removeAttribute('aria-invalid');
            dateError.textContent = '';
        };
        date.addEventListener('input', clearDateError);
        date.addEventListener('change', clearDateError);
    }
    /* Bind the capture-on-click listener to the ephemeral editor root itself, not the
     * persistent #panel-content. Repeated editor mount/cancel/render cycles replace
     * panel.innerHTML, which discards editorRoot and its listeners along with it; a listener
     * on #panel-content itself would instead accumulate one per cycle and retain old
     * TabContexts via its closure indefinitely. */
    if (editorRoot) {
        editorRoot.addEventListener('click', () => window.setTimeout(capture, 0));
    }
}

function prksMountWorkMetaEditor(ownerCtx, work) {
    if (!ownerCtx || !work || !prksRightPanelOwnedBy(ownerCtx)) return;
    if (prksWorkDetailsMode(ownerCtx, work) !== 'metadata') return;
    prksBindSegmentedHidden('meta-status');
    if (typeof initPrksDocTypeMenu === 'function') {
        const sourceKind = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
        initPrksDocTypeMenu('meta-doc-type', { disabled: sourceKind === 'video' });
    }
    prksBindWorkMetaDraftEditor(ownerCtx, work);
    const panel = document.getElementById('panel-content');
    if (panel && typeof prksBindAutosizeTextareas === 'function') prksBindAutosizeTextareas(panel);
    if (typeof prksMountWorkMetadataEditor === 'function') prksMountWorkMetadataEditor(ownerCtx, work.id, { editing: true });
    if (typeof prksMountWorkSourceEditor === 'function') prksMountWorkSourceEditor(ownerCtx, work.id, { editing: true });
}

function toggleWorkMetaEdit(isEditing) {
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    void toggleWorkMetaEditForContext(ownerCtx, isEditing);
}

/* A pending durable edit that has not been read out of IndexedDB yet is
 * indistinguishable from no pending edit at all. Building the metadata draft
 * from that would show the user stale text after a reload and then ask whether
 * to discard changes they had already saved -- so the one path that must be
 * correct rather than merely fast waits for the hydration ALREADY in flight.
 * Never a second read, never a poll, and no wait once it has settled. */
function prksAwaitPendingWorkMetadata() {
    if (typeof prksPendingWorkMetadataSettled !== 'function' ||
        typeof prksEnsurePendingWorkMetadata !== 'function' ||
        prksPendingWorkMetadataSettled()) {
        return null;
    }
    return prksEnsurePendingWorkMetadata();
}

async function prksSetWorkDetailsMode(mode) {
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const work = ownerCtx && ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
    const next = ['view', 'metadata', 'people', 'tags'].includes(mode) ? mode : 'view';
    if (!ownerCtx || !ownerCtx.ui || !work || !prksOwnerTabIsFocused(ownerCtx)) return;
    prksCaptureWorkMetaDraft(ownerCtx);
    ownerCtx.ui.workDetailsMode = next;
    if (next === 'metadata') {
        if (ownerCtx.ui.workMetaDraftWorkId !== String(work.id) || !ownerCtx.ui.workMetaDraft) {
            const hydration = prksAwaitPendingWorkMetadata();
            if (hydration) {
                await hydration;
                // Anything could have changed across that await.
                if (!ownerCtx.ui || ownerCtx.destroyed || ownerCtx.ui.workDetailsMode !== 'metadata' ||
                    !prksOwnerTabIsFocused(ownerCtx)) return;
                const current = ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
                if (!current || current.id !== work.id) return;
            }
            ownerCtx.ui.workMetaDraft = prksWorkMetaDraftFromWork(work);
            ownerCtx.ui.workMetaDraftWorkId = String(work.id);
        }
    } else if (next !== 'metadata' && ownerCtx.ui.workDetailsMode !== 'metadata') {
        /* A metadata draft only lives while metadata editing is active. */
    }
    if (next !== 'metadata') {
        ownerCtx.ui.workMetaDraft = null;
        ownerCtx.ui.workMetaDraftWorkId = null;
    }
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}
window.prksSetWorkDetailsMode = prksSetWorkDetailsMode;

async function prksCancelWorkMetaEdit() {
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const work = ownerCtx && ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
    if (!ownerCtx || !ownerCtx.ui || !work) return;
    prksCaptureWorkMetaDraft(ownerCtx);
    if (prksWorkMetaDraftIsDirty(ownerCtx, work)) {
        const confirmed = await prksConfirmDestructive({
            title: 'Discard metadata changes?',
            message: 'Your unsaved metadata edits will be discarded.',
            confirmLabel: 'Discard changes',
        });
        if (!confirmed) return;
    }
    await toggleWorkMetaEditForContext(ownerCtx, false);
}
window.prksCancelWorkMetaEdit = prksCancelWorkMetaEdit;

async function toggleWorkMetaEditForContext(ownerCtx, isEditing) {
    if (!ownerCtx || !prksOwnerTabIsFocused(ownerCtx)) return;
    const _cw = ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
    if (_cw) {
        if (!ownerCtx.ui) return;
        if (isEditing) {
            ownerCtx.ui.workDetailsMode = 'metadata';
            if (ownerCtx.ui.workMetaDraftWorkId !== String(_cw.id) || !ownerCtx.ui.workMetaDraft) {
                const hydration = prksAwaitPendingWorkMetadata();
                if (hydration) {
                    await hydration;
                    if (!ownerCtx.ui || ownerCtx.destroyed || !prksOwnerTabIsFocused(ownerCtx)) return;
                    const current = ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
                    if (!current || current.id !== _cw.id) return;
                }
                ownerCtx.ui.workMetaDraft = prksWorkMetaDraftFromWork(_cw);
                ownerCtx.ui.workMetaDraftWorkId = String(_cw.id);
            }
        } else {
            ownerCtx.ui.workDetailsMode = 'view';
            ownerCtx.ui.workMetaDraft = null;
            ownerCtx.ui.workMetaDraftWorkId = null;
        }
        const panel = prksPrepareRightPanelReplace(ownerCtx);
        if (panel) {
            panel.innerHTML = prksWorkRightPanelStackHtml(_cw, ownerCtx.ui.workDetailsMode, ownerCtx);
            if (!isEditing) initPrksPrivateNotesEditor('work', _cw.id, ownerCtx);
            initWorkTagCombobox(_cw.id, ownerCtx);
            if (!isEditing && typeof mountPlaylistAttachControls === 'function') {
                void mountPlaylistAttachControls(_cw, ownerCtx);
            }
            if (!isEditing && typeof mountFolderAttachControlsForWork === 'function') {
                void mountFolderAttachControlsForWork(_cw, ownerCtx);
            }
            if (typeof initWorkDetailRightPanelActions === 'function') {
                initWorkDetailRightPanelActions(_cw, ownerCtx);
            }
            if (isEditing) {
                prksMountWorkMetaEditor(ownerCtx, _cw);
            }
            if (!isEditing) prksBindAutosizeTextareas(panel);
        }
        if (ownerCtx.ui) ownerCtx.ui.rightPanelTab = 'details';
        prksSyncRightPanelTabStrip('details');
    }
}

/* `submitWorkMetaEdit()` and its post-save settle helper are gone. Every
 * user-editable Work metadata value they used to PATCH now belongs to a
 * durable save group -- Identity, Progress, Video source and Bibliographic
 * details -- so the editor has no online-only mutation path left. The last
 * version of that function sent an EMPTY payload, which is what finishing
 * the program looks like from the inside.
 */

function prksWorkHasRoleLink(roles, personId, roleType) {
    const pid = String(personId || '').trim();
    const rt = String(roleType || '').trim();
    if (!pid || !rt || !Array.isArray(roles)) return false;
    return roles.some(
        (r) =>
            String(r.person_id || r.id || '').trim() === pid &&
            String(r.role_type || '').trim() === rt
    );
}

/** Linked persons on the work details panel, grouped by role (order follows DB order_index). */

/** Linked persons on the work details panel, grouped by role (order follows DB order_index). */
function buildWorkLinkedPersonsHtml(work, options = {}) {
    const editable = !!options.editable;
    if (!work.roles || work.roles.length === 0) {
        return '<p class="meta-row work-linked-persons__empty">No persons linked.</p>';
    }
    const wid = String(work.id || '').trim();
    const roleOrder = [];
    const groups = Object.create(null);
    for (const r of work.roles) {
        const rt = (r.role_type && String(r.role_type).trim()) || 'Linked';
        if (!groups[rt]) {
            groups[rt] = [];
            roleOrder.push(rt);
        }
        groups[rt].push(r);
    }
    return roleOrder
        .map((rt) => {
            const chips = groups[rt]
                .map((a) => {
                    const display = prksRoleDisplayName(a) || 'Person';
                    const canonical = prksPersonCanonicalName(a) || display;
                    const safePersonId = encodeURIComponent(String(a.id || ''));
                    const pid = String(a.id || '').trim();
                    const oi =
                        a.order_index != null && a.order_index !== ''
                            ? String(a.order_index)
                            : '0';
                    const roleAttr = escapeHtml(rt);
                    const titleAttr =
                        display !== canonical
                            ? ` title="Profile: ${escapeHtml(canonical)}"`
                            : '';
                    const controls = editable
                        ? `<button type="button" class="work-linked-persons__edit-credit" aria-label="Edit name on this file" data-work-id="${escapeHtml(wid)}" data-person-id="${escapeHtml(pid)}" data-role-type="${roleAttr}" data-order-index="${escapeHtml(oi)}" data-display-name="${escapeHtml(display)}" data-canonical-name="${escapeHtml(canonical)}" onclick="event.stopPropagation(); void prksEditRoleCreditOnWork(this);">${typeof prksIcon === 'function' ? prksIcon('pencil', { size: 'sm' }) : '✎'}</button><button type="button" class="work-linked-persons__unlink" aria-label="Remove link from this file" data-work-id="${escapeHtml(wid)}" data-person-id="${escapeHtml(pid)}" data-role-type="${roleAttr}" data-order-index="${escapeHtml(oi)}" onclick="event.stopPropagation(); void prksRemoveWorkRoleLink(this);">×</button>`
                        : '';
                    return `<span class="work-linked-persons__chip tag"><a class="work-linked-persons__chip-link" href="#/people/${safePersonId}"${titleAttr}>${typeof prksIcon === 'function' ? prksIcon('user', { size: 'sm' }) : ''} ${escapeHtml(display)}</a>${controls}</span>`;
                })
                .join(' ');
            return `<div class="work-linked-persons__role"><h4 class="work-linked-persons__role-title">${escapeHtml(rt)}</h4><div class="tag-cloud">${chips}</div></div>`;
        })
        .join('');
}

async function prksRemoveWorkRoleLink(btn) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (!btn) return;
    const workId = (btn.getAttribute('data-work-id') || '').trim();
    const personId = (btn.getAttribute('data-person-id') || '').trim();
    const roleType = (btn.getAttribute('data-role-type') || '').trim();
    const orderIndex = (btn.getAttribute('data-order-index') || '0').trim();
    if (!workId || !personId || !roleType) {
        await prksAlertMessage('Missing link data.', 'Error');
        return;
    }
    const confirmed = await prksConfirmDestructive({
        title: 'Remove link?',
        message: 'Remove this person from the file for this role?',
        confirmLabel: 'Remove',
    });
    if (!confirmed) return;
    const params = new URLSearchParams({
        person_id: personId,
        role_type: roleType,
        order_index: orderIndex || '0',
    });
    let res;
    try {
        res = await prksRequest(`/api/works/${encodeURIComponent(workId)}/roles?${params}`, { method: 'DELETE' });
    } catch (e) {
        console.error(e);
        await prksAlertMessage('Could not remove link.', 'Error');
        return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        await prksAlertMessage(data.error || 'Could not remove link.', 'Could not save');
        return;
    }
    // Every role type stales People; Author additionally stales cached
    // Argument source authors. One helper owns both dependencies.
    const coherenceToken =
        typeof prksMarkWorkRoleChanged === 'function'
            ? prksMarkWorkRoleChanged(workId, roleType)
            : typeof prksOfflineMarkEntityChanged === 'function'
              ? prksOfflineMarkEntityChanged('work', workId)
              : null;
    await prksRefreshUiAfterWorkRoleRemoved(workId, ownerCtx, coherenceToken);
}

async function prksRefreshUiAfterWorkRoleRemoved(workId, ownerCtx, coherenceToken) {
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const route = ctx && (ctx.lastResolvedRoute || ctx.route);
    const wIdStr = String(workId);
    const _cwBefore = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
    if (
        route &&
        route.name === 'work' &&
        route.params &&
        String(route.params.workId) === wIdStr &&
        _cwBefore &&
        String(_cwBefore.id) === wIdStr
    ) {
        if (typeof fetchWorkDetails === 'function') {
            const _refreshedW = await fetchWorkDetails(wIdStr);
            if (_refreshedW && typeof prksOfflineCacheEntityIfCurrent === 'function' && coherenceToken != null) {
                void prksOfflineCacheEntityIfCurrent('work', workId, _refreshedW, coherenceToken);
            }
            if (typeof prksApplyOwnedWorkEntity !== 'function' || !prksApplyOwnedWorkEntity(ctx, workId, _refreshedW)) {
                return;
            }
            prksReplaceFocusedWorkDetailsPanel(ctx, _refreshedW);
        }
        return;
    }
    if (route && route.name === 'person') {
        const personId = route.params && route.params.personId ? route.params.personId : '';
        const expectedPerson = ctx && ctx.getEntity ? ctx.getEntity('person') : null;
        if (personId && typeof fetchPersonDetails === 'function') {
            try {
                const person = await fetchPersonDetails(personId);
                if (!ctx || ctx.destroyed || !ctx.mounted || !ctx.root) return;
                const routeAfter = ctx.lastResolvedRoute || ctx.route;
                if (!routeAfter || routeAfter.name !== 'person') return;
                if (!routeAfter.params || String(routeAfter.params.personId) !== String(personId)) return;
                const livePerson = ctx.getEntity ? ctx.getEntity('person') : null;
                if (!livePerson || String(livePerson.id) !== String(personId)) return;
                if (expectedPerson && String(expectedPerson.id) !== String(personId)) return;
                if (person && typeof renderPersonDetails === 'function' && prksOwnerTabIsFocused(ctx)) {
                    renderPersonDetails(ctx, person, ctx.root);
                }
            } catch (e) {
                console.error(e);
            }
        }
    }
}

function renderWorkMetaTab(work, mode = 'view') {
    const managingPeople = mode === 'people';
    const managingTags = mode === 'tags';
    /* The synchronized rows below are repainted from the overlay by the
     * metadata editor module, but the Original URL row is rendered HERE, from
     * this Work -- and `source_url` is a synchronized field. Rendering it from
     * the acknowledged record would hide a pending provenance edit and, worse,
     * point the link at the address the user just replaced. Same overlay,
     * same answer. */
    if (typeof prksEffectiveWorkSync === 'function') work = prksEffectiveWorkSync(work);

    const renderRow = (label, val) =>
        val ? `<p class="meta-row"><strong>${escapeHtml(label)}:</strong> ${escapeHtml(val)}</p>` : '';
    const showPublishedDate = !(work.year && String(work.year).trim());
    const inferredKindView = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
    const originalUrlPdf = inferredKindView === 'pdf' ? String(work.source_url || '').trim() : '';
    const statusRaw = String(work.status || '').trim();
    const statusText = statusRaw || 'Not Started';
    const statusClass = statusText.replace(/[^A-Za-z0-9 ]+/g, ' ').trim().replace(/\s+/g, ' ');
    const statusIconHtml =
        typeof prksProgressStatusIconHtml === 'function'
            ? prksProgressStatusIconHtml(statusText, { className: 'status-badge__icon', size: 'sm' })
            : '';
    const hasMetadata =
        work.year ||
        (showPublishedDate && work.published_date) ||
        work.publisher ||
        work.location ||
        work.edition ||
        work.journal ||
        work.volume ||
        work.issue ||
        work.pages ||
        work.isbn ||
        work.doi ||
        work.abstract ||
        originalUrlPdf;

    return `
        <div class="doc-meta-card">
            <div class="card-heading-row">
                <h3>Title</h3>
                <button type="button" onclick="prksSetWorkDetailsMode('metadata')" class="prks-btn prks-btn--ghost prks-btn--sm inline-action-btn">Edit metadata</button>
            </div>
            <p class="card-title">${escapeHtml(work.title)}</p>
            <div class="card-heading-row card-heading-row--wrap">
                <span class="meta-row">Status</span>
                <span class="status-badge ${statusClass}">${statusIconHtml}${escapeHtml(statusText)}</span>
            </div>
            <div class="card-heading-row card-heading-row--wrap">
                <span class="meta-row">Document type</span>
                ${typeof prksDocTypeBadgeHtml === 'function' ? prksDocTypeBadgeHtml(work.doc_type) : ''}
            </div>
        </div>
        <details class="doc-meta-card work-details-metadata">
            <summary><span>Metadata</span><span class="meta-row">Bibliographic details</span></summary>
            <div class="work-details-metadata__body">
            ${renderRow('Year', work.year)}
            ${showPublishedDate ? renderRow('Published', typeof prksFormatPublishedForDisplay === 'function' ? prksFormatPublishedForDisplay(work.published_date) : work.published_date) : ''}
            <div id="work-bib-rows" data-prks-role="work-bib-rows">${prksWorkBibRowsHtml(work)}</div>
            ${
                originalUrlPdf
                    ? `<p class="meta-row"><strong>Original URL:</strong> <a href="${escapeHtml(originalUrlPdf)}" target="_blank" rel="noopener noreferrer">${escapeHtml(originalUrlPdf)}</a></p>`
                    : ''
            }
            ${renderRow('Abstract', work.abstract)}
            ${!hasMetadata ? '<p class="meta-row meta-row--muted-italic" data-prks-role="work-meta-empty">No metadata available.</p>' : ''}
            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm copy-bibtex-btn" aria-live="polite">${typeof prksIcon === 'function' ? prksIcon('copy', { size: 'sm' }) : ''} Copy BibTeX</button>
            </div>
        </details>
        <div class="doc-meta-card">
            <div class="card-heading-row card-heading-row--wrap">
                <h3>Linked Persons</h3>
                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" onclick="prksSetWorkDetailsMode('${managingPeople ? 'view' : 'people'}')">${managingPeople ? 'Done' : 'Manage relationships'}</button>
            </div>
            ${managingPeople ? '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm work-link-person-btn" onclick="openModal(\'role-modal\')" title="Link a person to this file">Link person</button>' : ''}
            <div class="work-linked-persons-by-role">${buildWorkLinkedPersonsHtml(work, { editable: managingPeople })}</div>
        </div>
        <div class="doc-meta-card">
            <div class="card-heading-row card-heading-row--wrap"><h3>Tags</h3><button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" onclick="prksSetWorkDetailsMode('${managingTags ? 'view' : 'tags'}')">${managingTags ? 'Done' : 'Manage tags'}</button></div>
            <div id="work-tags-list" class="tag-cloud work-tags-list">${renderWorkTagsChips(work, { editable: managingTags })}</div>
            ${managingTags ? `<p class="tag-add-field__caption">Add a tag</p>
            <div class="tag-add-shell combobox-container">
                <div class="tag-add-shell__field">
                    ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : '<span class="tag-add-shell__icon"></span>'}
                    <input type="text" id="work-tag-search" class="tag-add-shell__input" placeholder="Search tags or add…" maxlength="120" autocomplete="off" aria-label="Search or add tag">
                </div>
                <div id="work-tag-search-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>` : ''}
        </div>
        <details class="doc-meta-card work-details-advanced">
            <summary>More</summary>
            <button type="button" class="prks-btn prks-btn--danger delete-work-btn" title="Delete this file">${typeof prksIcon === 'function' ? prksIcon('trash', { size: 'sm' }) : ''} Delete File</button>
        </details>
    `;
}

function renderFolderDetailsPanel(folder) {
    if (!folder) return '<p class="meta-row">Folder not found</p>';
    const desc = (folder.description || '').trim() || 'No description.';
    const parentEditing =
        window.__prksFolderParentEdit &&
        typeof window.__prksFolderParentEdit === 'object' &&
        window.__prksFolderParentEdit[String(folder.id)] === true;
    const parentLine = folder.parent
        ? `Parent: <a href="#/folders/${encodeURIComponent(String(folder.parent.id || ''))}" class="route-sidebar__link">${escapeHtml(
              folder.parent.title || folder.parent.id
          )}</a>`
        : 'Top-level folder';
    const children = Array.isArray(folder.children) ? folder.children : [];
    const childrenLine = children.length
        ? `<p class="meta-row">Subfolders: ${children
              .map(
                  (ch) =>
                      `<a href="#/folders/${encodeURIComponent(String(ch.id || ''))}" class="route-sidebar__link">${escapeHtml(
                          ch.title || ch.id
                      )}</a>`
              )
              .join(' · ')}</p>`
        : '<p class="meta-row">No subfolders.</p>';
    const editing =
        window.__prksFolderDetailEditing &&
        typeof window.__prksFolderDetailEditing === 'object' &&
        window.__prksFolderDetailEditing[folder.id] === true;
    const editLabel = editing ? 'Done' : 'Edit folder';
    const searchBlock = editing
        ? `
            <div class="tag-add-shell combobox-container">
                <div class="tag-add-shell__field">
                    ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : '<span class="tag-add-shell__icon"></span>'}
                    <input type="text" id="prks-folder-library-search" class="tag-add-shell__input" placeholder="Search library files…" maxlength="300" autocomplete="off" aria-label="Search files to add">
                </div>
                <div id="prks-folder-library-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
            <p class="meta-row meta-row--spaced">Search library files and add or move them into this folder.</p>
        `
        : '';
    return `
        <div class="doc-meta-card">
            <h3>Folder</h3>
            <p class="card-title">${escapeHtml(folder.title)}</p>
            <p class="meta-row">${escapeHtml(desc)}</p>
        </div>
        <div class="doc-meta-card">
            <div class="card-heading-row">
                <h3>Hierarchy</h3>
                <button type="button" class="prks-btn prks-btn--secondary" id="prks-folder-parent-edit-btn" aria-expanded="${
                    parentEditing ? 'true' : 'false'
                }">${parentEditing ? 'Done' : 'Move folder'}</button>
            </div>
            <p class="meta-row">${parentLine}</p>
            ${childrenLine}
            <div id="prks-folder-parent-edit-wrap" class="${parentEditing ? '' : 'hidden'}">
                <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                    <div class="tag-add-shell__field">
                        ${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : '<span class="tag-add-shell__icon"></span>'}
                        <input type="text" id="prks-folder-parent-search" class="tag-add-shell__input" placeholder="Search destination folder…" autocomplete="off" aria-label="Search destination folder">
                    </div>
                    <input type="hidden" id="prks-folder-parent-id" value="">
                    <div id="prks-folder-parent-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                </div>
                <div class="prks-work-folder-controls">
                    <button type="button" class="prks-btn prks-btn--primary" id="prks-folder-parent-save-btn">Move here</button>
                    ${
                        folder.parent
                            ? '<button type="button" class="prks-btn prks-btn--secondary" id="prks-folder-parent-top-btn">Make top-level</button>'
                            : ''
                    }
                </div>
                <p id="prks-folder-parent-status" class="meta-row meta-row--spaced" aria-live="polite"></p>
            </div>
        </div>
        <div class="doc-meta-card">
            <div class="card-heading-row">
                <h3>Folder files</h3>
                <button type="button" class="prks-btn prks-btn--secondary" id="prks-folder-library-edit-btn">${editLabel}</button>
            </div>
            <p class="meta-row">Add existing files from your library.</p>
            ${searchBlock}
            <p id="prks-folder-library-status" class="meta-row meta-row--spaced" aria-live="polite"></p>
        </div>
        <div class="doc-meta-card">
            <h3>Tags</h3>
            <div id="folder-panel-tags-list" class="tag-cloud work-tags-list">${renderFolderTagsChipsHtml(folder)}</div>
            <p class="tag-add-field__caption">Add a tag</p>
            <div class="tag-add-shell combobox-container">
                <div class="tag-add-shell__field">
                    ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : '<span class="tag-add-shell__icon"></span>'}
                    <input type="text" id="folder-tag-search" class="tag-add-shell__input" placeholder="Search tags or type a new name…" maxlength="120" autocomplete="off" aria-label="Search or add tag">
                </div>
                <div id="folder-tag-search-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
        </div>
    `;
}

function renderFolderTagsChipsHtml(folder) {
    const tags = folder.tags || [];
    if (tags.length === 0) {
        return '<span class="work-tags-empty">No tags yet.</span>';
    }
    return tags
        .map(
            (t) =>
                `<span class="tag work-tag-chip work-tag-chip--colored" style="--tag-accent:${escapeHtml(t.color || '#6d6cf7')};" ` +
                `role="button" tabindex="0" data-tag-nav="${encodeURIComponent(t.name)}" ` +
                `data-prks-route="#/search?tag=${encodeURIComponent(t.name)}" ` +
                `onkeydown="if(event && (event.key==='Enter' || event.key===' ')) {event.preventDefault(); this.click();}">` +
                `${escapeHtml(t.name)}` +
                `<button type="button" class="work-tag-remove" title="Remove tag" aria-label="Remove" data-folder-id="${escapeHtml(folder.id)}" data-tag-id="${escapeHtml(t.id)}">×</button>` +
                `</span>`
        )
        .join('');
}

function escapeHtml(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const PRKS_WORK_STATUS_LABELS = ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'];
const PRKS_UPLOAD_ROLE_LABELS = ['Author', 'Editor', 'Reviewer', 'Translator', 'Introduction', 'Foreword', 'Afterword'];
const PRKS_LINK_ROLE_LABELS = ['Author', 'Editor', 'Reviewer', 'Mentioned', 'Translator', 'Introduction', 'Foreword', 'Afterword'];

function prksEscapeAttr(s) {
    if (s == null || s === '') return '';
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

function prksMountRoleSegmented(mountId, hiddenId, selectedValue, labels, ariaLabel) {
    const mount = document.getElementById(mountId);
    if (!mount || typeof prksSegmentedControlHtml !== 'function') return;
    const labelsArr =
        Array.isArray(labels) && labels.length
            ? labels
            : Array.isArray(PRKS_UPLOAD_ROLE_LABELS)
              ? PRKS_UPLOAD_ROLE_LABELS
              : [];
    const fallback = labelsArr[0] || 'Author';
    const selRaw = selectedValue != null ? String(selectedValue) : '';
    const sel = labelsArr.includes(selRaw) ? selRaw : fallback;
    mount.innerHTML = prksSegmentedControlHtml(
        hiddenId,
        ariaLabel || 'Role for linked person',
        labelsArr,
        sel,
        'roles',
        {
            compact: true,
            withRoleIcons: true,
        },
    );
    const hidden = document.getElementById(hiddenId);
    if (hidden) delete hidden.dataset.prksSegBound;
    if (typeof prksBindSegmentedHidden === 'function') {
        prksBindSegmentedHidden(hiddenId);
    }
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(mount);
}

function prksMountUploadRoleSegmented(selectedValue) {
    prksMountRoleSegmented('upload-role-seg-mount', 'upload-role-type', selectedValue);
}

function prksMountLinkRoleSegmented(selectedValue) {
    prksMountRoleSegmented(
        'role-role-seg-mount',
        'role-type',
        selectedValue,
        PRKS_LINK_ROLE_LABELS,
        'Link role',
    );
}

function prksMountMetaRoleSegmented(selectedValue) {
    prksMountRoleSegmented('meta-role-seg-mount', 'meta-role-type', selectedValue);
}

window.prksMountUploadRoleSegmented = prksMountUploadRoleSegmented;
window.prksMountLinkRoleSegmented = prksMountLinkRoleSegmented;
window.prksMountMetaRoleSegmented = prksMountMetaRoleSegmented;

function prksSegmentedControlHtml(hiddenId, ariaLabel, labels, selectedValue, variant, options) {
    const opts = options && typeof options === 'object' ? options : {};
    const labelsArr = Array.isArray(labels) ? labels : [];
    const fallback = labelsArr[0] || '';
    const selRaw = selectedValue != null ? String(selectedValue) : '';
    const sel = labelsArr.includes(selRaw) ? selRaw : fallback;
    const compact = !!opts.compact;
    const withRoleIcons = !!opts.withRoleIcons && variant === 'roles';
    const segMod =
        (variant === 'status'
            ? ' prks-segmented--status prks-segmented--single-row'
            : variant === 'roles'
              ? ' prks-segmented--roles' + (withRoleIcons ? ' prks-segmented--roles-icons' : '')
              : '') + (compact ? ' prks-segmented--compact' : '');
    const buttons = labelsArr
        .map((l) => {
            const active = l === sel ? ' prks-segmented__btn--active' : '';
            const pressed = l === sel ? 'true' : 'false';
            if (withRoleIcons) {
                const iconFn = typeof prksRoleTypeIconHtml === 'function' ? prksRoleTypeIconHtml : null;
                const shortFn = typeof prksRoleTypeShortLabel === 'function' ? prksRoleTypeShortLabel : null;
                const label = shortFn ? shortFn(l) : l;
                const iconHtml = iconFn
                    ? `<span class="prks-segmented__btn-icon">${iconFn(l, { size: 'sm' })}</span>`
                    : '';
                return `<button type="button" class="prks-segmented__btn${active}" data-value="${prksEscapeAttr(l)}" aria-pressed="${pressed}" role="radio" aria-label="${prksEscapeAttr(l)}">${iconHtml}<span class="prks-segmented__btn-label">${escapeHtml(label)}</span></button>`;
            }
            const titleAttr = variant === 'status' ? ` title="${prksEscapeAttr(l)}"` : '';
            if (variant === 'status') {
                const statusIcon =
                    typeof prksProgressStatusIconHtml === 'function'
                        ? prksProgressStatusIconHtml(l, { size: 'sm' })
                        : '';
                const iconWrap = statusIcon
                    ? `<span class="prks-segmented__btn-icon">${statusIcon}</span>`
                    : '';
                return `<button type="button" class="prks-segmented__btn${active}" data-value="${prksEscapeAttr(l)}" aria-pressed="${pressed}" role="radio" aria-label="${prksEscapeAttr(l)}"${titleAttr}>${iconWrap}<span class="prks-segmented__btn-label">${escapeHtml(l)}</span></button>`;
            }
            return `<button type="button" class="prks-segmented__btn${active}" data-value="${prksEscapeAttr(l)}" aria-pressed="${pressed}" role="radio"${titleAttr}>${escapeHtml(l)}</button>`;
        })
        .join('');
    let wrapMod = variant === 'status' ? ' prks-segmented-wrap--status-row' : '';
    if (compact) wrapMod += ' prks-segmented-wrap--compact';
    const hiddenExtra = [
        opts.dataField ? ` data-field="${prksEscapeAttr(opts.dataField)}"` : '',
        opts.dataRole ? ` data-role="${prksEscapeAttr(opts.dataRole)}"` : '',
        // The hidden input IS the field control as far as the synchronized
        // metadata editor is concerned: it holds the value, and the buttons
        // are its presentation.
        opts.workField ? ` data-prks-work-field="${prksEscapeAttr(opts.workField)}"` : '',
    ].join('');
    return `<div class="prks-segmented-wrap${wrapMod}">
    <input type="hidden" id="${prksEscapeAttr(hiddenId)}" value="${prksEscapeAttr(sel)}"${hiddenExtra}>
    <div class="prks-segmented${segMod}" role="radiogroup" aria-label="${prksEscapeAttr(ariaLabel)}">${buttons}</div>
  </div>`;
}

function prksBindSegmentedHidden(hiddenId) {
    const hidden = document.getElementById(hiddenId);
    if (!hidden || hidden.dataset.prksSegBound === '1') return;
    const wrap = hidden.closest('.prks-segmented-wrap');
    const seg = wrap && wrap.querySelector('.prks-segmented');
    if (!seg) return;
    hidden.dataset.prksSegBound = '1';
    seg.querySelectorAll('.prks-segmented__btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const v = btn.getAttribute('data-value') || '';
            hidden.value = v;
            seg.querySelectorAll('.prks-segmented__btn').forEach((b) => {
                const on = b === btn;
                b.classList.toggle('prks-segmented__btn--active', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        });
    });
}

function renderWorkTagsChips(work, options = {}) {
    const editable = !!options.editable;
    const tags = work.tags || [];
    if (tags.length === 0) {
        return '<span class="work-tags-empty">No tags yet.</span>';
    }
    return tags
        .map(
            (t) =>
                `<span class="tag work-tag-chip work-tag-chip--colored" style="--tag-accent:${escapeHtml(t.color || '#6d6cf7')};">` +
                `<a href="#/search?tag=${encodeURIComponent(t.name)}" class="work-tag-chip__link">${escapeHtml(t.name)}</a>` +
                (editable ? `<button type="button" class="work-tag-remove" title="Remove tag" aria-label="Remove" data-work-id="${escapeHtml(work.id)}" data-tag-id="${escapeHtml(t.id)}">×</button>` : '') +
                `</span>`
        )
        .join('');
}

async function prksReloadEntityTagsUI(entityType, entityId, ownerCtx, coherenceToken) {
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (entityType === 'work') {
        const fresh = await fetchWorkDetails(entityId);
        if (fresh && typeof prksOfflineCacheEntityIfCurrent === 'function' && coherenceToken != null) {
            void prksOfflineCacheEntityIfCurrent('work', entityId, fresh, coherenceToken);
        }
        if (typeof prksApplyOwnedWorkEntity !== 'function' || !prksApplyOwnedWorkEntity(ctx, entityId, fresh)) {
            return;
        }
        prksReplaceFocusedWorkDetailsPanel(ctx, fresh);
    } else {
        const _tf = await fetchFolderDetails(entityId);
        if (!ctx || ctx.destroyed) return;
        const liveFolder = ctx.getEntity ? ctx.getEntity('folder') : null;
        if (!liveFolder || String(liveFolder.id) !== String(entityId)) return;
        const route = ctx.lastResolvedRoute || ctx.route;
        if (
            !route ||
            route.name !== 'folder-detail' ||
            !route.params ||
            String(route.params.folderId) !== String(entityId)
        ) {
            return;
        }
        if (typeof ctx.setEntity === 'function') ctx.setEntity('folder', _tf);
        if (ctx.root && ctx.mounted && typeof renderFolderDetails === 'function') {
            renderFolderDetails(ctx, _tf, ctx.root);
        }
        if (!prksOwnerTabIsFocused(ctx)) return;
        const panel = prksPrepareRightPanelReplace(ctx);
        if (panel && ((ctx.ui && ctx.ui.rightPanelTab) || 'details') === 'details' && _tf && _tf.id === entityId) {
            panel.innerHTML = prksFolderRightPanelStackHtml(_tf);
            initPrksPrivateNotesEditor('folder', entityId, ctx);
            initFolderTagCombobox(entityId);
            if (typeof mountFolderLibraryAttachControls === 'function') {
                void mountFolderLibraryAttachControls(_tf);
            }
        }
    }
}

function prksWorkTagOwnerLive(ownerCtx, generation, workId, input) {
    if (!ownerCtx || ownerCtx.destroyed || !ownerCtx.ui || ownerCtx.ui.workDetailsMode !== 'tags') return false;
    if (
        typeof prksTabContextOwnsEntityRoute === 'function' &&
        !prksTabContextOwnsEntityRoute(ownerCtx, generation, 'work', workId, 'work')
    ) return false;
    if (!prksOwnerTabIsFocused(ownerCtx) || !prksRightPanelOwnedBy(ownerCtx)) return false;
    const liveInput = document.getElementById('work-tag-search');
    return !!liveInput && liveInput === input && prksRightPanelOwnedBy(ownerCtx, liveInput);
}

async function prksAttachExistingTag(entityType, entityId, tagId, ownerCtx, triggerInput) {
    if (entityType === 'work') {
        await prksWorkTagEdit(ownerCtx, tagId, true);
        if (triggerInput) { triggerInput.disabled = false; triggerInput.removeAttribute('aria-busy'); }
        return;
    }
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const owner = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const generation = owner && typeof owner.generation === 'number' ? owner.generation : undefined;
    if (triggerInput) {
        triggerInput.disabled = true;
        triggerInput.setAttribute('aria-busy', 'true');
    }
    try {
        await addTagToFolder(entityId, tagId);
        const coherenceToken = null;
        if (entityType === 'work') {
            const input = document.getElementById('work-tag-search');
            if (prksWorkTagOwnerLive(owner, generation, entityId, input)) input.value = '';
        } else {
            const input = document.getElementById('folder-tag-search');
            if (input) input.value = '';
        }
        await prksReloadEntityTagsUI(entityType, entityId, owner, coherenceToken);
    } catch (e) {
        if (triggerInput) {
            triggerInput.disabled = false;
            triggerInput.removeAttribute('aria-busy');
        }
        if (prksOfflineWasGuardRefusal(e)) return;
        console.error(e);
        await prksAlertMessage('Could not add tag.', 'Error');
    }
}

function prksTagAliasesList(tag) {
    return Array.isArray(tag.aliases) ? tag.aliases : [];
}

function prksTagMatchesQuery(tag, valLower) {
    if (!valLower) return true;
    const n = (tag.name || '').toLowerCase();
    if (n.includes(valLower)) return true;
    return prksTagAliasesList(tag).some((a) => String(a || '').toLowerCase().includes(valLower));
}

function prksTagExactMatch(tag, valLower) {
    if (!valLower) return false;
    if ((tag.name || '').trim().toLowerCase() === valLower) return true;
    return prksTagAliasesList(tag).some((a) => String(a || '').trim().toLowerCase() === valLower);
}

/** Dropdown label: highlight alias → canonical when the query matches an alias. */
function prksTagComboboxLabel(tag, valLower) {
    const name = tag.name || '';
    if (!valLower) return name;
    const hit = prksTagAliasesList(tag).find((a) => String(a || '').toLowerCase().includes(valLower));
    if (hit) return String(hit) + ' → ' + name;
    return name;
}

async function prksSubmitNewTag(entityType, entityId, name, ownerCtx, triggerInput) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const trimmed = (name || '').trim();
    if (!trimmed) return;
    if (triggerInput) {
        triggerInput.disabled = true;
        triggerInput.setAttribute('aria-busy', 'true');
    }
    try {
        const res = await prksRequest('/api/tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: trimmed, color: '#6d6cf7' }),
        });
        const data = await res.json();
        if (!res.ok || !data.id) throw new Error(data.error || 'No tag id');
        if (typeof prksOfflineMarkTagsChanged === 'function') prksOfflineMarkTagsChanged();
        await prksAttachExistingTag(entityType, entityId, data.id, ownerCtx, triggerInput);
    } catch (e) {
        console.error(e);
        if (triggerInput) {
            triggerInput.disabled = false;
            triggerInput.removeAttribute('aria-busy');
        }
        await prksAlertMessage('Could not create tag.', 'Error');
    }
}

function initTagComboboxForEntity(entityType, entityId, inputId, resultsId, ownerCtx) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);
    if (!input || !results) return;
    const generation = ownerCtx && typeof ownerCtx.generation === 'number' ? ownerCtx.generation : undefined;

    function liveWorkInput() {
        return entityType !== 'work' || prksWorkTagOwnerLive(ownerCtx, generation, entityId, input);
    }

    function getAttachedIds() {
        if (entityType === 'work' && ownerCtx && ownerCtx.getEntity) {
            const work = ownerCtx.getEntity('work');
            if (!work || String(work.id) !== String(entityId)) return new Set();
            return new Set((work.tags || []).map((t) => t.id));
        }
        const ent = typeof prksFocusedEntity === 'function'
            ? prksFocusedEntity(entityType === 'work' ? 'work' : 'folder')
            : null;
        if (!ent || ent.id !== entityId) return new Set();
        return new Set((ent.tags || []).map((t) => t.id));
    }

    async function renderDropdown() {
        if (!liveWorkInput()) return;
        const all = await fetchTags({ used: false });
        if (!liveWorkInput()) return;

        const val = input.value.trim();
        const valLower = val.toLowerCase();
        const attached = getAttachedIds();
        const available = all.filter((t) => !attached.has(t.id));
        const filtered = !val
            ? available.slice(0, 40)
            : available.filter((t) => prksTagMatchesQuery(t, valLower)).slice(0, 40);
        const exactMatch = available.some((t) => prksTagExactMatch(t, valLower));

        results.innerHTML = '';
        if (val && !exactMatch) {
            const c = document.createElement('div');
            c.className = 'result-item result-item--create';
            c.textContent = 'Create tag "' + val + '"';
            c.onmousedown = (ev) => {
                ev.preventDefault();
                if (input.disabled) return;
                prksSubmitNewTag(entityType, entityId, val, ownerCtx, input);
            };
            results.appendChild(c);
        }
        filtered.forEach((tag) => {
            const div = document.createElement('div');
            div.className = 'result-item';
            div.textContent = prksTagComboboxLabel(tag, valLower);
            div.onmousedown = (ev) => {
                ev.preventDefault();
                if (input.disabled) return;
                prksAttachExistingTag(entityType, entityId, tag.id, ownerCtx, input);
            };
            results.appendChild(div);
        });
        if (results.childElementCount === 0) {
            prksHideInlineComboboxResults(results);
        } else {
            prksShowInlineComboboxResults(input, results);
        }
    }

    input.onfocus = () => void renderDropdown();
    input.oninput = () => void renderDropdown();
    input.onblur = () =>
        setTimeout(() => {
            prksHideInlineComboboxResults(results);
        }, 200);
}

function initWorkTagCombobox(workId, ownerCtx) {
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const _cw = ctx && ctx.getEntity ? ctx.getEntity('work') : (typeof prksFocusedEntity === 'function' ? prksFocusedEntity('work') : null);
    if (!_cw || _cw.id !== workId) return;
    if (typeof prksMountWorkTags === 'function') prksMountWorkTags(ctx, workId);
}

function initFolderTagCombobox(folderId) {
    const _cf = typeof prksFocusedEntity === 'function' ? prksFocusedEntity('folder') : null;
    if (!_cf || _cf.id !== folderId) return;
    initTagComboboxForEntity('folder', folderId, 'folder-tag-search', 'folder-tag-search-results');
}

async function mountFolderHierarchyControls(folder) {
    const fid = folder && folder.id ? String(folder.id) : '';
    if (!fid) return;
    const toggleBtn = document.getElementById('prks-folder-parent-edit-btn');
    const wrap = document.getElementById('prks-folder-parent-edit-wrap');
    if (!toggleBtn || !wrap) return;

    if (toggleBtn.dataset.bound !== '1') {
        toggleBtn.dataset.bound = '1';
        toggleBtn.onclick = () => {
            if (!window.__prksFolderParentEdit || typeof window.__prksFolderParentEdit !== 'object') {
                window.__prksFolderParentEdit = {};
            }
            window.__prksFolderParentEdit[fid] = !(window.__prksFolderParentEdit[fid] === true);
            if (typeof updatePanelContent === 'function') updatePanelContent('details');
        };
    }

    const editing =
        window.__prksFolderParentEdit &&
        typeof window.__prksFolderParentEdit === 'object' &&
        window.__prksFolderParentEdit[fid] === true;
    if (!editing) return;

    const input = document.getElementById('prks-folder-parent-search');
    const hidden = document.getElementById('prks-folder-parent-id');
    const results = document.getElementById('prks-folder-parent-results');
    const saveBtn = document.getElementById('prks-folder-parent-save-btn');
    const topBtn = document.getElementById('prks-folder-parent-top-btn');
    const status = document.getElementById('prks-folder-parent-status');
    if (!input || !hidden || !results || !saveBtn) return;

    let folderRows = await fetchFolders();
    if (!Array.isArray(folderRows)) folderRows = [];
    const descendants =
        typeof window.prksCollectFolderDescendantIds === 'function'
            ? window.prksCollectFolderDescendantIds(fid, folderRows)
            : new Set();
    descendants.add(fid);

    if (folder.parent) {
        hidden.value = String(folder.parent.id || '');
        const p = folderRows.find((x) => String(x.id) === String(folder.parent.id));
        const label =
            p && typeof window.prksFolderRowLabel === 'function'
                ? window.prksFolderRowLabel(p, folderRows)
                : String(folder.parent.title || folder.parent.id);
        input.value = label;
    } else {
        hidden.value = '';
        input.value = '';
    }

    function rowLabel(row) {
        if (typeof window.prksFolderRowLabel === 'function') {
            return window.prksFolderRowLabel(row, folderRows);
        }
        return String(row && row.title ? row.title : 'Folder');
    }

    function renderDropdown() {
        const q = String(input.value || '').trim().toLowerCase();
        const filtered = folderRows
            .filter((row) => !descendants.has(row.id))
            .filter((row) => {
                const label = rowLabel(row).toLowerCase();
                return !q || label.includes(q) || String(row.title || '').toLowerCase().includes(q);
            })
            .slice(0, 80);
        results.innerHTML = '';
        if (filtered.length === 0) {
            results.innerHTML = '<div class="result-item no-results">No matching folders</div>';
        } else {
            filtered.forEach((row) => {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent = rowLabel(row);
                div.onmousedown = (ev) => {
                    ev.preventDefault();
                    hidden.value = String(row.id || '');
                    input.value = div.textContent || '';
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            });
        }
        prksShowInlineComboboxResults(input, results);
    }

    input.onfocus = () => renderDropdown();
    input.oninput = () => {
        hidden.value = '';
        renderDropdown();
    };
    input.onblur = () =>
        setTimeout(() => {
            prksHideInlineComboboxResults(results);
        }, 200);

    saveBtn.onclick = async () => {
        const pid = String(hidden.value || '').trim();
        if (!pid) {
            if (status) status.textContent = 'Pick destination folder first.';
            return;
        }
        try {
            if (typeof patchFolder !== 'function') return;
            await patchFolder(fid, { parent_id: pid });
            window.location.reload();
        } catch (e) {
            if (status) status.textContent = String((e && e.message) || 'Could not move folder.');
        }
    };
    if (topBtn) {
        topBtn.onclick = async () => {
            try {
                if (typeof patchFolder !== 'function') return;
                await patchFolder(fid, { parent_id: null });
                window.location.reload();
            } catch (e) {
                if (status) status.textContent = String((e && e.message) || 'Could not move folder.');
            }
        };
    }
}

async function mountFolderLibraryAttachControls(folder) {
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const fid = folder && folder.id ? String(folder.id) : '';
    if (!fid) return;
    const editBtn = document.getElementById('prks-folder-library-edit-btn');
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            if (!window.__prksFolderDetailEditing || typeof window.__prksFolderDetailEditing !== 'object') {
                window.__prksFolderDetailEditing = {};
            }
            window.__prksFolderDetailEditing[fid] = !(window.__prksFolderDetailEditing[fid] === true);
            if (typeof updatePanelContent === 'function') updatePanelContent('details');
        };
    }
    const editing =
        window.__prksFolderDetailEditing &&
        typeof window.__prksFolderDetailEditing === 'object' &&
        window.__prksFolderDetailEditing[fid] === true;
    if (!editing) return;

    const input = document.getElementById('prks-folder-library-search');
    const results = document.getElementById('prks-folder-library-results');
    const status = document.getElementById('prks-folder-library-status');
    if (!input || !results) return;

    let debounceTimer = null;
    async function runSearch() {
        const q = String(input.value || '').trim();
        results.innerHTML = '';
        if (!q) {
            results.innerHTML = '<div class="result-item no-results">Type to search your library…</div>';
            prksShowInlineComboboxResults(input, results);
            return;
        }
        const rows = typeof fetchSearch === 'function' ? await fetchSearch(q) : [];
        const list = Array.isArray(rows) ? rows : [];
        if (list.length === 0) {
            results.innerHTML = '<div class="result-item no-results">No files found</div>';
        } else {
            for (const w of list.slice(0, 40)) {
                const wid = w && w.id ? String(w.id) : '';
                const title = w && w.title ? String(w.title) : wid;
                const wf = w && w.folder_id ? String(w.folder_id) : '';
                const row = document.createElement('div');
                row.className = 'result-item';
                row.style.cssText =
                    'display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;';
                const label = document.createElement('span');
                label.textContent = title;
                label.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;';
                const actionHost = document.createElement('div');
                if (!wf) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'prks-btn prks-btn--secondary prks-btn--sm';
                    btn.textContent = 'Add';
                    btn.onclick = async () => {
                        try {
                            if (typeof addWorkToFolder !== 'function') return;
                            await addWorkToFolder(fid, wid);
                            if (status) status.textContent = 'Added.';
                            await prksReloadEntityTagsUI('folder', fid, ownerCtx);
                        } catch (e) {
                            if (status) status.textContent = String((e && e.message) || 'Could not add.');
                        }
                    };
                    actionHost.appendChild(btn);
                } else if (wf === fid) {
                    const s = document.createElement('span');
                    s.className = 'meta-row';
                    s.style.cssText = 'font-size:0.75rem;color:var(--text-secondary);';
                    s.textContent = 'In this folder';
                    actionHost.appendChild(s);
                } else {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'prks-btn prks-btn--secondary prks-btn--sm';
                    btn.textContent = 'Move here';
                    btn.onclick = async () => {
                        try {
                            if (typeof patchWorkFolder !== 'function') return;
                            await patchWorkFolder(wid, fid);
                            if (status) status.textContent = 'Moved.';
                            await prksReloadEntityTagsUI('folder', fid, ownerCtx);
                        } catch (e) {
                            if (status) status.textContent = String((e && e.message) || 'Could not move.');
                        }
                    };
                    actionHost.appendChild(btn);
                }
                row.appendChild(label);
                row.appendChild(actionHost);
                results.appendChild(row);
            }
        }
        prksShowInlineComboboxResults(input, results);
    }

    input.onfocus = () => void runSearch();
    input.oninput = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => void runSearch(), 280);
    };
    input.onblur = () => setTimeout(() => prksHideInlineComboboxResults(results), 200);
}

async function prksRemoveWorkTag(workId, tagId, btn) {
    const panel = document.getElementById('panel-content');
    const ownerCtx = panel && typeof prksGetTabContext === 'function' ? prksGetTabContext(panel.dataset.prksOwnerTabId) : null;
    try { await prksWorkTagEdit(ownerCtx, tagId, false); }
    finally { if (btn && typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, false); }
}

/* The synchronized bibliographic rows, rendered from whatever Work object is
 * handed in -- the cached one for the first paint, the effective one (cached +
 * durable pending edits) once `work-metadata-editor.js` has read the queue. One
 * renderer, so the two can never drift apart. */
function prksWorkBibRowsHtml(work) {
    const fields = typeof PRKS_SYNCED_WORK_FIELDS !== 'undefined'
        ? PRKS_SYNCED_WORK_FIELDS
        : ['publisher', 'location', 'edition', 'journal', 'volume', 'issue', 'pages', 'isbn', 'doi'];
    const labels = typeof PRKS_SYNCED_WORK_FIELD_LABELS !== 'undefined' ? PRKS_SYNCED_WORK_FIELD_LABELS : {};
    return fields
        .map((field) => {
            const value = work && work[field] != null ? String(work[field]) : '';
            if (!value) return '';
            const label = labels[field] || field;
            // Publisher stays a search link; the rest are plain values.
            const rendered =
                field === 'publisher'
                    ? `<a href="#/search?publisher=${encodeURIComponent(value.trim())}" class="route-sidebar__link">${escapeHtml(value)}</a>`
                    : escapeHtml(value);
            return `<p class="meta-row"><strong>${escapeHtml(label)}:</strong> ${rendered}</p>`;
        })
        .join('');
}

function renderWorkMetaEditTab(work, draft) {
    const hasDraft = !!draft;
    // Same reason as prksWorkMetaDraftFromWork(): a pending durable edit is
    // what the user last saved, so it is what the form must show.
    const effective = typeof prksEffectiveWorkSync === 'function' ? prksEffectiveWorkSync(work) : work;
    work = Object.assign({}, effective || {}, draft || {});
    const safeStr = (str) => (str || '').toString().replace(/"/g, '&quot;');
    const filePath = work && work.file_path ? String(work.file_path).trim() : '';
    const inferredKind = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
    const isVideo = inferredKind === 'video';
    const thumbPage = (() => {
        const raw = work && work.thumb_page != null ? String(work.thumb_page).trim() : '';
        if (!raw) return '';
        const n = Number(raw);
        if (!Number.isFinite(n)) return '';
        const i = Math.floor(n);
        return i >= 1 ? String(i) : '';
    })();
    
    const metaDocNorm =
        typeof prksNormalizeDocType === 'function'
            ? prksNormalizeDocType(isVideo ? 'online' : work.doc_type)
            : 'misc';
    const metaDocMenu =
        typeof prksDocTypeMenuShellHtml === 'function'
            ? prksDocTypeMenuShellHtml('meta-doc-type', metaDocNorm, isVideo,
                { workField: 'doc_type' })
            : '';
    const dateLabel = isVideo ? 'Published date' : 'Published Date';
    const publishedDateValue =
        !hasDraft && typeof prksIsoToDdMmYyyy === 'function'
            ? prksIsoToDdMmYyyy(work.published_date)
            : safeStr(work.published_date);
    /* `author_text` is synchronized, so its control lives INSIDE the durable
     * section below -- for a video as "Channel name", for everything else as
     * the textual Author. A second copy outside that section would be a second
     * mutation path for one field. */
    const channelField = '';
    const bibFields = isVideo
        ? ''
        : `
        `;
    /* Status has its own bounded Save. It is not a bibliographic detail -- it
     * decides which Progress group the Work appears in -- so it must not ride
     * along on a button labelled "Save bibliographic details", and the
     * bibliographic fields must not ride along on this one. Both are durable
     * and behave identically online and offline. */
    /* Video source is its own bounded save, deliberately NOT part of the
     * bibliographic group. It is not a scalar: one decision rewrites
     * `source_kind`, `provider`, `provider_id` and `source_url` together, so
     * it has its own operation, its own revision and its own conflict. Putting
     * it beside the scalars would say it was one of them. */
    const videoSourceSection = !isVideo
        ? ''
        : `
            <section class="work-meta-editor__section" data-prks-role="work-source-editor">
                <h4>Video source</h4>
                <label for="meta-video-url">YouTube URL</label>
                <input type="url" id="meta-video-url" placeholder="https://www.youtube.com/watch?v=…" value="${safeStr(work.source_url)}" autocomplete="off" aria-describedby="meta-video-url-error">
                <p id="meta-video-url-error" class="field-error" aria-live="polite"></p>
                <p class="meta-row meta-row--hint">Replaces which video this file is. Different links to the same video are the same source.</p>
                <div class="prks-form-actions form-actions">
                    <button type="button" id="save-work-source-btn" class="prks-btn prks-btn--secondary" onclick="void prksSaveWorkSource('${work.id}')">Save video source</button>
                </div>
                <div class="meta-row" data-prks-role="work-source-sync" aria-live="polite"></div>
            </section>
        `;
    const statusSection = `
            <section class="work-meta-editor__section" data-prks-role="work-status-editor">
                <h4>Progress</h4>
                <div class="prks-work-upload-status-field">
                    <label for="meta-status">Status</label>
                    ${prksSegmentedControlHtml('meta-status', 'Status', PRKS_WORK_STATUS_LABELS, work.status, 'status', { workField: 'status' })}
                </div>
                <div class="prks-form-actions form-actions">
                    <button type="button" id="save-work-status-btn" class="prks-btn prks-btn--secondary" onclick="void prksSaveWorkMetadataFields('${work.id}', 'status')">Save status</button>
                </div>
                <div class="meta-row" data-prks-role="work-status-sync" aria-live="polite"></div>
            </section>
        `;
    /* The synchronized bibliographic fields are their own section with their
     * own Save. One button must not quietly mean "these fields into the
     * durable local queue, the rest over HTTP, either half able to fail
     * alone" -- that is a partial-save contract nobody could explain
     * afterwards. */
    const syncedBibSection = isVideo
        ? `
            <section class="work-meta-editor__section" data-prks-role="work-bib-editor">
                <h4>Channel</h4>
                <label for="meta-author-text">Channel name</label>
                <input type="text" id="meta-author-text" data-prks-work-field="author_text" value="${safeStr(work.author_text)}" autocomplete="off">
                <div class="prks-form-actions form-actions">
                    <button type="button" id="save-work-bib-btn" class="prks-btn prks-btn--secondary" onclick="void prksSaveWorkMetadataFields('${work.id}')">Save channel name</button>
                </div>
                <div class="meta-row" data-prks-role="work-bib-sync" aria-live="polite"></div>
            </section>
        `
        : `
            <section class="work-meta-editor__section" data-prks-role="work-bib-editor">
                <h4>Bibliographic details</h4>
                <label for="meta-author-text">Author (text)</label>
                <input type="text" id="meta-author-text" data-prks-work-field="author_text" value="${safeStr(work.author_text)}" autocomplete="off">
                <p class="meta-row meta-row--hint">Used for the credit line only when no Author is linked to this file. A linked Author always takes precedence; a linked Editor stands in when this is empty.</p>

                <div class="form-grid-2 form-grid-2--compact">
                    <div><label for="meta-year">Year</label><input type="text" id="meta-year" data-prks-work-field="year" value="${safeStr(work.year)}"></div>
                    <div><label for="meta-date">${dateLabel}</label><input type="text" id="meta-date" data-prks-work-field="published_date" value="${safeStr(publishedDateValue)}" placeholder="dd/mm/yyyy" inputmode="numeric" autocomplete="off" aria-describedby="meta-date-error"></div>
                </div>
                <p id="meta-date-error" class="field-error" aria-live="polite"></p>

                <label for="meta-publisher">Publisher</label>
                <input type="text" id="meta-publisher" data-prks-work-field="publisher" value="${safeStr(work.publisher)}">

                <label for="meta-location">Location (place of publication)</label>
                <input type="text" id="meta-location" data-prks-work-field="location" value="${safeStr(work.location)}" placeholder="e.g. Cambridge, UK or Paris; Berlin" autocomplete="off">
                <p class="meta-row meta-row--hint">Separate multiple places with semicolons; BibLaTeX export joins them with &quot; and &quot;.</p>

                <label for="meta-edition">Edition</label>
                <input type="text" id="meta-edition" data-prks-work-field="edition" value="${safeStr(work.edition)}" placeholder="e.g. 2 or revised" autocomplete="off">

                <label for="meta-journal">Journal</label>
                <input type="text" id="meta-journal" data-prks-work-field="journal" value="${safeStr(work.journal)}">

                <div class="form-grid-2 form-grid-2--compact">
                    <div><label for="meta-volume">Volume</label><input type="text" id="meta-volume" data-prks-work-field="volume" value="${safeStr(work.volume)}"></div>
                    <div><label for="meta-issue">Issue</label><input type="text" id="meta-issue" data-prks-work-field="issue" value="${safeStr(work.issue)}"></div>
                </div>

                <div class="form-grid-2 form-grid-2--compact">
                    <div><label for="meta-pages">Pages</label><input type="text" id="meta-pages" data-prks-work-field="pages" value="${safeStr(work.pages)}"></div>
                    <div><label for="meta-isbn">ISBN</label><input type="text" id="meta-isbn" data-prks-work-field="isbn" value="${safeStr(work.isbn)}"></div>
                </div>

                <label for="meta-doi">DOI</label>
                <input type="text" id="meta-doi" data-prks-work-field="doi" value="${safeStr(work.doi)}">

                <label for="meta-source-url">Original URL (optional)</label>
                <input type="url" id="meta-source-url" data-prks-work-field="source_url" placeholder="https://…" value="${safeStr(work.source_url)}" autocomplete="off">
                <p class="meta-row meta-row--hint">Online location if this file was converted or downloaded from the web. This is provenance only: it does not change what kind of file PRKS treats this as.</p>

                <label for="meta-abstract">Abstract</label>
                <textarea id="meta-abstract" class="textarea-md" data-prks-work-field="abstract">${safeStr(work.abstract)}</textarea>

                <label for="meta-thumb-page">Thumbnail page</label>
                <input type="number" id="meta-thumb-page" data-prks-work-field="thumb_page" min="1" step="1" inputmode="numeric" placeholder="1" value="${safeStr(thumbPage)}" aria-describedby="meta-thumb-page-error">
                <p id="meta-thumb-page-error" class="field-error" aria-live="polite"></p>
                <p class="meta-row meta-row--hint">Which page of the PDF to use as the card image. Leave empty for page 1.</p>

                <div class="prks-form-actions form-actions">
                    <button type="button" id="save-work-bib-btn" class="prks-btn prks-btn--secondary" onclick="void prksSaveWorkMetadataFields('${work.id}')">Save bibliographic details</button>
                </div>
                <div class="meta-row" data-prks-role="work-bib-sync" aria-live="polite"></div>
            </section>
        `;
    // Rendered INSIDE the synchronized section below, never here: a second
    // control for one field would be a second mutation path for it.
    const thumbField = '';

    return `
        <div class="doc-meta-card form-pane doc-meta-card--editing work-meta-editor">
            <div class="card-heading-row">
                <h3 class="doc-meta-card__accent-title">Edit Metadata</h3>
                <button type="button" onclick="void prksCancelWorkMetaEdit()" class="prks-icon-btn prks-icon-btn--ghost inline-action-btn inline-action-btn--close">&times;</button>
            </div>
            
            <section class="work-meta-editor__section" data-prks-role="work-identity-editor">
                <h4>Identity</h4>
                <label for="meta-title">Title</label>
                <input type="text" id="meta-title" data-prks-work-field="title" value="${safeStr(work.title)}" aria-describedby="meta-title-error">
                <p id="meta-title-error" class="field-error" aria-live="polite"></p>

                <label for="meta-doc-type-trigger">Document type (BibLaTeX)</label>
                ${metaDocMenu}
                <div class="prks-form-actions form-actions">
                    <button type="button" id="save-work-identity-btn" class="prks-btn prks-btn--secondary" onclick="void prksSaveWorkMetadataFields('${work.id}', 'identity')">Save identity</button>
                </div>
                <div class="meta-row" data-prks-role="work-identity-sync" aria-live="polite"></div>
            </section>
            
            <section class="work-meta-editor__section"><h4>Publication</h4>
            ${channelField}

            ${bibFields}
            </section>
            ${statusSection}
            ${videoSourceSection}
            ${syncedBibSection}
            <div class="prks-form-actions prks-form-actions--split form-actions work-meta-editor__sticky-actions">
                <button type="button" class="prks-btn prks-btn--secondary" onclick="void prksCancelWorkMetaEdit()">Close</button>
            </div>
        </div>
    `;
}

function prksHintBtnHtml(hintType, ariaLabel, extraClass) {
    const type = escapeHtml(hintType || '');
    const aria = escapeHtml(ariaLabel || 'Help');
    const xcls = extraClass ? ` ${extraClass}` : '';
    return `<button type="button" class="prks-hint-btn${xcls}" data-prks-hint-type="${type}" aria-label="${aria}" aria-expanded="false" aria-controls="prks-hint-popover">?</button>`;
}

function prksAnnotationsTabHintButton(hintType, ariaLabel) {
    return prksHintBtnHtml(hintType, ariaLabel, 'annotations-tab__hint-btn');
}

function prksRouteSidebarTitleRow(titleInnerHtml, hintType, ariaLabel) {
    const btn = hintType ? prksHintBtnHtml(hintType, ariaLabel, 'route-sidebar__hint-btn') : '';
    return `<div class="route-sidebar__title-row"><h2 class="prks-page-title route-sidebar__title">${titleInnerHtml}</h2>${btn}</div>`;
}

function renderWorkAnnotationsTab(work) {
    const inferredKind = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';

    const title =
        inferredKind === 'pdf' ? 'PDF annotations' : 'Annotations';
    return `
        <div class="annotations-tab" role="region" aria-label="PDF annotations">
            <header class="annotations-tab__header">
                <div class="annotations-tab__header-row">
                    <h3 class="annotations-tab__title">${title}</h3>
                    ${prksAnnotationsTabHintButton('ann-pdf', 'About PDF annotations')}
                </div>
            </header>
            <div id="annotation-fallback-list" class="annotation-fallback-list" role="list" aria-live="polite"></div>
            <section id="pdf-annotation-editor" class="pdf-annotation-editor hidden" aria-live="polite">
                <div class="pdf-annotation-editor__header">
                    <h4 class="pdf-annotation-editor__title">Annotation comment</h4>
                    <div class="pdf-annotation-editor__meta" id="pdf-annotation-editor-meta"></div>
                </div>
                <div class="form-pane pdf-annotation-editor__form">
                    <input type="hidden" id="pdf-annotation-editor-ann-id" value="">
                    <input type="hidden" id="pdf-annotation-editor-page-index" value="">
                    <label for="pdf-annotation-editor-text">Comment</label>
                    <textarea id="pdf-annotation-editor-text" class="textarea-md" placeholder="Add a note/comment for this annotation…"></textarea>
                    <div class="pdf-annotation-editor__actions">
                        <button type="button" class="prks-btn prks-btn--secondary" onclick="window.closePdfAnnotationEditor && window.closePdfAnnotationEditor()">Cancel</button>
                        <button type="button" class="prks-btn prks-btn--secondary" onclick="window.deletePdfAnnotationFromEditor && window.deletePdfAnnotationFromEditor()">Delete annotation</button>
                        <button type="button" class="prks-btn prks-btn--primary" onclick="window.savePdfAnnotationComment && window.savePdfAnnotationComment()">Save comment</button>
                    </div>
                </div>
            </section>
        </div>
    `;
}

// Advanced Upload Logic
let uploadRoles = [];
/** @type {{ id: string, name: string }[]} */
let uploadTagsSelected = [];

function renderUploadTagsChips() {
    const container = document.getElementById('upload-tags-list');
    if (!container) return;
    if (!uploadTagsSelected.length) {
        container.innerHTML = '<span class="status-chip-list__empty">No tags selected</span>';
        return;
    }
    container.innerHTML = uploadTagsSelected
        .map(
            (t, idx) =>
                `<span class="tag work-tag-chip">${escapeHtml(t.name || '')} ` +
                `<button type="button" class="work-tag-remove" title="Remove" aria-label="Remove tag" ` +
                `onclick="removeUploadTagFromModal(${idx})">&times;</button></span>`
        )
        .join('');
}

window.removeUploadTagFromModal = function (idx) {
    if (idx < 0 || idx >= uploadTagsSelected.length) return;
    uploadTagsSelected.splice(idx, 1);
    renderUploadTagsChips();
};

function initUploadTagCombobox() {
    const input = document.getElementById('upload-tag-search');
    const results = document.getElementById('upload-tag-results');
    if (!input || !results || input.dataset.bound === '1') return;
    input.dataset.bound = '1';

    const attachedIds = () => new Set(uploadTagsSelected.map((t) => t.id));

    async function renderDropdown() {
        const all = await fetchTags({ used: false });

        const val = input.value.trim();
        const valLower = val.toLowerCase();
        const attached = attachedIds();
        const available = all.filter((t) => !attached.has(t.id));
        const filtered = !val
            ? available.slice(0, 40)
            : available.filter((t) => prksTagMatchesQuery(t, valLower)).slice(0, 40);
        const exactMatch = available.some((t) => prksTagExactMatch(t, valLower));

        results.innerHTML = '';
        if (val && !exactMatch) {
            const c = document.createElement('div');
            c.className = 'result-item result-item--create';
            c.textContent = 'Create tag "' + val + '"';
            c.onmousedown = (ev) => {
                ev.preventDefault();
                void (async () => {
                    try {
                        const res = await prksRequest('/api/tags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: val, color: '#6d6cf7' }),
                        });
                        const data = await res.json();
                        if (!res.ok || !data.id) throw new Error(data.error || 'no id');
                        if (typeof prksOfflineMarkTagsChanged === 'function') prksOfflineMarkTagsChanged();
                        if (!attachedIds().has(data.id)) {
                            uploadTagsSelected.push({ id: data.id, name: data.name || val });
                            renderUploadTagsChips();
                        }
                        input.value = '';
                        prksHideInlineComboboxResults(results);
                    } catch (e) {
                        console.error(e);
                        await prksAlertMessage('Could not create tag.', 'Error');
                    }
                })();
            };
            results.appendChild(c);
        }
        filtered.forEach((tag) => {
            const div = document.createElement('div');
            div.className = 'result-item';
            div.textContent = prksTagComboboxLabel(tag, valLower);
            div.onmousedown = (ev) => {
                ev.preventDefault();
                if (!attachedIds().has(tag.id)) {
                    uploadTagsSelected.push({ id: tag.id, name: tag.name });
                    renderUploadTagsChips();
                }
                input.value = '';
                prksHideInlineComboboxResults(results);
            };
            results.appendChild(div);
        });
        if (results.childElementCount === 0) {
            prksHideInlineComboboxResults(results);
        } else {
            prksShowInlineComboboxResults(input, results);
        }
    }

    input.onfocus = async () => {
        await fetchTags({ used: false });
        renderDropdown();
    };
    input.oninput = () => renderDropdown();
    input.onblur = () =>
        setTimeout(() => {
            prksHideInlineComboboxResults(results);
        }, 200);
}

function prksTeardownUploadEmbedViewer() {
    const v = window.uploadViewer;
    if (v && typeof v.destroy === 'function') {
        try {
            Promise.resolve(v.destroy()).catch(() => {});
        } catch (_e) {}
    }
    window.uploadViewer = null;
}

/** Revoke blob URL and clear PDF preview; use before choosing another file. */
function removeUploadPdfPreview() {
    const url = window.__prksUploadPdfBlobUrl;
    if (url) {
        try {
            URL.revokeObjectURL(url);
        } catch (_e) {}
        window.__prksUploadPdfBlobUrl = null;
    }
    prksTeardownUploadEmbedViewer();
    const viewer = document.getElementById('upload-viewer');
    if (viewer) {
        viewer.innerHTML = '';
        viewer.classList.add('hidden');
    }
    const actions = document.getElementById('upload-pdf-preview-actions');
    if (actions) actions.classList.add('hidden');
    const selected = document.getElementById('upload-selected-file');
    if (selected) selected.classList.add('hidden');
    const nameEl = document.getElementById('upload-selected-file-name');
    if (nameEl) nameEl.textContent = '';
    const sizeEl = document.getElementById('upload-selected-file-size');
    if (sizeEl) sizeEl.textContent = '';
    const prompt = document.getElementById('drop-zone-prompt');
    if (prompt) prompt.classList.remove('hidden');
    const zone = document.getElementById('upload-drop-zone');
    if (zone) {
        zone.classList.remove('preview-pane--compact');
        zone.removeAttribute('aria-invalid');
    }
    const f = document.getElementById('work-file');
    if (f) f.value = '';
    window.__prksPendingUploadPdfFile = null;
}

function prksFormatByteSize(n) {
    const bytes = Number(n);
    if (!Number.isFinite(bytes) || bytes < 0) return '';
    if (bytes < 1024) return `${Math.round(bytes)} B`;
    if (bytes < 1024 * 1024) {
        const kb = bytes / 1024;
        return `${kb >= 10 ? Math.round(kb) : kb.toFixed(1).replace(/\.0$/, '')} KB`;
    }
    const mb = bytes / (1024 * 1024);
    return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1).replace(/\.0$/, '')} MB`;
}

function prksShowUploadPdfSelected(file) {
    if (!file) return;
    const prompt = document.getElementById('drop-zone-prompt');
    const selected = document.getElementById('upload-selected-file');
    const zone = document.getElementById('upload-drop-zone');
    const nameEl = document.getElementById('upload-selected-file-name');
    const sizeEl = document.getElementById('upload-selected-file-size');
    const viewer = document.getElementById('upload-viewer');
    if (prompt) prompt.classList.add('hidden');
    if (viewer) {
        viewer.innerHTML = '';
        viewer.classList.add('hidden');
    }
    if (nameEl) nameEl.textContent = String(file.name || 'PDF');
    if (sizeEl) sizeEl.textContent = prksFormatByteSize(file.size);
    if (selected) selected.classList.remove('hidden');
    if (zone) {
        zone.classList.add('preview-pane--compact');
        zone.removeAttribute('aria-invalid');
    }
    if (typeof prksRefreshIcons === 'function' && selected) prksRefreshIcons(selected);
}

function prksWorkModalDisclosureIds() {
    return ['work-upload-biblio-details', 'work-upload-more-details'];
}

function prksSyncWorkModalDisclosureInert() {
    prksWorkModalDisclosureIds().forEach((id) => {
        const details = document.getElementById(id);
        if (!details) return;
        const body = details.querySelector('.work-upload-meta__details-body');
        if (!body) return;
        if (details.open) body.removeAttribute('inert');
        else body.setAttribute('inert', '');
    });
}

function prksBindWorkModalDisclosures() {
    prksWorkModalDisclosureIds().forEach((id) => {
        const details = document.getElementById(id);
        if (!details || details.dataset.prksInertBound === '1') return;
        details.dataset.prksInertBound = '1';
        details.addEventListener('toggle', () => prksSyncWorkModalDisclosureInert());
    });
}

function prksClearWorkModalErrors() {
    const modal = document.getElementById('work-modal');
    if (!modal) return;
    modal.querySelectorAll('[data-prks-field-error]').forEach((el) => {
        el.textContent = '';
        el.classList.add('hidden');
    });
    modal.querySelectorAll('[aria-invalid="true"]').forEach((el) => {
        el.removeAttribute('aria-invalid');
    });
    const status = document.getElementById('upload-status-msg');
    if (status) {
        status.textContent = '';
        status.classList.add('hidden');
    }
}

function prksSetWorkModalFieldError(control, message, errorId) {
    const msg = String(message || '').trim();
    const err = errorId ? document.getElementById(errorId) : null;
    if (control && control.setAttribute) {
        control.setAttribute('aria-invalid', 'true');
        // `<p>` has no `for` semantics; connect via aria-describedby instead.
        if (errorId) {
            const existing = String(control.getAttribute('aria-describedby') || '');
            if (existing.split(/\s+/).indexOf(errorId) === -1) {
                control.setAttribute('aria-describedby', (existing + ' ' + errorId).trim());
            }
        }
    }
    if (err) {
        err.textContent = msg;
        err.classList.remove('hidden');
    }
    return control;
}

function prksFocusWorkModalControl(el) {
    if (!el || typeof el.focus !== 'function') return;
    try {
        el.focus({ preventScroll: false });
        if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    } catch (_e) {
        try {
            el.focus();
        } catch (_e2) {}
    }
}

function prksFocusWorkModalInitial() {
    const kindEl = document.getElementById('work-source-kind');
    const kind = kindEl ? String(kindEl.value || 'pdf') : 'pdf';
    if (kind === 'video') {
        const url = document.getElementById('work-video-url');
        prksFocusWorkModalControl(url);
        return;
    }
    if (window.__prksPendingUploadPdfFile instanceof File) {
        prksFocusWorkModalControl(document.getElementById('work-title'));
        return;
    }
    const zone = document.getElementById('upload-drop-zone');
    prksFocusWorkModalControl(zone || document.getElementById('work-title'));
}

function prksSetWorkModalCreateBusy(busy) {
    const btn = document.getElementById('save-work-btn');
    if (!btn) return;
    const on = !!busy;
    btn.disabled = on;
    btn.setAttribute('aria-busy', on ? 'true' : 'false');
    if (!btn.dataset.prksIdleLabel) btn.dataset.prksIdleLabel = 'Create File';
    btn.textContent = on ? 'Creating…' : btn.dataset.prksIdleLabel;
}

function prksFolderIdFromLocation() {
    if (typeof prksParseRoute === 'function') {
        const r = prksParseRoute(window.location.hash);
        if (r && r.name === 'folder-detail' && r.params && r.params.folderId) {
            return String(r.params.folderId);
        }
    }
    const hash = String(window.location.hash || '');
    if (hash.startsWith('#/folders/')) {
        const part = hash.split('/')[2] || '';
        return decodeURIComponent(part.split('?')[0] || '');
    }
    return '';
}

// Focused-pane context wins over Main's URL. A valid focused TabContext that
// is not a Folder means "default to Uncategorized", not "inherit Main".
// window.location.hash is only a compatibility fallback when there is no
// usable focused TabContext at all.
function prksFolderIdFromFocusedContext() {
    if (typeof window.prksFocusedRouteRecord === 'function') {
        const route = window.prksFocusedRouteRecord();
        if (route) {
            if (route.name === 'folder-detail' && route.params && route.params.folderId) {
                return String(route.params.folderId);
            }
            return '';
        }
    }
    return prksFolderIdFromLocation();
}

// The real top-level folder titled "Uncategorized", if it has already been
// materialized. Used so the combobox never shows a synthetic default row
// duplicating a real Uncategorized folder already present in loaded data.
function prksCanonicalUncategorizedFolder(folders) {
    const list = Array.isArray(folders) ? folders : [];
    return (
        list.find(
            (f) =>
                !f.parent_id &&
                String(f.title || '')
                    .trim()
                    .toLowerCase() === 'uncategorized'
        ) || null
    );
}

function prksSetWorkModalFolderFromId(folderId) {
    const hidden = document.getElementById('work-folder-id');
    const search = document.getElementById('work-folder-search');
    if (!hidden || !search) return;
    const id = String(folderId || '').trim();
    let folders = [];
    try {
        folders = Array.isArray(allFolders) ? allFolders : [];
    } catch (_e) {
        folders = [];
    }
    if (!id) {
        // Default destination. If the real Uncategorized folder is already
        // known, select it explicitly (transport ID) so display and stored
        // destination always agree. Otherwise use the committed empty-default
        // state, which the server materializes as Uncategorized on create.
        const canonical = prksCanonicalUncategorizedFolder(folders);
        delete search.dataset.prksFolderDefault;
        if (canonical) {
            hidden.value = canonical.id;
            search.value =
                typeof window.prksFolderRowLabel === 'function'
                    ? window.prksFolderRowLabel(canonical, folders)
                    : 'Uncategorized';
        } else {
            hidden.value = '';
            search.value = 'Uncategorized';
            search.dataset.prksFolderDefault = '1';
        }
        return;
    }
    hidden.value = id;
    delete search.dataset.prksFolderDefault;
    const row = folders.find((f) => String(f.id) === id);
    const label =
        row && typeof window.prksFolderRowLabel === 'function'
            ? window.prksFolderRowLabel(row, folders)
            : row && row.title
              ? String(row.title)
              : id;
    search.value = label;
}

// A folder selection is committed (never a bare, unselected search query)
// when either a real folder ID was explicitly chosen, or the explicit
// default-destination state is set. Free text the user typed but never
// selected/committed is invalid.
function prksIsWorkModalFolderCommitted() {
    const hidden = document.getElementById('work-folder-id');
    const search = document.getElementById('work-folder-search');
    if (hidden && String(hidden.value || '').trim()) return true;
    return !!(search && search.dataset.prksFolderDefault === '1');
}

// Shared YouTube URL/host contract used by creation validation. Kept in sync
// with backend _is_youtube_host()/_youtube_video_id() and
// components/works-video.js's embed-URL parsing.
const PRKS_YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be']);

function prksIsRecognizedYoutubeHost(hostname) {
    return PRKS_YOUTUBE_HOSTS.has(String(hostname || '').trim().toLowerCase());
}

function prksExtractYoutubeVideoId(rawUrl) {
    let u;
    try {
        u = new URL(String(rawUrl || '').trim());
    } catch (_e) {
        return '';
    }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    const host = (u.hostname || '').toLowerCase();
    if (!prksIsRecognizedYoutubeHost(host)) return '';
    if (host === 'youtu.be') {
        const id = u.pathname.replace(/^\//, '').split('/')[0];
        return id || '';
    }
    const v = u.searchParams.get('v') || '';
    if (v) return v;
    const parts = u.pathname.replace(/^\//, '').split('/');
    if (parts[0] === 'embed' && parts[1]) return parts[1];
    return '';
}

function prksIsValidYoutubeUrl(rawUrl) {
    return !!prksExtractYoutubeVideoId(rawUrl);
}

window.prksFormatByteSize = prksFormatByteSize;
window.prksShowUploadPdfSelected = prksShowUploadPdfSelected;
window.prksSyncWorkModalDisclosureInert = prksSyncWorkModalDisclosureInert;
window.prksClearWorkModalErrors = prksClearWorkModalErrors;
window.prksSetWorkModalFieldError = prksSetWorkModalFieldError;
window.prksFocusWorkModalControl = prksFocusWorkModalControl;
window.prksFocusWorkModalInitial = prksFocusWorkModalInitial;
window.prksSetWorkModalCreateBusy = prksSetWorkModalCreateBusy;
window.prksFolderIdFromLocation = prksFolderIdFromLocation;
window.prksFolderIdFromFocusedContext = prksFolderIdFromFocusedContext;
window.prksCanonicalUncategorizedFolder = prksCanonicalUncategorizedFolder;
window.prksSetWorkModalFolderFromId = prksSetWorkModalFolderFromId;
window.prksIsWorkModalFolderCommitted = prksIsWorkModalFolderCommitted;
window.prksIsRecognizedYoutubeHost = prksIsRecognizedYoutubeHost;
window.prksExtractYoutubeVideoId = prksExtractYoutubeVideoId;
window.prksIsValidYoutubeUrl = prksIsValidYoutubeUrl;

function resetUploadModal() {
    uploadRoles = [];
    uploadTagsSelected = [];
    if (typeof prksMountUploadRoleSegmented === 'function') {
        prksMountUploadRoleSegmented('Author');
    }
    renderUploadTagsChips();
    const tagSearch = document.getElementById('upload-tag-search');
    if (tagSearch) tagSearch.value = '';
    document.getElementById('work-title').value = '';
    document.getElementById('work-year').value = '';
    const wDate = document.getElementById('work-date');
    if (wDate) wDate.value = '';
    const wPub = document.getElementById('work-publisher');
    if (wPub) wPub.value = '';
    const wLoc = document.getElementById('work-location');
    if (wLoc) wLoc.value = '';
    const wEd = document.getElementById('work-edition');
    if (wEd) wEd.value = '';
    const wJour = document.getElementById('work-journal');
    if (wJour) wJour.value = '';
    const wVol = document.getElementById('work-volume');
    if (wVol) wVol.value = '';
    const wIss = document.getElementById('work-issue');
    if (wIss) wIss.value = '';
    const wPag = document.getElementById('work-pages');
    if (wPag) wPag.value = '';
    const wIsbn = document.getElementById('work-isbn');
    if (wIsbn) wIsbn.value = '';
    const wDoi = document.getElementById('work-doi');
    if (wDoi) wDoi.value = '';
    const wThumb = document.getElementById('work-thumb-page');
    if (wThumb) wThumb.value = '';
    const wPriv = document.getElementById('work-private-notes');
    if (wPriv) wPriv.value = '';
    const bibDetails = document.getElementById('work-upload-biblio-details');
    if (bibDetails) bibDetails.open = false;
    const moreDetails = document.getElementById('work-upload-more-details');
    if (moreDetails) moreDetails.open = false;
    if (typeof prksSyncWorkModalDisclosureInert === 'function') {
        prksSyncWorkModalDisclosureInert();
    }
    document.getElementById('work-abstract').value = '';
    const f = document.getElementById('work-file');
    if (f) f.value = '';
    const vid = document.getElementById('work-video-url');
    if (vid) vid.value = '';
    if (typeof prksSetWorkModalFolderFromId === 'function') {
        prksSetWorkModalFolderFromId('');
    } else {
        document.getElementById('work-folder-id').value = '';
        document.getElementById('work-folder-search').value = 'Uncategorized';
        document.getElementById('work-folder-search').dataset.prksFolderDefault = '1';
    }
    document.getElementById('upload-person-id').value = '';
    document.getElementById('upload-person-search').value = '';
    prksRefreshRoleCreditPicker('upload-role', null);
    removeUploadPdfPreview();
    document.getElementById('upload-roles-list').innerHTML = '<span class="status-chip-list__empty">No persons linked yet</span>';
    window.__prksUploadVideoMeta = null;
    const vPlSearch = document.getElementById('work-video-playlist-search');
    const vPlId = document.getElementById('work-video-playlist-id');
    if (vPlSearch) vPlSearch.value = '';
    if (vPlId) vPlId.value = '';
    const pdfSrc = document.getElementById('work-pdf-source-url');
    if (pdfSrc) pdfSrc.value = '';
    const kind = document.getElementById('work-source-kind');
    if (kind) kind.value = 'pdf';
    window.__prksUploadUiKind = 'pdf';
    if (typeof window.prksSyncUploadModalKindUi === 'function') {
        window.prksSyncUploadModalKindUi();
    }
}

let allPersons = [];
let allFolders = [];
let allWorks = [];

async function populateUploadComboboxes() {
    try {
        [allPersons, allFolders] = await Promise.all([fetchPersons(), fetchFolders()]);
    } catch (err) {
        console.error('populateUploadComboboxes: could not load persons/folders', err);
    }

    initSearchableCombobox('work-folder-search', 'folder-results', 'work-folder-id', 'folder');
    initSearchableCombobox('upload-person-search', 'person-results', 'upload-person-id', 'person', {
        onQuickCreate: (typedName) => {
            void prksQuickCreatePersonForSearchField(
                typedName,
                'upload-person-search',
                'upload-person-id',
                'Quick-created from upload'
            );
        },
        onPersonPick: (person) => prksRefreshRoleCreditPicker('upload-role', person),
    });
    prksBindRoleCreditPicker('upload-role');
    initUploadTagCombobox();
}

/** Extra line under the name in person comboboxes (disambiguate same names). Uses helpers from people.js when loaded. */
function formatPersonComboboxSubtitle(p) {
    const bits = [];
    if (typeof personLifespanDisplay === 'function') {
        const life = personLifespanDisplay(p);
        if (life) bits.push(life);
    }
    const aliases = (p.aliases || '').replace(/\s+/g, ' ').trim();
    if (aliases) {
        const aka = aliases.length > 52 ? `${aliases.slice(0, 49)}…` : aliases;
        bits.push(`Also known as: ${aka}`);
    }
    if (Array.isArray(p.assigned_roles) && p.assigned_roles.length) {
        bits.push(`Roles: ${p.assigned_roles.join(', ')}`);
    }
    if (typeof personExternalRefsSummary === 'function') {
        const refs = personExternalRefsSummary(p);
        if (refs) bits.push(refs);
    }
    if (Array.isArray(p.groups) && p.groups.length) {
        const names = p.groups.map((g) => g.name).filter(Boolean);
        const head = names.slice(0, 2).join(', ');
        const more = names.length > 2 ? ` +${names.length - 2}` : '';
        bits.push(`Groups: ${head}${more}`);
    }
    if (bits.length === 0 && typeof truncatePersonPreviewText === 'function') {
        const about = truncatePersonPreviewText(p.about || '', 90);
        if (about) bits.push(about);
    }
    let s = bits.join(' · ');
    if (s.length > 160) s = `${s.slice(0, 157)}…`;
    return s;
}

function personMatchesComboboxQuery(p, q) {
    const val = (q || '').toLowerCase().trim();
    if (!val) return true;
    const label = `${p.first_name || ''} ${p.last_name || ''}`.trim().toLowerCase();
    if (label.includes(val)) return true;
    if ((p.aliases || '').toLowerCase().includes(val)) return true;
    if ((p.about || '').toLowerCase().includes(val)) return true;
    if (Array.isArray(p.assigned_roles) && p.assigned_roles.some((r) => String(r).toLowerCase().includes(val))) {
        return true;
    }
    if (Array.isArray(p.groups) && p.groups.some((g) => (g.name || '').toLowerCase().includes(val))) {
        return true;
    }
    return false;
}

const PRKS_INLINE_COMBOBOX_COLLAPSE_MS = 220;

function prksIsInlineComboboxPanel(results) {
    return results && results.classList.contains('combobox-results--tag-panel');
}

function prksHideInlineComboboxResults(results) {
    if (!results || results.classList.contains('hidden')) return;
    if (!prksIsInlineComboboxPanel(results)) {
        results.classList.add('hidden');
        return;
    }
    if (results.__prksCollapseTimer) {
        clearTimeout(results.__prksCollapseTimer);
    }
    results.classList.remove('is-open');
    results.__prksCollapseTimer = window.setTimeout(() => {
        results.classList.add('hidden');
        results.__prksCollapseTimer = null;
    }, PRKS_INLINE_COMBOBOX_COLLAPSE_MS);
}

function prksShowInlineComboboxResults(input, results) {
    if (!results) return;
    if (results.__prksCollapseTimer) {
        clearTimeout(results.__prksCollapseTimer);
        results.__prksCollapseTimer = null;
    }
    if (!prksIsInlineComboboxPanel(results)) {
        results.classList.remove('hidden');
    } else {
        const needsOpenAnim =
            results.classList.contains('hidden') || !results.classList.contains('is-open');
        results.classList.remove('hidden');
        if (needsOpenAnim) {
            results.classList.remove('is-open');
            void results.offsetHeight;
            requestAnimationFrame(() => {
                results.classList.add('is-open');
            });
        }
    }
    if (input && typeof input.scrollIntoView === 'function') {
        window.setTimeout(() => {
            try {
                input.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            } catch (_e) {
                input.scrollIntoView({ block: 'nearest' });
            }
        }, 40);
    }
}

function initSearchableCombobox(inputId, resultsId, hiddenId, type, comboboxOptions = {}) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);
    const hidden = document.getElementById(hiddenId);
    if (!input || !results) return;

    const excludePersonIds =
        comboboxOptions.excludePersonIds instanceof Set ? comboboxOptions.excludePersonIds : null;

    input.onfocus = () => {
        if (type === 'folder' && input.dataset.prksFolderDefault === '1') {
            try {
                input.select();
            } catch (_e) {}
        }
        renderResults();
    };
    input.oninput = () => {
        hidden.value = '';
        if (type === 'folder') delete input.dataset.prksFolderDefault;
        renderResults();
    };
    
    // Hide results when focus moves away
    input.onblur = () => {
        // Delay hide to allow clicks on result items to fire first
        setTimeout(() => {
            prksHideInlineComboboxResults(results);
        }, 200);
    };

    function renderResults() {

        if (!input.value && type !== 'person' && type !== 'folder') { 
            // Optional: don't show all if empty? 
            // For now let's show all if focused
        }
        const valRaw = input.value || '';
        const val = valRaw.toLowerCase();
        const data =
            type === 'person'
                ? (Array.isArray(window.allPersons) ? window.allPersons : allPersons)
                : type === 'work'
                  ? Array.isArray(window.allWorks)
                      ? window.allWorks
                      : allWorks
                  : allFolders;
        const hostWorkInputId = comboboxOptions.hostWorkInputId;
        const hostWorkEl = hostWorkInputId ? document.getElementById(hostWorkInputId) : null;
        const hostWorkId = hostWorkEl && hostWorkEl.value ? String(hostWorkEl.value).trim() : '';
        const filtered = data.filter(item => {
            if (type === 'person' && excludePersonIds && excludePersonIds.has(String(item.id))) {
                return false;
            }
            if (type === 'work' && hostWorkId && String(item.id) === hostWorkId) {
                return false;
            }
            if (type === 'person') {
                return personMatchesComboboxQuery(item, val);
            }
            if (type === 'folder') {
                const hier =
                    typeof window.prksFolderRowLabel === 'function'
                        ? window.prksFolderRowLabel(item, data)
                        : String(item.title || '');
                const title = String(item.title || '');
                return (
                    !val ||
                    hier.toLowerCase().includes(val) ||
                    title.toLowerCase().includes(val)
                );
            }
            const label = item.title || '';
            return label.toLowerCase().includes(val);
        });

        results.innerHTML = '';
        if (type === 'folder') {
            // Only offer the synthetic default row when the real Uncategorized
            // folder doesn't exist yet in loaded data, to avoid two
            // duplicate-looking "Uncategorized" entries.
            const canonical =
                typeof prksCanonicalUncategorizedFolder === 'function'
                    ? prksCanonicalUncategorizedFolder(data)
                    : null;
            if (!canonical) {
                const rootItem = document.createElement('div');
                rootItem.className = 'result-item';
                rootItem.textContent = 'Uncategorized';
                rootItem.onmousedown = (e) => {
                    e.preventDefault();
                    input.value = 'Uncategorized';
                    hidden.value = '';
                    input.dataset.prksFolderDefault = '1';
                    prksHideInlineComboboxResults(results);
                };
                const q = valRaw.trim().toLowerCase();
                if (!q || 'uncategorized'.includes(q)) {
                    results.appendChild(rootItem);
                }
            }
        }
        if (type === 'person' && valRaw.trim() && typeof comboboxOptions.onQuickCreate === 'function') {
            const create = document.createElement('div');
            create.className = 'result-item result-item--create';
            create.textContent = `Quick-create person \"${valRaw.trim()}\"`;
            create.onmousedown = (ev) => {
                ev.preventDefault();
                prksHideInlineComboboxResults(results);
                comboboxOptions.onQuickCreate(valRaw.trim());
            };
            results.appendChild(create);
        }
        if (filtered.length === 0) {
            const allExcluded =
                type === 'person' &&
                excludePersonIds &&
                data.length > 0 &&
                data.every((item) => excludePersonIds.has(String(item.id)));
            if (results.childElementCount === 0) {
                results.innerHTML = `<div class="result-item no-results">${
                    allExcluded ? 'Everyone is already in this group.' : 'No results found'
                }</div>`;
            }
        } else {
            filtered.forEach(item => {
                const label =
                    type === 'person'
                        ? `${item.first_name || ''} ${item.last_name || ''}`.trim()
                        : type === 'folder' && typeof window.prksFolderRowLabel === 'function'
                          ? window.prksFolderRowLabel(item, data)
                          : item.title || '';
                const div = document.createElement('div');
                div.className =
                    type === 'person' ? 'result-item result-item--person-pick' : 'result-item';
                if (type === 'person') {
                    const primary = document.createElement('div');
                    primary.className = 'result-item__primary';
                    primary.textContent = label || '(Unnamed)';
                    div.appendChild(primary);
                    const sub = formatPersonComboboxSubtitle(item);
                    if (sub) {
                        const secondary = document.createElement('div');
                        secondary.className = 'result-item__secondary';
                        secondary.textContent = sub;
                        div.appendChild(secondary);
                    }
                } else {
                    div.textContent = label;
                }
                div.onmousedown = (e) => {
                    e.preventDefault(); // Prevent input blur before click
                    input.value = label;
                    hidden.value = item.id;
                    if (type === 'folder') delete input.dataset.prksFolderDefault;
                    if (type === 'person' && typeof comboboxOptions.onPersonPick === 'function') {
                        comboboxOptions.onPersonPick(item);
                    }
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            });
        }
        prksShowInlineComboboxResults(input, results);
    }
}

async function quickCreateFolder() {
    const title = document.getElementById('work-folder-search').value;
    if (!title) {
        await prksAlertMessage('Please enter a folder title first', 'Validation');
        return;
    }
    
    // Canonical create boundary: it owns the offline guard and the Folder
    // coherence hook, so this surface cannot silently reopen either gap.
    let newFolderId;
    try {
        newFolderId = await createFolder(title, 'Quick created via upload');
    } catch (e) {
        if (!prksOfflineWasGuardRefusal(e)) {
            await prksAlertMessage((e && e.message) || 'Could not create folder', 'Could not save');
        }
        return;
    }

    allFolders = await fetchFolders(); // Refresh cache
    document.getElementById('work-folder-id').value = newFolderId;
    document.getElementById('work-folder-search').value = title;
    delete document.getElementById('work-folder-search').dataset.prksFolderDefault;
    prksHideInlineComboboxResults(document.getElementById('folder-results'));
}

function addRoleToUploadList() {
    const hidden = document.getElementById('upload-person-id');
    const input = document.getElementById('upload-person-search');
    const rSelect = document.getElementById('upload-role-type');
    
    if (!hidden.value) {
        void prksAlertMessage(
            'Please select a person from the search results or create a new one first.',
            'Validation'
        );
        return;
    }
    
    const pName = input.value;
    const rType = rSelect.value;
    if (prksWorkHasRoleLink(uploadRoles, hidden.value, rType)) {
        void prksShowDuplicateRoleLinkAlert(rType);
        return;
    }

    const creditName = prksResolveRoleCreditNameForLink('upload-role', hidden.value, 'upload-person-search');
    const displayName = creditName || pName;
    uploadRoles.push({
        person_id: hidden.value,
        person_name: displayName,
        role_type: rType,
        credit_name: creditName,
    });
    renderUploadRoles();

    hidden.value = '';
    input.value = '';
    prksRefreshRoleCreditPicker('upload-role', null);
}


function renderUploadRoles() {
    const container = document.getElementById('upload-roles-list');
    if (uploadRoles.length === 0) {
        container.innerHTML = '<span class="status-chip-list__empty">No persons linked yet</span>';
        return;
    }
    container.innerHTML = uploadRoles.map((r, idx) => `
        <span class="tag author-tag">${typeof prksIcon === 'function' ? prksIcon('user', { size: 'sm' }) : ''} <span class="author-tag__name">${escapeHtml(r.person_name)}</span> <span class="author-tag__role">${escapeHtml(r.role_type)}</span> <button type="button" class="status-chip-remove" onclick="removeUploadRole(${idx})" aria-label="Remove ${escapeHtml(r.person_name)}">&times;</button></span>
    `).join(' ');
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function removeUploadRole(idx) {
    uploadRoles.splice(idx, 1);
    renderUploadRoles();
}

function initUploadDragAndDrop() {
    const zone = document.getElementById('upload-drop-zone');
    const input = document.getElementById('work-file');
    if (!zone) return;
    prksBindWorkModalDisclosures();

    const cancelBtn = document.getElementById('work-modal-cancel');
    if (cancelBtn && cancelBtn.dataset.bound !== '1') {
        cancelBtn.dataset.bound = '1';
        cancelBtn.addEventListener('click', () => requestModalClose('button'));
    }

    const personSearch = document.getElementById('upload-person-search');
    if (personSearch && personSearch.dataset.prksEnterLinkBound !== '1') {
        personSearch.dataset.prksEnterLinkBound = '1';
        personSearch.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            const hidden = document.getElementById('upload-person-id');
            if (hidden && hidden.value) {
                e.preventDefault();
                addRoleToUploadList();
            }
        });
    }

    if (zone.dataset.prksClickBound !== '1') {
        zone.dataset.prksClickBound = '1';
        zone.addEventListener('click', (e) => {
            if (e.target.closest('#upload-pdf-change-btn') || e.target.closest('#upload-selected-file')) {
                if (e.target.closest('#upload-pdf-change-btn')) return;
                return;
            }
            const prompt = document.getElementById('drop-zone-prompt');
            if (prompt && !prompt.classList.contains('hidden')) {
                input.click();
                return;
            }
        });
        zone.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            const prompt = document.getElementById('drop-zone-prompt');
            if (prompt && !prompt.classList.contains('hidden')) {
                e.preventDefault();
                input.click();
            }
        });
    }

    const changePdfBtn = document.getElementById('upload-pdf-change-btn');
    if (changePdfBtn && changePdfBtn.dataset.bound !== '1') {
        changePdfBtn.dataset.bound = '1';
        changePdfBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeUploadPdfPreview();
        });
    }

    zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('active'); };
    zone.ondragleave = () => zone.classList.remove('active');
    zone.ondrop = (e) => {
        e.preventDefault();
        zone.classList.remove('active');
        if (e.dataTransfer.files.length) {
            handleUploadFile(e.dataTransfer.files[0]);
        }
    };
    input.onchange = (e) => {
        if (e.target.files.length) handleUploadFile(e.target.files[0]);
    };

    const kind = document.getElementById('work-source-kind');
    // File kind toggle buttons (PDF / Video) drive the hidden #work-source-kind value.
    const toggleBtns = Array.from(document.querySelectorAll('.prks-kind-toggle__btn[data-kind]'));
    if (kind && kind.dataset.bound !== '1') {
        kind.dataset.bound = '1';
        // If some other code updates kind.value, keep UI in sync.
        kind.addEventListener('change', () => {
            if (typeof window.prksSyncUploadModalKindUi === 'function') {
                window.prksSyncUploadModalKindUi({ switched: true });
            }
        });
    }
    if (toggleBtns.length) {
        toggleBtns.forEach((btn) => {
            if (btn.dataset.bound === '1') return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', () => {
                const next = String(btn.getAttribute('data-kind') || '').trim();
                if (!next || !kind) return;
                const prev = String(kind.value || 'pdf');
                if (next === prev) return;
                kind.value = next;
                try {
                    kind.dispatchEvent(new Event('change', { bubbles: true }));
                } catch (_e) {
                    if (typeof window.prksSyncUploadModalKindUi === 'function') {
                        window.prksSyncUploadModalKindUi({ switched: true });
                    }
                }
            });
        });
    }

    const vurl = document.getElementById('work-video-url');
    if (vurl && vurl.dataset.bound !== '1') {
        vurl.dataset.bound = '1';
        // Do not load the iframe / oembed on every keystroke (input + change both fired → double load).
        // Commit preview when the field loses focus; clear stale preview if the URL is edited again.
        vurl.addEventListener('input', () => {
            const cur = String(vurl.value || '').trim();
            const last = String(window.__prksLastVideoPreviewUrl || '').trim();
            if (cur === last) return;
            window.__prksUploadVideoMeta = null;
            const viewer = document.getElementById('upload-viewer');
            const prompt = document.getElementById('drop-zone-prompt');
            const pdfActions = document.getElementById('upload-pdf-preview-actions');
            if (pdfActions) pdfActions.classList.add('hidden');
            if (viewer) {
                viewer.innerHTML = '';
                viewer.classList.add('hidden');
            }
            if (prompt) prompt.classList.remove('hidden');
        });
        vurl.addEventListener('blur', () => {
            if (typeof window.prksHandleVideoUrlInput === 'function') {
                void window.prksHandleVideoUrlInput(String(vurl.value || '').trim());
            }
        });
    }

    if (!window.prksSyncUploadModalKindUi) {
        window.prksSyncUploadModalKindUi = function (opts) {
            const switched = !!(opts && opts.switched);
            const kindEl = document.getElementById('work-source-kind');
            const toggleBtns = Array.from(document.querySelectorAll('.prks-kind-toggle__btn[data-kind]'));
            const vrow = document.getElementById('work-video-url-row');
            const pdfSource = document.getElementById('upload-source-pdf');
            const pdfUrlRow = document.getElementById('work-pdf-source-url-row');
            const dropLabel = document.getElementById('drop-zone-label');
            const prompt = document.getElementById('drop-zone-prompt');
            const viewer = document.getElementById('upload-viewer');
            const fileInput = document.getElementById('work-file');
            const docType = document.getElementById('work-doc-type');
            const urlDate = document.getElementById('work-video-urldate');
            const kindVal = kindEl ? String(kindEl.value || 'pdf') : 'pdf';
            const prevKind = window.__prksUploadUiKind != null ? String(window.__prksUploadUiKind) : 'pdf';
            window.__prksUploadUiKind = kindVal;

            if (toggleBtns.length) {
                toggleBtns.forEach((b) => {
                    const k = String(b.getAttribute('data-kind') || '').trim();
                    const active = k === kindVal;
                    b.classList.toggle('is-active', active);
                    b.setAttribute('aria-selected', active ? 'true' : 'false');
                });
            }

            if (vrow) vrow.classList.toggle('hidden', kindVal !== 'video');
            if (pdfSource) pdfSource.classList.toggle('hidden', kindVal !== 'pdf');
            if (pdfUrlRow) pdfUrlRow.classList.toggle('hidden', kindVal !== 'pdf');

            const pdfMeta = document.getElementById('work-upload-pdf-only-meta');
            if (pdfMeta) pdfMeta.classList.toggle('hidden', kindVal !== 'pdf');
            const pubCol = document.getElementById('work-upload-published-date-col');
            if (pubCol) pubCol.classList.toggle('hidden', kindVal !== 'pdf');

            if (switched && kindVal !== prevKind) {
                if (kindVal === 'video') {
                    removeUploadPdfPreview();
                } else {
                    const vid = document.getElementById('work-video-url');
                    if (vid) vid.value = '';
                    window.__prksLastVideoPreviewUrl = '';
                    window.__prksUploadVideoMeta = null;
                    if (viewer) {
                        viewer.innerHTML = '';
                        viewer.classList.add('hidden');
                    }
                    const chan = document.getElementById('work-video-channel');
                    if (chan) chan.value = '';
                }
                if (typeof prksClearWorkModalErrors === 'function') prksClearWorkModalErrors();
            }

            if (dropLabel) {
                dropLabel.innerHTML = 'Drop a PDF here<br><span class="drop-zone__label-sub">or click to browse · PDF files only</span>';
            }
            if (fileInput) fileInput.disabled = kindVal === 'video';

            if (kindVal === 'pdf' && window.__prksPendingUploadPdfFile instanceof File) {
                if (typeof prksShowUploadPdfSelected === 'function') {
                    prksShowUploadPdfSelected(window.__prksPendingUploadPdfFile);
                }
            } else if (kindVal === 'pdf' && prompt && !window.__prksPendingUploadPdfFile) {
                prompt.classList.remove('hidden');
            }

            if (kindVal === 'video') {
                if (typeof initPrksDocTypeMenu === 'function') {
                    initPrksDocTypeMenu('work-doc-type', { selectedValue: 'online', disabled: true });
                } else if (docType) {
                    docType.value = 'online';
                }
                if (urlDate && !String(urlDate.value || '').trim()) {
                    urlDate.value = 'Auto (last edit)';
                }
                if (typeof window.__prksInitNewFilePlaylistSearch === 'function') {
                    void window.__prksInitNewFilePlaylistSearch();
                }
            } else {
                const next =
                    docType && String(docType.value || '').trim().toLowerCase() === 'online'
                        ? 'article'
                        : docType
                          ? docType.value
                          : 'article';
                if (typeof initPrksDocTypeMenu === 'function') {
                    initPrksDocTypeMenu('work-doc-type', { selectedValue: next, disabled: false });
                } else if (docType) {
                    if (String(docType.value || '').trim().toLowerCase() === 'online') {
                        docType.value = 'article';
                    }
                }
            }
        };
    }

    if (!window.prksHandleVideoUrlInput) {
        window.prksHandleVideoUrlInput = async function (rawUrl) {
            const url = String(rawUrl || '').trim();
            const viewer = document.getElementById('upload-viewer');
            const prompt = document.getElementById('drop-zone-prompt');
            const pdfActions = document.getElementById('upload-pdf-preview-actions');
            const channelInput = document.getElementById('work-video-channel');
            if (!viewer) return;

            if (
                url &&
                url === String(window.__prksLastVideoPreviewUrl || '').trim() &&
                window.__prksUploadVideoMeta &&
                typeof window.__prksUploadVideoMeta === 'object'
            ) {
                return;
            }

            window.__prksUploadVideoMeta = null;
            if (pdfActions) pdfActions.classList.add('hidden');
            viewer.innerHTML = '';
            viewer.classList.add('hidden');
            if (prompt) prompt.classList.remove('hidden');

            if (!url) {
                window.__prksLastVideoPreviewUrl = '';
                return;
            }

            let embedUrl = '';
            const previewVideoId =
                typeof prksExtractYoutubeVideoId === 'function' ? prksExtractYoutubeVideoId(url) : '';
            if (previewVideoId) {
                embedUrl = `https://www.youtube.com/embed/${encodeURIComponent(previewVideoId)}`;
            }

            if (embedUrl) {
                if (pdfActions) pdfActions.classList.add('hidden');
                if (prompt) prompt.classList.add('hidden');
                viewer.classList.remove('hidden');
                viewer.innerHTML =
                    `<div class="prks-video-preview">` +
                    `<div class="prks-video-preview__frame">` +
                    `<iframe src="${embedUrl}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>` +
                    `</div>` +
                    `</div>`;
            }

            try {
                if (!previewVideoId) throw new Error('not a recognized YouTube URL');
                const oembed = `https://www.youtube.com/oembed?format=json&url=${encodeURIComponent(url)}`;
                const res = await fetch(oembed, { method: 'GET' });
                if (res.ok) {
                    const meta = await res.json().catch(() => null);
                    if (meta && typeof meta === 'object') {
                        window.__prksUploadVideoMeta = meta;
                        const titleInput = document.getElementById('work-title');
                        if (titleInput && !String(titleInput.value || '').trim() && meta.title) {
                            titleInput.value = String(meta.title).trim();
                        }
                        if (channelInput && !String(channelInput.value || '').trim() && meta.author_name) {
                            channelInput.value = String(meta.author_name).trim();
                        }
                    }
                }
            } catch (_e) {}

            window.__prksLastVideoPreviewUrl = url;
        };
    }

    if (!window.__prksRefreshAllPlaylistSelects) {
        window.__prksRefreshAllPlaylistSelects = async function (selectPlaylistId) {
            if (typeof fetchPlaylists !== 'function') return;
            const pls = await fetchPlaylists();
            const html =
                `<option value="">(No playlist)</option>` +
                pls
                    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.title || 'Playlist')}</option>`)
                    .join('');
            document.querySelectorAll('select[data-prks-playlist-select="1"]').forEach((sel) => {
                const prev = sel.value;
                sel.innerHTML = html;
                if (selectPlaylistId) sel.value = String(selectPlaylistId);
                else if (prev) sel.value = prev;
            });
        };
    }

    // New File (video): searchable playlist picker with inline quick-create.
    if (!window.__prksInitNewFilePlaylistSearch) {
        window.__prksInitNewFilePlaylistSearch = async function () {
            const input = document.getElementById('work-video-playlist-search');
            const hidden = document.getElementById('work-video-playlist-id');
            const results = document.getElementById('work-video-playlist-results');
            if (!input || !hidden || !results) return;
            if (input.dataset.bound === '1') return;
            input.dataset.bound = '1';

            async function loadPlaylists() {
                if (typeof fetchPlaylists !== 'function') return [];
                const pls = await fetchPlaylists();
                return Array.isArray(pls) ? pls : [];
            }

            let playlists = await loadPlaylists();

            function normalize(s) {
                return String(s || '').trim().toLowerCase();
            }

            async function quickCreate(title) {
                const t = String(title || '').trim();
                if (!t) return null;
                // Canonical wrapper: guards connectivity and owns the
                // Playlists-domain invalidation for this surface too.
                const newId = await createPlaylist(t, '');
                if (!newId) throw new Error('create failed');
                playlists = await loadPlaylists();
                return { id: newId, title: t };
            }

            function openDropdown() {
                const qRaw = String(input.value || '').trim();
                const q = normalize(qRaw);
                const filtered = !q
                    ? playlists.slice(0, 40)
                    : playlists.filter((p) => normalize(p.title).includes(q)).slice(0, 40);
                const exact = q && playlists.some((p) => normalize(p.title) === q);

                results.innerHTML = '';

                if (q && !exact) {
                    const c = document.createElement('div');
                    c.className = 'result-item result-item--create';
                    c.textContent = `Create playlist "${qRaw}"`;
                    c.onmousedown = async (ev) => {
                        ev.preventDefault();
                        try {
                            const created = await quickCreate(qRaw);
                            if (created) {
                                input.value = created.title;
                                hidden.value = created.id;
                                prksHideInlineComboboxResults(results);
                            }
                        } catch (_e) {
                            await prksAlertMessage('Could not create playlist.', 'Error');
                        }
                    };
                    results.appendChild(c);
                }

                if (filtered.length === 0) {
                    if (!results.childElementCount) {
                        results.innerHTML = `<div class="result-item no-results">No playlists found</div>`;
                    }
                } else {
                    for (const p of filtered) {
                        const div = document.createElement('div');
                        div.className = 'result-item';
                        div.textContent = p.title || 'Playlist';
                        div.onmousedown = (ev) => {
                            ev.preventDefault();
                            input.value = p.title || '';
                            hidden.value = p.id;
                            prksHideInlineComboboxResults(results);
                        };
                        results.appendChild(div);
                    }
                }

                results.classList.remove('hidden');
            }

            input.onfocus = async () => {
                playlists = await loadPlaylists();
                openDropdown();
            };
            input.oninput = () => {
                hidden.value = '';
                openDropdown();
            };
            input.onblur = () => setTimeout(() => prksHideInlineComboboxResults(results), 200);
        };
    }

    if (typeof window.prksSyncUploadModalKindUi === 'function') {
        window.prksSyncUploadModalKindUi();
    }
}

function handleUploadFile(file) {
    const kindEl = document.getElementById('work-source-kind');
    const kind = kindEl ? String(kindEl.value || 'pdf') : 'pdf';
    if (kind === 'video') {
        void prksAlertMessage('In YouTube URL mode, paste the link in Source.', 'Notice');
        return;
    }
    if (!file) return;
    const name = String(file.name || '').toLowerCase();
    const isPdf = name.endsWith('.pdf') || file.type === 'application/pdf';
    if (kind === 'pdf' && !isPdf) {
        if (typeof prksClearWorkModalErrors === 'function') prksClearWorkModalErrors();
        const zone = document.getElementById('upload-drop-zone');
        if (typeof prksSetWorkModalFieldError === 'function') {
            prksSetWorkModalFieldError(zone, 'Choose a PDF file.', 'work-file-error');
            prksFocusWorkModalControl(zone);
        } else {
            void prksAlertMessage('Please select a valid PDF file.', 'Validation');
        }
        return;
    }

    window.__prksPendingUploadPdfFile = file;
    prksShowUploadPdfSelected(file);
    if (typeof prksClearWorkModalErrors === 'function') {
        const err = document.getElementById('work-file-error');
        if (err) {
            err.textContent = '';
            err.classList.add('hidden');
        }
        const zone = document.getElementById('upload-drop-zone');
        if (zone) zone.removeAttribute('aria-invalid');
    }

    const titleInput = document.getElementById('work-title');
    if (titleInput && !titleInput.value) {
        titleInput.value = file.name
            .replace(/\.pdf$/i, '')
            .replace(/_/g, ' ');
    }
}

(function prksInitTagRemoveDelegation() {
    if (typeof document === 'undefined' || window.__prksTagRemoveDelegationBound) return;
    window.__prksTagRemoveDelegationBound = true;
    document.addEventListener(
        'click',
        (e) => {
            const btn = e.target.closest('.work-tag-remove');
            if (!btn) return;
            const tagId = btn.getAttribute('data-tag-id');
            if (!tagId) return;
            e.stopPropagation();
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, true);
            const folderId = btn.getAttribute('data-folder-id');
            if (folderId != null && folderId !== '') {
                if (typeof prksRemoveFolderTag === 'function') {
                    void prksRemoveFolderTag(folderId, tagId, btn);
                }
                return;
            }
            const workId = btn.getAttribute('data-work-id');
            if (workId != null && workId !== '') {
                void prksRemoveWorkTag(workId, tagId, btn);
            }
        },
        true
    );
})();
