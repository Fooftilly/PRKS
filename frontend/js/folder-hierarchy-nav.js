/**
 * Library Navigation V1 — compact hierarchical Folder switcher on Folder detail.
 *
 * Reuses the complete folders:index hierarchy (one set-based fetch). Selection
 * goes through prksNavigate with the owning TabContext id so Main / parked /
 * Secondary panes navigate their own route, not another workspace context.
 *
 * Ownership is instance-local: each Folder detail mounts its own trigger with
 * per-tab IDs and data attributes. Open/select derive tabId + folderId from
 * the clicked trigger — never from a module-level "last mount wins" slot.
 */
(function (root) {
    'use strict';

    const PANEL_ID = 'prks-folder-nav-panel';
    const LISTBOX_ID = 'prks-folder-nav-listbox';
    const FILTER_ID = 'prks-folder-nav-filter';
    const FILTER_LIMIT = 40;
    const NEARBY_CHILD_LIMIT = 60;
    const NEARBY_SIBLING_LIMIT = 80;

    let panelEl = null;
    let listboxEl = null;
    let optionEls = [];
    let activeIndex = 0;
    let restoreTarget = null;
    /** Session state for the currently open panel only (derived from trigger). */
    let openOwnerTabId = null;
    let openFolderId = null;
    let hierarchyRows = null; // raw folders:index base (before pending overlay)
    let hierarchyLoadError = false;
    /** Path labels memoized for the current hierarchyRows snapshot (cleared on reload). */
    let hierarchyPathCache = null;
    /** Fingerprint of unsettled folder-structure durable ops; changes invalidate the base. */
    let hierarchyOpsFingerprint = undefined;
    let hierarchySyncBound = false;
    let boundGlobal = false;
    let boundViewport = false;
    let filterQuery = '';

    const FOLDER_STRUCTURE_OPS = {
        CREATE_FOLDER: true,
        SET_FOLDER_FIELD: true,
        DELETE_FOLDER: true,
    };

    function folderStructureOpsFingerprint(ops) {
        return (Array.isArray(ops) ? ops : [])
            .filter(function (op) {
                return (
                    op &&
                    op.entity_type === 'folder' &&
                    FOLDER_STRUCTURE_OPS[op.operation] &&
                    op.status !== 'acknowledged'
                );
            })
            .map(function (op) {
                return String(op.op_id || '') + ':' + String(op.status || '') + ':' + String(op.sequence || 0);
            })
            .sort()
            .join('|');
    }

    function invalidateHierarchyBase() {
        hierarchyRows = null;
        clearHierarchyPathCache();
    }

    async function projectHierarchyRows(base) {
        if (!Array.isArray(base)) return base;
        if (typeof root.prksEffectiveFolderRows === 'function') {
            try {
                return await root.prksEffectiveFolderRows(base);
            } catch (_e) {
                return base;
            }
        }
        return base;
    }

    function ensureHierarchySyncBound() {
        if (hierarchySyncBound) return;
        hierarchySyncBound = true;
        if (!root.prksSync || typeof root.prksSync.subscribe !== 'function') return;
        root.prksSync.subscribe(function () {
            void (async function () {
                let ops = [];
                try {
                    if (root.prksSync.store && typeof root.prksSync.store.listOperations === 'function') {
                        ops = (await root.prksSync.store.listOperations()) || [];
                    }
                } catch (_e) {
                    return;
                }
                const fp = folderStructureOpsFingerprint(ops);
                if (hierarchyOpsFingerprint === undefined) {
                    hierarchyOpsFingerprint = fp;
                    return;
                }
                if (fp === hierarchyOpsFingerprint) return;
                hierarchyOpsFingerprint = fp;
                invalidateHierarchyBase();
                // Open panel: reload so crumbs/list match the new structure.
                if (panelEl && !panelEl.hidden && restoreTarget) {
                    const trigger = restoreTarget;
                    const rows = await loadHierarchy(true, signalForOwner(trigger));
                    if (!panelEl || panelEl.hidden || restoreTarget !== trigger) return;
                    renderPanelBody(rows);
                    if (Array.isArray(rows)) {
                        updateTriggerPath(trigger, { id: openFolderId }, rows);
                        positionPanel(trigger);
                    }
                }
            })();
        });
    }

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

    function safeDomId(value) {
        return String(value == null ? '' : value).replace(/[^A-Za-z0-9_-]/g, '_');
    }

    function triggerIdForTab(tabId) {
        const safe = safeDomId(tabId);
        return safe ? 'prks-folder-nav-trigger-' + safe : 'prks-folder-nav-trigger';
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

    function pathLabelFor(folderId, byId, pathCache) {
        const key = String(folderId);
        const cache = pathCache || hierarchyPathCache;
        if (cache && cache.has(key)) return cache.get(key);
        let label;
        if (typeof root.prksFolderPathLabel === 'function') {
            label = root.prksFolderPathLabel(folderId, byId);
        } else {
            const parts = [];
            const guard = new Set();
            let cur = byId.get(folderId);
            while (cur && !guard.has(String(cur.id))) {
                guard.add(String(cur.id));
                parts.unshift(String(cur.title || 'Folder'));
                cur = cur.parent_id ? byId.get(String(cur.parent_id)) : null;
            }
            label = parts.join(' → ');
        }
        if (cache) cache.set(key, label);
        return label;
    }

    function ensureHierarchyPathCache() {
        if (!hierarchyPathCache) hierarchyPathCache = new Map();
        return hierarchyPathCache;
    }

    function clearHierarchyPathCache() {
        hierarchyPathCache = null;
    }

    function filterMatchRank(titleLower, pathLower, q) {
        if (titleLower === q) return 0;
        if (titleLower.indexOf(q) === 0) return 1;
        if (titleLower.indexOf(q) !== -1) return 2;
        if (pathLower.indexOf(q) !== -1) return 3;
        return 4;
    }

    /** Pure: filter the complete hierarchy; rank then bound (exact titles win). */
    function prksFolderHierarchyFilter(rows, query, limit) {
        const list = Array.isArray(rows) ? rows : [];
        const byId = buildById(list);
        // Prefer the hierarchy-load memo when filtering the live catalogue;
        // fall back to a call-local map for pure selftests / alternate row sets.
        const pathCache =
            Array.isArray(hierarchyRows) && list === hierarchyRows
                ? ensureHierarchyPathCache()
                : new Map();
        const q = String(query || '')
            .trim()
            .toLowerCase();
        const max = typeof limit === 'number' && limit > 0 ? limit : FILTER_LIMIT;
        if (!q) return [];
        const scored = [];
        for (let i = 0; i < list.length; i++) {
            const row = list[i];
            if (!row || row.id == null) continue;
            const title = String(row.title || 'Folder');
            const titleLower = title.toLowerCase();
            const path = pathLabelFor(String(row.id), byId, pathCache);
            const pathLower = path.toLowerCase();
            const rank = filterMatchRank(titleLower, pathLower, q);
            if (rank > 3) continue;
            scored.push({
                id: String(row.id),
                title: title,
                path: path,
                parent_id: row.parent_id == null || row.parent_id === '' ? null : String(row.parent_id),
                _rank: rank,
            });
        }
        scored.sort(function (a, b) {
            if (a._rank !== b._rank) return a._rank - b._rank;
            return String(a.title || '').localeCompare(String(b.title || ''), undefined, {
                sensitivity: 'base',
            });
        });
        return scored.slice(0, max).map(function (row) {
            return {
                id: row.id,
                title: row.title,
                path: row.path,
                parent_id: row.parent_id,
            };
        });
    }

    function crumbSep() {
        return '<span class="prks-folder-nav__sep" aria-hidden="true">›</span>';
    }

    function crumbButton(id, title) {
        return (
            '<button type="button" class="prks-folder-nav__crumb" data-prks-folder-nav-goto="' +
            esc(String(id)) +
            '">' +
            esc(String(title || 'Folder')) +
            '</button>'
        );
    }

    function crumbCurrent(title) {
        return (
            '<span class="prks-folder-nav__crumb is-current" aria-current="page">' +
            esc(String(title || 'Folder')) +
            '</span>'
        );
    }

    function libraryCrumb() {
        return (
            '<button type="button" class="prks-folder-nav__crumb prks-folder-nav__crumb--library" ' +
            'data-prks-folder-nav-library="1">Library</button>'
        );
    }

    function initialCrumbsHtml(folder) {
        const title = String((folder && folder.title) || 'Folder');
        const parentTitle =
            folder && folder.parent && folder.parent.title ? String(folder.parent.title) : '';
        const parentId =
            folder && folder.parent && folder.parent.id != null ? String(folder.parent.id) : '';
        const parts = [libraryCrumb()];
        if (parentTitle && parentId) parts.push(crumbButton(parentId, parentTitle));
        parts.push(crumbCurrent(title));
        return parts.join(crumbSep());
    }

    function crumbHtml(folder, rows, options) {
        const id = folder && folder.id != null ? String(folder.id) : '';
        if (!id) return '';
        const opts = options || {};
        const tabId = opts.tabId != null ? String(opts.tabId) : '';
        const triggerId = triggerIdForTab(tabId);
        // Initial paint uses known parent/title; mount hydrates full path + nearby.
        return (
            '<div class="prks-folder-nav__band" data-prks-folder-nav-current="' +
            esc(id) +
            '"' +
            (tabId ? ' data-prks-folder-nav-tab-id="' + esc(tabId) + '"' : '') +
            '>' +
            '<div class="prks-folder-nav__location">' +
            '<div class="prks-folder-nav__location-head">' +
            '<span class="prks-folder-nav__eyebrow">Location</span>' +
            '<button type="button" class="prks-folder-nav__trigger" id="' +
            esc(triggerId) +
            '" ' +
            'aria-haspopup="dialog" aria-expanded="false" aria-controls="' +
            PANEL_ID +
            '" ' +
            'data-prks-folder-nav-current="' +
            esc(id) +
            '"' +
            (tabId ? ' data-prks-folder-nav-tab-id="' + esc(tabId) + '"' : '') +
            '>' +
            '<span class="prks-folder-nav__trigger-label">Browse hierarchy</span>' +
            '<span class="prks-sr-only">Open folder navigation</span>' +
            '<span class="prks-folder-nav__chevron" aria-hidden="true">' +
            (typeof root.prksIcon === 'function' ? root.prksIcon('chevronDown', { size: 14 }) : '▾') +
            '</span>' +
            '</button>' +
            '</div>' +
            '<div class="prks-folder-nav__crumbs" data-prks-role="folder-nav-crumbs">' +
            initialCrumbsHtml(folder) +
            '</div>' +
            '</div>' +
            '<div class="prks-folder-nav__nearby" data-prks-role="folder-nav-nearby" hidden>' +
            '<span class="prks-folder-nav__nearby-empty">Loading nearby folders…</span>' +
            '</div>' +
            '</div>'
        );
    }

    function findNavRoot(container) {
        if (!container || typeof container.querySelector !== 'function') return null;
        return container.querySelector('[data-prks-role="folder-hierarchy-nav"], .prks-folder-nav');
    }

    function navRootFrom(el) {
        if (!el || typeof el.closest !== 'function') return null;
        return el.closest('[data-prks-role="folder-hierarchy-nav"], .prks-folder-nav');
    }

    function ownerTabIdFrom(el) {
        const band =
            el && typeof el.closest === 'function'
                ? el.closest('[data-prks-folder-nav-tab-id], .prks-folder-nav__band, .prks-folder-nav')
                : null;
        return (
            (band && band.getAttribute('data-prks-folder-nav-tab-id')) ||
            (el && el.getAttribute && el.getAttribute('data-prks-folder-nav-tab-id')) ||
            openOwnerTabId ||
            null
        );
    }

    function paintBand(navRoot, folder, rows) {
        if (!navRoot) return;
        const band = navRoot.querySelector('.prks-folder-nav__band') || navRoot;
        const id =
            (folder && folder.id != null ? String(folder.id) : null) ||
            band.getAttribute('data-prks-folder-nav-current') ||
            (navRoot.querySelector('[data-prks-folder-nav-current]') &&
                navRoot
                    .querySelector('[data-prks-folder-nav-current]')
                    .getAttribute('data-prks-folder-nav-current'));
        if (!id) return;
        band.setAttribute('data-prks-folder-nav-current', id);
        const ctx = prksFolderHierarchyContext(id, rows);
        const crumbsHost = navRoot.querySelector('[data-prks-role="folder-nav-crumbs"]');
        const nearbyHost = navRoot.querySelector('[data-prks-role="folder-nav-nearby"]');
        const trigger = navRoot.querySelector('.prks-folder-nav__trigger');

        if (crumbsHost) {
            const parts = [libraryCrumb()];
            if (ctx.found && ctx.ancestors.length) {
                ctx.ancestors.forEach(function (row) {
                    parts.push(crumbButton(row.id, row.title));
                });
            } else if (!ctx.found && folder && folder.parent && folder.parent.id != null) {
                parts.push(crumbButton(folder.parent.id, folder.parent.title));
            }
            const currentTitle = ctx.current
                ? String(ctx.current.title || 'Folder')
                : String((folder && folder.title) || 'Folder');
            parts.push(crumbCurrent(currentTitle));
            crumbsHost.innerHTML = parts.join(crumbSep());
            const full = (ctx.pathParts.length ? ctx.pathParts : [currentTitle]).join(' › ');
            if (trigger) trigger.setAttribute('title', 'Browse hierarchy — ' + full);
            band.setAttribute('aria-label', 'Folder location: ' + full);
        }

        if (nearbyHost) {
            const chips = [];
            const siblingLimit = 8;
            const childLimit = 8;
            if (ctx.found) {
                const siblings = truncateList(
                    ctx.siblings.filter(function (row) {
                        return String(row.id) !== String(ctx.current.id);
                    }),
                    siblingLimit
                );
                if (siblings.length) {
                    chips.push(
                        '<div class="prks-folder-nav__nearby-group">' +
                            '<span class="prks-folder-nav__nearby-label">At this level</span>' +
                            '<div class="prks-folder-nav__chips">'
                    );
                    siblings.forEach(function (row) {
                        chips.push(
                            '<button type="button" class="prks-folder-nav__chip" data-prks-folder-nav-goto="' +
                                esc(String(row.id)) +
                                '">' +
                                esc(String(row.title || 'Folder')) +
                                '</button>'
                        );
                    });
                    if (ctx.siblings.length - 1 > siblings.length) {
                        chips.push(
                            '<span class="prks-folder-nav__nearby-more">+' +
                                (ctx.siblings.length - 1 - siblings.length) +
                                ' more</span>'
                        );
                    }
                    chips.push('</div></div>');
                }
                const children = truncateList(ctx.children, childLimit);
                if (children.length) {
                    chips.push(
                        '<div class="prks-folder-nav__nearby-group">' +
                            '<span class="prks-folder-nav__nearby-label">Inside</span>' +
                            '<div class="prks-folder-nav__chips">'
                    );
                    children.forEach(function (row) {
                        chips.push(
                            '<button type="button" class="prks-folder-nav__chip" data-prks-folder-nav-goto="' +
                                esc(String(row.id)) +
                                '">' +
                                esc(String(row.title || 'Folder')) +
                                '</button>'
                        );
                    });
                    if (ctx.children.length > children.length) {
                        chips.push(
                            '<span class="prks-folder-nav__nearby-more">+' +
                                (ctx.children.length - children.length) +
                                ' more</span>'
                        );
                    }
                    chips.push('</div></div>');
                } else if (!siblings.length) {
                    chips.push(
                        '<span class="prks-folder-nav__nearby-empty">No sibling or child folders nearby — use Browse hierarchy to jump elsewhere</span>'
                    );
                }
            } else if (Array.isArray(rows)) {
                chips.push(
                    '<span class="prks-folder-nav__nearby-empty">Hierarchy not cached yet — open Browse hierarchy</span>'
                );
            } else if (hierarchyLoadError) {
                chips.push(
                    '<span class="prks-folder-nav__nearby-empty">Could not load nearby folders — use Browse hierarchy to retry</span>'
                );
            } else {
                chips.push(
                    '<span class="prks-folder-nav__nearby-empty">Loading nearby folders…</span>'
                );
            }
            nearbyHost.innerHTML = chips.join('');
            nearbyHost.hidden = false;
        }
    }

    function navigateOwned(folderId, ownerEl) {
        const tabId = ownerTabIdFrom(ownerEl);
        const hash = '#/folders/' + encodeURIComponent(String(folderId || ''));
        closePanel({ skipFocus: true });
        if (typeof root.prksNavigate !== 'function') return;
        const opts = {};
        if (tabId) opts.tabId = tabId;
        root.prksNavigate(hash, opts);
    }

    function navigateLibrary(ownerEl) {
        const tabId = ownerTabIdFrom(ownerEl);
        closePanel({ skipFocus: true });
        if (typeof root.prksNavigate !== 'function') return;
        root.prksNavigate('#/folders', tabId ? { tabId: tabId } : {});
    }

    function ensurePanel() {
        const d = doc();
        if (!d || !d.body) return null;
        if (panelEl && d.body.contains(panelEl)) return panelEl;
        panelEl = d.createElement('div');
        panelEl.id = PANEL_ID;
        panelEl.className = 'prks-folder-nav__panel';
        panelEl.setAttribute('role', 'dialog');
        panelEl.setAttribute('aria-label', 'Folder hierarchy');
        panelEl.hidden = true;
        d.body.appendChild(panelEl);
        return panelEl;
    }

    function setTriggerExpanded(trigger, expanded) {
        if (trigger) {
            trigger.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            return;
        }
        if (restoreTarget) {
            restoreTarget.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        }
    }

    function closePanel(opts) {
        const options = opts || {};
        const panel = ensurePanel();
        if (!panel || panel.hidden) {
            if (!options.keepRestore) restoreTarget = null;
            openOwnerTabId = null;
            openFolderId = null;
            return;
        }
        panel.hidden = true;
        panel.replaceChildren();
        listboxEl = null;
        optionEls = [];
        activeIndex = 0;
        filterQuery = '';
        openOwnerTabId = null;
        openFolderId = null;
        setTriggerExpanded(restoreTarget, false);
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
        if (listboxEl) {
            const active = optionEls[i];
            if (active && typeof active.id === 'string' && active.id) {
                listboxEl.setAttribute('aria-activedescendant', active.id);
            }
        }
    }

    function navigateToFolder(folderId) {
        const hash = '#/folders/' + encodeURIComponent(String(folderId || ''));
        const tabId = openOwnerTabId;
        closePanel({ skipFocus: true });
        if (typeof root.prksNavigate !== 'function') return;
        const opts = {};
        if (tabId) opts.tabId = tabId;
        root.prksNavigate(hash, opts);
    }

    function addSectionLabel(host, text) {
        const d = doc();
        const lab = d.createElement('div');
        lab.className = 'prks-folder-nav__section';
        lab.setAttribute('role', 'presentation');
        lab.textContent = text;
        host.appendChild(lab);
    }

    function addOption(host, spec) {
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
        host.appendChild(btn);
        optionEls.push(btn);
        return btn;
    }

    function addEmpty(host, text) {
        const d = doc();
        const p = d.createElement('p');
        p.className = 'prks-folder-nav__empty meta-row';
        p.textContent = text;
        host.appendChild(p);
    }

    function truncateList(list, limit) {
        if (!Array.isArray(list)) return [];
        if (list.length <= limit) return list;
        return list.slice(0, limit);
    }

    function fillNearby(host, ctx) {
        if (!ctx.found) {
            addEmpty(host, 'This folder is not in the cached hierarchy.');
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
                const tabId = openOwnerTabId;
                closePanel({ skipFocus: true });
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate('#/folders', tabId ? { tabId: tabId } : {});
                }
            });
            host.appendChild(btn);
            optionEls.push(btn);
            return;
        }

        if (ctx.ancestors.length) {
            addSectionLabel(host, 'Path');
            ctx.ancestors.forEach(function (row, i) {
                addOption(host, {
                    id: String(row.id),
                    title: String(row.title || 'Folder'),
                    meta: i === ctx.ancestors.length - 1 ? 'Parent' : 'Ancestor',
                    depth: i,
                    current: false,
                });
            });
        } else {
            addSectionLabel(host, 'Path');
            addEmpty(host, 'Top-level folder');
        }

        addSectionLabel(host, 'This level');
        const siblings = truncateList(ctx.siblings, NEARBY_SIBLING_LIMIT);
        if (!siblings.length) {
            addEmpty(host, 'No folders at this level.');
        } else {
            siblings.forEach(function (row) {
                const isCurrent = String(row.id) === String(ctx.current.id);
                addOption(host, {
                    id: String(row.id),
                    title: String(row.title || 'Folder'),
                    meta: isCurrent ? null : 'Sibling',
                    current: isCurrent,
                });
            });
            if (ctx.siblings.length > siblings.length) {
                addEmpty(
                    host,
                    '+' + (ctx.siblings.length - siblings.length) + ' more — use filter to find them'
                );
            }
        }

        addSectionLabel(host, 'Inside');
        const children = truncateList(ctx.children, NEARBY_CHILD_LIMIT);
        if (!children.length) {
            addEmpty(host, 'No subfolders.');
        } else {
            children.forEach(function (row) {
                const childCount = Number(row.child_count || 0);
                addOption(host, {
                    id: String(row.id),
                    title: String(row.title || 'Folder'),
                    meta: childCount > 0 ? childCount + (childCount === 1 ? ' subfolder' : ' subfolders') : 'Child',
                    current: false,
                });
            });
            if (ctx.children.length > children.length) {
                addEmpty(
                    host,
                    '+' + (ctx.children.length - children.length) + ' more — use filter to find them'
                );
            }
        }
    }

    function fillFilter(host, rows, query) {
        const matches = prksFolderHierarchyFilter(rows, query, FILTER_LIMIT);
        addSectionLabel(host, 'Matching folders');
        if (!matches.length) {
            addEmpty(host, 'No folders match.');
            return;
        }
        matches.forEach(function (row) {
            const isCurrent = openFolderId && String(row.id) === String(openFolderId);
            addOption(host, {
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
        listboxEl = null;

        const filterWrap = d.createElement('div');
        filterWrap.className = 'prks-folder-nav__filter';
        const filterInput = d.createElement('input');
        filterInput.type = 'search';
        filterInput.id = FILTER_ID;
        filterInput.className = 'prks-input prks-folder-nav__filter-input';
        filterInput.setAttribute('aria-label', 'Filter folders');
        filterInput.setAttribute('aria-controls', LISTBOX_ID);
        filterInput.placeholder = 'Filter folders…';
        filterInput.autocomplete = 'off';
        filterInput.value = filterQuery;
        filterInput.addEventListener('input', function () {
            filterQuery = String(filterInput.value || '');
            void (async function () {
                const rows = await loadHierarchy(false, signalForOwner(restoreTarget));
                if (!panelEl || panelEl.hidden) return;
                renderPanelBody(rows);
                const again = d.getElementById(FILTER_ID);
                if (again) {
                    again.focus();
                    try {
                        const len = again.value.length;
                        again.setSelectionRange(len, len);
                    } catch (_e) {
                        /* ignore */
                    }
                }
            })();
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

        // Filter stays outside the listbox (valid dialog + listbox structure).
        const listHost = d.createElement('div');
        listHost.id = LISTBOX_ID;
        listHost.className = 'prks-folder-nav__list';
        listHost.setAttribute('role', 'listbox');
        listHost.setAttribute('aria-label', 'Folders');
        listHost.tabIndex = -1;
        panel.appendChild(listHost);
        listboxEl = listHost;

        const q = String(filterQuery || '').trim();
        if (hierarchyLoadError && !Array.isArray(rows)) {
            addEmpty(listHost, 'Could not load folders.');
            const retry = d.createElement('button');
            retry.type = 'button';
            retry.className = 'prks-folder-nav__option';
            retry.setAttribute('role', 'option');
            retry.id = 'prks-folder-nav-opt-retry';
            retry.setAttribute('aria-selected', 'false');
            retry.tabIndex = -1;
            const title = d.createElement('span');
            title.className = 'prks-folder-nav__option-title';
            title.textContent = 'Retry';
            retry.appendChild(title);
            retry.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                hierarchyRows = null;
                hierarchyLoadError = false;
                clearHierarchyPathCache();
                void (async function () {
                    const rows = await loadHierarchy(true, signalForOwner(restoreTarget));
                    if (!panelEl || panelEl.hidden) return;
                    renderPanelBody(rows);
                    if (restoreTarget) {
                        positionPanel(restoreTarget);
                        const navRoot = navRootFrom(restoreTarget);
                        const folderId =
                            restoreTarget.getAttribute('data-prks-folder-nav-current') ||
                            openFolderId;
                        if (navRoot && folderId) {
                            paintBand(navRoot, { id: folderId }, rows);
                        }
                    }
                })();
            });
            listHost.appendChild(retry);
            optionEls.push(retry);
        } else if (q) {
            fillFilter(listHost, rows || [], q);
        } else {
            fillNearby(listHost, prksFolderHierarchyContext(openFolderId, rows || []));
        }

        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(panel);

        const currentOpt = optionEls.find(function (el) {
            return el.classList.contains('is-current');
        });
        if (currentOpt) setActiveOption(optionEls.indexOf(currentOpt));
        else if (optionEls.length) setActiveOption(0);
    }

    function isAbortError(err) {
        if (typeof root.prksIsAbortError === 'function') return root.prksIsAbortError(err);
        return !!(err && err.name === 'AbortError');
    }

    function signalForOwner(owner) {
        if (!owner) return null;
        if (owner.abortController && owner.abortController.signal) {
            return owner.abortController.signal;
        }
        const tabId =
            typeof owner.getAttribute === 'function'
                ? owner.getAttribute('data-prks-folder-nav-tab-id')
                : owner.tabId != null
                  ? String(owner.tabId)
                  : null;
        if (!tabId || typeof root.prksGetTabContext !== 'function') return null;
        const ctx = root.prksGetTabContext(tabId);
        return ctx && ctx.abortController && ctx.abortController.signal
            ? ctx.abortController.signal
            : null;
    }

    /**
     * Load folders:index for the switcher. Pass the owning TabContext route
     * AbortSignal so cold-park / beginRoute aborts the request. Concurrent
     * panes each pass their own signal; prksRequest dedupes the underlying GET
     * and only cancels the network flight when every subscriber has aborted.
     *
     * The module caches the raw catalogue base. Pending CREATE/SET/DELETE
     * folder ops are projected on every read via prksEffectiveFolderRows, and
     * the base is invalidated when that unsettled structure fingerprint moves
     * (enqueue or ACK) so renamed/deleted folders cannot stick after sync.
     */
    async function loadHierarchy(force, signal) {
        ensureHierarchySyncBound();
        if (!force && Array.isArray(hierarchyRows)) {
            return await projectHierarchyRows(hierarchyRows);
        }
        if (!force && hierarchyLoadError && hierarchyRows === null) {
            return null;
        }
        if (signal && signal.aborted) return null;

        let rows = null;
        let offlineUnavailable = false;
        try {
            if (typeof root.prksOfflineListFetch === 'function') {
                const result = await root.prksOfflineListFetch(
                    'folders:index',
                    '/api/folders',
                    signal || null,
                    {
                        domain: 'folders',
                        validate:
                            typeof root.prksIsFoldersIndexShape === 'function'
                                ? root.prksIsFoldersIndexShape
                                : undefined,
                    }
                );
                if (signal && signal.aborted) return null;
                if (result && result.source === 'unavailable') {
                    // Do NOT fall through to fetchFolders(): that path collapses
                    // unavailable into [] via prksEffectiveFolderCatalogue and
                    // would clear hierarchyLoadError with a fake empty tree.
                    offlineUnavailable = true;
                    rows = null;
                } else if (typeof root.prksResolveOfflineFoldersIndex === 'function') {
                    rows = root.prksResolveOfflineFoldersIndex(result);
                } else if (result && Array.isArray(result.value)) {
                    rows = result.value;
                }
            }
        } catch (e) {
            if (isAbortError(e) || (signal && signal.aborted)) return null;
            offlineUnavailable = true;
            rows = null;
        }
        // Legacy/test path only when the source-aware offline reader is absent.
        if (
            !Array.isArray(rows) &&
            !offlineUnavailable &&
            typeof root.prksOfflineListFetch !== 'function' &&
            typeof root.fetchFolders === 'function'
        ) {
            try {
                rows = await root.fetchFolders(signal ? { signal: signal } : {});
                if (signal && signal.aborted) return null;
            } catch (e2) {
                if (isAbortError(e2) || (signal && signal.aborted)) return null;
                rows = null;
            }
        }
        if (signal && signal.aborted) return null;
        if (!Array.isArray(rows)) {
            hierarchyRows = null;
            hierarchyLoadError = true;
            clearHierarchyPathCache();
            return null;
        }
        if (signal && signal.aborted) return null;
        hierarchyRows = rows;
        hierarchyLoadError = false;
        clearHierarchyPathCache();
        try {
            if (root.prksSync && root.prksSync.store && typeof root.prksSync.store.listOperations === 'function') {
                hierarchyOpsFingerprint = folderStructureOpsFingerprint(
                    (await root.prksSync.store.listOperations()) || []
                );
            }
        } catch (_fp) {
            /* fingerprint stays; next sync subscribe will set it */
        }
        return await projectHierarchyRows(hierarchyRows);
    }

    function updateTriggerPath(trigger, folder, rows) {
        const navRoot = navRootFrom(trigger) || (trigger && trigger.parentElement);
        if (navRoot) {
            paintBand(navRoot, folder, rows);
            return;
        }
        if (!trigger) return;
        const id =
            (folder && folder.id != null ? String(folder.id) : null) ||
            trigger.getAttribute('data-prks-folder-nav-current') ||
            openFolderId;
        const ctx = prksFolderHierarchyContext(id, rows);
        const parts = ctx.pathParts.length
            ? ctx.pathParts
            : folder && folder.parent && folder.parent.title
              ? [String(folder.parent.title), String(folder.title || 'Folder')]
              : [String((folder && folder.title) || 'Folder')];
        trigger.setAttribute('title', 'Browse hierarchy — ' + parts.join(' › '));
    }

    function triggerStillLive(trigger) {
        const d = doc();
        return !!(trigger && d && d.contains(trigger));
    }

    async function openPanel(trigger) {
        const panel = ensurePanel();
        if (!panel || !trigger) return;
        if (!panel.hidden && restoreTarget === trigger) {
            closePanel();
            return;
        }
        // Close any other open session before adopting this trigger's context.
        if (!panel.hidden && restoreTarget && restoreTarget !== trigger) {
            closePanel({ skipFocus: true });
        }
        restoreTarget = trigger;
        openFolderId = trigger.getAttribute('data-prks-folder-nav-current') || null;
        openOwnerTabId = trigger.getAttribute('data-prks-folder-nav-tab-id') || null;
        setTriggerExpanded(trigger, true);
        filterQuery = '';
        panel.replaceChildren();
        listboxEl = null;
        const loading = doc().createElement('p');
        loading.className = 'prks-folder-nav__empty meta-row';
        loading.textContent = 'Loading folders…';
        panel.appendChild(loading);
        positionPanel(trigger);

        const rows = await loadHierarchy(false, signalForOwner(trigger));
        if (!panelEl || panelEl.hidden) return;
        // Trigger may have unmounted during the await (tab switch / remount).
        if (!triggerStillLive(trigger) || restoreTarget !== trigger) {
            closePanel({ skipFocus: true });
            return;
        }
        // Re-read ownership from the live trigger in case the instance remounted.
        openFolderId = trigger.getAttribute('data-prks-folder-nav-current') || openFolderId;
        openOwnerTabId = trigger.getAttribute('data-prks-folder-nav-tab-id') || openOwnerTabId;
        if (Array.isArray(rows)) updateTriggerPath(trigger, { id: openFolderId }, rows);
        renderPanelBody(rows);
        positionPanel(trigger);
        const d = doc();
        const filterInput = d && d.getElementById(FILTER_ID);
        if (filterInput) filterInput.focus();
        else if (optionEls.length) setActiveOption(activeIndex);
    }

    function onDocPointer(ev) {
        if (!panelEl || panelEl.hidden) return;
        const t = ev.target;
        if (panelEl.contains(t)) return;
        if (restoreTarget && (restoreTarget === t || (restoreTarget.contains && restoreTarget.contains(t)))) {
            return;
        }
        closePanel({ skipFocus: true });
    }

    function onViewportChange() {
        if (!panelEl || panelEl.hidden) return;
        if (!triggerStillLive(restoreTarget)) {
            closePanel({ skipFocus: true });
            return;
        }
        // Scroll/resize: keep the dialog attached by closing rather than chasing
        // a moving layout (fixed popover + workspace tiles).
        closePanel({ skipFocus: true });
    }

    function onScroll(ev) {
        if (!panelEl || panelEl.hidden) return;
        // Scrolling the options list must not dismiss the dialog.
        if (ev && ev.target && panelEl.contains(ev.target)) return;
        onViewportChange();
    }

    function onDocKey(ev) {
        if (!panelEl || panelEl.hidden) return;
        const t = ev.target;
        const filterFocused = t && t.id === FILTER_ID;
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

    function findTrigger(container) {
        if (!container || typeof container.querySelector !== 'function') return null;
        return container.querySelector('.prks-folder-nav__trigger');
    }

    function bindTrigger(container) {
        const trigger = findTrigger(container);
        if (!trigger || trigger.dataset.prksFolderNavBound === '1') return;
        trigger.dataset.prksFolderNavBound = '1';
        trigger.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            void openPanel(trigger);
        });
        // Enter/Space on a <button> also synthesize a click. Open only from click
        // for those keys so we do not open-then-immediately-close. ArrowDown alone
        // does not click, so it still opens from keydown.
        trigger.addEventListener('keydown', function (ev) {
            if (ev.key === 'ArrowDown') {
                if (panelEl && !panelEl.hidden && restoreTarget === trigger) return;
                ev.preventDefault();
                void openPanel(trigger);
            }
        });
    }

    function bindBandNav(container) {
        const navRoot = findNavRoot(container);
        if (!navRoot || navRoot.dataset.prksFolderNavBandBound === '1') return;
        navRoot.dataset.prksFolderNavBandBound = '1';
        navRoot.addEventListener('click', function (ev) {
            const t = ev.target;
            if (!t || typeof t.closest !== 'function') return;
            if (t.closest('.prks-folder-nav__trigger')) return;
            const libraryBtn = t.closest('[data-prks-folder-nav-library]');
            if (libraryBtn) {
                ev.preventDefault();
                ev.stopPropagation();
                navigateLibrary(libraryBtn);
                return;
            }
            const gotoBtn = t.closest('[data-prks-folder-nav-goto]');
            if (!gotoBtn) return;
            const dest = gotoBtn.getAttribute('data-prks-folder-nav-goto');
            if (!dest) return;
            ev.preventDefault();
            ev.stopPropagation();
            navigateOwned(dest, gotoBtn);
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
        if (!boundViewport && typeof root.addEventListener === 'function') {
            boundViewport = true;
            root.addEventListener('resize', onViewportChange);
            // Capture so nested scroll containers (tiles, page) dismiss the panel.
            root.addEventListener('scroll', onScroll, true);
        }
    }

    /**
     * Mount the Location + Nearby band on a Folder detail root. Loads the
     * hierarchy once to paint full path crumbs and nearby chips (one catalog
     * request, not per-row). Does not overwrite another pane's open session.
     */
    function prksMountFolderHierarchyNav(ctx, folder, container) {
        if (!container || !folder || folder.id == null) return;
        ensureGlobalListeners();
        const navRoot = findNavRoot(container);
        const band = navRoot && navRoot.querySelector('.prks-folder-nav__band');
        const trigger = findTrigger(container);
        if (band) {
            band.setAttribute('data-prks-folder-nav-current', String(folder.id));
            if (ctx && ctx.tabId != null) {
                band.setAttribute('data-prks-folder-nav-tab-id', String(ctx.tabId));
            }
        }
        if (trigger) {
            trigger.setAttribute('data-prks-folder-nav-current', String(folder.id));
            if (ctx && ctx.tabId != null) {
                trigger.setAttribute('data-prks-folder-nav-tab-id', String(ctx.tabId));
            }
        }
        bindTrigger(container);
        bindBandNav(container);
        if (navRoot) paintBand(navRoot, folder, hierarchyRows);

        if (ctx && typeof ctx.registerCleanup === 'function') {
            const ownerTabId = ctx.tabId != null ? String(ctx.tabId) : null;
            ctx.registerCleanup(function () {
                // Body-level popover survives Folder root teardown (Back /
                // beginRoute / cold-park). Close it whenever this TabContext
                // owns the open session — by trigger identity or tab id.
                const ownsTrigger = !!(restoreTarget && trigger && restoreTarget === trigger);
                const ownsTab =
                    !!(ownerTabId && openOwnerTabId && String(openOwnerTabId) === ownerTabId);
                if (ownsTrigger || ownsTab) {
                    closePanel({ skipFocus: true });
                }
            });
        }

        void (async function () {
            const signal = signalForOwner(ctx) || signalForOwner(trigger);
            const rows = await loadHierarchy(false, signal);
            const liveNav = findNavRoot(container);
            const live = findTrigger(container);
            // Aborted / torn-down mounts leave the initial Loading state; a
            // sticky hierarchyLoadError must paint an actionable failure so the
            // band does not stay on "Loading nearby folders…" forever.
            if (!liveNav || !live) return;
            if (signal && signal.aborted) return;
            if (String(live.getAttribute('data-prks-folder-nav-current') || '') !== String(folder.id)) {
                return;
            }
            if (!Array.isArray(rows)) {
                if (hierarchyLoadError) paintBand(liveNav, folder, null);
                return;
            }
            paintBand(liveNav, folder, rows);
        })();
    }

    function prksFolderNavTriggerHtml(folder, rows, options) {
        return crumbHtml(folder, rows, options);
    }

    function resetForTests() {
        closePanel({ skipFocus: true });
        hierarchyRows = null;
        hierarchyLoadError = false;
        hierarchyOpsFingerprint = undefined;
        clearHierarchyPathCache();
        openOwnerTabId = null;
        openFolderId = null;
        filterQuery = '';
        if (panelEl && panelEl.parentNode) panelEl.parentNode.removeChild(panelEl);
        panelEl = null;
        listboxEl = null;
        optionEls = [];
        boundGlobal = false;
        boundViewport = false;
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
