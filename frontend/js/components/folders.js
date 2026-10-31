function prksFolderPathLabel(folderId, byId) {
    const parts = [];
    const guard = new Set();
    let cur = byId.get(folderId);
    while (cur && !guard.has(cur.id)) {
        guard.add(cur.id);
        parts.unshift(String(cur.title || 'Folder'));
        cur = cur.parent_id ? byId.get(cur.parent_id) : null;
    }
    return parts.join(' → ');
}

function prksFolderRowLabel(folder, allList) {
    const list = Array.isArray(allList) ? allList : [];
    const byId = new Map(list.map((x) => [x.id, x]));
    const path = prksFolderPathLabel(folder.id, byId);
    const title = String(folder && folder.title ? folder.title : 'Folder');
    return path === title ? title : `${title} (${path})`;
}

function prksCollectFolderDescendantIds(folderId, allList) {
    const out = new Set();
    const list = Array.isArray(allList) ? allList : [];
    function walk(id) {
        list
            .filter((x) => x.parent_id === id)
            .forEach((ch) => {
                out.add(ch.id);
                walk(ch.id);
            });
    }
    walk(folderId);
    return out;
}

/* ---------------------------------------------------------------------------
 * Offline connectivity policy for the Folder surfaces.
 *
 * Reads (hierarchy, local search, expand/collapse, navigation) stay fully
 * usable from cache; every canonical write control settles live. The Folders
 * routes bind through their own TabContext, while the Work-side Folder card
 * lives in the shared right panel and mirrors the Playlist card's subscription.
 * ------------------------------------------------------------------------ */
/* Folder create / rename / delete / filing / tags are durable. No library
 * control is frozen solely for want of a connection. The Work Folder card
 * still refuses *starting* an edit session offline (catalogue search),
 * matching the Playlist card: Clear / New stay live; Search / Set freeze. */
const PRKS_WORK_FOLDER_MUTATION_SELECTOR = [
    '#prks-work-folder-search',
    '#prks-work-folder-set-btn',
].join(', ');

function prksFolderRuntimeOnline() {
    return typeof prksOfflineRuntimeState !== 'function' || prksOfflineRuntimeState() === 'online';
}

/** Library create/delete stay live (durable). Nothing to freeze on the route. */
function prksApplyFolderOfflineState(container) {
    void container;
}

/** The Folder detail's tag editor and delete button live in the shared right
 *  panel / page body, so only settle them for the context that owns them. */
function prksBindFolderOfflineState(ctx, container) {
    if (!container) return;
    if (typeof container.__prksFolderOfflineDispose === 'function') {
        try {
            container.__prksFolderOfflineDispose();
        } catch (_e) {
            /* a stale disposer must not block the new binding */
        }
    }
    const apply = function () {
        prksApplyFolderOfflineState(container);
        const panel = document.getElementById('panel-content');
        if (panel && (typeof prksRightPanelOwnedBy !== 'function' || prksRightPanelOwnedBy(ctx, panel))) {
            prksApplyFolderOfflineState(panel);
        }
    };
    // Read current state immediately: a page rendered after the runtime already
    // left 'online' is never briefly mutable.
    apply();
    const unsubscribe =
        (typeof prksOfflineRuntimeSubscribe === 'function' &&
            prksOfflineRuntimeSubscribe(function () {
                if (container.__prksFolderOfflineDispose !== dispose) return;
                apply();
            })) ||
        function () {};
    let unregister = function () {};
    function dispose() {
        if (container.__prksFolderOfflineDispose === dispose) container.__prksFolderOfflineDispose = null;
        unregister();
        unsubscribe();
    }
    container.__prksFolderOfflineDispose = dispose;
    if (ctx && typeof ctx.registerCleanup === 'function') unregister = ctx.registerCleanup(dispose) || function () {};
}

/* Settles every mounted Work Folder card live -- same shape as the Work
 * Playlist card, because the Work right panel is not owned by a Folder route.
 * An already-open editor keeps its typed draft and selection on screen; only
 * the canonical controls freeze. Done stays usable so a user can always leave
 * an edit they can no longer save. */
function prksApplyWorkFolderOfflineState(ctx) {
    const panel = document.getElementById('panel-content');
    if (!panel) return;
    if (typeof prksRightPanelOwnedBy === 'function' && !prksRightPanelOwnedBy(ctx, panel)) return;
    const online = prksFolderRuntimeOnline();
    const editing = !!(ctx && ctx.ui && ctx.ui.workFolderEditing);
    panel.querySelectorAll(PRKS_WORK_FOLDER_MUTATION_SELECTOR).forEach(function (el) {
        el.disabled = !online;
        if (online) el.removeAttribute('aria-disabled');
        else el.setAttribute('aria-disabled', 'true');
    });
    const editBtn = panel.querySelector('#prks-work-folder-edit-btn');
    if (editBtn) {
        // Closed card: Edit cannot start a new edit offline. Open card: the
        // same button is "Done" and must keep working. Clear / New stay live
        // — neither needs the live catalogue.
        editBtn.disabled = !online && !editing;
        if (editBtn.disabled) editBtn.setAttribute('aria-disabled', 'true');
        else editBtn.removeAttribute('aria-disabled');
    }
    ['#prks-work-folder-clear-btn', '#prks-work-folder-new-btn'].forEach(function (sel) {
        const el = panel.querySelector(sel);
        if (!el) return;
        el.disabled = false;
        el.removeAttribute('aria-disabled');
    });
    const results = panel.querySelector('#prks-work-folder-results');
    if (results) {
        results.inert = !online;
        if (!online) results.classList.add('hidden');
    }
}

