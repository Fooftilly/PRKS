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

function prksAutosizeTextarea(el) {
    if (!el || el.tagName !== 'TEXTAREA') return;
    if (el.id === 'research-notes-editor' || el.id === 'pdf-annotation-editor-text') return;
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
        if (el.id === 'research-notes-editor' || el.id === 'pdf-annotation-editor-text') return;
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
    if (typeof window.prksCloseTagsAliasModal === 'function') {
        window.prksCloseTagsAliasModal();
    }
    document.getElementById('modal-backdrop').classList.remove('hidden');
    document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));
    const modalEl = document.getElementById(id);
    modalEl.classList.remove('hidden');

    if (id === 'role-modal') {
        prepareRoleModal();
    } else if (id === 'work-modal') {
        populateUploadComboboxes();
        resetUploadModal();
    } else if (id === 'person-modal') {
        resetPersonAliasAutoSyncState();
        syncPersonAliasesFromNames();
    } else if (id === 'folder-modal' && typeof window.prksRefreshFolderModalValidation === 'function') {
        const parentSearch = document.getElementById('folder-parent-search');
        const parentId = document.getElementById('folder-parent-id');
        if (parentSearch && !window.__prksPendingWorkFolderAttach) parentSearch.value = '';
        if (parentId && !window.__prksPendingWorkFolderAttach) parentId.value = '';
        window.prksRefreshFolderModalValidation();
    } else if (id === 'group-modal' && typeof window.prksInitNewGroupModal === 'function') {
        void window.prksInitNewGroupModal();
    }
    requestAnimationFrame(() => prksBindAutosizeTextareas(modalEl));
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
        '<p>Use <strong>＋ New playlist</strong> in the right-hand panel on this page.</p>',
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
    document.body.classList.add('prks-right-panel-open', 'prks-overlay-open');
    document.body.classList.remove('prks-sidebar-open');
    prksSetOverlayBackdropVisible(true);
    prksSyncMobileToggleButtons();
}

function prksToggleSidebarDrawer(forceOpen) {
    if (!prksIsSmallScreen()) return;
    const open = document.body.classList.contains('prks-sidebar-open');
    const want = forceOpen === undefined ? !open : !!forceOpen;
    if (want) prksOpenSidebarDrawer();
    else prksCloseOverlays();
}

function prksToggleRightPanelOverlay(forceOpen) {
    if (!prksIsSmallScreen()) return;
    const open = document.body.classList.contains('prks-right-panel-open');
    const want = forceOpen === undefined ? !open : !!forceOpen;
    if (want) prksOpenRightPanelOverlay();
    else prksCloseOverlays();
}

function initMobileShell() {
    const navBtn = document.getElementById('prks-mobile-nav-btn');
    const detBtn = document.getElementById('prks-mobile-details-btn');
    const overlayBackdrop = document.getElementById('prks-overlay-backdrop');

    if (navBtn && navBtn.dataset.bound !== '1') {
        navBtn.dataset.bound = '1';
        navBtn.addEventListener('click', () => prksToggleSidebarDrawer());
    }
    if (detBtn && detBtn.dataset.bound !== '1') {
        detBtn.dataset.bound = '1';
        detBtn.addEventListener('click', () => prksToggleRightPanelOverlay());
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
    } else if (window.location.hash.startsWith('#/folders/')) {
        select.value = window.location.hash.split('/')[2];
    }
}

function closeModals() {
    const playlistModal = document.getElementById('playlist-modal');
    const playlistWasOpen = playlistModal && !playlistModal.classList.contains('hidden');
    document.getElementById('modal-backdrop').classList.add('hidden');
    document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));

    // If playlist modal was opened from "New File" flow, return to it.
    if (playlistWasOpen && window.__prksReturnToWorkModalAfterPlaylist === true) {
        window.__prksReturnToWorkModalAfterPlaylist = false;
        if (typeof openModal === 'function') {
            openModal('work-modal');
            if (typeof window.prksSyncUploadModalKindUi === 'function') {
                window.prksSyncUploadModalKindUi();
            }
            if (typeof window.__prksRefreshAllPlaylistSelects === 'function') {
                void window.__prksRefreshAllPlaylistSelects();
            }
        }
    }
}

function personDisplayName(p) {
    return `${(p.first_name || '').trim()} ${p.last_name || ''}`.trim();
}

/** Split typed display name into first / last (same rules as upload quick-create). */
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
    return { first_name: parts[0], last_name: parts.slice(1).join(' ') };
}

/** Create a person from the Link Person to Work modal and select them for linking. */
async function prksQuickCreatePersonForSearchField(typedName, searchInputId, hiddenInputId, aboutText) {
    const trimmed = String(typedName || '').trim();
    if (!trimmed) {
        alert('Type a name in the Person field first.');
        return;
    }
    const { first_name, last_name } = prksSplitTypedPersonName(trimmed);
    try {
        const res = await fetch('/api/persons', {
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
            alert(data.error || 'Could not create person.');
            return;
        }
        allPersons = await fetchPersons();
        window.allPersons = allPersons;
        const newPerson = allPersons.find((p) => String(p.id) === String(data.id));
        const personSearch = document.getElementById(searchInputId);
        const personHidden = document.getElementById(hiddenInputId);
        if (personHidden) personHidden.value = data.id;
        if (personSearch) {
            personSearch.value = newPerson ? personDisplayName(newPerson) : trimmed;
        }
    } catch (e) {
        console.error(e);
        alert('Could not create person.');
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
    });
    const workHidden = document.getElementById('meta-role-work-id');
    if (workHidden) workHidden.value = String(workId);
}

async function addRoleToWorkFromMetaEditor(workId) {
    const personHidden = document.getElementById('meta-role-person-id');
    const personSearch = document.getElementById('meta-role-person-search');
    const roleHidden = document.getElementById('meta-role-type');
    const list = document.getElementById('meta-linked-persons-list');
    const addBtn = document.getElementById('meta-role-add-btn');

    const resolvedWorkId = String(workId || '').trim();
    const personId = personHidden ? String(personHidden.value || '').trim() : '';
    const roleType = roleHidden ? String(roleHidden.value || '').trim() : '';
    if (!resolvedWorkId || !personId || !roleType) {
        alert('Select a person and role first.');
        return;
    }

    if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = 'Linking...';
    }
    try {
        const res = await fetch('/api/roles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                person_id: personId,
                work_id: resolvedWorkId,
                role_type: roleType,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert(data.error || 'Could not create link.');
            return;
        }
        if (typeof fetchWorkDetails === 'function') {
            window.currentWork = await fetchWorkDetails(resolvedWorkId);
            if (list && window.currentWork) {
                list.innerHTML = buildWorkLinkedPersonsHtml(window.currentWork);
            }
        }
        if (personHidden) personHidden.value = '';
        if (personSearch) personSearch.value = '';
    } catch (e) {
        console.error(e);
        alert('Could not create link.');
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
    });
    initSearchableCombobox('role-work-search', 'role-work-results', 'role-work-id', 'work');

    const hash = window.location.hash || '';
    const personSearch = document.getElementById('role-person-search');
    const personHidden = document.getElementById('role-person-id');
    const workSearch = document.getElementById('role-work-search');
    const workHidden = document.getElementById('role-work-id');

    personSearch.value = '';
    personHidden.value = '';
    workSearch.value = '';
    workHidden.value = '';

    if (
        hash.startsWith('#/people/') &&
        !hash.startsWith('#/people/role/') &&
        !hash.startsWith('#/people/groups')
    ) {
        const pid = hash.split('/')[2];
        const p = allPersons.find(x => String(x.id) === String(pid));
        if (p) {
            personHidden.value = p.id;
            personSearch.value = personDisplayName(p);
        }
    }

    let workId = null;
    if (hash.startsWith('#/works/')) {
        workId = hash.split('/')[2];
    } else if (window.currentWork && window.currentWork.id) {
        workId = window.currentWork.id;
    }
    if (workId) {
        const w = allWorks.find(x => String(x.id) === String(workId));
        if (w) {
            workHidden.value = w.id;
            workSearch.value = w.title || '';
        }
    }
}

