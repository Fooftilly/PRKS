/**
 * Route-scoped multi-select and bulk organization for generic work-card grids.
 * Selection is ephemeral UI state. Mutations go through POST /api/works/bulk.
 */
(function (root) {
    'use strict';

    const PRKS_BULK_SUPPORTED_ROUTES = new Set([
        'folder-detail',
        'recent',
        'saved-view-detail',
        'type-detail',
        'progress',
        'search',
    ]);

    const state = {
        active: false,
        ids: new Set(),
        submitting: false,
        sheetKind: null,
        inited: false,
        inFlightGen: null,
        inFlightHash: '',
        lastResolvedHash: '',
    };

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function currentRoute() {
        if (typeof root.prksParseRoute === 'function') {
            return root.prksParseRoute(root.location ? root.location.hash : '');
        }
        return { name: '', canonicalHash: '', hash: '' };
    }

    function isSupportedRoute(route) {
        return !!(route && PRKS_BULK_SUPPORTED_ROUTES.has(route.name));
    }

    function pageContent() {
        return typeof document !== 'undefined' ? document.getElementById('page-content') : null;
    }

    function countLabel(n) {
        const count = Number(n) || 0;
        return count === 1 ? '1 selected' : count + ' selected';
    }

    function filesWord(n) {
        return Number(n) === 1 ? 'file' : 'files';
    }

    function selectedIds() {
        return Array.from(state.ids);
    }

    function isCardVisible(el) {
        if (!el) return false;
        let cur = el;
        while (cur && cur !== document && cur !== document.documentElement) {
            if (cur.hidden) return false;
            if (typeof root.getComputedStyle === 'function') {
                try {
                    const st = root.getComputedStyle(cur);
                    if (st && (st.display === 'none' || st.visibility === 'hidden')) return false;
                } catch (_e) {
                    /* ignore */
                }
            }
            cur = cur.parentElement;
        }
        return true;
    }

    function workCards(all) {
        const host = pageContent();
        if (!host || !host.querySelectorAll) return [];
        const nodes = host.querySelectorAll('.project-card--work-card[data-work-id]');
        const list = Array.prototype.slice.call(nodes);
        if (all) return list;
        return list.filter(isCardVisible);
    }

    function cardTitle(card) {
        const el = card && card.querySelector ? card.querySelector('.card-title') : null;
        const t = el && el.textContent ? String(el.textContent).trim() : '';
        return t || 'file';
    }

    function ensureToolbar() {
        if (typeof document === 'undefined') return null;
        let bar = document.getElementById('prks-bulk-toolbar');
        if (bar) return bar;
        bar = document.createElement('div');
        bar.id = 'prks-bulk-toolbar';
        bar.className = 'prks-bulk-toolbar hidden';
        bar.setAttribute('role', 'toolbar');
        bar.setAttribute('aria-label', 'Bulk organization');
        bar.innerHTML =
            '<span class="prks-bulk-toolbar__count" data-bulk-count>0 selected</span>' +
            '<div class="prks-bulk-toolbar__actions prks-bulk-toolbar__actions--desktop">' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-bulk-open="status">Status</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-bulk-open="folder">Folder</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-bulk-open="tags">Tags</button>' +
            '</div>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm prks-bulk-toolbar__organize" data-bulk-open="organize">Organize…</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-bulk-select-all>Select all visible</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-bulk-clear>Clear</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm prks-bulk-toolbar__exit" data-bulk-exit aria-label="Exit selection">&times;</button>';
        document.body.appendChild(bar);
        bar.addEventListener('click', onToolbarClick);
        return bar;
    }

    function ensureSheet() {
        if (typeof document === 'undefined') return null;
        let sheet = document.getElementById('prks-bulk-sheet');
        if (sheet) return sheet;
        sheet = document.createElement('div');
        sheet.id = 'prks-bulk-sheet';
        sheet.className = 'prks-bulk-sheet hidden';
        sheet.setAttribute('role', 'dialog');
        sheet.setAttribute('aria-modal', 'true');
        sheet.setAttribute('aria-labelledby', 'prks-bulk-sheet-title');
        sheet.setAttribute('aria-hidden', 'true');
        sheet.innerHTML =
            '<div class="prks-bulk-sheet__scrim" data-bulk-cancel="1"></div>' +
            '<div class="prks-bulk-sheet__panel">' +
            '<h3 class="prks-bulk-sheet__title" id="prks-bulk-sheet-title"></h3>' +
            '<div class="prks-bulk-sheet__body"></div>' +
            '<p class="prks-bulk-sheet__confirm"></p>' +
            '<p class="prks-bulk-sheet__error" role="alert"></p>' +
            '<div class="prks-bulk-sheet__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" data-bulk-cancel="1">Cancel</button>' +
            '<button type="button" class="prks-btn prks-btn--primary" data-bulk-apply="1">Apply</button>' +
            '</div>' +
            '</div>';
        document.body.appendChild(sheet);
        sheet.addEventListener('click', onSheetClick);
        sheet.addEventListener('change', onSheetChange);
        return sheet;
    }

    function toolbarEls() {
        const bar = ensureToolbar();
        if (!bar) return {};
        return {
            bar: bar,
            count: bar.querySelector('[data-bulk-count]'),
            applyBtns: bar.querySelectorAll('[data-bulk-open]'),
        };
    }

    function setToolbarVisible(on) {
        const bar = ensureToolbar();
        if (!bar) return;
        bar.classList.toggle('hidden', !on);
        if (typeof document !== 'undefined' && document.body) {
            document.body.classList.toggle('prks-bulk-selection-active', !!on);
        }
    }

    function syncCountUi() {
        const n = state.ids.size;
        const label = countLabel(n);
        const els = toolbarEls();
        if (els.count) els.count.textContent = label;
        if (els.applyBtns) {
            els.applyBtns.forEach((btn) => {
                btn.disabled = n === 0 || state.submitting;
            });
        }
        const bar = els.bar;
        if (bar) {
            const selectAll = bar.querySelector('[data-bulk-select-all]');
            const clear = bar.querySelector('[data-bulk-clear]');
            if (selectAll) selectAll.disabled = state.submitting;
            if (clear) clear.disabled = n === 0 || state.submitting;
        }
        const apply = document.querySelector('#prks-bulk-sheet [data-bulk-apply]');
        if (apply && state.sheetKind && state.sheetKind !== 'organize') {
            apply.disabled = !canApplySheet() || state.submitting;
        }
    }

    function decorateCard(card) {
        if (!card || !card.getAttribute) return;
        const id = String(card.getAttribute('data-work-id') || '').trim();
        if (!id) return;
        const selected = state.ids.has(id);
        card.classList.toggle('is-selected', selected);
        card.setAttribute('aria-selected', selected ? 'true' : 'false');
        let box = card.querySelector('.work-card__select');
        if (!box) {
            box = document.createElement('label');
            box.className = 'work-card__select';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'work-card__checkbox';
            input.setAttribute('aria-label', 'Select ' + cardTitle(card));
            const mark = document.createElement('span');
            mark.className = 'work-card__select-mark';
            mark.setAttribute('aria-hidden', 'true');
            box.appendChild(input);
            box.appendChild(mark);
            if (card.firstChild) card.insertBefore(box, card.firstChild);
            else card.appendChild(box);
        }
        const input = box.querySelector('input');
        if (input) input.checked = selected;
    }

    function undecorateCard(card) {
        if (!card) return;
        card.classList.remove('is-selected');
        card.removeAttribute('aria-selected');
        const box = card.querySelector && card.querySelector('.work-card__select');
        if (box) box.remove();
    }

    function decorateAll() {
        workCards(true).forEach((card) => {
            if (state.active) decorateCard(card);
            else undecorateCard(card);
        });
    }

    function mountSelectButton(contentDiv, route) {
        if (!contentDiv || !contentDiv.querySelector) return;
        const existing = contentDiv.querySelector('.prks-work-select-btn');
        if (!isSupportedRoute(route)) {
            if (existing) existing.remove();
            return;
        }
        if (existing) {
            existing.hidden = !!state.active;
            existing.setAttribute('aria-pressed', state.active ? 'true' : 'false');
            return;
        }
        const header = contentDiv.querySelector('.page-header');
        if (!header) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'prks-btn prks-btn--secondary prks-btn--sm prks-work-select-btn';
        btn.textContent = 'Select';
        btn.setAttribute('aria-pressed', 'false');
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            enterSelection();
        });
        const h2 = header.querySelector('h2');
        if (h2 && header.classList.contains('page-header--search')) {
            let row = header.querySelector('.page-header__title-row');
            if (!row) {
                row = document.createElement('div');
                row.className = 'page-header__title-row';
                h2.parentNode.insertBefore(row, h2);
                row.appendChild(h2);
            }
            row.appendChild(btn);
            btn.hidden = !!state.active;
            return;
        }
        let actions = header.querySelector(':scope > .page-header__actions');
        if (!actions) {
            actions = document.createElement('div');
            actions.className = 'page-header__actions';
            const trailing = [];
            Array.prototype.forEach.call(header.children, function (child) {
                if (child === header.querySelector('h2') || (child.classList && child.classList.contains('prks-nav-back'))) return;
                if (child.tagName === 'BUTTON' || (child.classList && child.classList.contains('types-page__detail-type'))) {
                    trailing.push(child);
                }
            });
            trailing.forEach((el) => actions.appendChild(el));
            header.appendChild(actions);
        }
        actions.appendChild(btn);
        btn.hidden = !!state.active;
    }

    function enterSelection() {
        const route = currentRoute();
        if (!isSupportedRoute(route)) return;
        state.active = true;
        setToolbarVisible(true);
        const btn = document.querySelector('.prks-work-select-btn');
        if (btn) {
            btn.hidden = true;
            btn.setAttribute('aria-pressed', 'true');
        }
        decorateAll();
        syncCountUi();
    }

    function closeSheet() {
        const sheet = document.getElementById('prks-bulk-sheet');
        if (sheet) {
            sheet.classList.add('hidden');
            sheet.setAttribute('aria-hidden', 'true');
        }
        state.sheetKind = null;
        state.submitting = false;
    }

    function exitSelection(opts) {
        const options = opts || {};
        closeSheet();
        state.active = false;
        state.ids = new Set();
        state.submitting = false;
        setToolbarVisible(false);
        decorateAll();
        const btn = document.querySelector('.prks-work-select-btn');
        if (btn) {
            btn.hidden = false;
            btn.setAttribute('aria-pressed', 'false');
        }
        syncCountUi();
        if (options.refresh) refreshCurrentRoute(options.captured);
    }

    function setSelected(id, on) {
        const wid = String(id || '').trim();
        if (!wid) return;
        if (on) state.ids.add(wid);
        else state.ids.delete(wid);
        const host = pageContent();
        const card =
            host && host.querySelector
                ? host.querySelector('.project-card--work-card[data-work-id="' + cssEscape(wid) + '"]')
                : null;
        if (card) decorateCard(card);
        syncCountUi();
        updateSheetConfirm();
    }

    function toggleId(id) {
        const wid = String(id || '').trim();
        if (!wid) return;
        setSelected(wid, !state.ids.has(wid));
    }

    function selectAllVisible() {
        workCards(false).forEach((card) => {
            const id = String(card.getAttribute('data-work-id') || '').trim();
            if (id) state.ids.add(id);
        });
        decorateAll();
        syncCountUi();
        updateSheetConfirm();
    }

    function clearSelected() {
        state.ids = new Set();
        decorateAll();
        syncCountUi();
        updateSheetConfirm();
    }

    function cssEscape(id) {
        if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(id);
        return String(id).replace(/"/g, '\\"');
    }

    function onCardClickCapture(e) {
        if (!state.active || state.submitting) return;
        if (e.ctrlKey || e.metaKey || e.shiftKey) return;
        if (e.button != null && e.button !== 0) return;
        const target = e.target;
        if (!target || !target.closest) return;
        const card = target.closest('.project-card--work-card[data-work-id]');
        if (!card) return;
        const host = pageContent();
        if (host && host.contains && !host.contains(card)) return;
        if (target.closest('.work-card__select')) {
            e.stopPropagation();
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        toggleId(card.getAttribute('data-work-id'));
    }

    function onCardChange(e) {
        if (!state.active) return;
        const target = e.target;
        if (!target || target.type !== 'checkbox') return;
        if (!target.classList || !target.classList.contains('work-card__checkbox')) return;
        const card = target.closest && target.closest('.project-card--work-card[data-work-id]');
        if (!card) return;
        setSelected(card.getAttribute('data-work-id'), !!target.checked);
    }

    function onKeydownCapture(e) {
        if (e.key !== 'Escape') return;
        if (!state.active) return;
        if (typeof root.prksAnyModalOpen === 'function' && root.prksAnyModalOpen()) return;
        e.preventDefault();
        e.stopPropagation();
        if (state.sheetKind) {
            if (state.submitting) return;
            closeSheet();
            syncCountUi();
            return;
        }
        exitSelection();
    }

    function onToolbarClick(e) {
        const btn = e.target && e.target.closest ? e.target.closest('button') : null;
        if (!btn) return;
        if (btn.hasAttribute('data-bulk-exit')) {
            exitSelection();
            return;
        }
        if (btn.hasAttribute('data-bulk-clear')) {
            clearSelected();
            return;
        }
        if (btn.hasAttribute('data-bulk-select-all')) {
            selectAllVisible();
            return;
        }
        const kind = btn.getAttribute('data-bulk-open');
        if (kind) openSheet(kind);
    }

    function sheetParts() {
        const sheet = ensureSheet();
        if (!sheet) return {};
        return {
            sheet: sheet,
            title: sheet.querySelector('.prks-bulk-sheet__title'),
            body: sheet.querySelector('.prks-bulk-sheet__body'),
            confirm: sheet.querySelector('.prks-bulk-sheet__confirm'),
            error: sheet.querySelector('.prks-bulk-sheet__error'),
            apply: sheet.querySelector('[data-bulk-apply]'),
            cancel: sheet.querySelector('[data-bulk-cancel]'),
        };
    }

    function setSheetError(msg) {
        const parts = sheetParts();
        if (parts.error) parts.error.textContent = msg || '';
    }

    function statusValues() {
        if (root.PRKS_PROGRESS_STATUS_VALUES && root.PRKS_PROGRESS_STATUS_VALUES.length) {
            return root.PRKS_PROGRESS_STATUS_VALUES.slice();
        }
        return ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'];
    }

    function selectedStatus() {
        const sheet = document.getElementById('prks-bulk-sheet');
        const active = sheet && sheet.querySelector('.prks-segmented__btn--active[data-value]');
        return active ? String(active.getAttribute('data-value') || '') : '';
    }

    function selectedFolderId() {
        const sheet = document.getElementById('prks-bulk-sheet');
        const radio = sheet && sheet.querySelector('input[name="prks-bulk-folder"]:checked');
        if (!radio) return undefined;
        const v = radio.value;
        return v === '' ? null : v;
    }

    function selectedFolderName() {
        const sheet = document.getElementById('prks-bulk-sheet');
        const radio = sheet && sheet.querySelector('input[name="prks-bulk-folder"]:checked');
        if (!radio) return '';
        const lab = radio.closest('label');
        return lab && lab.querySelector('.prks-bulk-folder-label')
            ? String(lab.querySelector('.prks-bulk-folder-label').textContent || '').trim()
            : '';
    }

    function selectedTagIds() {
        const sheet = document.getElementById('prks-bulk-sheet');
        if (!sheet) return [];
        const boxes = sheet.querySelectorAll('input[name="prks-bulk-tag"]:checked');
        return Array.prototype.map.call(boxes, (el) => String(el.value || '').trim()).filter(Boolean);
    }

    function selectedTagOp() {
        const sheet = document.getElementById('prks-bulk-sheet');
        const active = sheet && sheet.querySelector('[data-tag-op].prks-segmented__btn--active');
        return active ? String(active.getAttribute('data-tag-op') || 'add') : 'add';
    }

    function canApplySheet() {
        if (state.ids.size === 0) return false;
        if (state.sheetKind === 'status') return !!selectedStatus();
        if (state.sheetKind === 'folder') return selectedFolderId() !== undefined;
        if (state.sheetKind === 'tags') return selectedTagIds().length > 0 && !!selectedTagOp();
        return false;
    }

    function updateSheetConfirm() {
        const parts = sheetParts();
        if (!parts.confirm) return;
        const n = state.ids.size;
        let text = '';
        if (state.sheetKind === 'status') {
            const st = selectedStatus();
            text = st ? 'Set ' + n + ' ' + filesWord(n) + ' to ' + st + '?' : 'Choose a status, then apply.';
        } else if (state.sheetKind === 'folder') {
            const fid = selectedFolderId();
            if (fid === undefined) text = 'Choose a folder, then apply.';
            else if (fid === null) text = 'Remove ' + n + ' ' + filesWord(n) + ' from their folders?';
            else text = 'Move ' + n + ' ' + filesWord(n) + ' to ' + (selectedFolderName() || 'the selected folder') + '?';
        } else if (state.sheetKind === 'tags') {
            const tags = selectedTagIds();
            const op = selectedTagOp();
            if (!tags.length) text = 'Select one or more tags.';
            else if (op === 'remove') {
                text = 'Remove ' + tags.length + (tags.length === 1 ? ' tag' : ' tags') + ' from ' + n + ' ' + filesWord(n) + '?';
            } else {
                text = 'Add ' + tags.length + (tags.length === 1 ? ' tag' : ' tags') + ' to ' + n + ' ' + filesWord(n) + '?';
            }
        }
        parts.confirm.textContent = text;
        if (parts.apply) {
            const applyLabel =
                state.sheetKind === 'status'
                    ? 'Set status for ' + n + ' ' + filesWord(n)
                    : 'Apply';
            if (!state.submitting) parts.apply.textContent = applyLabel;
            parts.apply.disabled = !canApplySheet() || state.submitting;
        }
    }

    function renderStatusBody() {
        const values = statusValues();
        let html =
            '<div class="prks-segmented-wrap prks-segmented-wrap--status-row"><div class="prks-segmented prks-segmented--status prks-segmented--single-row" role="radiogroup" aria-label="File status">';
        values.forEach((v) => {
            const icon =
                typeof root.prksProgressStatusIconHtml === 'function'
                    ? root.prksProgressStatusIconHtml(v, { className: 'prks-segmented__btn-icon', size: 'sm' })
                    : '';
            html +=
                '<button type="button" class="prks-segmented__btn" data-value="' +
                esc(v) +
                '" aria-pressed="false" role="radio">' +
                (icon ? '<span class="prks-segmented__btn-icon">' + icon + '</span>' : '') +
                '<span class="prks-segmented__btn-label">' +
                esc(v) +
                '</span></button>';
        });
        html += '</div></div>';
        return html;
    }

    function flattenFolders(list) {
        const rows = Array.isArray(list) ? list.slice() : [];
        const byId = new Map(rows.map((f) => [f.id, f]));
        const labelFn =
            typeof root.prksFolderPathLabel === 'function'
                ? function (f) {
                      return root.prksFolderPathLabel(f.id, byId) || f.title || 'Folder';
                  }
                : function (f) {
                      return f.title || 'Folder';
                  };
        return rows
            .map((f) => ({ id: f.id, label: labelFn(f), title: f.title || 'Folder' }))
            .sort((a, b) => String(a.label).localeCompare(String(b.label), undefined, { sensitivity: 'base' }));
    }

    function renderFolderBody(folders) {
        const rows = flattenFolders(folders);
        let html =
            '<label class="prks-bulk-choice"><input type="radio" name="prks-bulk-folder" value="">' +
            '<span class="prks-bulk-folder-label">No folder</span></label>';
        rows.forEach((f) => {
            html +=
                '<label class="prks-bulk-choice"><input type="radio" name="prks-bulk-folder" value="' +
                esc(f.id) +
                '"><span class="prks-bulk-folder-label">' +
                esc(f.label) +
                '</span></label>';
        });
        return '<div class="prks-bulk-choice-list">' + html + '</div>';
    }

    function renderTagsBody(tags) {
        const list = Array.isArray(tags) ? tags.slice() : [];
        list.sort((a, b) =>
            String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' })
        );
        let html =
            '<div class="prks-segmented-wrap prks-segmented-wrap--compact"><div class="prks-segmented" role="radiogroup" aria-label="Tag operation">' +
            '<button type="button" class="prks-segmented__btn prks-segmented__btn--active" data-tag-op="add" aria-pressed="true">Add selected tags</button>' +
            '<button type="button" class="prks-segmented__btn" data-tag-op="remove" aria-pressed="false">Remove selected tags</button>' +
            '</div></div>';
        if (!list.length) {
            html += '<p class="meta-row">No tags in the library yet. Create tags from the Tags page.</p>';
            return html;
        }
        html += '<div class="prks-bulk-choice-list">';
        list.forEach((t) => {
            html +=
                '<label class="prks-bulk-choice"><input type="checkbox" name="prks-bulk-tag" value="' +
                esc(t.id) +
                '"><span>' +
                esc(t.name || 'Tag') +
                '</span></label>';
        });
        html += '</div>';
        return html;
    }

    function renderOrganizeBody() {
        return (
            '<div class="prks-bulk-organize-actions">' +
            '<button type="button" class="prks-btn prks-btn--primary" data-bulk-open="status">Status</button>' +
            '<button type="button" class="prks-btn prks-btn--primary" data-bulk-open="folder">Folder</button>' +
            '<button type="button" class="prks-btn prks-btn--primary" data-bulk-open="tags">Tags</button>' +
            '</div>'
        );
    }

    async function openSheet(kind) {
        if (!state.active) return;
        if (kind !== 'organize' && state.ids.size === 0) return;
        const parts = sheetParts();
        if (!parts.body) return;
        state.sheetKind = kind;
        setSheetError('');
        parts.sheet.classList.remove('hidden');
        parts.sheet.setAttribute('aria-hidden', 'false');
        if (kind === 'organize') {
            parts.title.textContent = 'Organize';
            parts.body.innerHTML = renderOrganizeBody();
            if (parts.apply) parts.apply.hidden = true;
            parts.confirm.textContent = '';
            return;
        }
        if (parts.apply) parts.apply.hidden = false;
        if (kind === 'status') {
            parts.title.textContent = 'Set progress';
            parts.body.innerHTML = renderStatusBody();
        } else if (kind === 'folder') {
            parts.title.textContent = 'Move to folder';
            parts.body.innerHTML = '<p class="meta-row">Loading folders…</p>';
            let folders = [];
            try {
                const fn = typeof root.fetchFolders === 'function' ? root.fetchFolders : null;
                folders = fn ? await fn() : [];
            } catch (_e) {
                folders = [];
            }
            if (state.sheetKind !== 'folder') return;
            parts.body.innerHTML = renderFolderBody(folders);
        } else if (kind === 'tags') {
            parts.title.textContent = 'Tags';
            parts.body.innerHTML = '<p class="meta-row">Loading tags…</p>';
            let tags = [];
            try {
                const fn = typeof root.fetchTags === 'function' ? root.fetchTags : null;
                tags = fn ? await fn() : [];
            } catch (_e) {
                tags = [];
            }
            if (state.sheetKind !== 'tags') return;
            parts.body.innerHTML = renderTagsBody(tags);
        }
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(parts.sheet);
        updateSheetConfirm();
    }

    function onSheetClick(e) {
        const t = e.target;
        if (!t || !t.closest) return;
        if (t.closest('[data-bulk-cancel]')) {
            if (state.submitting) return;
            closeSheet();
            return;
        }
        const openKind = t.closest('[data-bulk-open]');
        if (openKind) {
            openSheet(openKind.getAttribute('data-bulk-open'));
            return;
        }
        const statusBtn = t.closest('.prks-segmented__btn[data-value]');
        if (statusBtn && state.sheetKind === 'status') {
            const wrap = statusBtn.closest('.prks-segmented');
            if (wrap) {
                wrap.querySelectorAll('.prks-segmented__btn').forEach((b) => {
                    const on = b === statusBtn;
                    b.classList.toggle('prks-segmented__btn--active', on);
                    b.setAttribute('aria-pressed', on ? 'true' : 'false');
                });
            }
            updateSheetConfirm();
            return;
        }
        const tagOp = t.closest('[data-tag-op]');
        if (tagOp && state.sheetKind === 'tags') {
            const wrap = tagOp.closest('.prks-segmented');
            if (wrap) {
                wrap.querySelectorAll('[data-tag-op]').forEach((b) => {
                    const on = b === tagOp;
                    b.classList.toggle('prks-segmented__btn--active', on);
                    b.setAttribute('aria-pressed', on ? 'true' : 'false');
                });
            }
            updateSheetConfirm();
            return;
        }
        if (t.closest('[data-bulk-apply]')) {
            void submitSheet();
        }
    }

    function onSheetChange(e) {
        if (!state.sheetKind) return;
        const t = e.target;
        if (!t) return;
        if (t.name === 'prks-bulk-folder' || t.name === 'prks-bulk-tag') updateSheetConfirm();
    }

    function buildPayload() {
        const ids = selectedIds();
        if (state.sheetKind === 'status') {
            return { work_ids: ids, action: 'set_status', status: selectedStatus() };
        }
        if (state.sheetKind === 'folder') {
            return { work_ids: ids, action: 'move_folder', folder_id: selectedFolderId() };
        }
        if (state.sheetKind === 'tags') {
            const op = selectedTagOp() === 'remove' ? 'remove_tags' : 'add_tags';
            return { work_ids: ids, action: op, tag_ids: selectedTagIds() };
        }
        return null;
    }

    function applyIdleLabel() {
        if (state.sheetKind === 'status') {
            const n = state.ids.size;
            return 'Set status for ' + n + ' ' + filesWord(n);
        }
        return 'Apply';
    }

    function setSubmitting(on) {
        state.submitting = !!on;
        const parts = sheetParts();
        if (parts.apply) {
            parts.apply.disabled = on || !canApplySheet();
            parts.apply.textContent = on ? 'Working…' : applyIdleLabel();
        }
        const cancels = document.querySelectorAll('#prks-bulk-sheet [data-bulk-cancel]');
        cancels.forEach((el) => {
            if (el.tagName === 'BUTTON') el.disabled = !!on;
        });
        syncCountUi();
    }

    function refreshCurrentRoute(captured) {
        const route = captured && captured.route ? captured.route : currentRoute();
        if (typeof root.prksCaptureCurrentRouteState === 'function') {
            root.prksCaptureCurrentRouteState(route);
        }
        if (typeof root.prksNavigate === 'function') {
            root.prksNavigate(route.canonicalHash || route.hash, { replace: true });
        } else if (typeof root.handleRoute === 'function') {
            void root.handleRoute();
        }
    }

    function stillOnCaptured(captured) {
        if (!captured) return false;
        if (captured.gen != null && typeof root.prksFocusedRouteGeneration === 'function' && captured.gen !== root.prksFocusedRouteGeneration()) return false;
        const now = currentRoute();
        if (captured.hash && now.canonicalHash !== captured.hash) return false;
        return true;
    }

    async function runBulkPayload(payload) {
        if (!payload || state.submitting) return;
        const captured = {
            gen: typeof root.prksFocusedRouteGeneration === 'function' ? root.prksFocusedRouteGeneration() : 0,
            hash: currentRoute().canonicalHash,
            route: currentRoute(),
        };
        state.inFlightGen = captured.gen;
        state.inFlightHash = captured.hash;
        setSubmitting(true);
        setSheetError('');
        try {
            const fn = typeof root.bulkUpdateWorks === 'function' ? root.bulkUpdateWorks : null;
            if (!fn) throw new Error('Could not update the selected files.');
            await fn(payload);
            if (!stillOnCaptured(captured)) return { stale: true, ok: true };
            closeSheet();
            exitSelection();
            if (stillOnCaptured(captured)) refreshCurrentRoute(captured);
            return { stale: false, ok: true };
        } catch (err) {
            if (!stillOnCaptured(captured)) return { stale: true, ok: false };
            setSubmitting(false);
            const msg =
                err && err.message ? String(err.message) : 'Could not update the selected files.';
            setSheetError(msg);
            updateSheetConfirm();
            return { stale: false, ok: false, error: msg };
        } finally {
            state.inFlightGen = null;
            state.inFlightHash = '';
        }
    }

    async function submitSheet() {
        if (state.submitting || !canApplySheet()) return;
        const payload = buildPayload();
        if (!payload) return;
        return runBulkPayload(payload);
    }

    function onRouteWillChange(prevHash, nextHash) {
        const prev =
            typeof root.prksParseRoute === 'function' ? root.prksParseRoute(prevHash || '') : { canonicalHash: prevHash };
        const next =
            typeof root.prksParseRoute === 'function' ? root.prksParseRoute(nextHash || '') : { canonicalHash: nextHash };
        if ((prev.canonicalHash || '') !== (next.canonicalHash || '')) {
            if (state.active || state.ids.size) exitSelection();
        }
    }

    function onRouteFinished(route, contentDiv) {
        mountSelectButton(contentDiv, route);
        if (state.active && isSupportedRoute(route)) {
            decorateAll();
            setToolbarVisible(true);
            syncCountUi();
        } else if (!isSupportedRoute(route) && state.active) {
            exitSelection();
        }
        state.lastResolvedHash = route && (route.canonicalHash || route.hash) ? route.canonicalHash || route.hash : '';
    }

    function wrapFinish() {
        const orig = root.prksFinishRouteRender;
        if (typeof orig === 'function' && orig._prksBulkWrapped) return;
        function wrapped(ctx, route, generation, contentDiv, options) {
            const ok = typeof orig === 'function' ? orig(ctx, route, generation, contentDiv, options) : true;
            if (ok !== false) onRouteFinished(route, contentDiv);
            return ok;
        }
        wrapped._prksBulkWrapped = true;
        root.prksFinishRouteRender = wrapped;
    }

    function wrapHandleRoute() {
        const orig = root.handleRoute;
        if (typeof orig !== 'function' || orig._prksBulkWrapped) return typeof orig === 'function';
        function wrapped() {
            const prev = state.lastResolvedHash || '';
            const next = root.location ? root.location.hash : '';
            onRouteWillChange(prev, next);
            return orig.apply(this, arguments);
        }
        wrapped._prksBulkWrapped = true;
        root.handleRoute = wrapped;
        return true;
    }

    function init() {
        if (state.inited) return;
        state.inited = true;
        if (typeof document === 'undefined') return;
        ensureToolbar();
        ensureSheet();
        document.addEventListener('click', onCardClickCapture, true);
        document.addEventListener('change', onCardChange);
        document.addEventListener('keydown', onKeydownCapture, true);
        wrapFinish();
        wrapHandleRoute();
    }

    function resetForTests() {
        state.active = false;
        state.ids = new Set();
        state.submitting = false;
        state.sheetKind = null;
        state.lastResolvedHash = '';
        closeSheet();
        setToolbarVisible(false);
        decorateAll();
    }

    const api = {
        PRKS_BULK_SUPPORTED_ROUTES: PRKS_BULK_SUPPORTED_ROUTES,
        prksWorkSelectionIsSupportedRoute: isSupportedRoute,
        prksWorkSelectionIsActive: function () {
            return !!state.active;
        },
        prksWorkSelectionGetIds: selectedIds,
        prksWorkSelectionCountText: function () {
            return countLabel(state.ids.size);
        },
        prksWorkSelectionEnter: enterSelection,
        prksWorkSelectionExit: exitSelection,
        prksWorkSelectionToggle: toggleId,
        prksWorkSelectionSelectAllVisible: selectAllVisible,
        prksWorkSelectionClear: clearSelected,
        prksWorkSelectionDecorate: decorateAll,
        prksWorkSelectionOpenSheet: openSheet,
        prksWorkSelectionSubmit: submitSheet,
        prksWorkSelectionSubmitPayload: runBulkPayload,
        prksWorkSelectionInit: init,
        prksWorkSelectionResetForTests: resetForTests,
        prksWorkSelectionOnRouteFinished: onRouteFinished,
        prksWorkSelectionOnRouteWillChange: onRouteWillChange,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () {
                init();
                wrapHandleRoute();
            });
        } else {
            init();
            wrapHandleRoute();
        }
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