if (typeof prksOfflineRuntimeSubscribe === 'function') {
    prksOfflineRuntimeSubscribe(function () {
        if (typeof prksForEachLiveTabContext !== 'function') return;
        prksForEachLiveTabContext(function (ctx) {
            prksApplyWorkFolderOfflineState(ctx);
        });
    });
}

function prksOpenFolderModalFromLibrarySearch(query) {
    // CREATE_FOLDER is durable: the folder is real the moment it is written.
    // No connectivity guard — unknown-base refusals live inside createFolder.
    const pre = String(query || '').trim();
    const titleEl = document.getElementById('folder-title');
    const descEl = document.getElementById('folder-description');
    const parentInputEl = document.getElementById('folder-parent-search');
    const parentHiddenEl = document.getElementById('folder-parent-id');
    if (titleEl) titleEl.value = pre;
    if (descEl) descEl.value = '';
    if (parentInputEl) parentInputEl.value = '';
    if (parentHiddenEl) parentHiddenEl.value = '';
    if (typeof openModal === 'function') openModal('folder-modal');
    if (typeof window.prksRefreshFolderModalValidation === 'function') {
        void window.prksRefreshFolderModalValidation();
    }
}

window.prksOpenFolderModalFromLibrarySearch = prksOpenFolderModalFromLibrarySearch;

function prksSwitchFolderLibraryTab(tab) {
    const st = window.__prksFolderDashboardState;
    if (!st || !st.container) return;
    const want = tab === 'recently-added' ? 'recently-added' : 'folders';
    // Vue owns tab + Recently Added rows. Prefer the awaitable bridge so
    // callers (and Playwright `page.evaluate`) settle after load+paint —
    // a bare button click returned before the first card existed.
    if (typeof st.switchTab === 'function') {
        return st.switchTab(want);
    }
    const root = st.container.querySelector('[data-prks-folder-library-view]');
    const btn =
        root &&
        root.querySelector(`.prks-folder-library__tab-btn[data-tab="${want}"]`);
    if (btn && typeof btn.click === 'function') {
        btn.click();
    }
}

function prksFolderLibraryCatalogGlanceParts(folders) {
    const list = Array.isArray(folders) ? folders : [];
    const folderCount = list.length;
    let workSum = 0;
    let workSumKnown = true;
    for (let i = 0; i < list.length; i += 1) {
        const n = Number(list[i] && list[i].work_count);
        if (!Number.isFinite(n)) {
            workSumKnown = false;
            break;
        }
        workSum += n;
    }
    return [
        folderCount + (folderCount === 1 ? ' folder' : ' folders'),
        /* Σ folder work_count is filed membership only — not the whole library. */
        workSumKnown
            ? workSum + (workSum === 1 ? ' file in folders' : ' files in folders')
            : null,
    ];
}

async function prksPeekCachedBrowseList(listKey) {
    /* Read-only peek: never start a network warm that would publish into an
     * independent browse domain from Home glance. Missing cache ⇒ omit. */
    try {
        if (typeof createPrksOfflineStore !== 'function') return null;
        const row = await createPrksOfflineStore().getList(listKey);
        const value = row && row.value;
        return Array.isArray(value) ? value : null;
    } catch (_e) {
        return null;
    }
}

/* Per-container Folder Library dashboard bridges. Dual-mounted Main/Secondary
 * surfaces must not overwrite a singleton — glance extras and tab helpers resolve
 * the owner that contains the requesting root, and unpublish restores another
 * still-mounted owner instead of clearing the last writer. */
const __prksFolderDashboardOwners = new Map();

function prksPublishFolderDashboardState(state) {
    if (!state || !state.container) {
        window.__prksFolderDashboardState = state;
        return;
    }
    __prksFolderDashboardOwners.set(state.container, state);
    window.__prksFolderDashboardState = state;
}

function prksUnpublishFolderDashboardState(container) {
    if (!container) return;
    __prksFolderDashboardOwners.delete(container);
    const st = window.__prksFolderDashboardState;
    if (!st || st.container !== container) return;
    const next = __prksFolderDashboardOwners.values().next();
    window.__prksFolderDashboardState = next.done ? undefined : next.value;
}

function prksFolderDashboardStateForRoot(root) {
    if (root) {
        for (const st of __prksFolderDashboardOwners.values()) {
            if (!st || !st.container) continue;
            if (
                st.container === root ||
                (typeof st.container.contains === 'function' && st.container.contains(root))
            ) {
                return st;
            }
        }
    }
    return window.__prksFolderDashboardState;
}

