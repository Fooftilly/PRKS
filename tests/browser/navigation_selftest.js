/**
 * Navigation self-tests. Load after frontend/js/navigation.js.
 * Browser fixture and Node both call prksRunNavigationSelfTests().
 */
(function (root) {
    'use strict';

    function prksRunNavigationSelfTests() {
        const rows = [];
        let passed = 0;
        let failed = 0;

        function record(name, ok, detail) {
            rows.push({ name: name, ok: !!ok, detail: detail || '' });
            if (ok) passed += 1;
            else failed += 1;
        }

        function assertEq(name, got, want) {
            const ok = got === want;
            record(name, ok, ok ? '' : 'got ' + JSON.stringify(got) + ' want ' + JSON.stringify(want));
        }

        function assert(name, cond, detail) {
            record(name, !!cond, detail);
        }

        const parse = root.prksParseRoute;
        if (typeof parse !== 'function') {
            record('prksParseRoute exists', false, 'missing');
            return { passed: passed, failed: failed + 1, rows: rows };
        }

        assertEq('#/folders name', parse('#/folders').name, 'folders');
        assertEq('#/folders/F-1 name', parse('#/folders/F-1').name, 'folder-detail');
        assertEq('#/folders/F-1 id', parse('#/folders/F-1').params.folderId, 'F-1');
        assertEq('#/works/W-1 name', parse('#/works/W-1').name, 'work');
        assertEq('#/works/W-1 id', parse('#/works/W-1').params.workId, 'W-1');
        assertEq('#/people/P-1 name', parse('#/people/P-1').name, 'person');
        assertEq('#/people/P-1 id', parse('#/people/P-1').params.personId, 'P-1');
        assertEq('#/people/role/Author name', parse('#/people/role/Author').name, 'people-role');
        assertEq('#/people/role/Author role', parse('#/people/role/Author').params.role, 'Author');
        assertEq('#/people/groups name', parse('#/people/groups').name, 'people-groups');
        assertEq('#/people/groups/PG-1 name', parse('#/people/groups/PG-1').name, 'person-group-detail');
        assertEq('#/people/groups/PG-1 id', parse('#/people/groups/PG-1').params.groupId, 'PG-1');
        assertEq('#/progress?status=In%20Progress name', parse('#/progress?status=In%20Progress').name, 'progress');
        assertEq('#/progress?status=In%20Progress status', parse('#/progress?status=In%20Progress').params.status, 'In Progress');
        assertEq('#/search?q=a&tag=b name', parse('#/search?q=a&tag=b').name, 'search');
        assertEq('#/search?q=a&tag=b q', parse('#/search?q=a&tag=b').params.q, 'a');
        assertEq('#/search?q=a&tag=b tag', parse('#/search?q=a&tag=b').params.tag, 'b');
        assertEq('#/recent name', parse('#/recent').name, 'recent');
        assertEq('#/concepts name', parse('#/concepts').name, 'concepts');
        assertEq('#/concepts/C-1 name', parse('#/concepts/C-1').name, 'concept-detail');
        assertEq('#/concepts/C-1 id', parse('#/concepts/C-1').params.conceptId, 'C-1');
        assertEq('#/positions name', parse('#/positions').name, 'positions');
        assertEq('#/positions/P-1 name', parse('#/positions/P-1').name, 'position-detail');
        assertEq('#/arguments name', parse('#/arguments').name, 'arguments');
        assertEq('#/arguments?kind=stance name', parse('#/arguments?kind=stance').name, 'arguments');
        assertEq('#/arguments?kind=stance kind', parse('#/arguments?kind=stance').params.kind, 'stance');
        assertEq('#/arguments/A-1 name', parse('#/arguments/A-1').name, 'argument-detail');
        assert('concept is detail', parse('#/concepts/C-1').detail === true);
        assert('arguments index not detail', parse('#/arguments').detail !== true);
        assertEq('#/views/SV-1 name', parse('#/views/SV-1').name, 'saved-view-detail');
        assertEq('#/views/SV-1 id', parse('#/views/SV-1').params.viewId, 'SV-1');
        assertEq('#/views/ missing id', parse('#/views/').name, 'unknown');
        assert('saved view is detail', parse('#/views/SV-1').detail === true);
        assertEq('#/types name', parse('#/types').name, 'types');
        assertEq('#/types/article name', parse('#/types/article').name, 'type-detail');
        assertEq('#/playlists name', parse('#/playlists').name, 'playlists');
        assertEq('#/playlists/PL-1 name', parse('#/playlists/PL-1').name, 'playlist-detail');
        assertEq('#/graph name', parse('#/graph').name, 'research-graph');
        assertEq('#/graph canonical', parse('#/graph').canonicalHash, '#/graph');
        assertEq('#/graph?focus=concept:C-1 name', parse('#/graph?focus=concept:C-1').name, 'research-graph');
        assertEq('#/graph?focus=concept:C-1 focus', parse('#/graph?focus=concept:C-1').params.focus, 'concept:C-1');
        assertEq('#/graph?focus=person:P-123 name', parse('#/graph?focus=person:P-123').name, 'research-graph');
        assertEq('#/graph?focus=person:P-123 focus', parse('#/graph?focus=person:P-123').params.focus, 'person:P-123');
        assert(
            'graph focus preserved in canonical',
            parse('#/graph?focus=concept:C-1').canonicalHash.indexOf('focus=') >= 0
        );
        assertEq('unknown graph focus ignored', parse('#/graph?focus=nope').params.focus, '');
        assertEq('unknown graph focus canonical', parse('#/graph?focus=nope').canonicalHash, '#/graph');
        assertEq('bare id focus ignored', parse('#/graph?focus=C-1').params.focus, '');
        assertEq('#/future-feature name', parse('#/future-feature').name, 'unknown');
        assertEq('malformed percent encoding', parse('#/works/%').name, 'unknown');
        assertEq('javascript hash', parse('javascript:alert(1)').name, 'unknown');
        assertEq('missing work id', parse('#/works/').name, 'unknown');
        assertEq('people/groups not person', parse('#/people/groups/PG-1').name, 'person-group-detail');
        assert('work is detail', parse('#/works/W-1').detail === true);
        assert('folders not detail', parse('#/folders').detail !== true);
        assertEq('progress missing status canonicalize', parse('#/progress').canonicalize, true);
        assertEq('progress default canonical', parse('#/progress').canonicalHash, '#/progress?status=Not%20Started');

        const href = root.prksSidebarHrefForRoute;
        if (typeof href === 'function') {
            assertEq('folder detail nav', href(parse('#/folders/F-1')), '#/folders');
            assertEq('concept detail nav', href(parse('#/concepts/C-1')), '#/concepts');
            assertEq('argument detail nav', href(parse('#/arguments/A-1')), '#/arguments');
            assertEq('position detail nav', href(parse('#/positions/P-1')), '#/positions');
            assertEq('saved view detail nav', href(parse('#/views/SV-1')), '#/views');
            assertEq('playlist detail nav', href(parse('#/playlists/PL-1')), '#/playlists');
            assertEq('type detail nav', href(parse('#/types/article')), '#/types');
            assertEq('person nav', href(parse('#/people/P-1')), '#/people');
            assertEq('group detail nav', href(parse('#/people/groups/PG-1')), '#/people/groups');
            assertEq('author role nav', href(parse('#/people/role/Author')), '#/people/role/Author');
            assertEq('paused progress nav', href(parse('#/progress?status=Paused')), '#/progress?status=Paused');
            assertEq('graph nav', href(parse('#/graph')), '#/graph');
            assertEq('graph focus nav', href(parse('#/graph?focus=concept:C-1')), '#/graph');
            assertEq('search nav none', href(parse('#/search?q=secret')), null);
            assertEq('work default nav', href(parse('#/works/W-1')), '#/folders');
            assertEq(
                'work from search nav none',
                href(parse('#/works/W-1'), parse('#/search?q=test')),
                null
            );
            assertEq(
                'work from recent nav',
                href(parse('#/works/W-1'), parse('#/recent')),
                '#/recent'
            );
        }

        if (typeof root.prksDocumentTitleText === 'function') {
            assertEq(
                'folders title',
                root.prksDocumentTitleText(parse('#/folders'), {}),
                'Folders — PRKS'
            );
            assertEq(
                'work entity title',
                root.prksDocumentTitleText(parse('#/works/W-1'), { entityTitle: 'Retorika' }),
                'Retorika — PRKS'
            );
            assertEq(
                'person entity title',
                root.prksDocumentTitleText(parse('#/people/P-1'), { entityTitle: 'Theodor Adorno' }),
                'Theodor Adorno — PRKS'
            );
            assertEq(
                'search title hides query',
                root.prksDocumentTitleText(parse('#/search?q=Adorno%20AND%20private'), {}),
                'Search — PRKS'
            );
            assertEq(
                'graph title',
                root.prksDocumentTitleText(parse('#/graph'), {}),
                'Research Graph — PRKS'
            );
            assert(
                'search title omits query string',
                root.prksDocumentTitleText(parse('#/search?q=secret-phrase'), {}).indexOf('secret-phrase') === -1
            );
            assertEq(
                'not found title',
                root.prksDocumentTitleText(parse('#/works/W-1'), { notFound: true, notFoundTitle: 'File not found' }),
                'File not found — PRKS'
            );
            assertEq(
                'unknown title',
                root.prksDocumentTitleText(parse('#/future-feature'), {}),
                'Section unavailable — PRKS'
            );
        }

        if (typeof root.sessionStorage !== 'undefined' && typeof root.prksRememberOrigin === 'function') {
            try {
                root.sessionStorage.removeItem(root.PRKS_ROUTE_STATES_KEY);
            } catch (_e) {}
            const search = parse('#/search?q=test');
            const work = parse('#/works/W-1');
            root.prksRememberOrigin(work, search);
            const origin = root.prksReadOriginForRoute(work);
            assert('origin from search', origin && origin.hash === '#/search?q=test', origin && origin.hash);
            const back = root.prksResolveBackTarget(null, work);
            assertEq('back hash from search', back.hash, '#/search?q=test');
            try {
                root.sessionStorage.removeItem(root.PRKS_ROUTE_STATES_KEY);
            } catch (_e) {}
            const direct = root.prksResolveBackTarget(null, parse('#/works/W-1'));
            assertEq('direct work fallback hash', direct.hash, '#/folders');
            assertEq('direct person fallback hash', root.prksResolveBackTarget(null, parse('#/people/P-1')).hash, '#/people');
            assertEq('direct group fallback hash', root.prksResolveBackTarget(null, parse('#/people/groups/PG-1')).hash, '#/people/groups');
            assertEq('direct playlist fallback hash', root.prksResolveBackTarget(null, parse('#/playlists/PL-1')).hash, '#/playlists');
            assertEq('direct saved view fallback hash', root.prksResolveBackTarget(null, parse('#/views/SV-1')).hash, '#/views');

            try {
                root.sessionStorage.removeItem(root.PRKS_ROUTE_STATES_KEY);
            } catch (_e) {}
            const sv = parse('#/views/SV-1');
            const workFromSv = parse('#/works/W-1');
            root.prksRememberOrigin(workFromSv, sv);
            const backSv = root.prksResolveBackTarget(null, workFromSv);
            assertEq('back hash from saved view', backSv.hash, '#/views/SV-1');
            if (typeof document !== 'undefined' && document.getElementById) {
                const main = document.getElementById('main-content');
                if (main && typeof root.prksCaptureCurrentRouteState === 'function') {
                    const ctx = {
                        tabId: 'selftest',
                        navigation: { routeStates: new Map(), origins: new Map() },
                        isCurrent: function (g) {
                            return g === 7;
                        },
                    };
                    main.scrollTop = 420;
                    root.prksCaptureCurrentRouteState(sv, ctx);
                    main.scrollTop = 0;
                    root.prksRestoreRouteState(ctx, sv, 7);
                    assertEq('saved view scroll restored', main.scrollTop, 420);
                }
            }

            const bad = root.prksValidateOriginRecord({ hash: 'javascript:alert(1)', label: 'x' });
            assertEq('reject javascript origin', bad, null);
            const ext = root.prksValidateOriginRecord({ hash: 'https://example.com/', label: 'x' });
            assertEq('reject external origin', ext, null);

            if (typeof root.prksRenderRouteError === 'function') {
                const retryBtn = {};
                const errorHost = {
                    innerHTML: '',
                    removeAttribute: function () {},
                    querySelector: function (sel) { return sel === '#prks-route-retry' ? retryBtn : null; },
                };
                const retryCtx = {
                    tabId: 'secondary-retry',
                    isCurrent: function (generation) { return generation === 9; },
                    route: parse('#/concepts/C-OLD'),
                };
                let retryCall = null;
                root.__prksWorkspaceReady = true;
                root.prksWorkspaceNavigate = function (hash, options) {
                    retryCall = { hash: hash, options: options };
                };
                root.prksRenderRouteError(errorHost, retryCtx, '#/concepts/C-FAILED', 9);
                retryBtn.onclick();
                assertEq('route Retry uses failed owner hash', retryCall && retryCall.hash, '#/concepts/C-FAILED');
                assertEq('route Retry uses failed owner tab', retryCall && retryCall.options.tabId, 'secondary-retry');
                assertEq('route Retry is replace', retryCall && retryCall.options.replace, true);
                delete root.prksWorkspaceNavigate;
                delete root.__prksWorkspaceReady;
            }

            if (typeof root.prksCaptureCurrentRouteState === 'function') {
                const a = parse('#/search?q=A');
                const b = parse('#/search?q=B');
                if (typeof document !== 'undefined' && document.getElementById) {
                    const main = document.getElementById('main-content');
                    if (main) {
                        const ctx = {
                            tabId: 'selftest',
                            navigation: { routeStates: new Map(), origins: new Map() },
                            generation: 1,
                            isCurrent: function (g) {
                                return g === ctx.generation;
                            },
                        };
                        main.scrollTop = 300;
                        root.prksCaptureCurrentRouteState(a, ctx);
                        main.scrollTop = 800;
                        root.prksCaptureCurrentRouteState(b, ctx);
                        root.prksRestoreRouteState(ctx, a, 1);
                        assertEq('search A scroll restored', main.scrollTop, 300);
                        root.prksRestoreRouteState(ctx, b, 1);
                        assertEq('search B scroll restored', main.scrollTop, 800);
                        main.scrollTop = 0;
                        ctx.generation = 2;
                        root.prksRestoreRouteState(ctx, a, 1);
                        assertEq('stale scroll ignored', main.scrollTop, 0);

                        const localA = { scrollTop: 111 };
                        const localB = { scrollTop: 777 };
                        function tileCtx(id, local) {
                            return {
                                tabId: id,
                                navigation: { routeStates: new Map(), origins: new Map() },
                                generation: 1,
                                isCurrent: function (g) { return g === 1; },
                                root: {
                                    querySelector: function () { return null; },
                                    closest: function (sel) { return sel === '.prks-tile__body' ? local : null; },
                                },
                            };
                        }
                        const ctxA = tileCtx('tile-a', localA);
                        const ctxB = tileCtx('tile-b', localB);
                        root.prksCaptureCurrentRouteState(a, ctxA);
                        root.prksCaptureCurrentRouteState(a, ctxB);
                        localA.scrollTop = 0;
                        localB.scrollTop = 0;
                        root.prksRestoreRouteState(ctxA, a, 1);
                        assertEq('tile A restores own scroller', localA.scrollTop, 111);
                        assertEq('tile A restore leaves B scroller', localB.scrollTop, 0);
                        root.prksRestoreRouteState(ctxB, a, 1);
                        assertEq('tile B restores own scroller', localB.scrollTop, 777);
                    }
                }
            }

            try {
                root.sessionStorage.setItem('prks-people-library-filter', 'Adorno');
            } catch (_e) {}
            root.prksCaptureCurrentRouteState(parse('#/people'));
            try {
                assertEq(
                    'people filter not overwritten by route state',
                    root.sessionStorage.getItem('prks-people-library-filter'),
                    'Adorno'
                );
                root.sessionStorage.removeItem('prks-people-library-filter');
            } catch (_e) {}

            try {
                root.sessionStorage.removeItem(root.PRKS_ROUTE_STATES_KEY);
            } catch (_e) {}
            for (let i = 0; i < 60; i++) {
                root.prksCaptureCurrentRouteState(parse('#/folders/F-' + i));
            }
            const raw = JSON.parse(root.sessionStorage.getItem(root.PRKS_ROUTE_STATES_KEY) || '{}');
            assert('state registry bounded', Array.isArray(raw.order) && raw.order.length <= 50);

            try {
                root.sessionStorage.setItem(root.PRKS_ROUTE_STATES_KEY, '{not json');
            } catch (_e) {}
            const recovered = root.prksReadOriginForRoute(parse('#/works/W-1'));
            assertEq('invalid session ignored', recovered, null);
        }

        if (typeof document !== 'undefined' && document.querySelectorAll && typeof root.prksSyncSidebarActive === 'function') {
            const links = document.querySelectorAll('.nav-link');
            if (links.length) {
                function currentCount() {
                    return document.querySelectorAll('.nav-link[aria-current="page"]').length;
                }
                function currentHref() {
                    const el = document.querySelector('.nav-link[aria-current="page"]');
                    return el ? el.getAttribute('href') : null;
                }
                root.prksSyncSidebarActive(parse('#/folders/F-1'));
                assertEq('folder detail → Folders', currentHref(), '#/folders');
                assertEq('one current folder', currentCount(), 1);
                root.prksSyncSidebarActive(parse('#/playlists/PL-1'));
                assertEq('playlist detail → Playlists', currentHref(), '#/playlists');
                assertEq('one current playlist', currentCount(), 1);
                root.prksSyncSidebarActive(parse('#/views'));
                assertEq('saved views current', currentHref(), '#/views');
                root.prksSyncSidebarActive(parse('#/views/SV-1'));
                assertEq('saved view detail → Saved Views', currentHref(), '#/views');
                assertEq('one current saved views', currentCount(), 1);
                root.prksSyncSidebarActive(parse('#/types/article'));
                assertEq('type detail → File Types', currentHref(), '#/types');
                root.prksSyncSidebarActive(parse('#/people/P-1'));
                assertEq('person → People', currentHref(), '#/people');
                root.prksSyncSidebarActive(parse('#/people/groups/PG-1'));
                assertEq('group → Groups', currentHref(), '#/people/groups');
                root.prksSyncSidebarActive(parse('#/people/role/Author'));
                assertEq('author role → Authors', currentHref(), '#/people/role/Author');
                assertEq('author not also People', currentCount(), 1);
                root.prksSyncSidebarActive(parse('#/progress?status=Paused'));
                assertEq('paused progress', currentHref(), '#/progress?status=Paused');
                assertEq('one current paused', currentCount(), 1);
                root.prksSyncSidebarActive(parse('#/graph'));
                assertEq('graph current', currentHref(), '#/graph');
                assertEq('one current graph', currentCount(), 1);
                root.prksSyncSidebarActive(parse('#/search?q=x'));
                assertEq('search marks none', currentCount(), 0);
                root.prksSyncSidebarActive(parse('#/future-feature'));
                assertEq('unknown marks none', currentCount(), 0);
            }
        }

        if (typeof root.prksSetResolvedDocumentTitle === 'function' && typeof document !== 'undefined') {
            const ctxMain = {
                tabId: 'selftest',
                navigation: { routeStates: new Map(), origins: new Map() },
            };
            document.title = 'Keep Me — PRKS';
            root.prksSetResolvedDocumentTitle(null, parse('#/folders'), {});
            assertEq('stale title ignored', document.title, 'Keep Me — PRKS');
            root.prksSetResolvedDocumentTitle(ctxMain, parse('#/recent'), {});
            assertEq('current title applied', document.title, 'Recent — PRKS');
            root.prksSetResolvedDocumentTitle(ctxMain, parse('#/views'), {});
            assertEq('saved views title', document.title, 'Saved Views — PRKS');
            root.prksSetResolvedDocumentTitle(ctxMain, parse('#/views/SV-1'), { entityTitle: 'Critical Theory' });
            assertEq('saved view entity title', document.title, 'Critical Theory — PRKS');
            root.prksSetResolvedDocumentTitle(ctxMain, parse('#/views/SV-missing'), {
                notFound: true,
                notFoundTitle: 'Saved View not found',
            });
            assertEq('saved view missing title', document.title, 'Saved View not found — PRKS');
        }

        if (typeof root.prksPublishRouteSidebar === 'function') {
            const ctx = {
                tabId: 'selftest',
                navigation: { routeStates: new Map(), origins: new Map() },
                routeSidebar: { keep: true },
                isCurrent: function (g) {
                    return g === 9;
                },
            };
            root.prksPublishRouteSidebar(ctx, { stale: true }, 3);
            assert('stale sidebar ignored', ctx.routeSidebar && ctx.routeSidebar.keep === true);
            root.prksPublishRouteSidebar(ctx, { ok: 1 }, 9);
            assert('current sidebar applied', ctx.routeSidebar && ctx.routeSidebar.ok === 1);
        }

        if (
            typeof root.prksNavDisclosureExpanded === 'function' &&
            typeof root.prksWriteNavExpandedPref === 'function' &&
            typeof root.PRKS_NAV_DISCLOSURES === 'object' &&
            typeof document !== 'undefined' &&
            document.querySelector &&
            document.querySelector('[data-nav-disclosure="people"]')
        ) {
            const peopleKey = root.PRKS_NAV_DISCLOSURES.people.prefKey;
            const progressKey = root.PRKS_NAV_DISCLOSURES.progress.prefKey;
            const researchKey = root.PRKS_NAV_DISCLOSURES.research.prefKey;
            try {
                root.localStorage.removeItem(peopleKey);
                root.localStorage.removeItem(progressKey);
                root.localStorage.removeItem(researchKey);
            } catch (_e) {}
            if (typeof root.prksInitNavDisclosures === 'function') root.prksInitNavDisclosures();

            // Unset preference: entering a People child route auto-expands People.
            const reviewerRoute = parse('#/people/role/Reviewer');
            if (root.location) root.location.hash = reviewerRoute.canonicalHash;
            assertEq('unset pref reads unset', root.prksReadNavExpandedPref(peopleKey), 'unset');
            assert('unset + reviewer route auto-expands People', root.prksNavDisclosureExpanded('people', reviewerRoute) === true);
            root.prksSyncNavDisclosures(reviewerRoute);
            const peopleWrap = document.querySelector('[data-nav-disclosure="people"]');
            const peopleList = document.getElementById('prks-nav-people-children');
            assert('People auto-opens on unset pref', peopleWrap.classList.contains('nav-disclosure--open'));
            assertEq('People list visible on auto-open', peopleList.hidden, false);

            // Explicit collapse: pressing the toggle while the family route is
            // active must visibly collapse it and persist the choice — no
            // forced-open no-op.
            const peopleToggle = document.querySelector('[data-nav-disclosure-toggle="people"]');
            peopleToggle.click();
            assertEq('collapse pref persisted', root.prksReadNavExpandedPref(peopleKey), 'collapsed');
            assert('People collapses immediately on explicit toggle', !peopleWrap.classList.contains('nav-disclosure--open'));
            assertEq('People list hidden after explicit collapse', peopleList.hidden, true);

            // Re-running sidebar sync (e.g. on subsequent navigations) must not
            // reopen a family the user explicitly collapsed, even though the
            // active route still belongs to that family.
            root.prksSyncNavDisclosures(reviewerRoute);
            assert('People stays collapsed after re-sync on same family route', !peopleWrap.classList.contains('nav-disclosure--open'));

            // Family navigation: Reviewer -> Author -> Person -> Reviewer,
            // with People explicitly collapsed throughout.
            root.prksSyncNavDisclosures(parse('#/people/role/Author'));
            assert('People stays collapsed on Author', !peopleWrap.classList.contains('nav-disclosure--open'));
            root.prksSyncNavDisclosures(parse('#/people/P-1'));
            assert('People stays collapsed on Person detail', !peopleWrap.classList.contains('nav-disclosure--open'));
            root.prksSyncNavDisclosures(reviewerRoute);
            assert('People stays collapsed back on Reviewer', !peopleWrap.classList.contains('nav-disclosure--open'));

            // Active-family indicator: even collapsed, the family row shows a
            // restrained contains-current state, but the People parent link
            // itself must not get aria-current="page" — only the exact
            // destination link does.
            assert('People family shows contains-current while collapsed', peopleWrap.classList.contains('nav-disclosure--contains-current'));
            if (typeof root.prksSyncSidebarActive === 'function') {
                root.prksSyncSidebarActive(reviewerRoute);
                const linkByHref = (href) => document.querySelectorAll('.nav-link').filter((x) => x.getAttribute('href') === href)[0];
                assertEq(
                    'Reviewer link is the exact aria-current destination',
                    (linkByHref('#/people/role/Reviewer') || {}).getAttribute
                        ? linkByHref('#/people/role/Reviewer').getAttribute('aria-current')
                        : null,
                    'page'
                );
                assertEq(
                    'People parent link has no aria-current while Reviewer is active',
                    (linkByHref('#/people') || {}).getAttribute ? linkByHref('#/people').getAttribute('aria-current') : null,
                    null
                );
            }

            // Explicit expand overrides the family predicate too: once the
            // user chooses expanded, leaving the family route must not
            // collapse it again.
            root.prksWriteNavExpandedPref(peopleKey, true);
            root.prksSyncNavDisclosures(parse('#/folders'));
            assert('People stays expanded off-family once explicitly expanded', peopleWrap.classList.contains('nav-disclosure--open'));
            assert('People has no contains-current off-family', !peopleWrap.classList.contains('nav-disclosure--contains-current'));

            try {
                root.localStorage.removeItem(peopleKey);
            } catch (_e) {}

            // Research / Progress: full-row native buttons, not link+chevron.
            ['research', 'progress'].forEach(function (which) {
                const key = root.PRKS_NAV_DISCLOSURES[which].prefKey;
                try {
                    root.localStorage.removeItem(key);
                } catch (_e) {}
                const familyRoute = which === 'research' ? parse('#/concepts') : parse('#/progress?status=Paused');
                if (root.location) root.location.hash = familyRoute.canonicalHash;
                const wrap = document.querySelector('[data-nav-disclosure="' + which + '"]');
                const list = document.getElementById(root.PRKS_NAV_DISCLOSURES[which].listId);
                const toggle = document.querySelector('[data-nav-disclosure-toggle="' + which + '"]');

                root.prksSyncNavDisclosures(familyRoute);
                assert(which + ' auto-expands on unset pref', wrap.classList.contains('nav-disclosure--open'));
                assertEq(which + ' aria-expanded true on auto-open', toggle.getAttribute('aria-expanded'), 'true');

                toggle.click();
                assertEq(which + ' collapse pref persisted', root.prksReadNavExpandedPref(key), 'collapsed');
                assert(which + ' collapses immediately on click', !wrap.classList.contains('nav-disclosure--open'));
                assertEq(which + ' aria-expanded false after collapse', toggle.getAttribute('aria-expanded'), 'false');
                assertEq(which + ' list hidden after collapse', list.hidden, true);

                root.prksSyncNavDisclosures(familyRoute);
                assert(which + ' stays collapsed after re-sync', !wrap.classList.contains('nav-disclosure--open'));
                assert(which + ' still shows contains-current while collapsed', wrap.classList.contains('nav-disclosure--contains-current'));

                toggle.click();
                assertEq(which + ' re-expand pref persisted', root.prksReadNavExpandedPref(key), 'expanded');
                assert(which + ' expands again on second click', wrap.classList.contains('nav-disclosure--open'));

                try {
                    root.localStorage.removeItem(key);
                } catch (_e) {}
            });
        }

        if (typeof root.prksContextualBackHtml === 'function') {
            const html = root.prksContextualBackHtml(null, parse('#/works/W-1'));
            assert('back is anchor', html.indexOf('<a class="prks-nav-back"') === 0);
            assert('back has aria-label', html.indexOf('aria-label="Back to ') !== -1);
            assert('back stays in hash', html.indexOf('href="#/') !== -1);
            assert('back not history.back', html.indexOf('history.back') === -1);
        }

        return { passed: passed, failed: failed, rows: rows };
    }

    root.prksRunNavigationSelfTests = prksRunNavigationSelfTests;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { prksRunNavigationSelfTests: prksRunNavigationSelfTests };
    }
})(typeof window !== 'undefined' ? window : globalThis);
