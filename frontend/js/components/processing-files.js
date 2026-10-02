/**
 * Processing Files writes and the preview resource.
 * The coordinator in app.js loads the inbox. Vue paints the cards.
 * This module keeps the upload-style save/import, the catalog helpers those
 * cards call, and the iframe plus resize listener for one owner.
 * There is no processing-file durable queue.
 *
 * Processing File roles share the People-navigation subset (excludes Mentioned).
 * navigation.js owns PRKS_PEOPLE_ROLES and loads before this module.
 */
const PRKS_PROCESSING_ROLE_TYPES = (
    typeof window !== 'undefined' && Array.isArray(window.PRKS_PEOPLE_ROLES)
        ? window.PRKS_PEOPLE_ROLES
        : []
);

const PRKS_PROCESSING_PREVIEW_MQ = '(max-width: 1240px)';
const prksProcessingResources = new WeakMap();

function prksProcessingRoleTypes() {
    return PRKS_PROCESSING_ROLE_TYPES.slice();
}

function prksProcessingDomPrefix(owner) {
    const raw = owner && owner.tabId ? String(owner.tabId) : 'main';
    return 'prks-pf-' + raw.replace(/[^a-zA-Z0-9_-]/g, '_');
}

function prksProcessingPreviewStacked() {
    return typeof window.matchMedia === 'function' && window.matchMedia(PRKS_PROCESSING_PREVIEW_MQ).matches;
}