async function prksCollectFolderLibraryGlanceExtras(options) {
    const parts = [];
    const CONTINUE_CAP = 3;
    const opts = options && typeof options === 'object' ? options : {};

    /* Continue / In Progress / Recently added reuse already-warmed snapshots or
     * in-memory tab state only. Never start browse/recently-added read-through
     * fetches here — boot on #/folders must not populate works-browse:index or
     * recent:index as a side effect of the glance band. */
    let recentRows = await prksPeekCachedBrowseList('recent:index');
    if (Array.isArray(recentRows) && recentRows.length) {
        if (typeof prksEffectiveBrowseRows === 'function') {
            recentRows = prksEffectiveBrowseRows(recentRows, 'recent');
        }
        const slice = recentRows.slice(0, CONTINUE_CAP);
        for (let i = 0; i < slice.length; i += 1) {
            const w = slice[i];
            if (!w || !w.id) continue;
            const title = String(w.title || 'Untitled').trim() || 'Untitled';
            parts.push({
                text: title,
                href: '#/works/' + encodeURIComponent(String(w.id)),
            });
        }
        if (recentRows.length > CONTINUE_CAP) {
            parts.push({ text: 'More recent', href: '#/recent' });
        }
    }

    let browseRows = await prksPeekCachedBrowseList('works-browse:index');
    if (Array.isArray(browseRows)) {
        if (typeof prksEffectiveBrowseRows === 'function') {
            browseRows = prksEffectiveBrowseRows(browseRows, 'works-browse');
        }
        const inProgressN = browseRows.filter(function (w) {
            return w && String(w.status || '') === 'In Progress';
        }).length;
        if (inProgressN > 0) {
            parts.push({
                text: inProgressN + ' in progress',
                href: '#/progress?status=' + encodeURIComponent('In Progress'),
            });
        }
    }

    /* Prefer caller-owned rows (Vue scheduleGlance) over any singleton bridge.
     * An explicit null means "this owner has no RA rows", not "fall back". */
    const raRows = Object.prototype.hasOwnProperty.call(opts, 'recentlyAddedWorks')
        ? (Array.isArray(opts.recentlyAddedWorks) ? opts.recentlyAddedWorks : null)
        : (() => {
              const stMem = window.__prksFolderDashboardState;
              return stMem && Array.isArray(stMem.recentlyAddedWorks)
                  ? stMem.recentlyAddedWorks
                  : null;
          })();
    /* Only reuse an already-loaded Recently-added RAM copy. Do not warm
     * recently-added:index from Home glance — that domain is independent. */
    if (Array.isArray(raRows) && raRows.length) {
        parts.push(raRows.length + ' recently added');
    }

    const procCount =
        typeof prksGetProcessingAttentionCount === 'function'
            ? prksGetProcessingAttentionCount()
            : null;
    if (procCount != null && procCount > 0) {
        parts.push({
            text: procCount + ' for processing',
            href: '#/processing-files',
        });
    }

    /* Sync attention: read the already-painted nav cue — do not open the durable store. */
    try {
        const cue = document.getElementById('prks-sync-queue-cue');
        if (cue && !cue.hidden && !cue.classList.contains('hidden')) {
            const label = String(cue.getAttribute('aria-label') || '').trim();
            if (label) parts.push(label.replace(/\.$/, ''));
        }
    } catch (_e) {
        /* omit */
    }

    return parts;
}

async function prksScheduleFolderLibraryGlance(root, options) {
    const host =
        root && root.querySelector
            ? root.querySelector('[data-prks-role="folder-library-glance-host"]')
            : null;
    if (!host) return;
    const token = String(Date.now()) + '-' + Math.random().toString(36).slice(2, 8);
    host.dataset.glanceToken = token;
    const opts = options && typeof options === 'object' ? options : {};
    const st = prksFolderDashboardStateForRoot(root);
    const folders = Array.isArray(opts.folders)
        ? opts.folders
        : ((st && st.folders) || []);
    const extrasOpts = Object.prototype.hasOwnProperty.call(opts, 'recentlyAddedWorks')
        ? { recentlyAddedWorks: opts.recentlyAddedWorks }
        : st
          ? { recentlyAddedWorks: st.recentlyAddedWorks }
          : {};
    const catalogParts = prksFolderLibraryCatalogGlanceParts(folders);
    let extras = [];
    try {
        extras = await prksCollectFolderLibraryGlanceExtras(extrasOpts);
    } catch (_e) {
        extras = [];
    }
    if (!host.isConnected || host.dataset.glanceToken !== token) return;
    const parts = catalogParts.concat(extras || []);
    if (typeof opts.onParts === 'function') opts.onParts(parts);
}

// Container width for compact Location+Nearby fallback. Kept below the common
// stacked Main content width (~700px at 1280 with the app sidebar) so normal
// desktop stays on tree|contents; true narrow tiles still fall under this.
const PRKS_FOLDER_DETAIL_NARROW_PX = 560;

