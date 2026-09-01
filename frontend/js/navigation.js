/**
 * Hash-route parser, session navigation state, sidebar family matching,
 * contextual Back, and document titles. Not a router framework.
 */
(function (root) {
    'use strict';

    const PRKS_HOME_HASH = '#/folders';
    const PRKS_ROUTE_STATES_KEY = 'prks.routeStates.v1';
    const PRKS_ROUTE_STATE_LIMIT = 50;
    const PRKS_TITLE_SUFFIX = ' — PRKS';

    const PRKS_PROGRESS_STATUS_VALUES = [
        'Not Started',
        'Planned',
        'In Progress',
        'Completed',
        'Paused',
    ];

    const PRKS_PEOPLE_ROLES = [
        'Author',
        'Editor',
        'Reviewer',
        'Translator',
        'Introduction',
        'Foreword',
        'Afterword',
    ];

    const PRKS_ROUTE_META = {
        folders: {
            title: 'Folders',
            loadingTitle: 'Folders',
            backLabel: 'Folders',
            navHref: '#/folders',
            fallbackBack: '#/folders',
            sectionHash: '#/folders',
        },
        'folder-detail': {
            title: 'Folder',
            loadingTitle: 'Folder',
            backLabel: 'Folder',
            navHref: '#/folders',
            fallbackBack: '#/folders',
            sectionHash: '#/folders',
            detail: true,
        },
        recent: {
            title: 'Recent',
            loadingTitle: 'Recent',
            backLabel: 'Recently opened',
            navHref: '#/recent',
            fallbackBack: '#/folders',
            sectionHash: '#/recent',
        },
        'saved-views': {
            title: 'Saved Views',
            loadingTitle: 'Saved Views',
            backLabel: 'Saved Views',
            navHref: '#/views',
            fallbackBack: '#/views',
            sectionHash: '#/views',
        },
        'saved-view-detail': {
            title: 'Saved View',
            loadingTitle: 'Saved View',
            backLabel: 'Saved View',
            navHref: '#/views',
            fallbackBack: '#/views',
            sectionHash: '#/views',
            detail: true,
        },
        types: {
            title: 'File Types',
            loadingTitle: 'File Types',
            backLabel: 'File types',
            navHref: '#/types',
            fallbackBack: '#/types',
            sectionHash: '#/types',
        },
        'type-detail': {
            title: 'File Type',
            loadingTitle: 'File Type',
            backLabel: 'File type',
            navHref: '#/types',
            fallbackBack: '#/types',
            sectionHash: '#/types',
            detail: true,
        },
        playlists: {
            title: 'Playlists',
            loadingTitle: 'Playlists',
            backLabel: 'Playlists',
            navHref: '#/playlists',
            fallbackBack: '#/playlists',
            sectionHash: '#/playlists',
        },
        'playlist-detail': {
            title: 'Playlist',
            loadingTitle: 'Playlist',
            backLabel: 'Playlist',
            navHref: '#/playlists',
            fallbackBack: '#/playlists',
            sectionHash: '#/playlists',
            detail: true,
        },
        tags: {
            title: 'Tags',
            loadingTitle: 'Tags',
            backLabel: 'Tags',
            navHref: '#/tags',
            fallbackBack: '#/folders',
            sectionHash: '#/tags',
        },
        publishers: {
            title: 'Publishers',
            loadingTitle: 'Publishers',
            backLabel: 'Publishers',
            navHref: '#/publishers',
            fallbackBack: '#/folders',
            sectionHash: '#/publishers',
        },
        people: {
            title: 'People',
            loadingTitle: 'People',
            backLabel: 'People',
            navHref: '#/people',
            fallbackBack: '#/people',
            sectionHash: '#/people',
        },
        person: {
            title: 'Person',
            loadingTitle: 'Person',
            backLabel: 'Person',
            navHref: '#/people',
            fallbackBack: '#/people',
            sectionHash: '#/people',
            detail: true,
        },
        'people-role': {
            title: 'People',
            loadingTitle: 'People',
            backLabel: 'People',
            navHref: null,
            fallbackBack: '#/people',
            sectionHash: '#/people',
        },
        'people-groups': {
            title: 'Groups',
            loadingTitle: 'People Groups',
            backLabel: 'Groups',
            navHref: '#/people/groups',
            fallbackBack: '#/people/groups',
            sectionHash: '#/people/groups',
        },
        'person-group-detail': {
            title: 'Group',
            loadingTitle: 'Group',
            backLabel: 'Group',
            navHref: '#/people/groups',
            fallbackBack: '#/people/groups',
            sectionHash: '#/people/groups',
            detail: true,
        },
        concepts: {
            title: 'Concepts',
            loadingTitle: 'Concepts',
            backLabel: 'Concepts',
            navHref: '#/concepts',
            fallbackBack: '#/concepts',
            sectionHash: '#/concepts',
        },
        'concept-detail': {
            title: 'Concept',
            loadingTitle: 'Concept',
            backLabel: 'Concept',
            navHref: '#/concepts',
            fallbackBack: '#/concepts',
            sectionHash: '#/concepts',
            detail: true,
        },
        positions: {
            title: 'Positions',
            loadingTitle: 'Positions',
            backLabel: 'Positions',
            navHref: '#/positions',
            fallbackBack: '#/positions',
            sectionHash: '#/positions',
        },
        'position-detail': {
            title: 'Position',
            loadingTitle: 'Position',
            backLabel: 'Position',
            navHref: '#/positions',
            fallbackBack: '#/positions',
            sectionHash: '#/positions',
            detail: true,
        },
        arguments: {
            title: 'Arguments & Stances',
            loadingTitle: 'Arguments & Stances',
            backLabel: 'Arguments & Stances',
            navHref: '#/arguments',
            fallbackBack: '#/arguments',
            sectionHash: '#/arguments',
        },
        'argument-detail': {
            title: 'Argument',
            loadingTitle: 'Argument',
            backLabel: 'Argument',
            navHref: '#/arguments',
            fallbackBack: '#/arguments',
            sectionHash: '#/arguments',
            detail: true,
        },
        'research-graph': {
            title: 'Research Graph',
            loadingTitle: 'Research Graph',
            backLabel: 'Research Graph',
            navHref: '#/graph',
            fallbackBack: '#/graph',
            sectionHash: '#/graph',
        },
        progress: {
            title: 'Progress',
            loadingTitle: 'Progress',
            backLabel: 'Progress',
            navHref: null,
            fallbackBack: '#/folders',
            sectionHash: '#/progress',
        },
        'processing-files': {
            title: 'Files for Processing',
            loadingTitle: 'Files for Processing',
            backLabel: 'Files for Processing',
            navHref: '#/processing-files',
            fallbackBack: '#/folders',
            sectionHash: '#/processing-files',
        },
        search: {
            title: 'Search',
            loadingTitle: 'Search',
            backLabel: 'Search results',
            navHref: null,
            fallbackBack: '#/folders',
            sectionHash: '#/search',
        },
        work: {
            title: 'File',
            loadingTitle: 'File',
            backLabel: 'File',
            navHref: '#/folders',
            fallbackBack: '#/folders',
            sectionHash: '#/folders',
            detail: true,
        },
        unknown: {
            title: 'Section unavailable',
            loadingTitle: 'Loading',
            backLabel: 'Folders',
            navHref: null,
            fallbackBack: '#/folders',
            sectionHash: null,
        },
    };

    function prksSafeDecode(raw) {
        const s = String(raw == null ? '' : raw);
        if (!s) return '';
        try {
            return decodeURIComponent(s);
        } catch (_e) {
            return null;
        }
    }

    function prksEncodePathSegment(raw) {
        return encodeURIComponent(String(raw == null ? '' : raw));
    }

    function prksUnknownRoute(hash) {
        const h = String(hash || '');
        return {
            name: 'unknown',
            hash: h,
            canonicalHash: h && h.charAt(0) === '#' ? h : h ? '#' + h : '#/unknown',
            params: {},
            parentSection: null,
            detail: false,
            canonicalize: false,
        };
    }

    function prksRouteRecord(name, hash, canonicalHash, params, extra) {
        const meta = PRKS_ROUTE_META[name] || PRKS_ROUTE_META.unknown;
        return {
            name: name,
            hash: hash,
            canonicalHash: canonicalHash,
            params: params || {},
            parentSection: meta.sectionHash || null,
            detail: !!(extra && extra.detail != null ? extra.detail : meta.detail),
            canonicalize: !!(extra && extra.canonicalize),
        };
    }

    function prksNormalizeHashInput(raw) {
        let s = String(raw == null ? '' : raw).trim();
        if (!s || s === '#') return PRKS_HOME_HASH;
        if (s.charAt(0) === '/') s = '#' + s;
        if (s.charAt(0) !== '#') return null;
        if (/[\0\r\n]/.test(s)) return null;
        const rest = s.slice(1);
        if (/^(javascript:|data:|vbscript:)/i.test(rest)) return null;
        return s;
    }

    function prksSplitHash(raw) {
        const normalized = prksNormalizeHashInput(raw);
        if (normalized == null) return null;
        const q = normalized.indexOf('?');
        const pathPart = q < 0 ? normalized : normalized.slice(0, q);
        const query = q < 0 ? '' : normalized.slice(q + 1);
        const path = pathPart.replace(/^#\/?/, '');
        const segments = path ? path.split('/') : [];
        return { hash: normalized, pathPart: pathPart, query: query, segments: segments };
    }

    function prksParseSearchParams(query) {
        const params = { q: '', tag: '', author: '', publisher: '', any: '' };
        if (!query) return params;
        let usp;
        try {
            usp = new URLSearchParams(query);
        } catch (_e) {
            return params;
        }
        params.q = usp.get('q') || '';
        params.tag = usp.get('tag') || '';
        params.author = usp.get('author') || '';
        params.publisher = usp.get('publisher') || '';
        params.any = usp.get('any') || '';
        return params;
    }

    function prksParseProgressStatus(query) {
        if (!query) return null;
        let usp;
        try {
            usp = new URLSearchParams(query);
        } catch (_e) {
            return null;
        }
        const raw = usp.get('status');
        if (raw == null || String(raw).trim() === '') return null;
        const decoded = String(raw).trim();
        return PRKS_PROGRESS_STATUS_VALUES.indexOf(decoded) >= 0 ? decoded : null;
    }

    function prksProgressCanonical(status) {
        const st = status || PRKS_PROGRESS_STATUS_VALUES[0];
        return '#/progress?status=' + prksEncodePathSegment(st);
    }

    const PRKS_GRAPH_FOCUS_RE = /^(concept|position|argument|work|person):[A-Za-z0-9][A-Za-z0-9._-]*$/;

    function prksParseGraphFocus(query) {
        if (!query) return '';
        let usp;
        try {
            usp = new URLSearchParams(query);
        } catch (_e) {
            return '';
        }
        const raw = usp.get('focus');
        if (raw == null) return '';
        const decoded = String(raw).trim();
        if (!PRKS_GRAPH_FOCUS_RE.test(decoded)) return '';
        return decoded;
    }

    function prksGraphCanonical(focus) {
        if (focus) return '#/graph?focus=' + prksEncodePathSegment(focus);
        return '#/graph';
    }

    function prksGraphFocusHash(nodeType, recordId) {
        const t = String(nodeType || '').trim();
        const id = String(recordId || '').trim();
        if (!t || !id) return '#/graph';
        const focus = t + ':' + id;
        if (!PRKS_GRAPH_FOCUS_RE.test(focus)) return '#/graph';
        return prksGraphCanonical(focus);
    }

    function prksParseRoute(hash) {
        const split = prksSplitHash(hash);
        if (!split) return prksUnknownRoute(String(hash || ''));
        const segs = split.segments;
        const rawHash = split.hash;
        if (!segs.length) {
            return prksRouteRecord('folders', rawHash, PRKS_HOME_HASH, {}, { canonicalize: rawHash !== PRKS_HOME_HASH });
        }

        const head = segs[0];

        if (head === 'graph' && segs.length === 1) {
            const focus = prksParseGraphFocus(split.query);
            const canonical = prksGraphCanonical(focus);
            return prksRouteRecord('research-graph', rawHash, canonical, { focus: focus });
        }

        if (head === 'folders' && segs.length === 1) {
            return prksRouteRecord('folders', rawHash, '#/folders', {});
        }
        if (head === 'folders' && segs.length === 2) {
            const folderId = prksSafeDecode(segs[1]);
            if (folderId == null) return prksUnknownRoute(rawHash);
            if (!folderId) return prksUnknownRoute(rawHash);
            return prksRouteRecord('folder-detail', rawHash, '#/folders/' + prksEncodePathSegment(folderId), {
                folderId: folderId,
            });
        }

        if (head === 'recent' && segs.length === 1) {
            return prksRouteRecord('recent', rawHash, '#/recent', {});
        }

        if (head === 'views' && segs.length === 1) {
            return prksRouteRecord('saved-views', rawHash, '#/views', {});
        }
        if (head === 'views' && segs.length === 2) {
            const viewId = prksSafeDecode(segs[1]);
            if (viewId == null || !viewId) return prksUnknownRoute(rawHash);
            return prksRouteRecord(
                'saved-view-detail',
                rawHash,
                '#/views/' + prksEncodePathSegment(viewId),
                { viewId: viewId }
            );
        }

        if (head === 'types' && segs.length === 1) {
            return prksRouteRecord('types', rawHash, '#/types', {});
        }
        if (head === 'types' && segs.length >= 2) {
            const encodedType = segs.slice(1).join('/');
            const docType = prksSafeDecode(encodedType);
            if (docType == null || !docType) return prksUnknownRoute(rawHash);
            return prksRouteRecord('type-detail', rawHash, '#/types/' + prksEncodePathSegment(docType), {
                docType: docType,
            });
        }

        if (head === 'playlists' && segs.length === 1) {
            return prksRouteRecord('playlists', rawHash, '#/playlists', {});
        }
        if (head === 'playlists' && segs.length === 2) {
            const playlistId = prksSafeDecode(segs[1]);
            if (playlistId == null || !playlistId) return prksUnknownRoute(rawHash);
            return prksRouteRecord('playlist-detail', rawHash, '#/playlists/' + prksEncodePathSegment(playlistId), {
                playlistId: playlistId,
            });
        }

        if (head === 'tags' && segs.length === 1) {
            return prksRouteRecord('tags', rawHash, '#/tags', {});
        }
        if (head === 'publishers' && segs.length === 1) {
            return prksRouteRecord('publishers', rawHash, '#/publishers', {});
        }

        if (head === 'concepts' && segs.length === 1) {
            return prksRouteRecord('concepts', rawHash, '#/concepts', {});
        }
        if (head === 'concepts' && segs.length === 2) {
            const conceptId = prksSafeDecode(segs[1]);
            if (conceptId == null || !conceptId) return prksUnknownRoute(rawHash);
            return prksRouteRecord(
                'concept-detail',
                rawHash,
                '#/concepts/' + prksEncodePathSegment(conceptId),
                { conceptId: conceptId }
            );
        }

        if (head === 'positions' && segs.length === 1) {
            return prksRouteRecord('positions', rawHash, '#/positions', {});
        }
        if (head === 'positions' && segs.length === 2) {
            const positionId = prksSafeDecode(segs[1]);
            if (positionId == null || !positionId) return prksUnknownRoute(rawHash);
            return prksRouteRecord(
                'position-detail',
                rawHash,
                '#/positions/' + prksEncodePathSegment(positionId),
                { positionId: positionId }
            );
        }

        if (head === 'arguments' && segs.length === 1) {
            let kind = '';
            try {
                kind = String(new URLSearchParams(split.query).get('kind') || '').trim();
            } catch (_e) {
                kind = '';
            }
            const canonical = kind ? '#/arguments?kind=' + prksEncodePathSegment(kind) : '#/arguments';
            return prksRouteRecord('arguments', rawHash, canonical, { kind: kind });
        }
        if (head === 'arguments' && segs.length === 2) {
            const argumentId = prksSafeDecode(segs[1]);
            if (argumentId == null || !argumentId) return prksUnknownRoute(rawHash);
            return prksRouteRecord(
                'argument-detail',
                rawHash,
                '#/arguments/' + prksEncodePathSegment(argumentId),
                { argumentId: argumentId }
            );
        }

        if (head === 'processing-files' && segs.length === 1) {
            return prksRouteRecord('processing-files', rawHash, '#/processing-files', {});
        }

        if (head === 'search' && segs.length === 1) {
            const params = prksParseSearchParams(split.query);
            const canonical = split.query ? '#/search?' + split.query : '#/search';
            return prksRouteRecord('search', rawHash, canonical, params);
        }

        if (head === 'progress' && segs.length === 1) {
            const status = prksParseProgressStatus(split.query);
            const canonical = prksProgressCanonical(status || PRKS_PROGRESS_STATUS_VALUES[0]);
            return prksRouteRecord(
                'progress',
                rawHash,
                canonical,
                { status: status || PRKS_PROGRESS_STATUS_VALUES[0] },
                { canonicalize: !status }
            );
        }

        if (head === 'works' && segs.length === 2) {
            const workId = prksSafeDecode(segs[1]);
            if (workId == null || !workId) return prksUnknownRoute(rawHash);
            return prksRouteRecord('work', rawHash, '#/works/' + prksEncodePathSegment(workId), { workId: workId });
        }

        if (head === 'people') {
            if (segs.length === 1) {
                return prksRouteRecord('people', rawHash, '#/people', {});
            }
            if (segs[1] === 'role' && segs.length >= 3) {
                const roleRaw = prksSafeDecode(segs.slice(2).join('/'));
                if (roleRaw == null) return prksUnknownRoute(rawHash);
                const role = PRKS_PEOPLE_ROLES.indexOf(roleRaw) >= 0 ? roleRaw : roleRaw;
                const known = PRKS_PEOPLE_ROLES.indexOf(role) >= 0;
                return prksRouteRecord(
                    'people-role',
                    rawHash,
                    '#/people/role/' + prksEncodePathSegment(role),
                    { role: role, knownRole: known }
                );
            }
            if (segs[1] === 'groups' && segs.length === 2) {
                return prksRouteRecord('people-groups', rawHash, '#/people/groups', {});
            }
            if (segs[1] === 'groups' && segs.length === 3) {
                const groupId = prksSafeDecode(segs[2]);
                if (groupId == null || !groupId) return prksUnknownRoute(rawHash);
                return prksRouteRecord(
                    'person-group-detail',
                    rawHash,
                    '#/people/groups/' + prksEncodePathSegment(groupId),
                    { groupId: groupId }
                );
            }
            if (segs.length === 2 && segs[1] !== 'role' && segs[1] !== 'groups') {
                const personId = prksSafeDecode(segs[1]);
                if (personId == null || !personId) return prksUnknownRoute(rawHash);
                return prksRouteRecord('person', rawHash, '#/people/' + prksEncodePathSegment(personId), {
                    personId: personId,
                });
            }
        }

        return prksUnknownRoute(rawHash);
    }

    function prksIsRecognizedRoute(route) {
        return !!(route && route.name && route.name !== 'unknown');
    }

    function prksIsRouteGenCurrent(routeGen) {
        if (typeof routeGen !== 'number') return true;
        if (typeof root.__prksRouteGen !== 'number') return true;
        return routeGen === root.__prksRouteGen;
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

    function prksBackLabelForRoute(route) {
        if (!route) return 'Folders';
        if (route.name === 'progress' && route.params && route.params.status) {
            return String(route.params.status);
        }
        if (route.name === 'people-role' && route.params && route.params.role) {
            return String(route.params.role);
        }
        if (route.name === 'folder-detail' && root.currentFolder && route.params && root.currentFolder.id === route.params.folderId) {
            const t = String(root.currentFolder.title || '').trim();
            if (t) return t;
        }
        if (route.name === 'person' && root.currentPerson && route.params && root.currentPerson.id === route.params.personId) {
            if (typeof root.personDisplayName === 'function') {
                const n = String(root.personDisplayName(root.currentPerson) || '').trim();
                if (n) return n;
            }
        }
        if (route.name === 'playlist-detail' && root.currentPlaylist && route.params && root.currentPlaylist.id === route.params.playlistId) {
            const t = String(root.currentPlaylist.title || '').trim();
            if (t) return t;
        }
        if (
            route.name === 'person-group-detail' &&
            root.currentPersonGroup &&
            route.params &&
            root.currentPersonGroup.id === route.params.groupId
        ) {
            const t = String(root.currentPersonGroup.name || '').trim();
            if (t) return t;
        }
        if (route.name === 'work' && root.currentWork && route.params && root.currentWork.id === route.params.workId) {
            const t = String(root.currentWork.title || '').trim();
            if (t) return t;
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
            show: 'Show Progress shortcuts',
            hide: 'Hide Progress shortcuts',
        },
        research: {
            listId: 'prks-nav-research-children',
            prefKey: PRKS_NAV_RESEARCH_EXPANDED_KEY,
            show: 'Show Research shortcuts',
            hide: 'Hide Research shortcuts',
        },
    };

    function prksReadNavExpandedPref(key) {
        try {
            const raw = String(root.localStorage.getItem(key) || '').trim();
            return raw === '1' || raw === 'true';
        } catch (_e) {
            return false;
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
            (route.name === 'people-role' ||
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

    function prksSetDisclosureVisual(which, expanded) {
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
            btn.setAttribute('aria-label', expanded ? spec.hide : spec.show);
        }
        if (list) {
            list.hidden = !expanded;
            if (expanded) list.removeAttribute('hidden');
            else list.setAttribute('hidden', '');
        }
        if (wrap) wrap.classList.toggle('nav-disclosure--open', !!expanded);
    }

    function prksSyncNavDisclosures(route) {
        if (typeof document === 'undefined') return;
        const peopleForced = prksPeopleRouteForcesOpen(route);
        const progressForced = prksProgressRouteForcesOpen(route);
        const researchForced = prksResearchRouteForcesOpen(route);
        prksSetDisclosureVisual(
            'people',
            peopleForced || prksReadNavExpandedPref(PRKS_NAV_PEOPLE_EXPANDED_KEY)
        );
        prksSetDisclosureVisual(
            'progress',
            progressForced || prksReadNavExpandedPref(PRKS_NAV_PROGRESS_EXPANDED_KEY)
        );
        prksSetDisclosureVisual(
            'research',
            researchForced || prksReadNavExpandedPref(PRKS_NAV_RESEARCH_EXPANDED_KEY)
        );
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
                const key = spec.prefKey;
                const route = prksParseRoute(root.location ? root.location.hash : '');
                const forced =
                    which === 'people'
                        ? prksPeopleRouteForcesOpen(route)
                        : which === 'progress'
                          ? prksProgressRouteForcesOpen(route)
                          : prksResearchRouteForcesOpen(route);
                if (forced) {
                    prksWriteNavExpandedPref(key, false);
                    prksSyncNavDisclosures(route);
                    return;
                }
                prksWriteNavExpandedPref(key, !prksReadNavExpandedPref(key));
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

    function prksRouteScrollElement() {
        if (typeof document === 'undefined' || !document.querySelector) return null;
        const inner = document.querySelector(
            '.prks-folder-library__pane:not(.is-hidden) .prks-folder-library__scroll, ' +
                '.prks-people-library__scroll:not(.prks-people-library__scroll--embedded), ' +
                '.prks-group-library__scroll'
        );
        if (inner) return inner;
        return document.getElementById('main-content');
    }

    function prksCaptureCurrentRouteState(prevRoute) {
        if (!prevRoute || !prksIsRecognizedRoute(prevRoute)) return;
        const el = prksRouteScrollElement();
        const scrollTop = el && Number.isFinite(el.scrollTop) ? el.scrollTop : 0;
        const store = prksReadStore();
        const key = prevRoute.canonicalHash;
        store.states[key] = { scrollTop: scrollTop };
        prksTouchOrder(store, key);
        prksPruneOrphans(store);
        prksWriteStore(store);
    }

    function prksRestoreRouteState(route, routeGen) {
        if (!prksIsRouteGenCurrent(routeGen)) return null;
        if (!route || !prksIsRecognizedRoute(route)) return null;
        const store = prksReadStore();
        const saved = store.states[route.canonicalHash];
        if (!saved || typeof saved !== 'object') return null;
        const top = Number(saved.scrollTop);
        if (!Number.isFinite(top) || top < 0) return null;
        const el = prksRouteScrollElement();
        if (!el) return null;
        el.scrollTop = top;
        if (typeof root.requestAnimationFrame === 'function') {
            root.requestAnimationFrame(function () {
                if (!prksIsRouteGenCurrent(routeGen)) return;
                const now = prksParseRoute(prksCurrentLocationHash());
                if (now.canonicalHash !== route.canonicalHash) return;
                const again = prksRouteScrollElement();
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

    function prksRememberOrigin(destRoute, fromRoute) {
        if (!destRoute || !destRoute.detail || !fromRoute) return;
        if (!prksIsRecognizedRoute(fromRoute)) return;
        if (fromRoute.canonicalHash === destRoute.canonicalHash) return;
        const rec = prksValidateOriginRecord({
            hash: fromRoute.canonicalHash,
            name: fromRoute.name,
            label: prksBackLabelForRoute(fromRoute),
        });
        if (!rec) return;
        const store = prksReadStore();
        store.origins[destRoute.canonicalHash] = rec;
        prksTouchOrder(store, destRoute.canonicalHash);
        prksPruneOrphans(store);
        prksWriteStore(store);
    }

    function prksReadOriginForRoute(route) {
        if (!route || !route.detail) return null;
        const store = prksReadStore();
        return prksValidateOriginRecord(store.origins[route.canonicalHash]);
    }

    function prksResolveBackTarget(route) {
        const fallbackHash = prksMeta(route).fallbackBack || PRKS_HOME_HASH;
        const fallbackRoute = prksParseRoute(fallbackHash);
        const origin = prksReadOriginForRoute(route);
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

    function prksContextualBackHtml(route) {
        if (!route || !route.detail) return '';
        const dest = prksResolveBackTarget(route);
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

    function prksMountContextualBack(container, route, routeGen) {
        if (!prksIsRouteGenCurrent(routeGen)) return;
        if (!container || !container.querySelector) return;
        if (!route || !route.detail) return;
        if (container.querySelector('.prks-nav-back')) return;
        const html = prksContextualBackHtml(route);
        if (!html) return;
        if (prksIsPdfWorkWithoutHeader(container)) {
            const detail = container.querySelector('.work-detail');
            if (detail) {
                detail.insertAdjacentHTML('afterbegin', '<div class="prks-nav-back-row prks-nav-back-row--work">' + html + '</div>');
            }
            return;
        }
        const header = container.querySelector('.page-header');
        if (header) {
            header.insertAdjacentHTML('afterbegin', html);
            return;
        }
        container.insertAdjacentHTML('afterbegin', '<div class="prks-nav-back-row">' + html + '</div>');
    }

    function prksDocumentTitleText(route, options) {
        const opts = options || {};
        if (opts.notFound) {
            return String(opts.notFoundTitle || 'Not found') + PRKS_TITLE_SUFFIX;
        }
        if (route && route.name === 'unknown') {
            return 'Section unavailable' + PRKS_TITLE_SUFFIX;
        }
        if (route && route.name === 'search') {
            return 'Search' + PRKS_TITLE_SUFFIX;
        }
        const entity = opts.entityTitle != null ? String(opts.entityTitle).trim() : '';
        if (entity) return entity + PRKS_TITLE_SUFFIX;
        const meta = prksMeta(route);
        const base = meta.title || 'PRKS';
        if (base === 'PRKS') return 'PRKS';
        return base + PRKS_TITLE_SUFFIX;
    }

    function prksSetResolvedDocumentTitle(route, options, routeGen) {
        if (!prksIsRouteGenCurrent(routeGen)) return;
        if (typeof document === 'undefined') return;
        document.title = prksDocumentTitleText(route, options);
    }

    function prksRouteLoadingTitle(hash) {
        const route = prksParseRoute(hash);
        return prksMeta(route).loadingTitle || 'Loading';
    }

    function prksPublishRouteSidebar(data, routeGen) {
        if (!prksIsRouteGenCurrent(routeGen)) return false;
        root.__prksRouteSidebar = data && typeof data === 'object' ? data : {};
        return true;
    }

    function prksAssignRouteEntity(key, value, routeGen) {
        if (!prksIsRouteGenCurrent(routeGen)) return false;
        root[key] = value;
        return true;
    }

    function prksNavigate(hash, options) {
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

    function prksRenderRouteError(contentDiv) {
        if (!contentDiv) return;
        contentDiv.innerHTML =
            '<div class="page-header"><h2>Could not load this view</h2></div>' +
            '<p class="prks-inline-message">Could not load this view.</p>' +
            '<p><button type="button" class="add-new-btn" id="prks-route-retry">Retry</button></p>';
        contentDiv.removeAttribute('aria-busy');
        const btn = contentDiv.querySelector('#prks-route-retry');
        if (btn) {
            btn.onclick = function () {
                prksNavigate(prksCurrentLocationHash(), { replace: true });
            };
        }
    }

    function prksFinishRouteRender(route, routeGen, contentDiv, options) {
        if (!prksIsRouteGenCurrent(routeGen)) return false;
        const opts = options || {};
        prksSetResolvedDocumentTitle(route, opts, routeGen);
        if (!prksIsRouteGenCurrent(routeGen)) return false;
        prksMountContextualBack(contentDiv, route, routeGen);
        const restored = prksRestoreRouteState(route, routeGen);
        if (contentDiv && contentDiv.removeAttribute) contentDiv.removeAttribute('aria-busy');
        root.__prksLastResolvedHash = route ? route.hash : prksCurrentLocationHash();
        root.__prksLastResolvedRouteName = route ? route.name : '';
        root.__prksRouteRenderDone = {
            gen: routeGen,
            hash: route ? route.canonicalHash : '',
            name: route ? route.name : '',
        };
        const skipAnim = restored && Number(restored.scrollTop) > 8;
        if (!skipAnim && typeof root.prksPlayPageEnterAnimation === 'function') {
            root.prksPlayPageEnterAnimation(contentDiv);
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
        prksCaptureCurrentRouteState: prksCaptureCurrentRouteState,
        prksRestoreRouteState: prksRestoreRouteState,
        prksRememberOrigin: prksRememberOrigin,
        prksReadOriginForRoute: prksReadOriginForRoute,
        prksResolveBackTarget: prksResolveBackTarget,
        prksContextualBackHtml: prksContextualBackHtml,
        prksMountContextualBack: prksMountContextualBack,
        prksSetResolvedDocumentTitle: prksSetResolvedDocumentTitle,
        prksDocumentTitleText: prksDocumentTitleText,
        prksRouteLoadingTitle: prksRouteLoadingTitle,
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