function initTabs() {
    const btns = document.querySelectorAll('#right-panel .tabs .tab-btn');
    btns.forEach((btn) => {
        btn.addEventListener('click', (e) => {
            const t = e.target;
            if (!t || !t.classList || !t.classList.contains('tab-btn')) return;
            btns.forEach((b) => b.classList.remove('active'));
            t.classList.add('active');
            const target = t.getAttribute('data-target');
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
    rp.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
    rp.querySelector('.tab-btn[data-target="details"]')?.classList.add('active');
}

/** Match tab button selection to the panel content (e.g. after opening another work from the graph). */
function prksSyncRightPanelTabStrip(tabId) {
    const rp = document.getElementById('right-panel');
    if (!rp || rp.classList.contains('right-panel--single-pane')) return;
    let want = tabId || 'details';
    const visible = [...rp.querySelectorAll('.tabs .tab-btn')].filter((b) => !b.hidden);
    const allowed = new Set(visible.map((b) => b.getAttribute('data-target')));
    if (!allowed.has(want)) want = 'details';
    visible.forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-target') === want);
    });
}

/**
 * Right column layout: full tabs (work), two tabs (folder, no annotations), or single contextual pane (everything else).
 */
function setRightPanelRouteContext(hash) {
    const rp = document.getElementById('right-panel');
    const tabs = rp?.querySelector('.tabs');
    const annBtn = rp?.querySelector('.tab-btn[data-target="annotations"]');
    if (!rp || !tabs) return;

    rp.classList.remove(
        'right-panel--graph-route',
        'right-panel--single-pane',
        'right-panel--mode-work',
        'right-panel--mode-folder',
        'right-panel--mode-person',
        'right-panel--mode-person-group'
    );
    rp.removeAttribute('data-right-panel-mode');

    if (annBtn) annBtn.hidden = false;

    const h = hash || '';

    function hideTabStripIfAtMostOneVisible() {
        const visibleTabs = [...tabs.querySelectorAll('.tab-btn')].filter((b) => !b.hidden);
        if (visibleTabs.length <= 1) {
            tabs.hidden = true;
            tabs.setAttribute('aria-hidden', 'true');
        }
    }

    if (window.currentWork) {
        rp.classList.add('right-panel--mode-work');
        rp.setAttribute('data-right-panel-mode', 'work');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        return;
    }

    if (window.currentFolder) {
        rp.classList.add('right-panel--mode-folder');
        rp.setAttribute('data-right-panel-mode', 'folder');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        if (annBtn) annBtn.hidden = true;
        const activeTarget = rp.querySelector('.tab-btn.active')?.getAttribute('data-target');
        if (activeTarget === 'annotations') activateRightPanelDetailsTab();
        hideTabStripIfAtMostOneVisible();
        return;
    }

    if (window.currentPerson && isPersonDetailHash(h)) {
        rp.classList.add('right-panel--mode-person');
        rp.setAttribute('data-right-panel-mode', 'person');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        if (annBtn) annBtn.hidden = true;
        const activeTarget = rp.querySelector('.tab-btn.active')?.getAttribute('data-target');
        if (activeTarget === 'annotations') activateRightPanelDetailsTab();
        hideTabStripIfAtMostOneVisible();
        return;
    }

    if (window.currentPersonGroup && isPersonGroupDetailHash(h)) {
        rp.classList.add('right-panel--mode-person-group');
        rp.setAttribute('data-right-panel-mode', 'person-group');
        tabs.hidden = false;
        tabs.removeAttribute('aria-hidden');
        if (annBtn) annBtn.hidden = true;
        const activeTarget = rp.querySelector('.tab-btn.active')?.getAttribute('data-target');
        if (activeTarget === 'annotations') activateRightPanelDetailsTab();
        hideTabStripIfAtMostOneVisible();
        return;
    }

    if (window.currentPlaylist && (h.startsWith('#/playlists/') || h === '#/playlists')) {
        rp.classList.add('right-panel--single-pane');
        rp.setAttribute('data-right-panel-mode', h.startsWith('#/playlists/') ? 'playlist' : 'playlists');
        tabs.hidden = true;
        tabs.setAttribute('aria-hidden', 'true');
        activateRightPanelDetailsTab();
        return;
    }

    const mode = inferRightPanelListMode(h);
    rp.classList.add('right-panel--single-pane');
    rp.setAttribute('data-right-panel-mode', mode);
    tabs.hidden = true;
    tabs.setAttribute('aria-hidden', 'true');
    activateRightPanelDetailsTab();
}

function isPersonGroupDetailHash(h) {
    if (!h || h === '#/people/groups') return false;
    return /^#\/people\/groups\/.+/.test(h);
}

function isPersonDetailHash(h) {
    if (!h || !h.startsWith('#/people/')) return false;
    if (h.startsWith('#/people/role/')) return false;
    if (h === '#/people/groups' || h.startsWith('#/people/groups/')) return false;
    const path = h.replace(/^#\/?/, '').split('/').filter(Boolean);
    if (path.length < 2 || path[0] !== 'people') return false;
    if (path[1] === 'groups' || path[1] === 'role') return false;
    return true;
}

function inferRightPanelListMode(h) {
    if (h === '#/folders') return 'library';
    if (h === '#/playlists') return 'playlists';
    if (h.startsWith('#/playlists/')) return 'playlist';
    if (h === '#/people') return 'people';
    if (h.startsWith('#/people/role/')) return 'people-role';
    if (h === '#/people/groups') return 'people-groups';
    if (h.startsWith('#/people/groups/')) return 'people-group';
    if (h.startsWith('#/people/')) return 'person';
    if (h === '#/recent') return 'recent';
    if (h.startsWith('#/progress')) return 'progress';
    if (h.startsWith('#/search')) return 'search';
    if (h === '#/tags') return 'tags';
    if (h === '#/publishers') return 'publishers';
    if (h === '#/types' || h.startsWith('#/types/')) return 'types';
    return 'default';
}

function renderRouteContextSidebar(mode) {
    const ctx = window.__prksRouteSidebar || {};
    const link = (href, label) =>
        `<p class="route-sidebar__action"><a href="${href}" class="route-sidebar__link">${label}</a></p>`;

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
        const dtLabel = prksSidebarEsc(ctx.docTypeLabel || ctx.docType || 'File types');
        const n = ctx.workCount != null ? Number(ctx.workCount) : null;
        const extra = n != null && !Number.isNaN(n) ? `<p class="route-sidebar__meta">${n} file${n === 1 ? '' : 's'} in this type.</p>` : '';
        return `
            <div class="route-sidebar">
                <h2 class="route-sidebar__title">${dtLabel}</h2>
                <p class="route-sidebar__lede">Browse files by BibTeX document type, regardless of folder.</p>
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
                    <button type="button" class="add-new-btn route-sidebar__new-playlist-btn" id="prks-create-playlist-btn">＋ New playlist</button>
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
    const active = document.querySelector('#right-panel .tab-btn.active');
    return (active && active.getAttribute('data-target')) || 'details';
}

function prksContextGraphPanelHtml(ledeText) {
    return `
        <div class="context-graph-panel context-graph-panel--stacked">
            <h3 class="context-graph-panel__title">Related graph</h3>
            <p class="context-graph-panel__lede meta-row">${ledeText}</p>
            <div id="prks-context-graph-network" class="context-graph-panel__canvas" role="img" aria-label="Graph of related files"></div>
            <p id="prks-context-graph-status" class="context-graph-panel__status meta-row">Loading…</p>
        </div>`;
}

const PRKS_WORK_CONTEXT_GRAPH_LEDE =
    'This file and every file directly linked to it using (wiki <code>[[links]]</code>, shared tags, co-cited unresolved links).';

function renderPrksPrivateNotesCard(entityType, entityId, initialText) {
    const text = initialText == null ? '' : String(initialText);
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

function initPrksPrivateNotesEditor(entityType, entityId) {
    const idSuffix = `${entityType}-${entityId}`;
    const ta = document.getElementById(`prks-private-notes-${idSuffix}`);
    if (!ta || ta.dataset.prksNotesBound === '1') return;
    ta.dataset.prksNotesBound = '1';
    const statusEl = document.getElementById(`prks-private-notes-status-${idSuffix}`);
    let debounceTimer;

    const persist = async () => {
        const url = entityType === 'work' ? `/api/works/${entityId}` : `/api/folders/${entityId}`;
        try {
            const res = await fetch(url, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ private_notes: ta.value }),
            });
            if (!res.ok) throw new Error('save failed');
            if (statusEl) {
                statusEl.textContent = 'Saved';
                window.setTimeout(() => {
                    if (statusEl.textContent === 'Saved') statusEl.textContent = '';
                }, 1800);
            }
            if (entityType === 'work' && window.currentWork && window.currentWork.id === entityId) {
                window.currentWork.private_notes = ta.value;
            }
            if (entityType === 'folder' && window.currentFolder && window.currentFolder.id === entityId) {
                window.currentFolder.private_notes = ta.value;
            }
        } catch (e) {
            console.error(e);
            if (statusEl) statusEl.textContent = 'Could not save';
        }
    };

    const schedule = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(persist, 850);
    };

    ta.addEventListener('input', schedule);
    ta.addEventListener('blur', () => {
        window.clearTimeout(debounceTimer);
        void persist();
    });
}

function prksWorkPanelActionsHtml() {
    return (
        '<div class="right-panel-work-actions">' +
        '<button type="button" class="tab-btn copy-bibtex-btn" aria-live="polite">📋 Copy BibTeX</button>' +
        '<button type="button" class="ribbon-btn delete-work-btn" title="Delete this file">🗑 Delete File</button>' +
        '</div>'
    );
}

function prksWorkRightPanelStackHtml(work, isEditing = false) {
    const notes = renderPrksPrivateNotesCard('work', work.id, work.private_notes);
    const inferred = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
    const playlistCard =
        inferred === 'video' && typeof renderPlaylistAttachControlsHtml === 'function'
            ? renderPlaylistAttachControlsHtml(work)
            : '';
    const folderCard =
        typeof renderFolderAttachControlsHtml === 'function' ? renderFolderAttachControlsHtml(work) : '';
    return (
        '<div class="right-panel-stack">' +
        prksWorkPanelActionsHtml() +
        notes +
        playlistCard +
        folderCard +
        renderWorkMetaTab(work, isEditing) +
        prksContextGraphPanelHtml(PRKS_WORK_CONTEXT_GRAPH_LEDE) +
        '</div>'
    );
}

function prksRemountWorkContextGraph(work) {
    if (typeof mountPrksContextGraphPanel === 'function' && work && work.id) {
        void mountPrksContextGraphPanel({
            mode: 'work',
            centerWorkId: work.id,
            workMeta: work,
        });
    }
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

    if (typeof destroyPrksContextGraph === 'function') {
        destroyPrksContextGraph();
    }

    setRightPanelRouteContext(window.location.hash || '');

    if (window.currentWork) {
        if (tabId === 'details') {
            panel.innerHTML = prksWorkRightPanelStackHtml(window.currentWork, false);
            initPrksPrivateNotesEditor('work', window.currentWork.id);
            if (typeof mountPlaylistAttachControls === 'function') {
                void mountPlaylistAttachControls(window.currentWork);
            }
            if (typeof mountFolderAttachControlsForWork === 'function') {
                void mountFolderAttachControlsForWork(window.currentWork);
            }
            initWorkTagCombobox(window.currentWork.id);
            prksRemountWorkContextGraph(window.currentWork);
            if (typeof initWorkDetailRightPanelActions === 'function') {
                initWorkDetailRightPanelActions(window.currentWork);
            }
        } else if (tabId === 'annotations') {
            panel.innerHTML = renderWorkAnnotationsTab(window.currentWork);
            if (typeof window.applyCachedAnnotationListToPanel === 'function') {
                window.applyCachedAnnotationListToPanel();
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Use Details or Annotations.</p>';
        }
    } else if (window.currentPlaylist && (window.location.hash || '').startsWith('#/playlists/')) {
        // Playlist detail route uses single-pane right panel; show summary or editor.
        const editing = window.__prksPlaylistDetailEditing === true;
        panel.innerHTML = editing ? renderPlaylistEditSidebarHtml(window.currentPlaylist) : renderPlaylistSummarySidebarHtml(window.currentPlaylist);
        if (editing) {
            void mountPlaylistEditSidebar(window.currentPlaylist);
        } else {
            // bind edit button
            const btn = document.getElementById('prks-playlist-edit-btn');
            if (btn && btn.dataset.bound !== '1') {
                btn.dataset.bound = '1';
                btn.onclick = () => {
                    window.__prksPlaylistDetailEditing = true;
                    updatePanelContent('details');
                };
            }
        }
    } else if (window.currentFolder) {
        if (tabId === 'details') {
            panel.innerHTML = prksFolderRightPanelStackHtml(window.currentFolder);
            initPrksPrivateNotesEditor('folder', window.currentFolder.id);
            initFolderTagCombobox(window.currentFolder.id);
            if (typeof mountFolderHierarchyControls === 'function') {
                void mountFolderHierarchyControls(window.currentFolder);
            }
            if (typeof mountFolderLibraryAttachControls === 'function') {
                void mountFolderLibraryAttachControls(window.currentFolder);
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Folder details and tags are above.</p>';
        }
    } else if (window.currentPerson && isPersonDetailHash(window.location.hash || '')) {
        if (tabId === 'details') {
            let topHtml;
            if (window.__prksPersonDetailEditing && typeof renderPersonProfileEditFormHtml === 'function') {
                topHtml = renderPersonProfileEditFormHtml(window.currentPerson);
            } else if (typeof renderPersonProfileDetailsSidebarHtml === 'function') {
                topHtml = renderPersonProfileDetailsSidebarHtml(window.currentPerson);
            } else {
                topHtml = '<p class="meta-row">Person panel unavailable.</p>';
            }
            const ids = (window.currentPerson.works || []).map((w) => w.id).filter(Boolean);
            panel.innerHTML =
                '<div class="right-panel-stack">' +
                topHtml +
                prksContextGraphPanelHtml(
                    'Files linked to this person and edges among them (wiki links, shared tags, co-citations). Click a node to open.'
                ) +
                '</div>';
            if (typeof mountPrksContextGraphPanel === 'function') {
                void mountPrksContextGraphPanel({ mode: 'person', workIds: ids });
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Use the Details tab.</p>';
        }
    } else if (
        window.currentPersonGroup &&
        isPersonGroupDetailHash(window.location.hash || '')
    ) {
        if (tabId === 'details') {
            const g = window.currentPersonGroup;
            let topHtml;
            if (
                window.__prksPersonGroupDetailEditing &&
                typeof renderPersonGroupEditSidebarHtml === 'function'
            ) {
                topHtml = renderPersonGroupEditSidebarHtml(g);
            } else if (typeof renderPersonGroupSummarySidebarHtml === 'function') {
                topHtml = renderPersonGroupSummarySidebarHtml(g);
            } else {
                topHtml = '<p class="meta-row">Group panel unavailable.</p>';
            }
            const addMemberHtml =
                typeof renderPersonGroupAddMemberPanelHtml === 'function'
                    ? renderPersonGroupAddMemberPanelHtml()
                    : '';
            panel.innerHTML = '<div class="right-panel-stack">' + topHtml + addMemberHtml + '</div>';
            if (
                window.__prksPersonGroupDetailEditing &&
                typeof mountPersonGroupEditPanel === 'function'
            ) {
                void mountPersonGroupEditPanel(g);
            }
            if (typeof mountPersonGroupAddMemberControls === 'function') {
                void mountPersonGroupAddMemberControls(g);
            }
        } else {
            panel.innerHTML = '<p class="panel-empty-message">Use the Details tab.</p>';
        }
    } else {
        if (window.currentPlaylist && (window.location.hash || '').startsWith('#/playlists/')) {
            const pl = window.currentPlaylist;
            const editing = window.__prksPlaylistDetailEditing === true;
            const panel = document.getElementById('panel-content');
            if (!panel) return;
            panel.innerHTML = editing ? renderPlaylistEditSidebarHtml(pl) : renderPlaylistSummarySidebarHtml(pl);
            prksBindAutosizeTextareas(panel);
            if (editing) {
                void mountPlaylistEditSidebar(pl);
            }
            return;
        }
        const mode = inferRightPanelListMode(window.location.hash || '');
        panel.innerHTML = renderRouteContextSidebar(mode);
        if (mode === 'playlists' && typeof window.prksBindPlaylistsIndexCreateBtn === 'function') {
            window.prksBindPlaylistsIndexCreateBtn();
        }
    }

    prksBindAutosizeTextareas(panel);
    prksSyncRightPanelTabStrip(tabId || 'details');

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
                    <button type="button" class="ribbon-btn form-actions__btn" id="prks-playlist-edit-btn">Edit</button>
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
                    <button type="button" class="close-btn" id="prks-playlist-edit-close" aria-label="Close">&times;</button>
                </div>
                <label for="prks-playlist-edit-title">Title</label>
                <input type="text" id="prks-playlist-edit-title" value="${title}" autocomplete="off">
                <label for="prks-playlist-edit-desc">Description</label>
                <textarea id="prks-playlist-edit-desc" class="textarea-sm">${desc}</textarea>
                <label for="prks-playlist-edit-original-url">Original playlist URL</label>
                <input type="url" id="prks-playlist-edit-original-url" value="${originalUrl}" placeholder="https://..." autocomplete="off">
                <div class="form-actions">
                    <button type="button" class="ribbon-btn form-actions__btn form-actions__btn--secondary" id="prks-playlist-edit-cancel">Cancel</button>
                    <button type="button" class="add-new-btn form-actions__btn form-actions__btn--primary" id="prks-playlist-edit-save">Save</button>
                </div>
                <p class="meta-row meta-row--spaced" id="prks-playlist-edit-status" aria-live="polite"></p>
            </div>

            <div class="doc-meta-card">
                <h3>Add video</h3>
                <p class="meta-row meta-row--compact">Search for a video and click Add.</p>
                <div class="tag-add-shell combobox-container tag-add-shell--flush">
                    <div class="tag-add-shell__field">
                        <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
                        <input type="text" id="prks-playlist-add-search" class="tag-add-shell__input" placeholder="Search videos…" maxlength="300" autocomplete="off" aria-label="Search videos to add">
                    </div>
                    <div id="prks-playlist-add-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                </div>
                <p class="meta-row meta-row--spaced" id="prks-playlist-add-status" aria-live="polite"></p>
            </div>
        </div>
    `;
}

async function mountPlaylistEditSidebar(pl) {
    if (!pl || !pl.id) return;
    prksBindAutosizeTextareas(document.getElementById('panel-content'));
    const editBtn = document.getElementById('prks-playlist-edit-btn');
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            window.__prksPlaylistDetailEditing = true;
            updatePanelContent('details');
        };
    }
    const close = () => {
        window.__prksPlaylistDetailEditing = false;
        updatePanelContent('details');
    };

    document.getElementById('prks-playlist-edit-close')?.addEventListener('click', close);
    document.getElementById('prks-playlist-edit-cancel')?.addEventListener('click', close);

    const saveBtn = document.getElementById('prks-playlist-edit-save');
    const statusEl = document.getElementById('prks-playlist-edit-status');
    if (saveBtn && saveBtn.dataset.bound !== '1') {
        saveBtn.dataset.bound = '1';
        saveBtn.onclick = async () => {
            const title = String(document.getElementById('prks-playlist-edit-title')?.value || '').trim();
            const description = String(document.getElementById('prks-playlist-edit-desc')?.value || '').trim();
            const originalUrl = String(document.getElementById('prks-playlist-edit-original-url')?.value || '').trim();
            if (!title) {
                if (statusEl) statusEl.textContent = 'Title is required.';
                return;
            }
            try {
                const res = await fetch('/api/playlists/' + encodeURIComponent(pl.id), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, description, original_url: originalUrl }),
                });
                if (!res.ok) throw new Error('save failed');
                const fresh = typeof fetchPlaylistDetails === 'function' ? await fetchPlaylistDetails(pl.id) : null;
                if (fresh) {
                    window.currentPlaylist = fresh;
                    window.__prksRouteSidebar = { playlistTitle: fresh.title || 'Playlist', itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0 };
                }
                window.__prksPlaylistDetailEditing = false;
                updatePanelContent('details');
            } catch (_e) {
                if (statusEl) statusEl.textContent = 'Could not save.';
            }
        };
    }

    const input = document.getElementById('prks-playlist-add-search');
    const results = document.getElementById('prks-playlist-add-results');
    const addStatus = document.getElementById('prks-playlist-add-status');
    if (!input || !results) return;

    const present = new Set((Array.isArray(pl.items) ? pl.items : []).map((w) => String(w.id || '')).filter(Boolean));
    const works = typeof fetchWorks === 'function' ? await fetchWorks() : [];
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
                btn.className = 'ribbon-btn';
                btn.textContent = 'Add';
                btn.style.flex = '0 0 auto';
                btn.onmousedown = (ev) => ev.preventDefault();
                btn.onclick = async (ev) => {
                    ev.preventDefault();
                    try {
                        if (typeof addWorkToPlaylist !== 'function') throw new Error('no api');
                        await addWorkToPlaylist(pl.id, w.id);
                        if (addStatus) addStatus.textContent = 'Added.';
                        const fresh =
                            typeof fetchPlaylistDetails === 'function' ? await fetchPlaylistDetails(pl.id) : null;
                        if (fresh) {
                            window.currentPlaylist = fresh;
                            window.__prksRouteSidebar = {
                                playlistTitle: fresh.title || 'Playlist',
                                itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
                            };
                            const page = document.getElementById('page-content');
                            if (page && typeof renderPlaylistDetail === 'function') {
                                renderPlaylistDetail(fresh, page);
                            }
                            // Stay in edit mode and keep the editor open.
                            window.__prksPlaylistDetailEditing = true;
                            updatePanelContent('details');
                        }
                    } catch (_e) {
                        if (addStatus) addStatus.textContent = 'Could not add.';
                    }
                };
                row.appendChild(btn);
                results.appendChild(row);
            }
        }
        results.classList.remove('hidden');
    }

    input.onfocus = () => renderDropdown();
    input.oninput = () => renderDropdown();
    input.onblur = () => setTimeout(() => results.classList.add('hidden'), 180);
}