function prksBindFolderDetailLayout(root, ctx) {
    if (!root) return;
    // Folder→Folder in-place updates must not stack ResizeObservers.
    if (root.dataset.prksFolderLayoutBound === '1') {
        const locked = root.getAttribute('data-prks-folder-layout-lock');
        if (locked === 'narrow' || locked === 'wide') {
            root.setAttribute('data-prks-folder-layout', locked);
            return;
        }
        const w = root.clientWidth || 0;
        const narrow = w > 0 && w < PRKS_FOLDER_DETAIL_NARROW_PX;
        root.setAttribute('data-prks-folder-layout', narrow ? 'narrow' : 'wide');
        return;
    }
    root.dataset.prksFolderLayoutBound = '1';
    const apply = function () {
        // Tests/screenshots may lock layout; otherwise width drives wide vs narrow.
        const locked = root.getAttribute('data-prks-folder-layout-lock');
        if (locked === 'narrow' || locked === 'wide') {
            root.setAttribute('data-prks-folder-layout', locked);
            return;
        }
        const w = root.clientWidth || 0;
        // Unmeasured (0) defaults to wide so first paint matches desktop IA;
        // ResizeObserver corrects once layout settles.
        const narrow = w > 0 && w < PRKS_FOLDER_DETAIL_NARROW_PX;
        root.setAttribute('data-prks-folder-layout', narrow ? 'narrow' : 'wide');
    };
    apply();
    if (typeof ResizeObserver !== 'function') return;
    const ro = new ResizeObserver(function () {
        apply();
    });
    ro.observe(root);
    if (ctx && typeof ctx.registerCleanup === 'function') {
        ctx.registerCleanup(function () {
            try {
                ro.disconnect();
            } catch (_e) {
                /* ignore */
            }
            try {
                delete root.dataset.prksFolderLayoutBound;
            } catch (_e2) {
                /* ignore */
            }
        });
    }
}

/**
 * Claim a Folder-detail hierarchy refresh token for `mode`, or null when the
 * TabContext cannot begin (destroyed / API absent). Shared by the initial
 * claim and the selection-fallback full claim so Sonar does not flag the
 * begin+commitAllowed shape twice in prksFillFolderDetailTree.
 */
function prksBeginFolderHierarchyRefresh(ctx, mode) {
    return ctx && typeof ctx.beginFolderHierarchyRefresh === 'function'
        ? ctx.beginFolderHierarchyRefresh(mode)
        : null;
}

/**
 * True when this claim may still write the live hierarchy host.
 * Handles destroyed-ctx (null token) the same way at both gate sites.
 */
function prksFolderHierarchyClaimMayCommit(ctx, token, container, signal) {
    if (token == null && ctx && ctx.destroyed) return false;
    return (
        typeof prksFolderHierarchyTreeCommitAllowed === 'function' &&
        prksFolderHierarchyTreeCommitAllowed(ctx, token, container, signal)
    );
}

/**
 * Live Folder-detail hierarchy refill. Same-route overlap ownership uses
 * TabContext beginFolderHierarchyRefresh(mode) / prksFolderHierarchyTreeCommitAllowed
 * so an older async catalogue result cannot overwrite a newer committed tree.
 * Successful in-place selection never supersedes an in-flight full topology
 * refresh. When selection-only falls through to a full tree rebuild (missing
 * tree or destination row), it claims full ownership first so an older full
 * cannot overwrite that newer topology. Route AbortSignal still covers A→B→C.
 */

function prksReadFolderDetailTreeSnapshot(host) {
    return host && host.__prksFolderDetailTreeSnapshot ? host.__prksFolderDetailTreeSnapshot : null;
}

function prksSnapshotHasFolder(snapshot, folderId) {
    if (!snapshot || snapshot.status !== 'ready' || !Array.isArray(snapshot.rows)) return false;
    const id = String(folderId);
    return snapshot.rows.some(function (row) {
        return row && String(row.id) === id;
    });
}

/**
 * Hand a typed hierarchy snapshot to the Vue tree. The host callback renders
 * rows; this function does not write HTML.
 */
function prksPublishFolderDetailTree(host, snapshot) {
    if (!host) return;
    host.__prksFolderDetailTreeSnapshot = snapshot;
    if (typeof host.__prksApplyFolderDetailTree === 'function') {
        host.__prksApplyFolderDetailTree(snapshot);
    }
}

