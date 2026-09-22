/**
 * Library Navigation V1 — compact hierarchical Folder switcher on Folder detail.
 *
 * Reuses the complete folders:index hierarchy (one set-based fetch). Selection
 * goes through prksNavigate with the owning TabContext id so Main / parked /
 * Secondary panes navigate their own route, not another workspace context.
 */
(function (root) {
    'use strict';

    const PANEL_ID = 'prks-folder-nav-panel';
    const FILTER_LIMIT = 40;
    const NEARBY_CHILD_LIMIT = 60;
    const NEARBY_SIBLING_LIMIT = 80;

    let panelEl = null;
    let optionEls = [];
    let activeIndex = 0;
    let restoreTarget = null;
    let ownerTabId = null;
    let currentFolderId = null;
    let hierarchyRows = null;
    let loadPromise = null;
    let boundGlobal = false;
    let filterQuery = '';

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        if (s == null || s === '') return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function titleSort(a, b) {
        return String((a && a.title) || '').localeCompare(String((b && b.title) || ''), undefined, {
            sensitivity: 'base',
        });
    }

    function buildById(rows) {
        const byId = new Map();
        const list = Array.isArray(rows) ? rows : [];
        for (let i = 0; i < list.length; i++) {
            const row = list[i];
            if (row && row.id != null) byId.set(String(row.id), row);
        }
        return byId;
    }

    /** Pure: ancestors (root→parent), parent, siblings, children, path parts. */
    function prksFolderHierarchyContext(folderId, rows) {
        const id = folderId == null || folderId === '' ? null : String(folderId);
        const list = Array.isArray(rows) ? rows.slice() : [];
        const byId = buildById(list);
        const current = id ? byId.get(id) || null : null;
        const ancestors = [];
        if (current) {
            const guard = new Set();
            let cur = current;
            while (cur && cur.parent_id && !guard.has(String(cur.parent_id))) {
                guard.add(String(cur.parent_id));
                const parent = byId.get(String(cur.parent_id));
                if (!parent) break;
                ancestors.unshift(parent);
                cur = parent;
            }
        }
        const parent = ancestors.length ? ancestors[ancestors.length - 1] : null;
        const parentKey = current && current.parent_id != null ? String(current.parent_id) : null;
        const siblings = list
            .filter(function (row) {
                if (!row || row.id == null) return false;
                const pid = row.parent_id == null || row.parent_id === '' ? null : String(row.parent_id);
                return pid === parentKey;
            })
            .sort(titleSort);
        const children = id
            ? list
                  .filter(function (row) {
                      return row && row.parent_id != null && String(row.parent_id) === id;
                  })
                  .sort(titleSort)
            : [];
        const pathParts = ancestors
            .map(function (a) {
                return String(a.title || 'Folder');
            })
            .concat(current ? [String(current.title || 'Folder')] : []);
        return {
            current: current,
            ancestors: ancestors,
            parent: parent,
            siblings: siblings,
            children: children,
            pathParts: pathParts,
            byId: byId,
            found: !!current,
        };
    }

    function pathLabelFor(folderId, byId) {
        if (typeof root.prksFolderPathLabel === 'function') {
            return root.prksFolderPathLabel(folderId, byId);
        }
        const parts = [];
        const guard = new Set();
        let cur = byId.get(folderId);
        while (cur && !guard.has(String(cur.id))) {
            guard.add(String(cur.id));
            parts.unshift(String(cur.title || 'Folder'));
            cur = cur.parent_id ? byId.get(String(cur.parent_id)) : null;
        }
        return parts.join(' → ');
    }

    /** Pure: filter the complete hierarchy; bounded results with path labels. */
    function prksFolderHierarchyFilter(rows, query, limit) {
        const list = Array.isArray(rows) ? rows : [];
        const byId = buildById(list);
        const q = String(query || '')
            .trim()
            .toLowerCase();
        const max = typeof limit === 'number' && limit > 0 ? limit : FILTER_LIMIT;
        if (!q) return [];
        const out = [];
        for (let i = 0; i < list.length; i++) {
            const row = list[i];
            if (!row || row.id == null) continue;
            const title = String(row.title || '').toLowerCase();
            const path = pathLabelFor(String(row.id), byId).toLowerCase();
            if (title.indexOf(q) === -1 && path.indexOf(q) === -1) continue;
            out.push({
                id: String(row.id),
                title: String(row.title || 'Folder'),
                path: pathLabelFor(String(row.id), byId),
                parent_id: row.parent_id == null || row.parent_id === '' ? null : String(row.parent_id),
            });
            if (out.length >= max) break;
        }
        out.sort(function (a, b) {
            return String(a.title || '').localeCompare(String(b.title || ''), undefined, {
                sensitivity: 'base',
            });
        });
        return out;
    }

    function crumbHtml(folder, rows) {
        const id = folder && folder.id != null ? String(folder.id) : '';
        if (!id) return '';
        let parts = [];
        if (Array.isArray(rows) && rows.length) {
            const ctx = prksFolderHierarchyContext(id, rows);
            parts = ctx.pathParts;
        } else if (folder && folder.parent && folder.parent.title) {
            parts = [String(folder.parent.title), String(folder.title || 'Folder')];
        } else {
            parts = [String((folder && folder.title) || 'Folder')];
        }
        const full = parts.join(' › ');
        const shown =
            parts.length <= 3
                ? full
                : '… › ' + parts.slice(-2).join(' › ');
        return (
            '<button type="button" class="prks-folder-nav__trigger" id="prks-folder-nav-trigger" ' +
            'aria-haspopup="listbox" aria-expanded="false" aria-controls="' +
            PANEL_ID +
            '" ' +
            'title="' +
            esc(full) +
            '" data-prks-folder-nav-current="' +
            esc(id) +
            '">' +
            '<span class="prks-folder-nav__path">' +
            esc(shown) +
            '</span>' +
            '<span class="prks-folder-nav__chevron" aria-hidden="true">' +
            (typeof root.prksIcon === 'function' ? root.prksIcon('chevronDown', { size: 14 }) : '▾') +
            '</span>' +
            '<span class="prks-sr-only">Open folder navigation</span>' +
            '</button>'
        );
    }

    function ensurePanel() {
        const d = doc();
        if (!d || !d.body) return null;
        if (panelEl && d.body.contains(panelEl)) return panelEl;
        panelEl = d.createElement('div');
        panelEl.id = PANEL_ID;
        panelEl.className = 'prks-folder-nav__panel';
        panelEl.setAttribute('role', 'listbox');
        panelEl.setAttribute('aria-label', 'Folder hierarchy');
        panelEl.hidden = true;
        d.body.appendChild(panelEl);
        return panelEl;
    }

    function setTriggerExpanded(expanded) {
        const d = doc();
        const trigger = d && d.getElementById('prks-folder-nav-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }

    function closePanel(opts) {
        const options = opts || {};
        const panel = ensurePanel();
        if (!panel || panel.hidden) {
            if (!options.keepRestore) restoreTarget = null;
            setTriggerExpanded(false);
            return;
        }
        panel.hidden = true;
        panel.replaceChildren();
        optionEls = [];
        activeIndex = 0;
        filterQuery = '';
        setTriggerExpanded(false);
        const target = restoreTarget;
        restoreTarget = null;
        const d = doc();
        if (
            !options.skipFocus &&
            target &&
            typeof target.focus === 'function' &&
            d &&
            d.contains(target)
        ) {
            target.focus({ preventScroll: true });
        }
    }

    function setActiveOption(index) {
        if (!optionEls.length) return;
        let i = index;
        if (i < 0) i = optionEls.length - 1;
        if (i >= optionEls.length) i = 0;
        activeIndex = i;
        optionEls.forEach(function (el, n) {
            const on = n === i;
            el.classList.toggle('is-active', on);
            el.setAttribute('aria-selected', on ? 'true' : 'false');
            el.tabIndex = on ? 0 : -1;
            if (on) el.focus({ preventScroll: true });
        });
        const panel = panelEl;
        if (panel) {
            const active = optionEls[i];
            if (active && typeof active.id === 'string' && active.id) {
                panel.setAttribute('aria-activedescendant', active.id);
            }
        }
    }

    function navigateToFolder(folderId) {
        const hash = '#/folders/' + encodeURIComponent(String(folderId || ''));
        closePanel({ skipFocus: true });
        const opts = {};
        if (ownerTabId) opts.tabId = ownerTabId;
        if (typeof root.prksNavigate === 'function') {
            root.prksNavigate(hash, opts);
            return;
        }
        if (typeof root.location !== 'undefined') root.location.hash = hash;
    }

    function addSectionLabel(panel, text) {
        const d = doc();
        const lab = d.createElement('div');
        lab.className = 'prks-folder-nav__section';
        lab.setAttribute('role', 'presentation');
        lab.textContent = text;
        panel.appendChild(lab);
    }

    function addOption(panel, spec) {
        const d = doc();
        const btn = d.createElement('button');
        btn.type = 'button';
        btn.className =
            'prks-folder-nav__option' +
            (spec.current ? ' is-current' : '') +
            (spec.depth ? ' prks-folder-nav__option--depth-' + Math.min(3, spec.depth) : '');
        btn.setAttribute('role', 'option');
        btn.id = 'prks-folder-nav-opt-' + optionEls.length;
        btn.setAttribute('aria-selected', 'false');
        if (spec.current) btn.setAttribute('aria-current', 'true');
        btn.tabIndex = -1;
        btn.dataset.folderId = String(spec.id || '');

        const title = d.createElement('span');
        title.className = 'prks-folder-nav__option-title';
        title.textContent = spec.title || 'Folder';
        btn.appendChild(title);

        if (spec.meta) {
            const meta = d.createElement('span');
            meta.className = 'prks-folder-nav__option-meta';
            meta.textContent = spec.meta;
            btn.appendChild(meta);
        }

        if (spec.current) {
            const mark = d.createElement('span');
            mark.className = 'prks-folder-nav__option-mark';
            mark.textContent = 'Current';
            btn.appendChild(mark);
        }

        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (spec.current) {
                closePanel();
                return;
            }
            navigateToFolder(spec.id);
        });
        panel.appendChild(btn);
        optionEls.push(btn);
        return btn;
    }

    function addEmpty(panel, text) {
        const d = doc();
        const p = d.createElement('p');
        p.className = 'prks-folder-nav__empty meta-row';
        p.textContent = text;
        panel.appendChild(p);
    }

    function truncateList(list, limit) {
        if (!Array.isArray(list)) return [];
        if (list.length <= limit) return list;
        return list.slice(0, limit);
    }

    function fillNearby(panel, ctx) {
        if (!ctx.found) {
            addEmpty(panel, 'This folder is not in the cached hierarchy.');
            const d = doc();
            const btn = d.createElement('button');
            btn.type = 'button';
            btn.className = 'prks-folder-nav__option';
            btn.setAttribute('role', 'option');
            btn.id = 'prks-folder-nav-opt-library';
            btn.setAttribute('aria-selected', 'false');
            btn.tabIndex = -1;
            const title = d.createElement('span');
            title.className = 'prks-folder-nav__option-title';
            title.textContent = 'Open folder library';
            btn.appendChild(title);
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                closePanel({ skipFocus: true });
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate('#/folders', ownerTabId ? { tabId: ownerTabId } : {});
                }
            });
            panel.appendChild(btn);
            optionEls.push(btn);
            return;
        }

        if (ctx.ancestors.length) {
            addSectionLabel(panel, 'Path');
            ctx.ancestors.forEach(function (row, i) {
                addOption(panel, {
                    id: String(row.id),
                    title: String(row.title || 'Folder'),
                    meta: i === ctx.ancestors.length - 1 ? 'Parent' : 'Ancestor',
                    depth: i,
                    current: false,
                });
            });
        } else {
            addSectionLabel(panel, 'Path');
            addEmpty(panel, 'Top-level folder');
        }

        addSectionLabel(panel, 'This level');
        const siblings = truncateList(ctx.siblings, NEARBY_SIBLING_LIMIT);
        if (!siblings.length) {
            addEmpty(panel, 'No folders at this level.');
        } else {
            siblings.forEach(function (row) {
                const isCurrent = String(row.id) === String(ctx.current.id);
                addOption(panel, {
                    id: String(row.id),
                    title: String(row.title || 'Folder'),
                    meta: isCurrent ? null : 'Sibling',
                    current: isCurrent,
                });
            });
            if (ctx.siblings.length > siblings.length) {
                addEmpty(
                    panel,
                    '+' + (ctx.siblings.length - siblings.length) + ' more — use filter to find them'
                );
            }
        }

        addSectionLabel(panel, 'Inside');
        const children = truncateList(ctx.children, NEARBY_CHILD_LIMIT);
        if (!children.length) {
            addEmpty(panel, 'No subfolders.');
        } else {
            children.forEach(function (row) {
                const childCount = Number(row.child_count || 0);
                addOption(panel, {
                    id: String(row.id),
                    title: String(row.title || 'Folder'),
                    meta: childCount > 0 ? childCount + (childCount === 1 ? ' subfolder' : ' subfolders') : 'Child',
                    current: false,
                });
            });
            if (ctx.children.length > children.length) {
                addEmpty(
                    panel,
                    '+' + (ctx.children.length - children.length) + ' more — use filter to find them'
                );
            }
        }
    }

    function fillFilter(panel, rows, query) {
        const matches = prksFolderHierarchyFilter(rows, query, FILTER_LIMIT);
        addSectionLabel(panel, 'Matching folders');
        if (!matches.length) {
            addEmpty(panel, 'No folders match.');
            return;
        }
        matches.forEach(function (row) {
            const isCurrent = currentFolderId && String(row.id) === String(currentFolderId);
            addOption(panel, {
                id: row.id,
                title: row.title,
                meta: row.path === row.title ? null : row.path,
                current: isCurrent,
            });
        });
    }

    function positionPanel(anchor) {
        const d = doc();
        const panel = ensurePanel();
        if (!panel || !anchor) return;
        panel.hidden = false;
        const rect = typeof anchor.getBoundingClientRect === 'function' ? anchor.getBoundingClientRect() : null;
        const vw = (d.documentElement && d.documentElement.clientWidth) || root.innerWidth || 800;
        const vh = (d.documentElement && d.documentElement.clientHeight) || root.innerHeight || 600;
        const mw = Math.min(360, Math.max(260, (rect && rect.width) || 280));
        panel.style.width = mw + 'px';
        const mh = panel.offsetHeight || 280;
        let x = rect ? rect.left : 8;
        let y = rect ? rect.bottom + 4 : 8;
        if (x + mw > vw - 8) x = Math.max(8, vw - mw - 8);
        if (y + mh > vh - 8) y = Math.max(8, (rect ? rect.top : vh) - mh - 4);
        if (x < 8) x = 8;
        if (y < 8) y = 8;
        panel.style.left = Math.round(x) + 'px';
        panel.style.top = Math.round(y) + 'px';
    }

    function renderPanelBody(rows) {
        const d = doc();
        const panel = ensurePanel();
        if (!panel || !d) return;
        panel.replaceChildren();
        optionEls = [];
        activeIndex = 0;

        const filterWrap = d.createElement('div');
        filterWrap.className = 'prks-folder-nav__filter';
        const filterInput = d.createElement('input');
        filterInput.type = 'search';
        filterInput.id = 'prks-folder-nav-filter';
        filterInput.className = 'prks-input prks-folder-nav__filter-input';
        filterInput.setAttribute('aria-label', 'Filter folders');
        filterInput.placeholder = 'Filter folders…';
        filterInput.autocomplete = 'off';
        filterInput.value = filterQuery;
        filterInput.addEventListener('input', function () {
            filterQuery = String(filterInput.value || '');
            renderPanelBody(hierarchyRows || []);
            const again = d.getElementById('prks-folder-nav-filter');
            if (again) {
                again.focus();
                try {
                    const len = again.value.length;
                    again.setSelectionRange(len, len);
                } catch (_e) {
                    /* ignore */
                }
            }
        });
        filterInput.addEventListener('keydown', function (ev) {
            if (ev.key === 'ArrowDown') {
                ev.preventDefault();
                if (optionEls.length) setActiveOption(0);
                return;
            }
            if (ev.key === 'ArrowUp') {
                ev.preventDefault();
                if (optionEls.length) setActiveOption(optionEls.length - 1);
                return;
            }
            if (ev.key === 'Escape') {
                ev.preventDefault();
                closePanel();
            }
        });
        filterWrap.appendChild(filterInput);
        panel.appendChild(filterWrap);

        const listHost = d.createElement('div');
        listHost.className = 'prks-folder-nav__list';
        panel.appendChild(listHost);

        const q = String(filterQuery || '').trim();
        if (q) {
            fillFilter(listHost, rows, q);
        } else {
            fillNearby(listHost, prksFolderHierarchyContext(currentFolderId, rows));
        }

        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(panel);

        const currentOpt = optionEls.find(function (el) {
            return el.classList.contains('is-current');
        });
        if (currentOpt) setActiveOption(optionEls.indexOf(currentOpt));
        else if (optionEls.length) setActiveOption(0);
    }

    async function loadHierarchy() {
        if (Array.isArray(hierarchyRows)) return hierarchyRows;
        if (loadPromise) return loadPromise;
        loadPromise = (async function () {
            let rows = null;
            try {
                if (typeof root.prksOfflineListFetch === 'function') {
                    const result = await root.prksOfflineListFetch(
                        'folders:index',
                        '/api/folders',
                        null,
                        {
                            domain: 'folders',
                            validate:
                                typeof root.prksIsFoldersIndexShape === 'function'
                                    ? root.prksIsFoldersIndexShape
                                    : undefined,
                        }
                    );
                    if (typeof root.prksResolveOfflineFoldersIndex === 'function') {
                        rows = root.prksResolveOfflineFoldersIndex(result);
                    } else if (result && Array.isArray(result.value)) {
                        rows = result.value;
                    }
                }
            } catch (_e) {
                rows = null;
            }
            if (!Array.isArray(rows) && typeof root.fetchFolders === 'function') {
                try {
                    rows = await root.fetchFolders();
                } catch (_e2) {
                    rows = [];
                }
            }
            if (!Array.isArray(rows)) rows = [];
            if (typeof root.prksEffectiveFolderRows === 'function') {
                try {
                    rows = await root.prksEffectiveFolderRows(rows);
                } catch (_e3) {
                    /* keep raw */
                }
            }
            hierarchyRows = Array.isArray(rows) ? rows : [];
            return hierarchyRows;
        })();
        try {
            return await loadPromise;
        } finally {
            loadPromise = null;
        }
    }

    function updateTriggerPath(folder, rows) {
        const d = doc();
        const trigger = d && d.getElementById('prks-folder-nav-trigger');
        if (!trigger) return;
        const pathEl = trigger.querySelector('.prks-folder-nav__path');
        if (!pathEl) return;
        const id = folder && folder.id != null ? String(folder.id) : currentFolderId;
        const ctx = prksFolderHierarchyContext(id, rows);
        const parts = ctx.pathParts.length
            ? ctx.pathParts
            : folder && folder.parent && folder.parent.title
              ? [String(folder.parent.title), String(folder.title || 'Folder')]
              : [String((folder && folder.title) || 'Folder')];
        const full = parts.join(' › ');
        const shown =
            parts.length <= 3 ? full : '… › ' + parts.slice(-2).join(' › ');
        pathEl.textContent = shown;
        trigger.setAttribute('title', full);
    }

    async function openPanel(trigger) {
        const panel = ensurePanel();
        if (!panel || !trigger) return;
        if (!panel.hidden && restoreTarget === trigger) {
            closePanel();
            return;
        }
        restoreTarget = trigger;
        currentFolderId = trigger.getAttribute('data-prks-folder-nav-current') || currentFolderId;
        setTriggerExpanded(true);
        filterQuery = '';
        panel.replaceChildren();
        const loading = doc().createElement('p');
        loading.className = 'prks-folder-nav__empty meta-row';
        loading.textContent = 'Loading folders…';
        panel.appendChild(loading);
        positionPanel(trigger);

        const rows = await loadHierarchy();
        if (!panelEl || panelEl.hidden) return;
        // Owner/trigger may have unmounted during the await (tab switch).
        const d = doc();
        const liveTrigger = d && d.getElementById('prks-folder-nav-trigger');
        if (!liveTrigger || liveTrigger !== trigger) {
            closePanel({ skipFocus: true });
            return;
        }
        updateTriggerPath({ id: currentFolderId }, rows);
        renderPanelBody(rows);
        positionPanel(trigger);
        const filterInput = d.getElementById('prks-folder-nav-filter');
        if (filterInput) filterInput.focus();
        else if (optionEls.length) setActiveOption(activeIndex);
    }

    function onDocPointer(ev) {
        if (!panelEl || panelEl.hidden) return;
        const t = ev.target;
        if (panelEl.contains(t)) return;
        const trigger = doc().getElementById('prks-folder-nav-trigger');
        if (trigger && (trigger === t || (trigger.contains && trigger.contains(t)))) return;
        closePanel({ skipFocus: true });
    }

    function onDocKey(ev) {
        if (!panelEl || panelEl.hidden) return;
        const t = ev.target;
        const filterFocused = t && t.id === 'prks-folder-nav-filter';
        if (ev.key === 'Escape') {
            ev.preventDefault();
            closePanel();
            return;
        }
        if (filterFocused) return;
        if (ev.key === 'ArrowDown') {
            ev.preventDefault();
            setActiveOption(activeIndex + 1);
            return;
        }
        if (ev.key === 'ArrowUp') {
            ev.preventDefault();
            setActiveOption(activeIndex - 1);
            return;
        }
        if (ev.key === 'Home') {
            ev.preventDefault();
            setActiveOption(0);
            return;
        }
        if (ev.key === 'End') {
            ev.preventDefault();
            setActiveOption(optionEls.length - 1);
            return;
        }
        if (ev.key === 'Enter' || ev.key === ' ') {
            if (t && optionEls.indexOf(t) >= 0) {
                ev.preventDefault();
                t.click();
            }
        }
    }

    function bindTrigger(container) {
        const trigger = container && container.querySelector('#prks-folder-nav-trigger');
        if (!trigger || trigger.dataset.prksFolderNavBound === '1') return;
        trigger.dataset.prksFolderNavBound = '1';
        trigger.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            void openPanel(trigger);
        });
        trigger.addEventListener('keydown', function (ev) {
            if (ev.key === 'ArrowDown' || ev.key === 'Enter' || ev.key === ' ') {
                if (panelEl && !panelEl.hidden) return;
                ev.preventDefault();
                void openPanel(trigger);
            }
        });
    }

    function ensureGlobalListeners() {
        if (boundGlobal) return;
        const d = doc();
        if (!d) return;
        boundGlobal = true;
        ensurePanel();
        d.addEventListener('pointerdown', onDocPointer, true);
        d.addEventListener('keydown', onDocKey, true);
    }

    /**
     * Mount the switcher on a Folder detail root. Loads the hierarchy once to
     * paint the full path crumb (still a single catalog request, not per-row).
     */
    function prksMountFolderHierarchyNav(ctx, folder, container) {
        if (!container || !folder || folder.id == null) return;
        ensureGlobalListeners();
        ownerTabId = ctx && ctx.tabId ? String(ctx.tabId) : null;
        currentFolderId = String(folder.id);
        hierarchyRows = null;
        closePanel({ skipFocus: true, keepRestore: false });
        bindTrigger(container);

        void (async function () {
            const rows = await loadHierarchy();
            const d = doc();
            const trigger = d && d.getElementById('prks-folder-nav-trigger');
            if (!trigger) return;
            if (String(trigger.getAttribute('data-prks-folder-nav-current') || '') !== String(folder.id)) {
                return;
            }
            if (ctx && typeof ctx.isCurrent === 'function' && ctx.routeGeneration != null) {
                /* TabContext may have moved on; still safe to update path text if trigger matches. */
            }
            updateTriggerPath(folder, rows);
        })();
    }

    function prksFolderNavTriggerHtml(folder, rows) {
        return crumbHtml(folder, rows);
    }

    function resetForTests() {
        closePanel({ skipFocus: true });
        hierarchyRows = null;
        loadPromise = null;
        ownerTabId = null;
        currentFolderId = null;
        filterQuery = '';
        if (panelEl && panelEl.parentNode) panelEl.parentNode.removeChild(panelEl);
        panelEl = null;
        optionEls = [];
        boundGlobal = false;
    }

    const api = {
        prksFolderHierarchyContext: prksFolderHierarchyContext,
        prksFolderHierarchyFilter: prksFolderHierarchyFilter,
        prksFolderNavTriggerHtml: prksFolderNavTriggerHtml,
        prksMountFolderHierarchyNav: prksMountFolderHierarchyNav,
        prksCloseFolderHierarchyNav: closePanel,
        prksFolderHierarchyNavResetForTests: resetForTests,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