function toggleWorkMetaEdit(isEditing) {
    if (window.currentWork) {
        if (typeof destroyPrksContextGraph === 'function') {
            destroyPrksContextGraph();
        }
        const panel = document.getElementById('panel-content');
        if (panel) {
            panel.innerHTML = prksWorkRightPanelStackHtml(window.currentWork, isEditing);
            initPrksPrivateNotesEditor('work', window.currentWork.id);
            if (!isEditing) initWorkTagCombobox(window.currentWork.id);
            if (typeof mountPlaylistAttachControls === 'function') {
                void mountPlaylistAttachControls(window.currentWork);
            }
            if (typeof mountFolderAttachControlsForWork === 'function') {
                void mountFolderAttachControlsForWork(window.currentWork);
            }
            prksRemountWorkContextGraph(window.currentWork);
            if (typeof initWorkDetailRightPanelActions === 'function') {
                initWorkDetailRightPanelActions(window.currentWork);
            }
            if (isEditing) {
                prksBindSegmentedHidden('meta-status');
                prksBindSegmentedHidden('meta-role-type');
                void initWorkMetaRoleLinker(window.currentWork.id);
                if (typeof initPrksDocTypeMenu === 'function') {
                    const inf =
                        typeof prksInferWorkSourceKind === 'function'
                            ? prksInferWorkSourceKind(window.currentWork)
                            : '';
                    initPrksDocTypeMenu('meta-doc-type', { disabled: inf === 'video' });
                }
            }
            prksBindAutosizeTextareas(panel);
        }
        prksSyncRightPanelTabStrip('details');
    }
}