async function prksFillFolderDetailTree(ctx, folder, container, options) {
    const opts = options && typeof options === 'object' ? options : {};
    const selectionOnly = !!opts.selectionOnly;
    const host = container && container.querySelector('[data-prks-folder-detail-tree-host]');
    if (!host || !folder) return;
    // Capture before await: A→B→C can abort B while load() is in flight. The
    // preserved shell stays connected for C, so an aborted B must not move
    // aria-current / is-selected after it resolves.
    const signal =
        ctx && ctx.abortController && ctx.abortController.signal
            ? ctx.abortController.signal
            : null;
    // Mode-aware ownership: full vs selection use separate counters so a
    // Folder→Folder in-place selection fill cannot invalidate a pending sync
    // full refill. Fallback rebuilds claim full below.
    const refreshToken = prksBeginFolderHierarchyRefresh(
        ctx,
        selectionOnly ? 'selection' : 'full'
    );
    // Destroyed before await: begin returned null. After await we re-check via
    // prksFolderHierarchyClaimMayCommit.
    if (refreshToken == null && ctx && ctx.destroyed) return;
    const load =
        typeof prksLoadFolderHierarchyCatalogue === 'function'
            ? prksLoadFolderHierarchyCatalogue
            : typeof prksFolderHierarchyNavLoadForTests === 'function'
              ? prksFolderHierarchyNavLoadForTests
              : null;
    let rows = null;
    if (load) {
        try {
            rows = await load(false, signal);
        } catch (_e) {
            rows = null;
        }
    }
    if (!prksFolderHierarchyClaimMayCommit(ctx, refreshToken, container, signal)) {
        return;
    }
    const liveHost = container.querySelector('[data-prks-folder-detail-tree-host]');
    if (!liveHost) return;
    // Re-read the live Folder entity after the await (in-place Folder→Folder
    // can advance selection while a sync-triggered full refill was in flight).
    const liveFolder =
        ctx && typeof ctx.getEntity === 'function' ? ctx.getEntity('folder') : null;
    const commitFolder =
        liveFolder && liveFolder.id != null && liveFolder.id !== '' ? liveFolder : folder;
    if (!Array.isArray(rows)) {
        prksPublishFolderDetailTree(liveHost, {
            status: 'error',
            rows: [],
            selectedId: String(commitFolder.id || ''),
            selectionOnly: false,
        });
        return;
    }
    // Same-workspace Folder→Folder: keep the published rows; only move selection.
    const previous = prksReadFolderDetailTreeSnapshot(liveHost);
    if (selectionOnly && prksSnapshotHasFolder(previous, commitFolder.id)) {
        prksPublishFolderDetailTree(liveHost, {
            status: 'ready',
            rows: previous.rows,
            selectedId: String(commitFolder.id),
            selectionOnly: true,
        });
        return;
    }
    // Selection-only fell through to a topology rebuild (tree not built yet, or
    // destination row absent). Claim full ownership before writing so an older
    // in-flight full cannot overwrite this newer tree. The successful in-place
    // select path above does not bump full and leaves pending fulls current.
    if (selectionOnly) {
        const fullClaim = prksBeginFolderHierarchyRefresh(ctx, 'full');
        if (!prksFolderHierarchyClaimMayCommit(ctx, fullClaim, container, signal)) {
            return;
        }
    }
    prksPublishFolderDetailTree(liveHost, {
        status: 'ready',
        rows: rows,
        selectedId: String(commitFolder.id),
        selectionOnly: false,
    });
}

/**
 * Full-refill every live Folder-detail hierarchy tree after structure sync.
 * selectionOnly is intentionally not used: CREATE/rename/reparent/DELETE can
 * change topology, not just which row is current.
 */
function prksRefreshLiveFolderDetailTrees() {
    const hosts = document.querySelectorAll('[data-prks-folder-detail-tree-host]');
    hosts.forEach(function (host) {
        if (!host || !host.isConnected) return;
        const ctx =
            typeof prksContextFromElement === 'function' ? prksContextFromElement(host) : null;
        if (!ctx || ctx.destroyed || !ctx.mounted) return;
        const folder = ctx.getEntity && ctx.getEntity('folder');
        if (!folder || folder.id == null || folder.id === '') return;
        const container = ctx.root || host.closest('.prks-tab-root');
        if (!container || !container.contains(host)) return;
        void prksFillFolderDetailTree(ctx, folder, container, { selectionOnly: false });
    });
}

/**
 * Folder detail route paint lives in frontend-app/src/features/folder-detail/.
 * Vue renders the hierarchy rows, subfolder list, and page summary. This module
 * still owns the hierarchy refresh claim, the layout observer, offline binding,
 * and the canonical new-folder / delete wrappers. prksCommitFolderDetailSurface
 * binds those and publishes a typed tree snapshot; it does not write row HTML.
 * Vue Folder-detail Work cards are PrksWorkCard.
 * Folder Library Recently Added is Vue-owned (`RecentlyAddedPane` / `PrksWorkCard`).
 */

function prksEffectiveFolderDetailWorks(folder) {
    const works = folder && Array.isArray(folder.works) ? folder.works : [];
    /* Acknowledged summaries + pending Work-field edits. The cached Folder is
     * never mutated; the overlay is applied where the rows are rendered, and
     * the rules for it live in work-metadata-state.js so this component never
     * learns to read the durable queue. */
    return typeof prksEffectiveWorkSummaryRows === 'function'
        ? prksEffectiveWorkSummaryRows(works)
        : works;
}

function prksOpenNewFolderFromDetail(folder) {
    prksOpenFolderModalFromLibrarySearch('');
    const parentHidden = document.getElementById('folder-parent-id');
    const parentInput = document.getElementById('folder-parent-search');
    if (parentHidden) parentHidden.value = String((folder && folder.id) || '');
    if (parentInput) parentInput.value = String((folder && folder.title) || '');
    if (typeof window.prksRefreshFolderModalValidation === 'function') {
        void window.prksRefreshFolderModalValidation();
    }
}

