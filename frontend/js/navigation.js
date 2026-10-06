/**
 * Publishes the typed route model on window, plus session navigation state,
 * sidebar family matching, contextual Back, and document titles. Not a router
 * framework and not a second parser.
 */
(function (root) {
    'use strict';

    // The hash parser, route registry, and route-owned value lists live in the
    // typed route model (frontend-app/src/routing/route-model.ts), built
    // as frontend/js/route-model.js and loaded before this file. That script
    // also carries the Work status and People role vocabularies, owned by
    // frontend-app/src/domain/. These names are aliases, not copies.
    const prksRouteModel =
        root.prksRouteModel ||
        (typeof module !== 'undefined' && module.exports && typeof require === 'function'
            ? require('./route-model.js')
            : null);
    if (!prksRouteModel) throw new Error('navigation.js requires route-model.js');

    const PRKS_HOME_HASH = prksRouteModel.HOME_HASH;
    const PRKS_ROUTE_STATES_KEY = 'prks.routeStates.v1';
    const PRKS_ROUTE_STATE_LIMIT = 50;
    const PRKS_TITLE_SUFFIX = ' — PRKS';

    const PRKS_PROGRESS_STATUS_VALUES = prksRouteModel.WORK_STATUSES;
    const PRKS_PEOPLE_ROLES = prksRouteModel.PEOPLE_ROLES;
    const PRKS_ROUTE_META = prksRouteModel.ROUTE_META;
    const prksParseRoute = prksRouteModel.parseRoute;
    const prksParseGraphFocus = prksRouteModel.parseGraphFocus;
    const prksGraphFocusHash = prksRouteModel.graphFocusHash;
    const prksIsRecognizedRoute = prksRouteModel.isRecognizedRoute;

    function prksIsRouteGenCurrent(a, b) {
        // Transition helper: accept either (ctx, generation) or (generation, ctx).
        let ctx = a;
        let generation = b;
        if (typeof a === 'number') {
            generation = a;
            ctx = b;
        }
        if (typeof generation !== 'number') return true;
        if (!ctx || typeof ctx.isCurrent !== 'function') return true;
        return ctx.isCurrent(generation);
    }

    function prksCurrentLocationHash() {
        if (typeof root.location === 'undefined' || root.location.hash == null) return PRKS_HOME_HASH;
        return root.location.hash || PRKS_HOME_HASH;
    }

    function prksCurrentCanonicalHash() {
        const hash = prksCurrentLocationHash();
        const route = prksParseRoute(hash);
        return route && route.canonicalHash ? route.canonicalHash : hash;
    }

    function prksMeta(route) {
        return (route && PRKS_ROUTE_META[route.name]) || PRKS_ROUTE_META.unknown;
    }

    function prksEscapeNavText(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function prksBackLabelForRoute(route, ctx) {
        if (!route) return 'Folders';
        if (route.name === 'progress' && route.params && route.params.status) {
            return String(route.params.status);
        }
        if (route.name === 'people-role' && route.params && route.params.role) {
            return String(route.params.role);
        }
        const owner =
            ctx ||
            (typeof root.prksGetFocusedTabContext === 'function' ? root.prksGetFocusedTabContext() : null);
        function ent(type) {
            return owner && typeof owner.getEntity === 'function' ? owner.getEntity(type) : null;
        }
        if (route.name === 'folder-detail' && route.params) {
            const folder = ent('folder');
            if (folder && folder.id === route.params.folderId) {
                const t = String(folder.title || '').trim();
                if (t) return t;
            }
        }
        if (route.name === 'person' && route.params) {
            const person = ent('person');
            if (person && person.id === route.params.personId && typeof root.personDisplayName === 'function') {
                const n = String(root.personDisplayName(person) || '').trim();
                if (n) return n;
            }
        }
        if (route.name === 'playlist-detail' && route.params) {
            const pl = ent('playlist');
            if (pl && pl.id === route.params.playlistId) {
                const t = String(pl.title || '').trim();
                if (t) return t;
            }
        }
        if (route.name === 'person-group-detail' && route.params) {
            const group = ent('personGroup');
            if (group && group.id === route.params.groupId) {
                const t = String(group.name || '').trim();
                if (t) return t;
            }
        }
        if (route.name === 'work' && route.params) {
            const work = ent('work');
            if (work && work.id === route.params.workId) {
                const t = String(work.title || '').trim();
                if (t) return t;
            }
        }
        return prksMeta(route).backLabel || 'Folders';
    }

    function prksEmptyStore() {
        return { v: 1, order: [], states: {}, origins: {} };
    }

    function prksReadStore() {
        try {
            if (typeof root.sessionStorage === 'undefined') return prksEmptyStore();
            const raw = root.sessionStorage.getItem(PRKS_ROUTE_STATES_KEY);
            if (!raw) return prksEmptyStore();
            const data = JSON.parse(raw);
            if (!data || data.v !== 1 || typeof data !== 'object') return prksEmptyStore();
            if (!Array.isArray(data.order) || typeof data.states !== 'object' || typeof data.origins !== 'object') {
                return prksEmptyStore();
            }
            return {
                v: 1,
                order: data.order.map(String),
                states: data.states || {},
                origins: data.origins || {},
            };
        } catch (_e) {
            return prksEmptyStore();
        }
    }

    function prksWriteStore(store) {
        try {
            if (typeof root.sessionStorage === 'undefined') return;
            root.sessionStorage.setItem(PRKS_ROUTE_STATES_KEY, JSON.stringify(store));
        } catch (_e) {
            /* quota / private mode */
        }
    }

    function prksTouchOrder(store, key) {
        const next = store.order.filter((k) => k !== key);
        next.push(key);
        while (next.length > PRKS_ROUTE_STATE_LIMIT) {
            const drop = next.shift();
            delete store.states[drop];
            delete store.origins[drop];
        }
        store.order = next;
    }

    function prksPruneOrphans(store) {
        const keep = Object.create(null);
        for (let i = 0; i < store.order.length; i++) keep[store.order[i]] = true;
        Object.keys(store.states).forEach((k) => {
            if (!keep[k]) delete store.states[k];
        });
        Object.keys(store.origins).forEach((k) => {
            if (!keep[k]) delete store.origins[k];
        });
    }

    function prksSidebarHrefForRoute(route, originRoute) {
        if (!route || route.name === 'unknown') return null;
        if (route.name === 'work') {
            if (originRoute && prksIsRecognizedRoute(originRoute) && originRoute.name !== 'work') {
                return prksSidebarHrefForRoute(originRoute, null);
            }
            return '#/folders';
        }
        if (route.name === 'search') return null;
        if (route.name === 'progress') return route.canonicalHash;
        if (route.name === 'people-role') {
            if (route.params && route.params.knownRole) return route.canonicalHash;
            return '#/people';
        }
        return prksMeta(route).navHref || null;
    }

    const PRKS_NAV_PEOPLE_EXPANDED_KEY = 'prks.nav.peopleExpanded';
    const PRKS_NAV_PROGRESS_EXPANDED_KEY = 'prks.nav.progressExpanded';
    const PRKS_NAV_RESEARCH_EXPANDED_KEY = 'prks.nav.researchExpanded';

    const PRKS_NAV_DISCLOSURES = {
        people: {
            listId: 'prks-nav-people-children',
            prefKey: PRKS_NAV_PEOPLE_EXPANDED_KEY,
            show: 'Show People shortcuts',
            hide: 'Hide People shortcuts',
        },
        progress: {
            listId: 'prks-nav-progress-children',
            prefKey: PRKS_NAV_PROGRESS_EXPANDED_KEY,
        },
        research: {
            listId: 'prks-nav-research-children',
            prefKey: PRKS_NAV_RESEARCH_EXPANDED_KEY,
        },
    };

    /**
     * Tri-state disclosure preference: 'unset' (no explicit choice yet),
     * 'expanded', or 'collapsed'. Missing localStorage key stays 'unset' so
     * route-family auto-expansion can still apply until the user picks a side.
     */
    function prksReadNavExpandedPref(key) {
        try {
            const raw = root.localStorage.getItem(key);
            if (raw == null) return 'unset';
            const trimmed = String(raw).trim();
            if (trimmed === '1' || trimmed === 'true') return 'expanded';
            if (trimmed === '0' || trimmed === 'false') return 'collapsed';
            return 'unset';
        } catch (_e) {
            return 'unset';
        }
    }

    function prksWriteNavExpandedPref(key, on) {
        try {
            root.localStorage.setItem(key, on ? '1' : '0');
        } catch (_e) {}
    }

    function prksPeopleRouteForcesOpen(route) {
        return !!(
            route &&
            (route.name === 'person' ||
                route.name === 'people-role' ||
                route.name === 'people-groups' ||
                route.name === 'person-group-detail')
        );
    }

    function prksProgressRouteForcesOpen(route) {
        return !!(route && route.name === 'progress');
    }

    function prksResearchRouteForcesOpen(route) {
        return !!(
            route &&
            (route.name === 'concepts' ||
                route.name === 'concept-detail' ||
                route.name === 'positions' ||
                route.name === 'position-detail' ||
                route.name === 'arguments' ||
                route.name === 'argument-detail' ||
                route.name === 'research-graph')
        );
    }

    function prksNavFamilyForcesOpen(which, route) {
        if (which === 'people') return prksPeopleRouteForcesOpen(route);
        if (which === 'progress') return prksProgressRouteForcesOpen(route);
        if (which === 'research') return prksResearchRouteForcesOpen(route);
        return false;
    }

    /**
     * Effective open/closed state for a family: an explicit user preference
     * always wins; only when unset does the active route family decide.
     */
    function prksNavDisclosureExpanded(which, route) {
        const spec = PRKS_NAV_DISCLOSURES[which];
        if (!spec) return false;
        const pref = prksReadNavExpandedPref(spec.prefKey);
        if (pref === 'expanded') return true;
        if (pref === 'collapsed') return false;
        return prksNavFamilyForcesOpen(which, route);
    }

    function prksSetDisclosureVisual(which, expanded, containsCurrent) {
        if (typeof document === 'undefined' || !document.getElementById) return;
        const spec = PRKS_NAV_DISCLOSURES[which];
        if (!spec) return;
        const list = document.getElementById(spec.listId);
        const btn =
            document.querySelector &&
            document.querySelector('[data-nav-disclosure-toggle="' + which + '"]');
        const wrap =
            document.querySelector && document.querySelector('[data-nav-disclosure="' + which + '"]');
        if (btn) {
            btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            if (spec.show && spec.hide) {
                btn.setAttribute('aria-label', expanded ? spec.hide : spec.show);
            }
        }
        if (list) {
            list.hidden = !expanded;
            if (expanded) list.removeAttribute('hidden');
            else list.setAttribute('hidden', '');
        }
        if (wrap) {
            wrap.classList.toggle('nav-disclosure--open', !!expanded);
            wrap.classList.toggle('nav-disclosure--contains-current', !!containsCurrent);
        }
    }

    function prksSyncNavDisclosures(route) {
        if (typeof document === 'undefined') return;
        Object.keys(PRKS_NAV_DISCLOSURES).forEach(function (which) {
            const expanded = prksNavDisclosureExpanded(which, route);
            const containsCurrent = prksNavFamilyForcesOpen(which, route);
            prksSetDisclosureVisual(which, expanded, containsCurrent);
        });
    }

    function prksInitNavDisclosures() {
        if (typeof document === 'undefined' || !document.querySelectorAll) return;
        const buttons = document.querySelectorAll('[data-nav-disclosure-toggle]');
        for (let i = 0; i < buttons.length; i++) {
            const btn = buttons[i];
            if (!btn || btn.dataset.bound === '1') continue;
            btn.dataset.bound = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                const which = btn.getAttribute('data-nav-disclosure-toggle');
                const spec = PRKS_NAV_DISCLOSURES[which];
                if (!spec) return;
                const route = prksParseRoute(root.location ? root.location.hash : '');
                const app = document.getElementById && document.getElementById('app-container');
                const body = document.body;
                const opensRailNavigation = !!(
                    btn.classList &&
                    btn.classList.contains('nav-disclosure__toggle--full') &&
                    app &&
                    app.classList &&
                    app.classList.contains('app-container--tiled') &&
                    body &&
                    body.classList &&
                    !body.classList.contains('prks-sidebar-open')
                );
                if (opensRailNavigation) {
                    if (typeof root.prksToggleSidebarDrawer === 'function') {
                        root.prksToggleSidebarDrawer(true);
                    } else if (typeof root.prksOpenSidebarDrawer === 'function') {
                        root.prksOpenSidebarDrawer();
                    }
                    prksWriteNavExpandedPref(spec.prefKey, true);
                    prksSyncNavDisclosures(route);
                    return;
                }
                const expandedNow = prksNavDisclosureExpanded(which, route);
                prksWriteNavExpandedPref(spec.prefKey, !expandedNow);
                prksSyncNavDisclosures(route);
            });
        }
        prksSyncNavDisclosures(prksParseRoute(root.location ? root.location.hash : ''));
    }

    function prksSyncSidebarActive(route) {
        if (typeof document === 'undefined' || !document.querySelectorAll) return;
        const origin = prksReadOriginForRoute(route);
        const href = prksSidebarHrefForRoute(route, origin);
        const links = document.querySelectorAll('.nav-link');
        links.forEach((l) => {
            l.classList.remove('active');
            l.removeAttribute('aria-current');
        });
        let match = null;
        if (href) {
            links.forEach((l) => {
                if (l.getAttribute('href') === href) match = l;
            });
            if (!match && route && route.name === 'progress' && route.params && route.params.status) {
                links.forEach((l) => {
                    if (l.getAttribute('data-status') === route.params.status) match = l;
                });
            }
        }
        if (match) {
            match.classList.add('active');
            match.setAttribute('aria-current', 'page');
        }
        prksSyncNavDisclosures(route);
    }

    function prksRouteScrollElement(ctx) {
        if (typeof document === 'undefined' || !document.querySelector) return null;
        const scope = ctx && ctx.root && typeof ctx.root.querySelector === 'function' ? ctx.root : document;
        const inner = scope.querySelector(
            '.prks-folder-library__pane:not(.is-hidden) .prks-folder-library__scroll, ' +
                '.prks-people-library__scroll:not(.prks-people-library__scroll--embedded), ' +
                '.prks-group-library__scroll'
        );
        if (inner) return inner;
        if (ctx && ctx.root && typeof ctx.root.closest === 'function') {
            const tileBody = ctx.root.closest('.prks-tile__body');
            if (tileBody) return tileBody;
        }
        return document.getElementById('main-content');
    }

    function prksCaptureCurrentRouteState(prevRoute, ctx) {
        if (!prevRoute || !prksIsRecognizedRoute(prevRoute)) return;
        const el = prksRouteScrollElement(ctx);
        const scrollTop = el && Number.isFinite(el.scrollTop) ? el.scrollTop : 0;
        const key = prevRoute.canonicalHash;
        if (ctx && ctx.navigation && ctx.navigation.routeStates && typeof ctx.navigation.routeStates.set === 'function') {
            ctx.navigation.routeStates.set(key, { scrollTop: scrollTop });
            return;
        }
        const store = prksReadStore();
        store.states[key] = { scrollTop: scrollTop };
        prksTouchOrder(store, key);
        prksPruneOrphans(store);
        prksWriteStore(store);
    }

    function prksRestoreRouteState(ctx, route, generation) {
        if (ctx && typeof ctx.isCurrent === 'function' && typeof generation === 'number' && !ctx.isCurrent(generation)) return null;
        if (!route || !prksIsRecognizedRoute(route)) return null;

        const key = route.canonicalHash;
        let saved = null;
        if (ctx && ctx.navigation && ctx.navigation.routeStates && typeof ctx.navigation.routeStates.get === 'function') {
            saved = ctx.navigation.routeStates.get(key);
        } else {
            const store = prksReadStore();
            saved = store.states[key];
        }
        if (!saved || typeof saved !== 'object') return null;
        const top = Number(saved.scrollTop);
        if (!Number.isFinite(top) || top < 0) return null;
        const el = prksRouteScrollElement(ctx);
        if (!el) return null;
        el.scrollTop = top;
        if (typeof root.requestAnimationFrame === 'function') {
            root.requestAnimationFrame(function () {
                if (ctx && typeof ctx.isCurrent === 'function' && typeof generation === 'number' && !ctx.isCurrent(generation)) return;
                if (ctx && ctx.lastResolvedRoute && ctx.lastResolvedRoute.canonicalHash !== route.canonicalHash) return;
                const again = prksRouteScrollElement(ctx);
                if (again) again.scrollTop = top;
            });
        }
        return { scrollTop: top };
    }

    function prksValidateOriginRecord(raw) {
        if (!raw || typeof raw !== 'object') return null;
        const parsed = prksParseRoute(raw.hash);
        if (!prksIsRecognizedRoute(parsed)) return null;
        if (parsed.canonicalHash.charAt(0) !== '#' || parsed.canonicalHash.charAt(1) !== '/') return null;
        let label = typeof raw.label === 'string' ? raw.label : prksBackLabelForRoute(parsed);
        label = String(label || '').replace(/\s+/g, ' ').trim().slice(0, 80);
        if (!label) label = prksBackLabelForRoute(parsed);
        return { hash: parsed.canonicalHash, name: parsed.name, label: label };
    }

    function prksRememberOrigin(destRoute, fromRoute, ctx) {
        if (!destRoute || !destRoute.detail || !fromRoute) return;
        if (!prksIsRecognizedRoute(fromRoute)) return;
        if (fromRoute.canonicalHash === destRoute.canonicalHash) return;
        const rec = prksValidateOriginRecord({
            hash: fromRoute.canonicalHash,
            name: fromRoute.name,
            label: prksBackLabelForRoute(fromRoute, ctx),
        });
        if (!rec) return;
        const key = destRoute.canonicalHash;
        if (ctx && ctx.navigation && ctx.navigation.origins && typeof ctx.navigation.origins.set === 'function') {
            ctx.navigation.origins.set(key, rec);
            // Preserve roughly-bounded memory like session fallback.
            if (typeof PRKS_ROUTE_STATE_LIMIT === 'number' && ctx.navigation.origins.size > PRKS_ROUTE_STATE_LIMIT) {
                const over = ctx.navigation.origins.size - PRKS_ROUTE_STATE_LIMIT;
                const keys = Array.from(ctx.navigation.origins.keys());
                for (let i = 0; i < over; i++) ctx.navigation.origins.delete(keys[i]);
            }
            return;
        }
        const store = prksReadStore();
        store.origins[key] = rec;
        prksTouchOrder(store, key);
        prksPruneOrphans(store);
        prksWriteStore(store);
    }

    function prksReadOriginForRoute(route, ctx) {
        if (!route || !route.detail) return null;
        const key = route.canonicalHash;
        if (ctx && ctx.navigation && ctx.navigation.origins && typeof ctx.navigation.origins.get === 'function') {
            const rec = ctx.navigation.origins.get(key);
            return rec && typeof rec === 'object' ? prksValidateOriginRecord(rec) : null;
        }
        const store = prksReadStore();
        return prksValidateOriginRecord(store.origins[key]);
    }

    function prksResolveBackTarget(ctx, route) {
        const fallbackHash = prksMeta(route).fallbackBack || PRKS_HOME_HASH;
        const fallbackRoute = prksParseRoute(fallbackHash);
        const origin = prksReadOriginForRoute(route, ctx);
        if (origin) {
            const parsed = prksParseRoute(origin.hash);
            if (prksIsRecognizedRoute(parsed) && parsed.canonicalHash !== (route && route.canonicalHash)) {
                return {
                    hash: parsed.canonicalHash,
                    label: origin.label || prksBackLabelForRoute(parsed),
                };
            }
        }
        return {
            hash: fallbackRoute.canonicalHash || PRKS_HOME_HASH,
            label: prksBackLabelForRoute(fallbackRoute),
        };
    }

    function prksContextualBackHtml(ctx, route) {
        if (!route || !route.detail) return '';
        const dest = prksResolveBackTarget(ctx, route);
        if (!dest || !dest.hash) return '';
        const label = String(dest.label || 'Back').replace(/\s+/g, ' ').trim() || 'Back';
        const href = dest.hash;
        const aria = 'Back to ' + label;
        return (
            '<a class="prks-nav-back" href="' +
            prksEscapeNavText(href) +
            '" aria-label="' +
            prksEscapeNavText(aria) +
            '">' +
            '<span class="prks-nav-back__icon" aria-hidden="true">←</span>' +
            '<span class="prks-nav-back__label">' +
            prksEscapeNavText(label) +
            '</span></a>'
        );
    }

    function prksIsPdfWorkWithoutHeader(container) {
        if (!container || !container.querySelector) return false;
        return !!(container.querySelector('.work-detail') && !container.querySelector('.page-header--work'));
    }

    function prksMountContextualBack(ctx, route, generation, container) {
        if (ctx && typeof ctx.isCurrent === 'function' && typeof generation === 'number' && !ctx.isCurrent(generation)) return;
        const el = container || (ctx && ctx.root ? ctx.root : null);
        if (!el || !el.querySelector) return;
        if (!route || !route.detail) return;
        if (el.querySelector('.prks-nav-back')) return;
        const html = prksContextualBackHtml(ctx, route);
        if (!html) return;
        if (prksIsPdfWorkWithoutHeader(el)) {
            const detail = el.querySelector('.work-detail');
            if (detail) {
                detail.insertAdjacentHTML('afterbegin', '<div class="prks-nav-back-row prks-nav-back-row--work">' + html + '</div>');
            }
            return;
        }
        const header = el.querySelector('.page-header');
        if (header) {
            header.insertAdjacentHTML('afterbegin', html);
            return;
        }
        el.insertAdjacentHTML('afterbegin', '<div class="prks-nav-back-row">' + html + '</div>');
    }

    function prksResolvedRouteTitle(route, options) {
        const opts = options || {};
        if (opts.notFound) {
            return String(opts.notFoundTitle || 'Not found');
        }
        if (route && route.name === 'unknown') {
            return 'Section unavailable';
        }
        if (route && route.name === 'search') {
            return 'Search';
        }
        const entity = opts.entityTitle != null ? String(opts.entityTitle).trim() : '';
        if (entity) return entity;
        const meta = prksMeta(route);
        return meta.title || 'PRKS';
    }

    function prksDocumentTitleText(route, options) {
        const base = prksResolvedRouteTitle(route, options);
        if (base === 'PRKS') return 'PRKS';
        return base + PRKS_TITLE_SUFFIX;
    }

    function prksSetResolvedDocumentTitle(ctx, route, options) {
        const isMain =
            typeof root.prksIsMainTabContext === 'function' ? root.prksIsMainTabContext(ctx) : !!ctx;
        if (!isMain) return;
        if (typeof document === 'undefined') return;
        document.title = prksDocumentTitleText(route, options);
    }

    function prksRouteLoadingTitle(hash) {
        const route = prksParseRoute(hash);
        return prksMeta(route).loadingTitle || 'Loading';
    }

    function prksRouteTabIcon(hash) {
        const route = prksParseRoute(hash);
        return prksMeta(route).tabIcon || 'file-text';
    }

    const PRKS_TILE_ROUTE_NAMES = {
        work: true,
        person: true,
        'concept-detail': true,
        'position-detail': true,
        'argument-detail': true,
        'playlist-detail': true,
        'folder-detail': true,
    };

    function prksRouteSupportsTile(hashOrRoute) {
        let route = hashOrRoute;
        if (route == null || typeof route === 'string') route = prksParseRoute(route);
        if (!route || !route.name) return false;
        return !!PRKS_TILE_ROUTE_NAMES[route.name];
    }

    function prksPublishMainShell(ctx, options) {
        if (!ctx) return;
        const route = ctx.lastResolvedRoute || ctx.route;
        if (!route) return;
        const opts = options && typeof options === 'object' ? Object.assign({}, options) : {};
        if (!opts.entityTitle && ctx.tabId && typeof root.prksWorkspaceSnapshot === 'function') {
            const snap = root.prksWorkspaceSnapshot();
            if (snap && Array.isArray(snap.tabs)) {
                for (let i = 0; i < snap.tabs.length; i++) {
                    if (snap.tabs[i].id === ctx.tabId && snap.tabs[i].title) {
                        opts.entityTitle = snap.tabs[i].title;
                        break;
                    }
                }
            }
        }
        prksSetResolvedDocumentTitle(ctx, route, opts);
        if (typeof prksSyncSidebarActive === 'function') prksSyncSidebarActive(route);
        if (typeof prksSyncNavDisclosures === 'function') prksSyncNavDisclosures(route);
    }

    function prksPublishRouteSidebar(ctx, data, generation) {
        if (!ctx || typeof ctx.isCurrent !== 'function') return false;
        if (!ctx.isCurrent(generation)) return false;
        ctx.routeSidebar = data && typeof data === 'object' ? data : {};
        return true;
    }

    function prksAssignRouteEntity(key, value, routeGen) {
        if (!prksIsRouteGenCurrent(routeGen)) return false;
        root[key] = value;
        return true;
    }

    function prksNavigate(hash, options) {
        if (typeof root.prksWorkspaceNavigate === 'function' && root.__prksWorkspaceReady) {
            return root.prksWorkspaceNavigate(hash, options);
        }
        const route = prksParseRoute(hash);
        const target = route.canonicalHash || PRKS_HOME_HASH;
        if (!target || target.charAt(0) !== '#' || target.charAt(1) !== '/') return;
        const replace = !!(options && options.replace);
        const current = prksCurrentLocationHash();
        const currentCanon = prksParseRoute(current).canonicalHash;
        if (typeof root.location === 'undefined') return;
        if (replace) {
            try {
                const url = new URL(root.location.href);
                url.hash = target;
                root.history.replaceState(null, '', url.href);
            } catch (_e) {
                root.location.hash = target;
            }
            if (typeof root.handleRoute === 'function') void root.handleRoute();
            return;
        }
        if (current === target || currentCanon === target) {
            if (current !== target) {
                try {
                    const url = new URL(root.location.href);
                    url.hash = target;
                    root.history.replaceState(null, '', url.href);
                } catch (_e) {
                    root.location.hash = target;
                }
            }
            if (typeof root.handleRoute === 'function') void root.handleRoute();
            return;
        }
        root.location.hash = target;
    }

    function prksRenderRouteError(contentDiv, ctx, failedHash, generation) {
        if (!contentDiv) return;
        contentDiv.innerHTML =
            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Could not load this view</h2></div>' +
            '<p class="prks-inline-message">Could not load this view.</p>' +
            '<p><button type="button" class="prks-btn prks-btn--primary" id="prks-route-retry">Retry</button></p>';
        contentDiv.removeAttribute('aria-busy');
        const btn = contentDiv.querySelector('#prks-route-retry');
        if (btn) {
            btn.onclick = function () {
                if (
                    ctx &&
                    typeof ctx.isCurrent === 'function' &&
                    typeof generation === 'number' &&
                    !ctx.isCurrent(generation)
                ) {
                    return;
                }
                const retryHash = failedHash || (ctx && ctx.route && (ctx.route.canonicalHash || ctx.route.hash));
                if (!retryHash) return;
                prksNavigate(retryHash, { replace: true, tabId: ctx && ctx.tabId });
            };
        }
    }

    function prksFinishRouteRender(ctx, route, generation, contentDiv, options) {
        if (!ctx || typeof ctx.isCurrent !== 'function') return false;
        if (!ctx.isCurrent(generation)) return false;

        const opts = options || {};
        ctx.lastResolvedRoute = route || null;

        const restored = prksRestoreRouteState(ctx, route, generation);
        prksMountContextualBack(ctx, route, generation, contentDiv);

        const cd = contentDiv || (ctx && ctx.root ? ctx.root : null);
        if (cd && cd.removeAttribute) cd.removeAttribute('aria-busy');
        if (ctx.root && ctx.root.removeAttribute) ctx.root.removeAttribute('aria-busy');
        if (typeof root.prksSetFolderPendingOwnerInert === 'function') {
            root.prksSetFolderPendingOwnerInert(ctx, cd, false);
        }

        const resolvedTitle = prksResolvedRouteTitle(route, opts);
        const routeHash = route ? route.canonicalHash || route.hash : '';
        if (typeof root.prksWorkspaceSetResolvedTitleForTab === 'function') {
            root.prksWorkspaceSetResolvedTitleForTab(ctx.tabId, routeHash, resolvedTitle, generation);
        } else if (typeof root.prksWorkspaceSetResolvedTitle === 'function') {
            root.prksWorkspaceSetResolvedTitle(routeHash, resolvedTitle, generation);
        }

        if (typeof root.prksIsMainTabContext === 'function' && root.prksIsMainTabContext(ctx)) {
            prksSetResolvedDocumentTitle(ctx, route, opts);
            if (typeof prksSyncSidebarActive === 'function') prksSyncSidebarActive(route);
            if (typeof prksSyncNavDisclosures === 'function') prksSyncNavDisclosures(route);
        }

        const skipAnim =
            !!(opts && opts.skipPageEnter) ||
            (restored && Number(restored.scrollTop) > 8);
        if (!skipAnim && typeof root.prksPlayPageEnterAnimation === 'function' && cd) {
            root.prksPlayPageEnterAnimation(cd);
        }
        return true;
    }

    const api = {
        PRKS_HOME_HASH: PRKS_HOME_HASH,
        PRKS_ROUTE_META: PRKS_ROUTE_META,
        PRKS_ROUTE_STATES_KEY: PRKS_ROUTE_STATES_KEY,
        PRKS_PROGRESS_STATUS_VALUES: PRKS_PROGRESS_STATUS_VALUES,
        PRKS_PEOPLE_ROLES: PRKS_PEOPLE_ROLES,
        prksParseRoute: prksParseRoute,
        prksGraphFocusHash: prksGraphFocusHash,
        prksParseGraphFocus: prksParseGraphFocus,
        prksCurrentCanonicalHash: prksCurrentCanonicalHash,
        prksNavigate: prksNavigate,
        prksSyncSidebarActive: prksSyncSidebarActive,
        prksSyncNavDisclosures: prksSyncNavDisclosures,
        prksInitNavDisclosures: prksInitNavDisclosures,
        prksReadNavExpandedPref: prksReadNavExpandedPref,
        prksWriteNavExpandedPref: prksWriteNavExpandedPref,
        prksNavDisclosureExpanded: prksNavDisclosureExpanded,
        prksNavFamilyForcesOpen: prksNavFamilyForcesOpen,
        PRKS_NAV_DISCLOSURES: PRKS_NAV_DISCLOSURES,
        prksCaptureCurrentRouteState: prksCaptureCurrentRouteState,
        prksRestoreRouteState: prksRestoreRouteState,
        prksRememberOrigin: prksRememberOrigin,
        prksReadOriginForRoute: prksReadOriginForRoute,
        prksResolveBackTarget: prksResolveBackTarget,
        prksContextualBackHtml: prksContextualBackHtml,
        prksMountContextualBack: prksMountContextualBack,
        prksSetResolvedDocumentTitle: prksSetResolvedDocumentTitle,
        prksResolvedRouteTitle: prksResolvedRouteTitle,
        prksDocumentTitleText: prksDocumentTitleText,
        prksRouteLoadingTitle: prksRouteLoadingTitle,
        prksRouteTabIcon: prksRouteTabIcon,
        prksRouteSupportsTile: prksRouteSupportsTile,
        prksPublishMainShell: prksPublishMainShell,
        prksPublishRouteSidebar: prksPublishRouteSidebar,
        prksAssignRouteEntity: prksAssignRouteEntity,
        prksIsRouteGenCurrent: prksIsRouteGenCurrent,
        prksFinishRouteRender: prksFinishRouteRender,
        prksRenderRouteError: prksRenderRouteError,
        prksSidebarHrefForRoute: prksSidebarHrefForRoute,
        prksIsRecognizedRoute: prksIsRecognizedRoute,
        prksValidateOriginRecord: prksValidateOriginRecord,
        prksRouteScrollElement: prksRouteScrollElement,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