async function submitWorkMetaEdit(workId) {
    const metaDoc = document.getElementById('meta-doc-type');
    const v = (id) => {
        const el = document.getElementById(id);
        return el ? el.value : '';
    };
    const payload = {
        title: v('meta-title'),
        status: v('meta-status'),
        doc_type: metaDoc ? metaDoc.value : 'article',
        thumb_page: (() => {
            const el = document.getElementById('meta-thumb-page');
            if (!el) return null;
            const raw = String(el.value || '').trim();
            if (!raw) return null;
            const n = Number(raw);
            if (!Number.isFinite(n)) return null;
            const i = Math.floor(n);
            return i >= 1 ? i : null;
        })(),
        year: v('meta-year'),
        published_date: v('meta-date'),
        publisher: v('meta-publisher'),
        location: (() => {
            const el = document.getElementById('meta-location');
            return el ? String(el.value || '') : '';
        })(),
        edition: v('meta-edition'),
        journal: v('meta-journal'),
        volume: v('meta-volume'),
        issue: v('meta-issue'),
        pages: v('meta-pages'),
        isbn: v('meta-isbn'),
        doi: v('meta-doi'),
        abstract: v('meta-abstract')
    };
    const metaAuthorEl = document.getElementById('meta-author-text');
    if (metaAuthorEl) {
        payload.author_text = String(metaAuthorEl.value || '').trim();
    }
    const metaSrcEl = document.getElementById('meta-source-url');
    if (metaSrcEl) {
        payload.source_url = String(metaSrcEl.value || '').trim();
    }
    
    // Disable save button to prevent double submission
    const saveBtn = document.getElementById('inline-save-metadata-btn');
    if (saveBtn) {
        saveBtn.innerText = "Saving...";
        saveBtn.disabled = true;
    }

    try {
        const saveRes = await fetch(`/api/works/${workId}`, { method: 'PATCH', body: JSON.stringify(payload) });
        if (!saveRes.ok) {
            const errData = await saveRes.json().catch(() => ({}));
            throw new Error(errData.error || `Server error ${saveRes.status}`);
        }
        // Refresh work in memory
        window.currentWork = await fetchWorkDetails(workId);
        toggleWorkMetaEdit(false);
        // Refresh the main page header to reflect new title / document type
        const headerTitle = document.querySelector('.page-header--work-title');
        if (headerTitle) headerTitle.innerText = window.currentWork.title;
        const typeSlot = document.getElementById('work-header-doc-type-slot');
        if (typeSlot && typeof prksDocTypeBadgeHtml === 'function') {
            typeSlot.innerHTML = prksDocTypeBadgeHtml(window.currentWork.doc_type);
        }
        // Also refresh works list if on works dashboard, but not strictly needed 
        // as hash change will trigger full refresh.
    } catch (err) {
        console.error("Failed to save metadata", err);
        if (saveBtn) {
            saveBtn.innerText = "Save Changes";
            saveBtn.disabled = false;
        }
    }
}

/** Linked persons on the work details panel, grouped by role (order follows DB order_index). */
function buildWorkLinkedPersonsHtml(work) {
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
                    const name = `${a.first_name || ''} ${a.last_name || ''}`.trim() || 'Person';
                    const safePersonId = encodeURIComponent(String(a.id || ''));
                    const pid = String(a.id || '').trim();
                    const oi =
                        a.order_index != null && a.order_index !== ''
                            ? String(a.order_index)
                            : '0';
                    const roleAttr = escapeHtml(rt);
                    return `<span class="work-linked-persons__chip tag"><a class="work-linked-persons__chip-link" href="#/people/${safePersonId}">👤 ${escapeHtml(name)}</a><button type="button" class="work-linked-persons__unlink" aria-label="Remove link from this file" data-work-id="${escapeHtml(wid)}" data-person-id="${escapeHtml(pid)}" data-role-type="${roleAttr}" data-order-index="${escapeHtml(oi)}" onclick="event.stopPropagation(); void prksRemoveWorkRoleLink(this);">×</button></span>`;
                })
                .join(' ');
            return `<div class="work-linked-persons__role"><h4 class="work-linked-persons__role-title">${escapeHtml(rt)}</h4><div class="tag-cloud">${chips}</div></div>`;
        })
        .join('');
}

async function prksRemoveWorkRoleLink(btn) {
    if (!btn) return;
    const workId = (btn.getAttribute('data-work-id') || '').trim();
    const personId = (btn.getAttribute('data-person-id') || '').trim();
    const roleType = (btn.getAttribute('data-role-type') || '').trim();
    const orderIndex = (btn.getAttribute('data-order-index') || '0').trim();
    if (!workId || !personId || !roleType) {
        alert('Missing link data.');
        return;
    }
    if (!window.confirm('Remove this person from the file for this role?')) return;
    const params = new URLSearchParams({
        person_id: personId,
        role_type: roleType,
        order_index: orderIndex || '0',
    });
    let res;
    try {
        res = await fetch(`/api/works/${encodeURIComponent(workId)}/roles?${params}`, { method: 'DELETE' });
    } catch (e) {
        console.error(e);
        alert('Could not remove link.');
        return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        alert(data.error || 'Could not remove link.');
        return;
    }
    await prksRefreshUiAfterWorkRoleRemoved(workId);
}

async function prksRefreshUiAfterWorkRoleRemoved(workId) {
    const hash = window.location.hash || '';
    const wIdStr = String(workId);
    if (
        hash.startsWith('#/works/') &&
        window.currentWork &&
        String(window.currentWork.id) === wIdStr
    ) {
        if (typeof fetchWorkDetails === 'function') {
            window.currentWork = await fetchWorkDetails(wIdStr);
            const panel = document.getElementById('panel-content');
            const tab =
                typeof getActiveRightPanelTab === 'function' ? getActiveRightPanelTab() : 'details';
            if (panel && tab === 'details' && typeof prksWorkRightPanelStackHtml === 'function') {
                panel.innerHTML = prksWorkRightPanelStackHtml(window.currentWork, false);
                if (typeof initPrksPrivateNotesEditor === 'function') {
                    initPrksPrivateNotesEditor('work', window.currentWork.id);
                }
                if (typeof initWorkTagCombobox === 'function') initWorkTagCombobox(window.currentWork.id);
                if (typeof mountPlaylistAttachControls === 'function') {
                    void mountPlaylistAttachControls(window.currentWork);
                }
                if (typeof mountFolderAttachControlsForWork === 'function') {
                    void mountFolderAttachControlsForWork(window.currentWork);
                }
                if (typeof prksRemountWorkContextGraph === 'function') {
                    prksRemountWorkContextGraph(window.currentWork);
                }
                if (typeof initWorkDetailRightPanelActions === 'function') {
                    initWorkDetailRightPanelActions(window.currentWork);
                }
            }
        }
        return;
    }
    if (hash.startsWith('#/people/') && !hash.includes('/groups')) {
        const parts = hash.split('/');
        const personId = parts[2] ? decodeURIComponent(parts[2]) : '';
        if (personId && typeof fetchPersonDetails === 'function') {
            const container = document.getElementById('page-content');
            try {
                const person = await fetchPersonDetails(personId);
                if (person && container && typeof renderPersonDetails === 'function') {
                    renderPersonDetails(person, container);
                }
            } catch (e) {
                console.error(e);
            }
        }
    }
}