function prksSyncWorkModalFolderFromDetail(folderId) {
    setTimeout(function () {
        const select = document.getElementById('work-folder-id');
        if (select) select.value = folderId;
    }, 100);
}

/**
 * Bind layout, offline state, hierarchy nav, and the live tree into a Vue-owned
 * Folder detail host. preserveFolderWorkspace keeps selection-only tree updates.
 */
function prksCommitFolderDetailSurface(ctx, folder, container, options) {
    if (!container || !folder) return;
    const opts = options && typeof options === 'object' ? options : {};
    const detailRoot =
        container.getAttribute && container.getAttribute('data-prks-role') === 'folder-detail'
            ? container
            : container.querySelector('[data-prks-role="folder-detail"]');
    if (detailRoot) prksBindFolderDetailLayout(detailRoot, ctx);
    prksBindFolderOfflineState(ctx, container);
    if (typeof prksMountFolderHierarchyNav === 'function') {
        prksMountFolderHierarchyNav(ctx, folder, container);
    }
    void prksFillFolderDetailTree(ctx, folder, container, {
        selectionOnly: !!opts.preserveFolderWorkspace,
    });
    prksSyncWorkModalFolderFromDetail(folder.id);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

async function prksRemoveFolderTag(folderId, tagId, btn) {
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    try {
        if (typeof prksFolderTagEdit === 'function') {
            await prksFolderTagEdit(ownerCtx, tagId, false);
        } else {
            await removeTagFromFolder(folderId, tagId);
            await prksReloadEntityTagsUI('folder', folderId, ownerCtx);
        }
    } catch (e) {
        if (btn && typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, false);
        if (typeof prksOfflineWasGuardRefusal === 'function' && prksOfflineWasGuardRefusal(e)) return;
        console.error(e);
        await prksAlertMessage('Could not remove tag.', 'Error');
    }
}

async function deleteFolder(f_id, still) {
    const confirmed = await prksConfirmDestructive({
        title: 'Delete folder?',
        message: 'Are you sure you want to delete this empty folder?',
        confirmLabel: 'Delete folder',
    });
    if (!confirmed) return;
    if (typeof still === 'function' && !still()) return;
    try {
        await deleteFolderCanonical(f_id);
        if (typeof still === 'function' && !still()) return;
        if (typeof prksNavigate === 'function') prksNavigate('#/folders');
    } catch (e) {
        if (prksOfflineWasGuardRefusal(e)) return;
        await prksAlertMessage('Error deleting folder: ' + ((e && e.message) || 'unknown error'), 'Error');
    }
}

function prksNormFolderTitleKey(s) {
    return String(s || '')
        .trim()
        .toLowerCase();
}

function prksWorkFolderEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderFolderAttachControlsHtml(work, ownerCtx) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return '';
    const current = work && work.folder_id ? String(work.folder_id) : '';
    const currentTitle = work && work.folder_title ? String(work.folder_title) : '';
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const editing = !!(ctx && ctx.ui && ctx.ui.workFolderEditing);
    const currentSummary = current
        ? `<span class="meta-row">Folder:</span> <a href="#/folders/${encodeURIComponent(
              current
          )}" class="route-sidebar__link">${prksWorkFolderEsc(currentTitle || current)}</a>`
        : '<span class="meta-row">Not in a folder</span>';
    const currentLineExpanded = current
        ? `Now in <a href="#/folders/${encodeURIComponent(current)}" class="route-sidebar__link">${prksWorkFolderEsc(
              currentTitle || current
          )}</a>. Choose another folder below or clear.`
        : 'Search for a folder, then set or leave cleared.';

    const summaryTitleAttr = current ? ` title="${prksWorkFolderEsc(currentTitle || current)}"` : '';

    if (!editing) {
        return `
        <div class="doc-meta-card prks-work-folder-card prks-work-folder-card--compact">
            <div class="card-heading-row">
                <span class="prks-work-folder-summary" data-prks-role="work-folder-read"${summaryTitleAttr}>
                    ${currentSummary}
                </span>
                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-work-folder-edit-btn" aria-expanded="false">Edit</button>
            </div>
        </div>
    `;
    }

    return `
        <div class="doc-meta-card prks-work-folder-card">
            <div class="card-heading-row">
                <h3>Folder</h3>
                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-work-folder-edit-btn" aria-expanded="true">Done</button>
            </div>
            <p class="meta-row meta-row--spaced">${currentLineExpanded}</p>
            <div class="tag-add-shell combobox-container">
                <div class="tag-add-shell__field">
                    ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : ''}
                    <input type="text" id="prks-work-folder-search" class="tag-add-shell__input" placeholder="Search folders…" maxlength="300" autocomplete="off" aria-label="Search folders">
                    <input type="hidden" id="prks-work-folder-id" value="${prksWorkFolderEsc(current)}">
                </div>
                <div id="prks-work-folder-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
            <div class="prks-work-folder-controls">
                <button type="button" class="prks-btn prks-btn--primary" id="prks-work-folder-set-btn">Set folder</button>
                <button type="button" class="prks-btn prks-btn--secondary" id="prks-work-folder-clear-btn">Clear</button>
                <button type="button" class="prks-btn prks-btn--secondary" id="prks-work-folder-new-btn">New...</button>
            </div>
            <p id="prks-work-folder-status" class="meta-row meta-row--spaced" aria-live="polite"></p>
        </div>
    `;
}

