/**
 * Frozen legacy hash-parser oracle for the typed route-model migration
 * safety net. The application does not load this file. Production parsing
 * goes through frontend/js/route-model.js (built from
 * frontend-app/src/routing/route-model.ts), which navigation.js aliases.
 *
 * Copied verbatim from frontend/js/navigation.js at b8d57ae (the last master
 * commit before the port): the route registry, value lists, and parser.
 * Do not edit it to match new behavior. A deliberate parser change updates
 * the differential selftest's expectations instead; delete this oracle with
 * that selftest once the port no longer needs comparing.
 */
(function () {
    'use strict';

    const PRKS_HOME_HASH = '#/folders';
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
    /* Intentionally excludes Mentioned. Mirrors backend
     * work_role_sync.PEOPLE_ROLE_TYPES; pinned by tests/test_contract_parity.py. */

    const PRKS_ROUTE_META = {
        folders: {
            title: 'Folders',
            loadingTitle: 'Folders',
            backLabel: 'Folders',
            navHref: '#/folders',
            fallbackBack: '#/folders',
            sectionHash: '#/folders',
            tabIcon: 'folder',
        },
        'folder-detail': {
            title: 'Folder',
            loadingTitle: 'Folder',
            backLabel: 'Folder',
            navHref: '#/folders',
            fallbackBack: '#/folders',
            sectionHash: '#/folders',
            detail: true,
            tabIcon: 'folder',
        },
        recent: {
            title: 'Recent',
            loadingTitle: 'Recent',
            backLabel: 'Recently opened',
            navHref: '#/recent',
            fallbackBack: '#/folders',
            sectionHash: '#/recent',
            tabIcon: 'clock',
        },
        'saved-views': {
            title: 'Saved Views',
            loadingTitle: 'Saved Views',
            backLabel: 'Saved Views',
            navHref: '#/views',
            fallbackBack: '#/views',
            sectionHash: '#/views',
            tabIcon: 'bookmark',
        },
        'saved-view-detail': {
            title: 'Saved View',
            loadingTitle: 'Saved View',
            backLabel: 'Saved View',
            navHref: '#/views',
            fallbackBack: '#/views',
            sectionHash: '#/views',
            detail: true,
            tabIcon: 'bookmark',
        },
        types: {
            title: 'File Types',
            loadingTitle: 'File Types',
            backLabel: 'File types',
            navHref: '#/types',
            fallbackBack: '#/types',
            sectionHash: '#/types',
            tabIcon: 'library',
        },
        'type-detail': {
            title: 'File Type',
            loadingTitle: 'File Type',
            backLabel: 'File type',
            navHref: '#/types',
            fallbackBack: '#/types',
            sectionHash: '#/types',
            detail: true,
            tabIcon: 'library',
        },
        playlists: {
            title: 'Playlists',
            loadingTitle: 'Playlists',
            backLabel: 'Playlists',
            navHref: '#/playlists',
            fallbackBack: '#/playlists',
            sectionHash: '#/playlists',
            tabIcon: 'clapperboard',
        },
        'playlist-detail': {
            title: 'Playlist',
            loadingTitle: 'Playlist',
            backLabel: 'Playlist',
            navHref: '#/playlists',
            fallbackBack: '#/playlists',
            sectionHash: '#/playlists',
            detail: true,
            tabIcon: 'clapperboard',
        },
        tags: {
            title: 'Tags',
            loadingTitle: 'Tags',
            backLabel: 'Tags',
            navHref: '#/tags',
            fallbackBack: '#/folders',
            sectionHash: '#/tags',
            tabIcon: 'tags',
        },
        publishers: {
            title: 'Publishers',
            loadingTitle: 'Publishers',
            backLabel: 'Publishers',
            navHref: '#/publishers',
            fallbackBack: '#/folders',
            sectionHash: '#/publishers',
            tabIcon: 'building-2',
        },
        people: {
            title: 'People',
            loadingTitle: 'People',
            backLabel: 'People',
            navHref: '#/people',
            fallbackBack: '#/people',
            sectionHash: '#/people',
            tabIcon: 'users',
        },
        person: {
            title: 'Person',
            loadingTitle: 'Person',
            backLabel: 'Person',
            navHref: '#/people',
            fallbackBack: '#/people',
            sectionHash: '#/people',
            detail: true,
            tabIcon: 'user',
        },
        'people-role': {
            title: 'People',
            loadingTitle: 'People',
            backLabel: 'People',
            navHref: null,
            fallbackBack: '#/people',
            sectionHash: '#/people',
            tabIcon: 'users',
        },
        'people-groups': {
            title: 'Groups',
            loadingTitle: 'People Groups',
            backLabel: 'Groups',
            navHref: '#/people/groups',
            fallbackBack: '#/people/groups',
            sectionHash: '#/people/groups',
            tabIcon: 'folders',
        },
        'person-group-detail': {
            title: 'Group',
            loadingTitle: 'Group',
            backLabel: 'Group',
            navHref: '#/people/groups',
            fallbackBack: '#/people/groups',
            sectionHash: '#/people/groups',
            detail: true,
            tabIcon: 'folders',
        },
        concepts: {
            title: 'Concepts',
            loadingTitle: 'Concepts',
            backLabel: 'Concepts',
            navHref: '#/concepts',
            fallbackBack: '#/concepts',
            sectionHash: '#/concepts',
            tabIcon: 'network',
        },
        'concept-detail': {
            title: 'Concept',
            loadingTitle: 'Concept',
            backLabel: 'Concept',
            navHref: '#/concepts',
            fallbackBack: '#/concepts',
            sectionHash: '#/concepts',
            detail: true,
            tabIcon: 'network',
        },
        positions: {
            title: 'Positions',
            loadingTitle: 'Positions',
            backLabel: 'Positions',
            navHref: '#/positions',
            fallbackBack: '#/positions',
            sectionHash: '#/positions',
            tabIcon: 'flag',
        },
        'position-detail': {
            title: 'Position',
            loadingTitle: 'Position',
            backLabel: 'Position',
            navHref: '#/positions',
            fallbackBack: '#/positions',
            sectionHash: '#/positions',
            detail: true,
            tabIcon: 'flag',
        },
        arguments: {
            title: 'Arguments & Stances',
            loadingTitle: 'Arguments & Stances',
            backLabel: 'Arguments & Stances',
            navHref: '#/arguments',
            fallbackBack: '#/arguments',
            sectionHash: '#/arguments',
            tabIcon: 'messages-square',
        },
        'argument-detail': {
            title: 'Argument',
            loadingTitle: 'Argument',
            backLabel: 'Argument',
            navHref: '#/arguments',
            fallbackBack: '#/arguments',
            sectionHash: '#/arguments',
            detail: true,
            tabIcon: 'messages-square',
        },
        'research-graph': {
            title: 'Research Graph',
            loadingTitle: 'Research Graph',
            backLabel: 'Research Graph',
            navHref: '#/graph',
            fallbackBack: '#/graph',
            sectionHash: '#/graph',
            tabIcon: 'share-2',
        },
        progress: {
            title: 'Progress',
            loadingTitle: 'Progress',
            backLabel: 'Progress',
            navHref: null,
            fallbackBack: '#/folders',
            sectionHash: '#/progress',
            tabIcon: 'list',
        },
        'processing-files': {
            title: 'Files for Processing',
            loadingTitle: 'Files for Processing',
            backLabel: 'Files for Processing',
            navHref: '#/processing-files',
            fallbackBack: '#/folders',
            sectionHash: '#/processing-files',
            tabIcon: 'inbox',
        },
        search: {
            title: 'Search',
            loadingTitle: 'Search',
            backLabel: 'Search results',
            navHref: null,
            fallbackBack: '#/folders',
            sectionHash: '#/search',
            tabIcon: 'search',
        },
        work: {
            title: 'File',
            loadingTitle: 'Work',
            backLabel: 'File',
            navHref: '#/folders',
            fallbackBack: '#/folders',
            sectionHash: '#/folders',
            detail: true,
            tabIcon: 'file-text',
        },
        unknown: {
            title: 'Section unavailable',
            loadingTitle: 'Loading',
            backLabel: 'Folders',
            navHref: null,
            fallbackBack: '#/folders',
            sectionHash: null,
            tabIcon: 'file',
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

    module.exports = {
        PRKS_HOME_HASH: PRKS_HOME_HASH,
        PRKS_ROUTE_META: PRKS_ROUTE_META,
        PRKS_PROGRESS_STATUS_VALUES: PRKS_PROGRESS_STATUS_VALUES,
        PRKS_PEOPLE_ROLES: PRKS_PEOPLE_ROLES,
        prksParseRoute: prksParseRoute,
        prksParseGraphFocus: prksParseGraphFocus,
        prksGraphFocusHash: prksGraphFocusHash,
        prksIsRecognizedRoute: prksIsRecognizedRoute,
    };
})();