function renderWorkMetaTab(work, isEditing = false) {
    if (isEditing) return renderWorkMetaEditTab(work);

    const renderRow = (label, val) =>
        val ? `<p class="meta-row"><strong>${escapeHtml(label)}:</strong> ${escapeHtml(val)}</p>` : '';
    const showPublishedDate = !(work.year && String(work.year).trim());
    const inferredKindView = typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';
    const originalUrlPdf = inferredKindView === 'pdf' ? String(work.source_url || '').trim() : '';
    const statusRaw = String(work.status || '').trim();
    const statusText = statusRaw || 'Not Started';
    const statusClass = statusText.replace(/[^A-Za-z0-9 ]+/g, ' ').trim().replace(/\s+/g, ' ');
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
                <button onclick="toggleWorkMetaEdit(true)" class="inline-action-btn">Edit</button>
            </div>
            <p class="card-title">${escapeHtml(work.title)}</p>
            <div class="card-heading-row card-heading-row--wrap">
                <span class="meta-row">Status</span>
                <span class="status-badge ${statusClass}">${escapeHtml(statusText)}</span>
            </div>
            <div class="card-heading-row card-heading-row--wrap">
                <span class="meta-row">Document type</span>
                ${typeof prksDocTypeBadgeHtml === 'function' ? prksDocTypeBadgeHtml(work.doc_type) : ''}
            </div>
        </div>
        <div class="doc-meta-card">
            <h3>Metadata</h3>
            ${renderRow('Year', work.year)}
            ${showPublishedDate ? renderRow('Published', work.published_date) : ''}
            ${
                work.publisher
                    ? `<p class="meta-row"><strong>Publisher:</strong> <a href="#/search?publisher=${encodeURIComponent(String(work.publisher).trim())}" class="route-sidebar__link">${escapeHtml(work.publisher)}</a></p>`
                    : ''
            }
            ${renderRow('Location', work.location)}
            ${renderRow('Edition', work.edition)}
            ${renderRow('Journal', work.journal)}
            ${renderRow('Volume', work.volume)}
            ${renderRow('Issue', work.issue)}
            ${renderRow('Pages', work.pages)}
            ${renderRow('ISBN', work.isbn)}
            ${renderRow('DOI', work.doi)}
            ${
                originalUrlPdf
                    ? `<p class="meta-row"><strong>Original URL:</strong> <a href="${escapeHtml(originalUrlPdf)}" target="_blank" rel="noopener noreferrer">${escapeHtml(originalUrlPdf)}</a></p>`
                    : ''
            }
            ${renderRow('Abstract', work.abstract)}
            ${!hasMetadata ? '<p class="meta-row meta-row--muted-italic">No metadata available.</p>' : ''}
        </div>
        <div class="doc-meta-card">
            <div class="card-heading-row card-heading-row--wrap">
                <h3>Linked Persons</h3>
                <button type="button" class="work-link-person-btn" onclick="openModal('role-modal')" title="Link a person to this file (search or quick-create)">Link person</button>
            </div>
            <div class="work-linked-persons-by-role">${buildWorkLinkedPersonsHtml(work)}</div>
        </div>
        <div class="doc-meta-card">
            <h3>Tags</h3>
            <div id="work-tags-list" class="tag-cloud work-tags-list">
                ${renderWorkTagsChips(work)}
            </div>
            <p class="tag-add-field__caption">Add a tag</p>
            <div class="tag-add-shell combobox-container">
                <div class="tag-add-shell__field">
                    <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
                    <input type="text" id="work-tag-search" class="tag-add-shell__input" placeholder="Search tags or type a new name…" maxlength="120" autocomplete="off" aria-label="Search or add tag">
                </div>
                <div id="work-tag-search-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
        </div>
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
                    <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
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
                <button type="button" class="ribbon-btn form-actions__btn" id="prks-folder-parent-edit-btn" aria-expanded="${
                    parentEditing ? 'true' : 'false'
                }">${parentEditing ? 'Done' : 'Move folder'}</button>
            </div>
            <p class="meta-row">${parentLine}</p>
            ${childrenLine}
            <div id="prks-folder-parent-edit-wrap" class="${parentEditing ? '' : 'hidden'}">
                <div class="combobox-container">
                    <div class="tag-add-shell">
                        <div class="tag-add-shell__field">
                            <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                            <input type="text" id="prks-folder-parent-search" class="tag-add-shell__input" placeholder="Search destination folder…" autocomplete="off" aria-label="Search destination folder">
                        </div>
                    </div>
                    <input type="hidden" id="prks-folder-parent-id" value="">
                    <div id="prks-folder-parent-results" class="combobox-results hidden"></div>
                </div>
                <div class="prks-work-folder-controls" style="margin-top:10px;">
                    <button type="button" class="add-new-btn" id="prks-folder-parent-save-btn">Move here</button>
                    ${
                        folder.parent
                            ? '<button type="button" class="ribbon-btn form-actions__btn" id="prks-folder-parent-top-btn">Make top-level</button>'
                            : ''
                    }
                </div>
                <p id="prks-folder-parent-status" class="meta-row meta-row--spaced" aria-live="polite"></p>
            </div>
        </div>
        <div class="doc-meta-card">
            <div class="card-heading-row">
                <h3>Folder files</h3>
                <button type="button" class="ribbon-btn form-actions__btn" id="prks-folder-library-edit-btn">${editLabel}</button>
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
                    <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
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
                `onclick="window.location.hash='#/search?tag='+this.getAttribute('data-tag-nav')" ` +
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