async function mountFolderAttachControlsForWork(work, ownerCtx) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return;
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const generation = ctx && ctx.generation;
    function folderStill() {
        if (!ctx || ctx.destroyed) return false;
        if (typeof prksTabContextOwnsEntityRoute === 'function' &&
            !prksTabContextOwnsEntityRoute(ctx, generation, 'work', wid, 'work')) return false;
        const panel = document.getElementById('panel-content');
        if (panel && panel.dataset.prksOwnerTabId && panel.dataset.prksOwnerTabId !== String(ctx.tabId)) return false;
        return true;
    }
    const editBtn = document.getElementById('prks-work-folder-edit-btn');
    const status = document.getElementById('prks-work-folder-status');
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            if (ctx && ctx.ui) ctx.ui.workFolderEditing = !ctx.ui.workFolderEditing;
            const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
            if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                updatePanelContent('details');
            }
        };
    }

    const editing = !!(ctx && ctx.ui && ctx.ui.workFolderEditing);
    // Settle the closed card too: Edit must already be inert when a cached Work
    // page mounts offline, not only after the next connectivity event.
    prksApplyWorkFolderOfflineState(ctx);
    if (!editing) return;
    // An editor left open across a disconnect keeps its draft on screen, but it
    // must not go to the network for the folder catalog.
    if (!prksFolderRuntimeOnline()) {
        prksApplyWorkFolderOfflineState(ctx);
        return;
    }

    const input = document.getElementById('prks-work-folder-search');
    const hidden = document.getElementById('prks-work-folder-id');
    const results = document.getElementById('prks-work-folder-results');
    const setBtn = document.getElementById('prks-work-folder-set-btn');
    const clearBtn = document.getElementById('prks-work-folder-clear-btn');
    const newBtn = document.getElementById('prks-work-folder-new-btn');
    if (!input || !hidden || !results || !setBtn || !clearBtn || !newBtn) return;

    let folderRows = await fetchFolders();
    if (typeof prksApplyOwnedWorkEntity === 'function' && !prksApplyOwnedWorkEntity(ctx, wid)) return;
    if (!Array.isArray(folderRows)) folderRows = [];
    // The catalog read can straddle a disconnect; settle again on the result.
    prksApplyWorkFolderOfflineState(ctx);

    if (work && work.folder_id && work.folder_title && !String(input.value || '').trim()) {
        input.value = String(work.folder_title);
        hidden.value = String(work.folder_id);
    }

    async function assignToNewFolderAndRefresh(newFolderId, message) {
        if (!folderStill() || typeof patchWorkFolder !== 'function') return;
        const coherenceToken = await patchWorkFolder(wid, newFolderId, folderStill);
        if (!folderStill()) return;
        folderRows = await fetchFolders();
        if (!Array.isArray(folderRows)) folderRows = [];
        if (status) status.textContent = message || 'Folder set.';
        if (typeof fetchWorkDetails === 'function') {
            const _fw = await fetchWorkDetails(wid);
            if (_fw && typeof prksOfflineCacheEntityIfCurrent === 'function') {
                void prksOfflineCacheEntityIfCurrent('work', wid, _fw, coherenceToken);
            }
            if (typeof prksApplyOwnedWorkEntity === 'function' && prksApplyOwnedWorkEntity(ctx, wid, _fw)) {
                if (ctx && ctx.ui) ctx.ui.workFolderEditing = false;
                const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                    updatePanelContent('details');
                }
            }
        }
    }

    function renderDropdown() {
        const rawQ = String(input.value || '').trim();
        const q = rawQ.toLowerCase();
        const filtered = !q
            ? folderRows.slice(0, 40)
            : folderRows
                  .filter((f) => {
                      const label = prksFolderRowLabel(f, folderRows).toLowerCase();
                      return label.includes(q) || String(f.title || '').toLowerCase().includes(q);
                  })
                  .slice(0, 40);
        const keyQ = prksNormFolderTitleKey(rawQ);
        const exactExists =
            keyQ &&
            folderRows.some((f) => prksNormFolderTitleKey(f.title) === keyQ);
        results.innerHTML = '';
        if (keyQ && !exactExists) {
            const c = document.createElement('div');
            c.className = 'result-item result-item--create';
            c.textContent = 'Create folder "' + rawQ + '"';
            c.onmousedown = (ev) => {
                ev.preventDefault();
                void (async () => {
                    try {
                        if (typeof createFolder !== 'function') return;
                        const newId = await createFolder(rawQ, '');
                        await assignToNewFolderAndRefresh(newId, 'Folder created and set.');
                    } catch (e) {
                        if (status) status.textContent = String((e && e.message) || 'Could not create folder.');
                    }
                })();
            };
            results.appendChild(c);
        }
        if (filtered.length === 0) {
            if (!keyQ || exactExists) {
                const empty = document.createElement('div');
                empty.className = 'result-item no-results';
                empty.textContent = 'No folders found';
                results.appendChild(empty);
            }
        } else {
            for (const f of filtered) {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent = prksFolderRowLabel(f, folderRows);
                div.onmousedown = (ev) => {
                    ev.preventDefault();
                    input.value = prksFolderRowLabel(f, folderRows);
                    hidden.value = f.id;
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            }
        }
        if (typeof prksShowInlineComboboxResults === 'function') {
            prksShowInlineComboboxResults(input, results);
        } else {
            results.classList.remove('hidden');
        }
    }

    newBtn.onclick = () => {
        window.__prksPendingWorkFolderAttach = {
            workId: wid,
            tabId: ctx && ctx.tabId != null ? String(ctx.tabId) : '',
            generation: generation,
        };
        const titleEl = document.getElementById('folder-title');
        const descEl = document.getElementById('folder-description');
        const parentInputEl = document.getElementById('folder-parent-search');
        const parentHiddenEl = document.getElementById('folder-parent-id');
        const pre = String(input.value || '').trim();
        if (titleEl) titleEl.value = pre;
        if (descEl) descEl.value = '';
        if (parentInputEl) parentInputEl.value = '';
        if (parentHiddenEl) parentHiddenEl.value = '';
        if (typeof openModal === 'function') openModal('folder-modal');
        if (typeof window.prksRefreshFolderModalValidation === 'function') {
            void window.prksRefreshFolderModalValidation();
        }
    };

    input.onfocus = () => renderDropdown();
    input.oninput = () => {
        hidden.value = '';
        renderDropdown();
    };
    input.onblur = () => setTimeout(() => prksHideInlineComboboxResults(results), 200);

    setBtn.onclick = async () => {
        const pid = String(hidden.value || '').trim();
        if (!pid || !folderStill()) return;
        try {
            if (typeof patchWorkFolder !== 'function') return;
            if (!folderStill()) return;
            const coherenceToken = await patchWorkFolder(wid, pid, folderStill);
            if (!folderStill()) return;
            if (status) status.textContent = 'Folder updated.';
            if (typeof fetchWorkDetails === 'function') {
                const _uw = await fetchWorkDetails(wid);
                if (_uw && typeof prksOfflineCacheEntityIfCurrent === 'function') {
                    void prksOfflineCacheEntityIfCurrent('work', wid, _uw, coherenceToken);
                }
                if (typeof prksApplyOwnedWorkEntity === 'function' && prksApplyOwnedWorkEntity(ctx, wid, _uw)) {
                    if (ctx && ctx.ui) ctx.ui.workFolderEditing = false;
                    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                    if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                        updatePanelContent('details');
                    }
                }
            }
        } catch (e) {
            if (status) status.textContent = String((e && e.message) || 'Could not set folder.');
        }
    };

    clearBtn.onclick = async () => {
        try {
            if (!folderStill() || typeof patchWorkFolder !== 'function') return;
            const coherenceToken = await patchWorkFolder(wid, null, folderStill);
            if (!folderStill()) return;
            input.value = '';
            hidden.value = '';
            if (status) status.textContent = 'Removed from folder.';
            if (typeof fetchWorkDetails === 'function') {
                const _rw = await fetchWorkDetails(wid);
                if (_rw && typeof prksOfflineCacheEntityIfCurrent === 'function') {
                    void prksOfflineCacheEntityIfCurrent('work', wid, _rw, coherenceToken);
                }
                if (typeof prksApplyOwnedWorkEntity === 'function' && prksApplyOwnedWorkEntity(ctx, wid, _rw)) {
                    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                    if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                        updatePanelContent('details');
                    }
                }
            }
        } catch (e) {
            if (status) status.textContent = String((e && e.message) || 'Could not clear.');
        }
    };
}