function prksProcessingCssEscape(value) {
    const raw = String(value || '');
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(raw);
    return raw.replace(/["\\]/g, '\\$&');
}

function prksProcessingPreviewAnchor(host, fileId) {
    if (!host || typeof host.querySelector !== 'function') return null;
    if (!fileId) return host.querySelector('[data-prks-processing-anchor="layout"]');
    return host.querySelector('[data-prks-processing-anchor="' + prksProcessingCssEscape(fileId) + '"]');
}

function prksProcessingBuildAside() {
    const aside = document.createElement('aside');
    aside.className = 'prks-processing-inline-preview';
    aside.setAttribute('data-prks-processing-preview', 'true');
    aside.innerHTML =
        '<h3 class="prks-processing-inline-preview__title">PDF Preview</h3>' +
        '<p class="prks-processing-inline-preview__file" data-prks-processing-preview-file>No file selected</p>' +
        '<p class="prks-processing-inline-preview__empty" data-prks-processing-preview-empty>Click Preview on file card to open PDF here.</p>' +
        '<div class="prks-processing-inline-preview__frame-shell hidden" data-prks-processing-preview-shell>' +
        '<iframe class="prks-processing-inline-preview__frame hidden" data-prks-processing-preview-frame title="PDF preview" loading="lazy" referrerpolicy="no-referrer"></iframe>' +
        '</div>' +
        '<p class="meta-row"><a class="route-sidebar__link hidden" data-prks-processing-preview-link href="#" target="_blank" rel="noopener">Open preview in new tab</a></p>';
    return aside;
}

function prksProcessingPlaceAside(rec, fileId) {
    const host = rec.host;
    const layoutAnchor = prksProcessingPreviewAnchor(host, '');
    let target = layoutAnchor;
    if (fileId && prksProcessingPreviewStacked()) {
        const cardAnchor = prksProcessingPreviewAnchor(host, fileId);
        if (cardAnchor) target = cardAnchor;
    }
    if (target && rec.aside && rec.aside.parentNode !== target) target.appendChild(rec.aside);
    const layout = host && typeof host.querySelector === 'function'
        ? host.querySelector('.prks-processing-main-layout')
        : null;
    if (layout) {
        if (fileId && target && target !== layoutAnchor) layout.setAttribute('data-preview-for', String(fileId));
        else layout.removeAttribute('data-preview-for');
    }
}

function prksProcessingSyncPreviewSlot(rec) {
    if (!rec || !rec.host || !rec.host.isConnected) return;
    prksProcessingPlaceAside(rec, rec.previewFileId || '');
}

/**
 * Install this owner's preview pane and resize listener.
 * A later attach for the same owner replaces the previous iframe.
 */
function prksProcessingAttachResources(owner, host) {
    prksProcessingReleaseResources(owner);
    if (!owner || !host) return;
    const aside = prksProcessingBuildAside();
    const rec = {
        host: host,
        aside: aside,
        frame: aside.querySelector('[data-prks-processing-preview-frame]'),
        timer: 0,
        previewFileId: '',
        onResize: null,
    };
    rec.onResize = function () {
        window.clearTimeout(rec.timer);
        rec.timer = window.setTimeout(function () {
            prksProcessingSyncPreviewSlot(rec);
        }, 150);
    };
    window.addEventListener('resize', rec.onResize);
    prksProcessingResources.set(owner, rec);
    prksProcessingPlaceAside(rec, '');
}

/** Drop the iframe document and the resize listener for this owner only. */
function prksProcessingReleaseResources(owner) {
    if (!owner) return;
    const rec = prksProcessingResources.get(owner);
    if (!rec) return;
    window.clearTimeout(rec.timer);
    if (rec.onResize) window.removeEventListener('resize', rec.onResize);
    if (rec.frame) {
        rec.frame.removeAttribute('src');
        rec.frame.classList.add('hidden');
    }
    if (rec.aside && rec.aside.parentNode) rec.aside.parentNode.removeChild(rec.aside);
    prksProcessingResources.delete(owner);
}

/**
 * Point this owner's iframe at one inbox PDF, or clear it.
 * Returns where the pane was placed: `side`, `card`, or `unavailable`.
 */
function prksProcessingSetPreview(owner, file) {
    const rec = owner ? prksProcessingResources.get(owner) : null;
    if (!rec || !rec.host || !rec.host.isConnected || !rec.aside) return 'unavailable';
    const fileId = file && file.id ? String(file.id) : '';
    const canPreview = !!(file && file.canPreview);
    const label = String((file && (file.filename || file.relPath)) || 'Selected file');
    const fileEl = rec.aside.querySelector('[data-prks-processing-preview-file]');
    const emptyEl = rec.aside.querySelector('[data-prks-processing-preview-empty]');
    const shell = rec.aside.querySelector('[data-prks-processing-preview-shell]');
    const link = rec.aside.querySelector('[data-prks-processing-preview-link]');
    if (fileEl) fileEl.textContent = label;
    if (!canPreview) {
        rec.previewFileId = '';
        prksProcessingPlaceAside(rec, '');
        if (emptyEl) {
            emptyEl.textContent = 'Preview unavailable for this file.';
            emptyEl.classList.remove('hidden');
        }
        if (shell) shell.classList.add('hidden');
        if (rec.frame) {
            rec.frame.classList.add('hidden');
            rec.frame.removeAttribute('src');
        }
        if (link) link.classList.add('hidden');
        return 'unavailable';
    }
    const src = '/api/processing-files/' + encodeURIComponent(fileId) + '/pdf';
    if (shell) shell.classList.remove('hidden');
    if (rec.frame) {
        rec.frame.setAttribute('src', src);
        rec.frame.setAttribute('title', 'PDF preview for ' + label);
        rec.frame.classList.remove('hidden');
    }
    if (emptyEl) emptyEl.classList.add('hidden');
    if (link) {
        link.setAttribute('href', src);
        link.classList.remove('hidden');
    }
    const stacked = prksProcessingPreviewStacked();
    rec.previewFileId = stacked ? fileId : '';
    prksProcessingPlaceAside(rec, rec.previewFileId);
    if (stacked && rec.aside) {
        window.requestAnimationFrame(function () {
            if (rec.aside && rec.aside.isConnected && typeof rec.aside.scrollIntoView === 'function') {
                rec.aside.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
        return 'card';
    }
    return 'side';
}

function prksProcessingNormalizeDraft(draft) {
    const next = Object.assign({}, draft || {});
    if (typeof prksParsePublishedDateInput === 'function') {
        next.published_date = prksParsePublishedDateInput(next.published_date);
    }
    return next;
}

async function prksProcessingSave(fileId, draft) {
    return patchProcessingFile(fileId, prksProcessingNormalizeDraft(draft));
}

async function prksProcessingImport(fileId) {
    return importProcessingFile(fileId);
}

async function prksProcessingSearchTags() {
    if (typeof fetchTags !== 'function') return [];
    const rows = await fetchTags({ used: false });
    return Array.isArray(rows) ? rows : [];
}

async function prksProcessingCreateTag(name) {
    if (typeof prksCreateTagDurably !== 'function') {
        throw new Error('Tag creation is not available.');
    }
    const created = await prksCreateTagDurably({ name: name, color: '#6d6cf7' });
    if (!created || !created.entity_id) throw new Error('no id');
    return { id: String(created.entity_id), name: String(name || '').trim() };
}

async function prksProcessingQuickCreateFolder(title) {
    const trimmed = String(title || '').trim();
    if (!trimmed) {
        if (typeof prksAlertMessage === 'function') {
            await prksAlertMessage('Enter folder title in search field first.', 'Validation');
        }
        return { ok: false };
    }
    let newFolderId;
    try {
        newFolderId = await createFolder(trimmed, 'Quick-created from processing inbox');
    } catch (e) {
        if (typeof prksOfflineWasGuardRefusal !== 'function' || !prksOfflineWasGuardRefusal(e)) {
            if (typeof prksAlertMessage === 'function') {
                await prksAlertMessage((e && e.message) || 'Could not create folder.', 'Could not save');
            }
        }
        return { ok: false };
    }
    const errorOwner = {};
    let folders = [];
    let foldersFailed = false;
    try {
        const fetched = await fetchFolders({ errorOwner: errorOwner });
        const failure = typeof prksConsumeApiError === 'function' ? prksConsumeApiError(errorOwner) : null;
        if (failure) foldersFailed = true;
        else folders = Array.isArray(fetched) ? fetched : [];
    } catch (_e) {
        foldersFailed = true;
    }
    if (!foldersFailed) {
        try {
            allFolders = folders;
            window.allFolders = allFolders;
        } catch (_e2) {}
    }
    return {
        ok: true,
        id: String(newFolderId || ''),
        title: trimmed,
        folders: foldersFailed ? null : folders,
        foldersFailed: foldersFailed,
    };
}

async function prksProcessingQuickCreatePerson(name) {
    if (typeof prksQuickCreatePersonForSearchField !== 'function') return null;
    const painted = Array.isArray(window.__prksProcessingPeople) ? window.__prksProcessingPeople.slice() : [];
    const search = document.createElement('input');
    const hidden = document.createElement('input');
    await prksQuickCreatePersonForSearchField(
        name,
        search,
        hidden,
        'Quick-created from processing inbox',
        { stillApplies: function () { return true; } }
    );
    if (!hidden.value) {
        window.__prksProcessingPeople = painted;
        return null;
    }
    const id = String(hidden.value);
    const createdName = String(search.value || name || '');
    const people = painted.slice();
    if (!people.some(function (person) { return person && String(person.id) === id; })) {
        people.push({ id: id, name: createdName });
    }
    window.__prksProcessingPeople = people;
    return { id: id, name: createdName, people: people };
}

window.prksProcessingRoleTypes = prksProcessingRoleTypes;
window.prksProcessingDomPrefix = prksProcessingDomPrefix;
window.prksProcessingAttachResources = prksProcessingAttachResources;
window.prksProcessingReleaseResources = prksProcessingReleaseResources;
window.prksProcessingSetPreview = prksProcessingSetPreview;
window.prksProcessingSave = prksProcessingSave;
window.prksProcessingImport = prksProcessingImport;
window.prksProcessingSearchTags = prksProcessingSearchTags;
window.prksProcessingCreateTag = prksProcessingCreateTag;
window.prksProcessingQuickCreateFolder = prksProcessingQuickCreateFolder;
window.prksProcessingQuickCreatePerson = prksProcessingQuickCreatePerson;