function prksSegmentedControlHtml(hiddenId, ariaLabel, labels, selectedValue, variant) {
    const labelsArr = Array.isArray(labels) ? labels : [];
    const fallback = labelsArr[0] || '';
    const selRaw = selectedValue != null ? String(selectedValue) : '';
    const sel = labelsArr.includes(selRaw) ? selRaw : fallback;
    const segMod =
        variant === 'status'
            ? ' prks-segmented--status prks-segmented--single-row'
            : variant === 'roles'
              ? ' prks-segmented--roles'
              : '';
    const buttons = labelsArr
        .map((l) => {
            const active = l === sel ? ' prks-segmented__btn--active' : '';
            const pressed = l === sel ? 'true' : 'false';
            return `<button type="button" class="prks-segmented__btn${active}" data-value="${prksEscapeAttr(l)}" aria-pressed="${pressed}" role="radio">${escapeHtml(l)}</button>`;
        })
        .join('');
    const wrapMod = variant === 'status' ? ' prks-segmented-wrap--status-row' : '';
    return `<div class="prks-segmented-wrap${wrapMod}">
    <input type="hidden" id="${prksEscapeAttr(hiddenId)}" value="${prksEscapeAttr(sel)}">
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

function renderWorkTagsChips(work) {
    const tags = work.tags || [];
    if (tags.length === 0) {
        return '<span class="work-tags-empty">No tags yet.</span>';
    }
    return tags
        .map(
            (t) =>
                `<span class="tag work-tag-chip work-tag-chip--colored" style="--tag-accent:${escapeHtml(t.color || '#6d6cf7')};" ` +
                `role="button" tabindex="0" data-tag-nav="${encodeURIComponent(t.name)}" ` +
                `onclick="window.location.hash='#/search?tag='+this.getAttribute('data-tag-nav')" ` +
                `onkeydown="if(event && (event.key==='Enter' || event.key===' ')) {event.preventDefault(); this.click();}">` +
                `${escapeHtml(t.name)}` +
                `<button type="button" class="work-tag-remove" title="Remove tag" aria-label="Remove" data-work-id="${escapeHtml(work.id)}" data-tag-id="${escapeHtml(t.id)}">×</button>` +
                `</span>`
        )
        .join('');
}

async function prksReloadEntityTagsUI(entityType, entityId) {
    if (entityType === 'work') {
        window.currentWork = await fetchWorkDetails(entityId);
        const panel = document.getElementById('panel-content');
        if (panel && getActiveRightPanelTab() === 'details' && window.currentWork && window.currentWork.id === entityId) {
            if (typeof destroyPrksContextGraph === 'function') {
                destroyPrksContextGraph();
            }
            panel.innerHTML = prksWorkRightPanelStackHtml(window.currentWork, false);
            initPrksPrivateNotesEditor('work', entityId);
            initWorkTagCombobox(entityId);
            if (typeof mountPlaylistAttachControls === 'function') {
                void mountPlaylistAttachControls(window.currentWork);
            }
            if (typeof mountFolderAttachControlsForWork === 'function') {
                void mountFolderAttachControlsForWork(window.currentWork);
            }
            prksRemountWorkContextGraph(window.currentWork);
            if (typeof initWorkDetailRightPanelActions === 'function') {
                initWorkDetailRightPanelActions(window.currentWork);
            }
        }
    } else {
        window.currentFolder = await fetchFolderDetails(entityId);
        const contentDiv = document.getElementById('page-content');
        if (contentDiv && window.location.hash === '#/folders/' + entityId) {
            renderFolderDetails(window.currentFolder, contentDiv);
        }
        const panel = document.getElementById('panel-content');
        if (panel && getActiveRightPanelTab() === 'details' && window.currentFolder && window.currentFolder.id === entityId) {
            panel.innerHTML = prksFolderRightPanelStackHtml(window.currentFolder);
            initPrksPrivateNotesEditor('folder', entityId);
            initFolderTagCombobox(entityId);
            if (typeof mountFolderLibraryAttachControls === 'function') {
                void mountFolderLibraryAttachControls(window.currentFolder);
            }
        }
    }
    refreshSidebarTags();
}

async function prksAttachExistingTag(entityType, entityId, tagId) {
    try {
        const url =
            entityType === 'work' ? `/api/works/${entityId}/tags` : `/api/folders/${entityId}/tags`;
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag_id: tagId }),
        });
        if (!res.ok) throw new Error('attach failed');
        window.__prksAllTagsCache = null;
        const input = document.getElementById(entityType === 'work' ? 'work-tag-search' : 'folder-tag-search');
        if (input) input.value = '';
        await prksReloadEntityTagsUI(entityType, entityId);
    } catch (e) {
        console.error(e);
        alert('Could not add tag.');
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

async function prksSubmitNewTag(entityType, entityId, name) {
    const trimmed = (name || '').trim();
    if (!trimmed) return;
    try {
        const res = await fetch('/api/tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: trimmed, color: '#6d6cf7' }),
        });
        const data = await res.json();
        if (!res.ok || !data.id) throw new Error(data.error || 'No tag id');
        window.__prksAllTagsCache = null;
        await prksAttachExistingTag(entityType, entityId, data.id);
    } catch (e) {
        console.error(e);
        alert('Could not create tag.');
    }
}

function initTagComboboxForEntity(entityType, entityId, inputId, resultsId) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);
    if (!input || !results) return;

    function getAttachedIds() {
        const ent = entityType === 'work' ? window.currentWork : window.currentFolder;
        if (!ent || ent.id !== entityId) return new Set();
        return new Set((ent.tags || []).map((t) => t.id));
    }

    async function renderDropdown() {
        if (!window.__prksAllTagsCache) {
            window.__prksAllTagsCache = await fetchTags({ used: false });
        }
        const all = window.__prksAllTagsCache;
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
                prksSubmitNewTag(entityType, entityId, val);
            };
            results.appendChild(c);
        }
        filtered.forEach((tag) => {
            const div = document.createElement('div');
            div.className = 'result-item';
            div.textContent = prksTagComboboxLabel(tag, valLower);
            div.onmousedown = (ev) => {
                ev.preventDefault();
                prksAttachExistingTag(entityType, entityId, tag.id);
            };
            results.appendChild(div);
        });
        results.classList.toggle('hidden', results.childElementCount === 0);
    }

    input.onfocus = async () => {
        window.__prksAllTagsCache = await fetchTags({ used: false });
        renderDropdown();
    };
    input.oninput = () => renderDropdown();
    input.onblur = () =>
        setTimeout(() => {
            results.classList.add('hidden');
        }, 200);
}

function initWorkTagCombobox(workId) {
    if (!window.currentWork || window.currentWork.id !== workId) return;
    initTagComboboxForEntity('work', workId, 'work-tag-search', 'work-tag-search-results');
}

function initFolderTagCombobox(folderId) {
    if (!window.currentFolder || window.currentFolder.id !== folderId) return;
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
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            });
        }
        results.classList.remove('hidden');
    }

    input.onfocus = () => renderDropdown();
    input.oninput = () => {
        hidden.value = '';
        renderDropdown();
    };
    input.onblur = () =>
        setTimeout(() => {
            results.classList.add('hidden');
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
            results.classList.remove('hidden');
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
                    btn.className = 'ribbon-btn';
                    btn.style.marginTop = '0';
                    btn.textContent = 'Add';
                    btn.onclick = async () => {
                        try {
                            if (typeof addWorkToFolder !== 'function') return;
                            await addWorkToFolder(fid, wid);
                            if (status) status.textContent = 'Added.';
                            await prksReloadEntityTagsUI('folder', fid);
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
                    btn.className = 'ribbon-btn';
                    btn.style.marginTop = '0';
                    btn.textContent = 'Move here';
                    btn.onclick = async () => {
                        try {
                            if (typeof patchWorkFolder !== 'function') return;
                            await patchWorkFolder(wid, fid);
                            if (status) status.textContent = 'Moved.';
                            await prksReloadEntityTagsUI('folder', fid);
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
        results.classList.remove('hidden');
    }

    input.onfocus = () => void runSearch();
    input.oninput = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => void runSearch(), 280);
    };
    input.onblur = () => setTimeout(() => results.classList.add('hidden'), 180);
}

async function prksRemoveWorkTag(workId, tagId) {
    try {
        await fetch(
            `/api/works/${encodeURIComponent(workId)}/tags/${encodeURIComponent(tagId)}`,
            { method: 'DELETE' }
        );
        window.__prksAllTagsCache = null;
        await prksReloadEntityTagsUI('work', workId);
    } catch (e) {
        console.error(e);
        alert('Could not remove tag.');
    }
}

function renderWorkMetaEditTab(work) {
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
            ? prksDocTypeMenuShellHtml('meta-doc-type', metaDocNorm, isVideo)
            : '';
    const dateLabel = isVideo ? 'Published date' : 'Published Date';
    const publishedType = isVideo ? 'text' : 'date';
    const publishedPlaceholder = isVideo ? 'dd/mm/yyyy' : '';
    const publishedInputMode = isVideo ? ' inputmode="numeric" autocomplete="off"' : '';
    const channelField = isVideo
        ? `
            <label for="meta-author-text">Channel name</label>
            <input type="text" id="meta-author-text" value="${safeStr(work.author_text)}" autocomplete="off">
        `
        : '';
    const bibFields = isVideo
        ? ''
        : `
            <label for="meta-publisher">Publisher</label>
            <input type="text" id="meta-publisher" value="${safeStr(work.publisher)}">

            <label for="meta-location">Location (place of publication)</label>
            <input type="text" id="meta-location" value="${safeStr(work.location)}" placeholder="e.g. Cambridge, UK or Paris; Berlin" autocomplete="off">
            <p class="meta-row meta-row--hint">Separate multiple places with semicolons; BibLaTeX export joins them with &quot; and &quot;.</p>
            
            <label for="meta-edition">Edition</label>
            <input type="text" id="meta-edition" value="${safeStr(work.edition)}" placeholder="e.g. 2 or revised" autocomplete="off">
            
            <label for="meta-journal">Journal</label>
            <input type="text" id="meta-journal" value="${safeStr(work.journal)}">
            
            <div class="form-grid-2 form-grid-2--compact">
                <div><label for="meta-volume">Volume</label><input type="text" id="meta-volume" value="${safeStr(work.volume)}"></div>
                <div><label for="meta-issue">Issue</label><input type="text" id="meta-issue" value="${safeStr(work.issue)}"></div>
            </div>
            
            <div class="form-grid-2 form-grid-2--compact">
                <div><label for="meta-pages">Pages</label><input type="text" id="meta-pages" value="${safeStr(work.pages)}"></div>
                <div><label for="meta-isbn">ISBN</label><input type="text" id="meta-isbn" value="${safeStr(work.isbn)}"></div>
            </div>
            
            <label for="meta-doi">DOI</label>
            <input type="text" id="meta-doi" value="${safeStr(work.doi)}">
            
            <label for="meta-source-url">Original URL (optional)</label>
            <input type="url" id="meta-source-url" placeholder="https://…" value="${safeStr(work.source_url)}" autocomplete="off">
            <p class="meta-row meta-row--hint">Online location if this file was converted or downloaded from the web.</p>
        `;
    const thumbField = isVideo
        ? ''
        : `
            <label for="meta-thumb-page">Thumbnail page</label>
            <input type="number" id="meta-thumb-page" min="1" step="1" inputmode="numeric" placeholder="1" value="${safeStr(thumbPage)}">
        `;

    return `
        <div class="doc-meta-card form-pane doc-meta-card--editing">
            <div class="card-heading-row">
                <h3 class="doc-meta-card__accent-title">Edit Metadata</h3>
                <button onclick="toggleWorkMetaEdit(false)" class="inline-action-btn inline-action-btn--close">&times;</button>
            </div>
            
            <label for="meta-title">Title</label>
            <input type="text" id="meta-title" value="${safeStr(work.title)}">
            
            <label for="meta-status">Status</label>
            ${prksSegmentedControlHtml('meta-status', 'Status', PRKS_WORK_STATUS_LABELS, work.status, 'status')}

            <label for="meta-doc-type-trigger">Document type (BibLaTeX)</label>
            ${metaDocMenu}
            
            <div class="form-grid-2 form-grid-2--compact">
                <div><label for="meta-year">Year</label><input type="text" id="meta-year" value="${safeStr(work.year)}"></div>
                <div><label for="meta-date">${dateLabel}</label><input type="${publishedType}" id="meta-date" value="${safeStr(work.published_date)}" placeholder="${publishedPlaceholder}"${publishedInputMode}></div>
            </div>
            ${channelField}
            
            ${bibFields}
            ${thumbField}
            
            <label for="meta-abstract">Abstract</label>
            <textarea id="meta-abstract" class="textarea-md">${safeStr(work.abstract)}</textarea>
            
            <div class="card-heading-row card-heading-row--wrap">
                <h4>Linked Persons</h4>
            </div>
            <p class="meta-row meta-row--hint">Search a person, choose a role (including <strong>Translator</strong>), then click <strong>+ Link</strong>.</p>
            <input type="hidden" id="meta-role-work-id" value="${safeStr(work.id)}">
            <div class="prks-upload-person-stack">
                <div class="form-row prks-upload-person-stack__search">
                    <div class="prks-combobox-with-action">
                        <div class="combobox-container">
                            <div class="tag-add-shell">
                                <div class="tag-add-shell__field">
                                    <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                                    <input type="text" id="meta-role-person-search" class="tag-add-shell__input" placeholder="Search person..." autocomplete="off" aria-label="Search person for role link">
                                </div>
                            </div>
                            <input type="hidden" id="meta-role-person-id">
                            <div id="meta-role-person-results" class="combobox-results hidden"></div>
                        </div>
                    </div>
                    <button type="button" id="meta-role-add-btn" onclick="addRoleToWorkFromMetaEditor('${work.id}')" class="ribbon-btn">+ Link</button>
                </div>
                <div class="prks-upload-person-stack__roles">
                    <div class="prks-upload-person-stack__role-caption">Role</div>
                    <div class="prks-upload-role-seg">
                        ${prksSegmentedControlHtml('meta-role-type', 'Role for linked person', PRKS_UPLOAD_ROLE_LABELS, 'Author', 'roles')}
                    </div>
                </div>
            </div>
            <div id="meta-linked-persons-list" class="work-linked-persons-by-role">${buildWorkLinkedPersonsHtml(work)}</div>
            
            <div class="form-actions">
                <button class="ribbon-btn form-actions__btn form-actions__btn--secondary" onclick="toggleWorkMetaEdit(false)">Cancel</button>
                <button id="inline-save-metadata-btn" class="add-new-btn form-actions__btn form-actions__btn--primary" onclick="submitWorkMetaEdit('${work.id}')">Save Changes</button>
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
    return `<div class="route-sidebar__title-row"><h2 class="route-sidebar__title">${titleInnerHtml}</h2>${btn}</div>`;
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
                        <button type="button" class="ribbon-btn" onclick="window.closePdfAnnotationEditor && window.closePdfAnnotationEditor()">Cancel</button>
                        <button type="button" class="add-new-btn" onclick="window.savePdfAnnotationComment && window.savePdfAnnotationComment()">Save comment</button>
                    </div>
                </div>
            </section>
        </div>
    `;
}

// Side-bar Tag Cloud (only tags attached to at least one work or folder)
async function refreshSidebarTags() {
    let tags;
    try {
        tags = await fetchTags({ recent: true, limit: 8 });
    } catch (err) {
        console.error('refreshSidebarTags: could not load tags', err);
        return;
    }
    const cloud = document.getElementById('sidebar-tag-cloud');
    if (!cloud) return;

    const allTagsLink =
        '<a href="#/tags" class="tag tag--sidebar tag--sidebar-all" aria-label="View all tags">…all tags</a>';

    if (tags.length === 0) {
        cloud.innerHTML =
            '<span class="sidebar-tag-cloud-empty">No recent tags.</span> ' + allTagsLink;
        return;
    }

    cloud.innerHTML =
        tags
            .map(
                (t) =>
                    `<span class="tag tag--sidebar tag--sidebar-colored" style="--tag-accent:${escapeHtml(t.color || '#6d6cf7')};" ` +
                    `role="button" tabindex="0" data-sidebar-tag-q="${encodeURIComponent(t.name)}">${escapeHtml(t.name)}</span>`
            )
            .join('') + allTagsLink;

    cloud.onclick = (e) => {
        const el = e.target.closest('[data-sidebar-tag-q]');
        if (!el) return;
        const q = el.getAttribute('data-sidebar-tag-q');
        if (q != null) window.location.hash = '#/search?tag=' + q;
    };
    cloud.onkeydown = (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const el = e.target.closest('[data-sidebar-tag-q]');
        if (!el) return;
        e.preventDefault();
        const q = el.getAttribute('data-sidebar-tag-q');
        if (q != null) window.location.hash = '#/search?tag=' + q;
    };
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
        if (!window.__prksAllTagsCache) {
            window.__prksAllTagsCache = await fetchTags({ used: false });
        }
        const all = window.__prksAllTagsCache;
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
                        const res = await fetch('/api/tags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: val, color: '#6d6cf7' }),
                        });
                        const data = await res.json();
                        if (!res.ok || !data.id) throw new Error(data.error || 'no id');
                        window.__prksAllTagsCache = null;
                        if (!attachedIds().has(data.id)) {
                            uploadTagsSelected.push({ id: data.id, name: data.name || val });
                            renderUploadTagsChips();
                        }
                        input.value = '';
                        results.classList.add('hidden');
                    } catch (e) {
                        console.error(e);
                        alert('Could not create tag.');
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
                results.classList.add('hidden');
            };
            results.appendChild(div);
        });
        results.classList.toggle('hidden', results.childElementCount === 0);
    }

    input.onfocus = async () => {
        window.__prksAllTagsCache = await fetchTags({ used: false });
        renderDropdown();
    };
    input.oninput = () => renderDropdown();
    input.onblur = () =>
        setTimeout(() => {
            results.classList.add('hidden');
        }, 200);
}

function prksTeardownUploadEmbedViewer() {
    if (
        typeof window.__prksEmbedPdfSelectionAssistDetach === 'function' &&
        window.__prksEmbedPdfSelectionAssistViewer === window.uploadViewer
    ) {
        try {
            window.__prksEmbedPdfSelectionAssistDetach();
        } catch (_e) {}
    }
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
        if (typeof window.prksDetachHideEmbedPdfErrorCloseButton === 'function') {
            window.prksDetachHideEmbedPdfErrorCloseButton(viewer);
        }
        viewer.innerHTML = '';
        viewer.classList.add('hidden');
    }
    const actions = document.getElementById('upload-pdf-preview-actions');
    if (actions) actions.classList.add('hidden');
    const prompt = document.getElementById('drop-zone-prompt');
    if (prompt) prompt.classList.remove('hidden');
    const f = document.getElementById('work-file');
    if (f) f.value = '';
    window.__prksPendingUploadPdfFile = null;
}

function resetUploadModal() {
    uploadRoles = [];
    uploadTagsSelected = [];
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
    document.getElementById('work-abstract').value = '';
    const f = document.getElementById('work-file');
    if (f) f.value = '';
    const vid = document.getElementById('work-video-url');
    if (vid) vid.value = '';
    document.getElementById('work-folder-id').value = '';
    document.getElementById('work-folder-search').value = '';
    document.getElementById('upload-person-id').value = '';
    document.getElementById('upload-person-search').value = '';
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
    });
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

function initSearchableCombobox(inputId, resultsId, hiddenId, type, comboboxOptions = {}) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);
    const hidden = document.getElementById(hiddenId);
    if (!input || !results) return;

    const excludePersonIds =
        comboboxOptions.excludePersonIds instanceof Set ? comboboxOptions.excludePersonIds : null;

    input.onfocus = () => renderResults();
    input.oninput = () => {
        hidden.value = '';
        renderResults();
    };
    
    // Hide results when focus moves away
    input.onblur = () => {
        // Delay hide to allow clicks on result items to fire first
        setTimeout(() => {
            results.classList.add('hidden');
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
            const label = item.title || '';
            return label.toLowerCase().includes(val);
        });

        results.innerHTML = '';
        if (type === 'person' && valRaw.trim() && typeof comboboxOptions.onQuickCreate === 'function') {
            const create = document.createElement('div');
            create.className = 'result-item result-item--create';
            create.textContent = `Quick-create person \"${valRaw.trim()}\"`;
            create.onmousedown = (ev) => {
                ev.preventDefault();
                results.classList.add('hidden');
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
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            });
        }
        results.classList.remove('hidden');
    }
}