window.renderFolderAttachControlsHtml = renderFolderAttachControlsHtml;
window.mountFolderAttachControlsForWork = mountFolderAttachControlsForWork;
window.prksFolderRowLabel = prksFolderRowLabel;
window.prksCollectFolderDescendantIds = prksCollectFolderDescendantIds;
window.prksRefreshLiveFolderDetailTrees = prksRefreshLiveFolderDetailTrees;
window.prksFillFolderDetailTree = prksFillFolderDetailTree;
window.prksFolderLibraryCatalogGlanceParts = prksFolderLibraryCatalogGlanceParts;
window.prksScheduleFolderLibraryGlance = prksScheduleFolderLibraryGlance;
window.prksPublishFolderDashboardState = prksPublishFolderDashboardState;
window.prksUnpublishFolderDashboardState = prksUnpublishFolderDashboardState;
window.prksFolderDashboardStateForRoot = prksFolderDashboardStateForRoot;
window.prksBindFolderOfflineState = prksBindFolderOfflineState;
window.prksEffectiveFolderDetailWorks = prksEffectiveFolderDetailWorks;
window.prksOpenNewFolderFromDetail = prksOpenNewFolderFromDetail;
window.prksCommitFolderDetailSurface = prksCommitFolderDetailSurface;
window.prksDeleteFolderFromDetail = deleteFolder;
window.prksBindFolderDetailLayout = prksBindFolderDetailLayout;