async function quickCreateFolder() {
    const title = document.getElementById('work-folder-search').value;
    if (!title) return alert("Please enter a folder title first");
    
    const payload = { title: title, description: "Quick created via upload" };
    const res = await fetch('/api/folders', { method: 'POST', body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        alert(data.error || 'Could not create folder');
        return;
    }

    allFolders = await fetchFolders(); // Refresh cache
    document.getElementById('work-folder-id').value = data.id;
    document.getElementById('work-folder-search').value = title;
    document.getElementById('folder-results').classList.add('hidden');
}

function addRoleToUploadList() {
    const hidden = document.getElementById('upload-person-id');
    const input = document.getElementById('upload-person-search');
    const rSelect = document.getElementById('upload-role-type');
    
    if (!hidden.value) {
        alert("Please select a person from the search results or create a new one first.");
        return;
    }
    
    const pName = input.value;
    const rType = rSelect.value;
    
    uploadRoles.push({ person_id: hidden.value, person_name: pName, role_type: rType });
    renderUploadRoles();
    
    // Clear person input for next author
    hidden.value = "";
    input.value = "";
}


function renderUploadRoles() {
    const container = document.getElementById('upload-roles-list');
    if (uploadRoles.length === 0) {
        container.innerHTML = '<span class="status-chip-list__empty">No persons linked yet</span>';
        return;
    }
    container.innerHTML = uploadRoles.map((r, idx) => `
        <span class="tag author-tag">👤 ${escapeHtml(r.person_name)} (${escapeHtml(r.role_type)}) <i onclick="removeUploadRole(${idx})" class="status-chip-remove">&times;</i></span>
    `).join(' ');
}

function removeUploadRole(idx) {
    uploadRoles.splice(idx, 1);
    renderUploadRoles();
}

function initUploadDragAndDrop() {
    const zone = document.getElementById('upload-drop-zone');
    const input = document.getElementById('work-file');
    if (!zone) return;

    if (zone.dataset.prksClickBound !== '1') {
        zone.dataset.prksClickBound = '1';
        zone.addEventListener('click', (e) => {
            const viewer = document.getElementById('upload-viewer');
            const prompt = document.getElementById('drop-zone-prompt');
            const actions = document.getElementById('upload-pdf-preview-actions');
            if (e.target.closest('#upload-pdf-remove-btn')) {
                return;
            }
            let clickInsideViewer = false;
            if (viewer && !viewer.classList.contains('hidden')) {
                if (typeof e.composedPath === 'function') {
                    const path = e.composedPath();
                    clickInsideViewer = path.includes(viewer);
                }
                if (!clickInsideViewer) {
                    clickInsideViewer = viewer.contains(e.target);
                }
            }
            if (clickInsideViewer) {
                return;
            }
            if (actions && !actions.classList.contains('hidden') && actions.contains(e.target)) {
                return;
            }
            if (prompt && !prompt.classList.contains('hidden')) {
                input.click();
                return;
            }
            if (viewer && !viewer.classList.contains('hidden')) {
                return;
            }
            input.click();
        });
    }

    const removePdfBtn = document.getElementById('upload-pdf-remove-btn');
    if (removePdfBtn && removePdfBtn.dataset.bound !== '1') {
        removePdfBtn.dataset.bound = '1';
        removePdfBtn.addEventListener('click', (e) => {
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
                window.prksSyncUploadModalKindUi();
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
                kind.value = next;
                // Fire a change event so existing logic reacts.
                try {
                    kind.dispatchEvent(new Event('change', { bubbles: true }));
                } catch (_e) {
                    if (typeof window.prksSyncUploadModalKindUi === 'function') {
                        window.prksSyncUploadModalKindUi();
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
        window.prksSyncUploadModalKindUi = function () {
            const kindEl = document.getElementById('work-source-kind');
            const toggleBtns = Array.from(document.querySelectorAll('.prks-kind-toggle__btn[data-kind]'));
            const vrow = document.getElementById('work-video-url-row');
            const pdfUrlRow = document.getElementById('work-pdf-source-url-row');
            const dropLabel = document.getElementById('drop-zone-label');
            const prompt = document.getElementById('drop-zone-prompt');
            const viewer = document.getElementById('upload-viewer');
            const fileInput = document.getElementById('work-file');
            const docType = document.getElementById('work-doc-type');
            const urlDate = document.getElementById('work-video-urldate');
            const kindVal = kindEl ? String(kindEl.value || 'pdf') : 'pdf';

            // Sync toggle button active state.
            if (toggleBtns.length) {
                toggleBtns.forEach((b) => {
                    const k = String(b.getAttribute('data-kind') || '').trim();
                    const active = k === kindVal;
                    b.classList.toggle('is-active', active);
                    b.setAttribute('aria-selected', active ? 'true' : 'false');
                });
            }

            if (vrow) vrow.classList.toggle('hidden', kindVal !== 'video');
            if (pdfUrlRow) pdfUrlRow.classList.toggle('hidden', kindVal !== 'pdf');

            const pdfMeta = document.getElementById('work-upload-pdf-only-meta');
            if (pdfMeta) pdfMeta.classList.toggle('hidden', kindVal !== 'pdf');
            const pubCol = document.getElementById('work-upload-published-date-col');
            if (pubCol) pubCol.classList.toggle('hidden', kindVal !== 'pdf');

            const pdfActions = document.getElementById('upload-pdf-preview-actions');
            if (pdfActions) pdfActions.classList.add('hidden');
            if (viewer && typeof window.prksDetachHideEmbedPdfErrorCloseButton === 'function') {
                window.prksDetachHideEmbedPdfErrorCloseButton(viewer);
            }
            if (kindVal === 'video' && window.__prksUploadPdfBlobUrl) {
                try {
                    URL.revokeObjectURL(window.__prksUploadPdfBlobUrl);
                } catch (_e) {}
                window.__prksUploadPdfBlobUrl = null;
                prksTeardownUploadEmbedViewer();
            }
            if (kindVal === 'video') {
                window.__prksPendingUploadPdfFile = null;
            }
            if (viewer) viewer.innerHTML = '';
            if (viewer) viewer.classList.add('hidden');
            if (prompt) prompt.classList.remove('hidden');
            window.__prksLastVideoPreviewUrl = '';
            window.__prksUploadVideoMeta = null;

            if (dropLabel) {
                if (kindVal === 'pdf') dropLabel.innerHTML = 'Drag & Drop a PDF here<br><span class="drop-zone__label-sub">or click to browse</span>';
                else dropLabel.innerHTML = 'Video URL mode<br><span class="drop-zone__label-sub">Paste a link on the right</span>';
            }
            if (fileInput) fileInput.disabled = kindVal === 'video';

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
            if (!viewer || !prompt) return;

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
            prompt.classList.remove('hidden');

            if (!url) {
                window.__prksLastVideoPreviewUrl = '';
                return;
            }

            let embedUrl = '';
            try {
                const u = new URL(url);
                const host = (u.hostname || '').toLowerCase();
                if (host.includes('youtu.be')) {
                    const id = u.pathname.replace(/^\//, '').split('/')[0];
                    if (id) embedUrl = `https://www.youtube.com/embed/${encodeURIComponent(id)}`;
                } else if (host.includes('youtube.com')) {
                    const v = u.searchParams.get('v') || '';
                    if (v) embedUrl = `https://www.youtube.com/embed/${encodeURIComponent(v)}`;
                    const parts = u.pathname.replace(/^\//, '').split('/');
                    if (!embedUrl && parts[0] === 'embed' && parts[1]) {
                        embedUrl = `https://www.youtube.com/embed/${encodeURIComponent(parts[1])}`;
                    }
                }
            } catch (_e) {}

            if (embedUrl) {
                if (pdfActions) pdfActions.classList.add('hidden');
                prompt.classList.add('hidden');
                viewer.classList.remove('hidden');
                viewer.innerHTML =
                    `<div class="prks-video-preview">` +
                    `<div class="prks-video-preview__frame">` +
                    `<iframe src="${embedUrl}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>` +
                    `</div>` +
                    `</div>`;
            }

            try {
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
                const res = await fetch('/api/playlists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: t, description: '' }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.id) throw new Error(data.error || 'create failed');
                playlists = await loadPlaylists();
                return { id: data.id, title: t };
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
                                results.classList.add('hidden');
                            }
                        } catch (_e) {
                            alert('Could not create playlist.');
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
                            results.classList.add('hidden');
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
            input.onblur = () => setTimeout(() => results.classList.add('hidden'), 180);
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
        alert('In Video URL mode, paste the link on the right.');
        return;
    }
    if (!file) return;
    const name = String(file.name || '').toLowerCase();
    const isPdf = name.endsWith('.pdf') || file.type === 'application/pdf';
    if (kind === 'pdf' && !isPdf) {
        alert("Please select a valid PDF file.");
        return;
    }
    
    // Preview Logic
    const viewerContainer = document.getElementById('upload-viewer');
    const prompt = document.getElementById('drop-zone-prompt');
    const pdfActions = document.getElementById('upload-pdf-preview-actions');

    if (!viewerContainer || !prompt) return;

    if (window.__prksUploadPdfBlobUrl) {
        try {
            URL.revokeObjectURL(window.__prksUploadPdfBlobUrl);
        } catch (_e) {}
        window.__prksUploadPdfBlobUrl = null;
    }
    prksTeardownUploadEmbedViewer();
    if (viewerContainer && typeof window.prksDetachHideEmbedPdfErrorCloseButton === 'function') {
        window.prksDetachHideEmbedPdfErrorCloseButton(viewerContainer);
    }
    if (viewerContainer) viewerContainer.innerHTML = '';

    prompt.classList.add('hidden');
    viewerContainer.classList.remove('hidden');
    if (pdfActions) pdfActions.classList.remove('hidden');

    window.__prksPendingUploadPdfFile = file;
    const url = URL.createObjectURL(file);
    window.__prksUploadPdfBlobUrl = url;
    Promise.all([
        import('/js/components/works-pdf.js'),
        import('https://cdn.jsdelivr.net/npm/@embedpdf/snippet@2/dist/embedpdf.js'),
    ])
        .then(([, embedModule]) => {
            if (viewerContainer && typeof window.prksAttachHideEmbedPdfErrorCloseButton === 'function') {
                window.prksAttachHideEmbedPdfErrorCloseButton(viewerContainer);
            }
            const EmbedPDF = embedModule.default;
            const ZoomMode = embedModule.ZoomMode;
            const disabledCategories = [
                'annotation-shape',
                'annotation-ink',
                'redaction',
                'form',
                'annotation-text',
                'annotation-stamp',
                'stamp',
                'insert-rubber-stamp',
                'document',
                'panel-sidebar',
                'panel-comment',
            ];

            const initResult = EmbedPDF.init({
                type: 'container',
                target: viewerContainer,
                src: url,
                disabledCategories,
                annotations: { annotationAuthor: getPrksAnnotationAuthor() },
                zoom: ZoomMode ? { defaultZoomLevel: ZoomMode.FitWidth } : undefined,
                theme:
                    typeof window.getPrksEmbedPdfTheme === 'function'
                        ? window.getPrksEmbedPdfTheme()
                        : { preference: window.localStorage.getItem('prks-theme') || 'system' },
            });

            function finishUploadViewer(viewer) {
                window.uploadViewer = viewer;
                const run =
                    typeof window.prksApplyEmbedPdfCustomizationWithRetry === 'function'
                        ? window.prksApplyEmbedPdfCustomizationWithRetry(viewer)
                        : typeof window.applyEmbedPdfUiCustomization === 'function'
                          ? window.applyEmbedPdfUiCustomization(viewer)
                          : Promise.resolve();
                return Promise.resolve(run).catch(() => {});
            }

            if (initResult && typeof initResult.then === 'function') {
                initResult.then(finishUploadViewer).catch(() => {});
            } else {
                void finishUploadViewer(initResult);
            }
        });
        
    // Auto-fill title if empty
    const titleInput = document.getElementById('work-title');
    if (!titleInput.value) {
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
            const folderId = btn.getAttribute('data-folder-id');
            if (folderId != null && folderId !== '') {
                if (typeof prksRemoveFolderTag === 'function') {
                    void prksRemoveFolderTag(folderId, tagId);
                }
                return;
            }
            const workId = btn.getAttribute('data-work-id');
            if (workId != null && workId !== '') {
                void prksRemoveWorkTag(workId, tagId);
            }
        },
        true
    );
})();


