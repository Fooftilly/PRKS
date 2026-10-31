/**
 * Command palette: navigate, search, quick-open, create, a few context actions.
 * Commands are an explicit allowlist. Query text is never executed as JavaScript.
 */
(function (root) {
    'use strict';

    const DEBOUNCE_MS = 120;
    const MIN_DYNAMIC_LEN = 2;
    const MAX_OPTIONS = 16;
    const MAX_WORKS = 6;
    const MAX_FOLDERS = 4;
    const MAX_PERSONS = 4;
    const MAX_GROUPS = 3;
    const MAX_PLAYLISTS = 3;
    const MAX_SAVED_VIEWS = 4;
    const MAX_CONCEPTS = 4;
    const MAX_POSITIONS = 3;
    const MAX_ARGUMENTS = 4;

    const ALLOWED_MODALS = {
        'work-modal': true,
        'folder-modal': true,
        'person-modal': true,
        'group-modal': true,
        'role-modal': true,
        'settings-modal': true,
    };

    const CONTEXT_ACTIONS = {
        'select-enter': function () {
            if (typeof root.prksWorkSelectionEnter === 'function') root.prksWorkSelectionEnter();
        },
        'select-exit': function () {
            if (typeof root.prksWorkSelectionExit === 'function') root.prksWorkSelectionExit();
        },
        'save-search-view': function () {
            if (typeof root.prksOpenSavedViewModalFromCurrentSearch === 'function') {
                root.prksOpenSavedViewModalFromCurrentSearch();
            }
        },
        'edit-saved-view': function () {
            if (typeof root.prksOpenSavedViewModalForCurrentView === 'function') {
                root.prksOpenSavedViewModalForCurrentView();
            }
        },
        'workspace-overview': function () {
            /* closePalette({ restoreFocus: true }) already restored the
             * palette opener; pass it through so Overview does not prefer the
             * fixed toolbar Overview button over the real invocation source. */
            const opener = root.document && root.document.activeElement;
            if (typeof root.prksWorkspaceOverviewOpen === 'function') {
                root.prksWorkspaceOverviewOpen(opener || undefined);
            }
        },
    };

    const EMPTY_IDS = [
        'navigate-folders',
        'navigate-recent',
        'navigate-saved-views',
        'navigate-people',
        'navigate-processing',
        'new-file',
        'new-folder',
        'new-person',
        'navigate-progress-in-progress',
        'navigate-progress-planned',
        'navigate-progress-paused',
        'settings',
    ];

    const CREATE_EMPTY_IDS = ['new-folder', 'new-person', 'new-group', 'new-file'];

    function progressHash(status) {
        return '#/progress?status=' + encodeURIComponent(status);
    }

    const COMMANDS = [
        {
            id: 'navigate-folders',
            kind: 'navigate',
            label: 'Folders',
            keywords: ['library', 'files', 'all folders'],
            icon: 'folder',
            hash: '#/folders',
            section: 'goto',
        },
        {
            id: 'navigate-recent',
            kind: 'navigate',
            label: 'Recent',
            keywords: ['library', 'opened'],
            icon: 'clock',
            hash: '#/recent',
            section: 'goto',
        },
        {
            id: 'navigate-saved-views',
            kind: 'navigate',
            label: 'Saved Views',
            keywords: ['library', 'search', 'smart views'],
            icon: 'bookmark',
            hash: '#/views',
            section: 'goto',
        },
        {
            id: 'navigate-types',
            kind: 'navigate',
            label: 'File Types',
            keywords: ['library', 'documents'],
            icon: 'library',
            hash: '#/types',
            section: 'goto',
        },
        {
            id: 'navigate-playlists',
            kind: 'navigate',
            label: 'Playlists',
            keywords: ['library', 'videos'],
            icon: 'clapperboard',
            hash: '#/playlists',
            section: 'goto',
        },
        {
            id: 'navigate-tags',
            kind: 'navigate',
            label: 'Tags',
            keywords: ['organize', 'all tags'],
            icon: 'tags',
            hash: '#/tags',
            section: 'goto',
        },
        {
            id: 'navigate-publishers',
            kind: 'navigate',
            label: 'Publishers',
            keywords: ['organize'],
            icon: 'building-2',
            hash: '#/publishers',
            section: 'goto',
        },
        {
            id: 'navigate-people',
            kind: 'navigate',
            label: 'People',
            keywords: ['library', 'authors', 'network'],
            icon: 'users',
            hash: '#/people',
            section: 'goto',
        },
        {
            id: 'navigate-people-groups',
            kind: 'navigate',
            label: 'People Groups',
            keywords: ['people', 'groups'],
            icon: 'folders',
            hash: '#/people/groups',
            section: 'goto',
        },
        {
            id: 'navigate-concepts',
            kind: 'navigate',
            label: 'Concepts',
            keywords: ['research', 'ideas', 'terms'],
            icon: 'network',
            hash: '#/concepts',
            section: 'goto',
        },
        {
            id: 'navigate-positions',
            kind: 'navigate',
            label: 'Positions',
            keywords: ['research', 'claims', 'theories'],
            icon: 'flag',
            hash: '#/positions',
            section: 'goto',
        },
        {
            id: 'navigate-arguments',
            kind: 'navigate',
            label: 'Arguments & Stances',
            keywords: ['research', 'stance', 'argument'],
            icon: 'messages-square',
            hash: '#/arguments',
            section: 'goto',
        },
        {
            id: 'navigate-research-graph',
            kind: 'navigate',
            label: 'Research Graph',
            keywords: ['research', 'graph', 'network', 'relationships'],
            icon: 'share-2',
            hash: '#/graph',
            section: 'goto',
        },
        {
            id: 'navigate-people-authors',
            kind: 'navigate',
            label: 'Authors',
            keywords: ['people', 'role', 'writer'],
            icon: 'pen-line',
            hash: '#/people/role/Author',
            section: 'goto',
        },
        {
            id: 'navigate-people-editors',
            kind: 'navigate',
            label: 'Editors',
            keywords: ['people', 'role'],
            icon: 'file-pen',
            hash: '#/people/role/Editor',
            section: 'goto',
        },
        {
            id: 'navigate-people-reviewers',
            kind: 'navigate',
            label: 'Reviewers',
            keywords: ['people', 'role'],
            icon: 'clipboard-list',
            hash: '#/people/role/Reviewer',
            section: 'goto',
        },
        {
            id: 'navigate-people-translators',
            kind: 'navigate',
            label: 'Translators',
            keywords: ['people', 'role'],
            icon: 'languages',
            hash: '#/people/role/Translator',
            section: 'goto',
        },
        {
            id: 'navigate-people-introduction',
            kind: 'navigate',
            label: 'Introduction writers',
            keywords: ['people', 'role', 'introduction'],
            icon: 'book-marked',
            hash: '#/people/role/Introduction',
            section: 'goto',
        },
        {
            id: 'navigate-people-foreword',
            kind: 'navigate',
            label: 'Foreword writers',
            keywords: ['people', 'role', 'foreword'],
            icon: 'book-open',
            hash: '#/people/role/Foreword',
            section: 'goto',
        },
        {
            id: 'navigate-people-afterword',
            kind: 'navigate',
            label: 'Afterword writers',
            keywords: ['people', 'role', 'afterword'],
            icon: 'book',
            hash: '#/people/role/Afterword',
            section: 'goto',
        },
        {
            id: 'navigate-processing',
            kind: 'navigate',
            label: 'Files for Processing',
            keywords: ['inbox', 'import', 'drop'],
            icon: 'inbox',
            hash: '#/processing-files',
            section: 'goto',
        },
        {
            id: 'workspace-overview',
            kind: 'context',
            label: 'Workspace overview',
            keywords: ['tabs', 'panes', 'split', 'open', 'overview'],
            icon: 'layout-dashboard',
            actionId: 'workspace-overview',
            section: 'goto',
        },
        {
            id: 'navigate-progress-not-started',
            kind: 'navigate',
            label: 'Not Started',
            keywords: ['progress', 'status'],
            icon: 'circle',
            hash: progressHash('Not Started'),
            section: 'progress',
        },
        {
            id: 'navigate-progress-planned',
            kind: 'navigate',
            label: 'Planned',
            keywords: ['progress', 'status'],
            icon: 'clipboard-list',
            hash: progressHash('Planned'),
            section: 'progress',
        },
        {
            id: 'navigate-progress-in-progress',
            kind: 'navigate',
            label: 'In Progress',
            keywords: ['progress', 'status'],
            icon: 'play',
            hash: progressHash('In Progress'),
            section: 'progress',
        },
        {
            id: 'navigate-progress-paused',
            kind: 'navigate',
            label: 'Paused',
            keywords: ['progress', 'status'],
            icon: 'pause',
            hash: progressHash('Paused'),
            section: 'progress',
        },
        {
            id: 'navigate-progress-completed',
            kind: 'navigate',
            label: 'Completed',
            keywords: ['progress', 'status', 'done'],
            icon: 'check',
            hash: progressHash('Completed'),
            section: 'progress',
        },
        {
            id: 'new-file',
            kind: 'modal',
            label: 'New File',
            keywords: ['create', 'add', 'work', 'upload'],
            icon: 'file-plus',
            modalId: 'work-modal',
            section: 'create',
            create: true,
        },
        {
            id: 'new-folder',
            kind: 'modal',
            label: 'New Folder',
            keywords: ['create', 'add'],
            icon: 'folder',
            modalId: 'folder-modal',
            section: 'create',
            create: true,
        },
        {
            id: 'new-person',
            kind: 'modal',
            label: 'New Person',
            keywords: ['create', 'add', 'people', 'author'],
            icon: 'user',
            modalId: 'person-modal',
            section: 'create',
            create: true,
        },
        {
            id: 'new-group',
            kind: 'modal',
            label: 'New Group',
            keywords: ['create', 'add', 'people'],
            icon: 'users',
            modalId: 'group-modal',
            section: 'create',
            create: true,
        },
        {
            id: 'link-person',
            kind: 'modal',
            label: 'Link Person to Work',
            keywords: ['role', 'credit', 'author'],
            icon: 'link',
            modalId: 'role-modal',
            section: 'actions',
        },
        {
            id: 'settings',
            kind: 'modal',
            label: 'Settings',
            keywords: ['preferences', 'theme', 'backup'],
            icon: 'settings',
            modalId: 'settings-modal',
            section: 'actions',
        },
    ];

    const COMMAND_BY_ID = Object.create(null);
    COMMANDS.forEach(function (c, i) {
        c._index = i;
        COMMAND_BY_ID[c.id] = c;
    });

    const state = {
        open: false,
        scope: 'all',
        query: '',
        activeIndex: 0,
        results: [],
        queryGen: 0,
        sessionGen: 0,
        debounceTimer: null,
        prevFocus: null,
        folderCache: null,
        personCache: null,
        groupCache: null,
        playlistCache: null,
        savedViewCache: null,
        conceptCache: null,
        positionCache: null,
        argumentCache: null,
        folderPromise: null,
        personPromise: null,
        groupPromise: null,
        playlistPromise: null,
        savedViewPromise: null,
        savedViewUnwatch: null,
        savedViewLoading: false,
        savedViewReloadDue: false,
        conceptPromise: null,
        positionPromise: null,
        argumentPromise: null,
        fetchFailed: false,
        works: [],
        worksLoading: false,
        inited: false,
        emptyCreate: false,
        navigationTarget: 'current',
        splitPlacement: null,
        viewRevision: 0,
    };

    const viewListeners = [];

    function debounceMs() {
        return typeof root.__prksPaletteDebounceMs === 'number' ? root.__prksPaletteDebounceMs : DEBOUNCE_MS;
    }

    function doc() {
        return typeof document !== 'undefined' ? document : null;
    }

    function normalizeQuery(raw) {
        return String(raw || '')
            .trim()
            .toLowerCase()
            .replace(/\s+/g, ' ');
    }

    function scoreText(text, q) {
        const t = normalizeQuery(text);
        if (!q || !t) return 0;
        if (t === q) return 1000;
        if (t.startsWith(q)) return 800;
        const words = t.split(' ');
        for (let i = 0; i < words.length; i++) {
            if (words[i].startsWith(q)) return 600;
        }
        if (t.indexOf(q) >= 0) return 400;
        return 0;
    }

    function scoreCommand(cmd, q) {
        if (!cmd) return 0;
        const labelScore = scoreText(cmd.label, q);
        let kwScore = 0;
        const kws = cmd.keywords || [];
        for (let i = 0; i < kws.length; i++) {
            const t = normalizeQuery(kws[i]);
            if (!t) continue;
            if (t.startsWith(q)) kwScore = Math.max(kwScore, 300);
            else if (t.indexOf(q) >= 0) kwScore = Math.max(kwScore, 100);
        }
        return Math.max(labelScore, kwScore);
    }

    function searchHash(kind, q) {
        const codec = root.prksSearchQueryCodec;
        if (!codec || typeof codec.hashFromDefinition !== 'function') return null;
        const hashFromDefinition = codec.hashFromDefinition;
        if (kind === 'all') {
            return hashFromDefinition({
                mode: 'all',
                q: q,
                tag: '',
                author: '',
                publisher: '',
            });
        }
        if (kind === 'keywords') {
            return hashFromDefinition({
                mode: 'advanced',
                q: q,
                tag: '',
                author: '',
                publisher: '',
            });
        }
        if (kind === 'people') {
            return hashFromDefinition({
                mode: 'advanced',
                q: '',
                tag: '',
                author: q,
                publisher: '',
            });
        }
        return hashFromDefinition({
            mode: 'advanced',
            q: '',
            tag: '',
            author: '',
            publisher: kind === 'publisher' ? q : '',
        });
    }

    function searchCommands(rawQuery) {
        const trimmed = String(rawQuery || '').trim();
        if (!trimmed) return [];
        const quoted = '“' + trimmed + '”';
        return [
            {
                id: 'search-all',
                kind: 'search',
                searchKind: 'all',
                label: 'Search all for ' + quoted,
                icon: 'search',
                hash: searchHash('all', trimmed),
                section: 'search',
            },
            {
                id: 'search-keywords',
                kind: 'search',
                searchKind: 'keywords',
                label: 'Search keywords for ' + quoted,
                icon: 'search',
                hash: searchHash('keywords', trimmed),
                section: 'search',
            },
            {
                id: 'search-people',
                kind: 'search',
                searchKind: 'people',
                label: 'Search people for ' + quoted,
                icon: 'search',
                hash: searchHash('people', trimmed),
                section: 'search',
            },
            {
                id: 'search-publisher',
                kind: 'search',
                searchKind: 'publisher',
                label: 'Search publishers for ' + quoted,
                icon: 'search',
                hash: searchHash('publisher', trimmed),
                section: 'search',
            },
        ].filter(function (row) {
            return row.hash != null;
        });
    }

    function filterCommands(query, opts) {
        const scope = opts && opts.scope ? opts.scope : 'all';
        const q = normalizeQuery(query);
        let pool = COMMANDS;
        if (scope === 'create') pool = COMMANDS.filter(function (c) { return c.create === true; });
        if (!q) {
            const ids = scope === 'create' ? CREATE_EMPTY_IDS : EMPTY_IDS;
            const out = [];
            for (let i = 0; i < ids.length; i++) {
                const cmd = COMMAND_BY_ID[ids[i]];
                if (cmd && (scope !== 'create' || cmd.create)) out.push(cmd);
            }
            return out;
        }
        const ranked = [];
        for (let i = 0; i < pool.length; i++) {
            const score = scoreCommand(pool[i], q);
            if (score > 0) ranked.push({ cmd: pool[i], score: score, index: pool[i]._index });
        }
        ranked.sort(function (a, b) {
            if (b.score !== a.score) return b.score - a.score;
            return a.index - b.index;
        });
        return ranked.map(function (r) { return r.cmd; });
    }

    function personLabel(p) {
        if (typeof root.personDisplayName === 'function') return root.personDisplayName(p);
        return String((p && p.first_name) || '').trim() + ' ' + String((p && p.last_name) || '').trim();
    }

    function personHaystacks(p) {
        const names = [personLabel(p).trim()];
        if (p && p.first_name) names.push(String(p.first_name));
        if (p && p.last_name) names.push(String(p.last_name));
        if (typeof root.prksParsePersonAliases === 'function') {
            const aliases = root.prksParsePersonAliases(p) || [];
            for (let i = 0; i < aliases.length; i++) names.push(aliases[i]);
        } else if (p && p.aliases) {
            String(p.aliases)
                .split(',')
                .forEach(function (a) {
                    names.push(a);
                });
        }
        return names;
    }

    function conceptHaystacks(c) {
        const names = [String((c && c.name) || '')];
        const aliases = (c && c.aliases) || [];
        for (let i = 0; i < aliases.length; i++) names.push(String(aliases[i] || ''));
        return names;
    }

    function bestScore(haystacks, q) {
        let best = 0;
        for (let i = 0; i < haystacks.length; i++) {
            best = Math.max(best, scoreText(haystacks[i], q));
        }
        return best;
    }

    function workSubtitle(w) {
        if (!w) return '';
        /* Search results come from the server, so while an edit is pending
         * they still carry the acknowledged values. The same effective-Work
         * overlay every other surface uses corrects that -- this file must not
         * grow its own reading of the durable queue.
         *
         * The overlay is applied BEFORE the credit is composed, never after.
         * `author_text` is only one of three possible sources of a credit: a
         * linked Author outranks it and a linked Editor stands in when it is
         * empty, so a pending value read off the acknowledged row would both
         * miss the edit and, in the other direction, be unable to reveal the
         * Editor when the field is cleared. */
        /* BOTH overlays, in the same fixed order every other surface uses:
         * relationships decide who is linked and recompute the flattened
         * credit columns, then metadata decides what `author_text` is. */
        const linked = typeof prksEffectiveWorkRoles === 'function'
            ? prksEffectiveWorkRoles(w) : w;
        const work = typeof prksEffectiveWorkSync === 'function'
            ? prksEffectiveWorkSync(linked) : linked;
        const author = String(work.linked_authors || work.primary_author ||
            work.author_text || work.primary_editor || '').trim();
        let year = String(work.year || '').trim();
        if (!year && work.published_date) {
            const m = String(work.published_date).match(/^(\d{4})/);
            if (m) year = m[1];
        }
        if (author && year) return author + ' · ' + year;
        return author || year || '';
    }

    function entityRows(list, q, kind, labelFn, hashFn, icon, limit) {
        const ranked = [];
        for (let i = 0; i < list.length; i++) {
            const item = list[i];
            if (!item || !item.id) continue;
            const score = bestScore(labelFn(item), q);
            if (score <= 0) continue;
            ranked.push({ item: item, score: score, index: i });
        }
        ranked.sort(function (a, b) {
            if (b.score !== a.score) return b.score - a.score;
            return a.index - b.index;
        });
        const out = [];
        const n = Math.min(limit, ranked.length);
        for (let i = 0; i < n; i++) {
            const item = ranked[i].item;
            const labels = labelFn(item);
            out.push({
                id: 'open-' + kind + '-' + item.id,
                kind: 'open',
                entity: kind,
                entityId: item.id,
                label: labels[0] || String(item.title || item.name || item.id),
                subtitle: kind === 'work' ? workSubtitle(item) : '',
                icon: icon,
                hash: hashFn(item),
                section: 'open',
            });
        }
        return out;
    }

    function contextCommands() {
        if (state.scope === 'create') return [];
        const hash = root.location ? root.location.hash : '';
        const route = typeof root.prksParseRoute === 'function' ? root.prksParseRoute(hash) : null;
        const supported =
            typeof root.prksWorkSelectionIsSupportedRoute === 'function'
                ? root.prksWorkSelectionIsSupportedRoute(route)
                : false;
        const active = typeof root.prksWorkSelectionIsActive === 'function' && root.prksWorkSelectionIsActive();
        const out = [];
        if (supported && !active) {
            out.push({
                id: 'select-files',
                kind: 'context',
                actionId: 'select-enter',
                label: 'Select files on this page',
                keywords: ['bulk', 'organize'],
                icon: 'check',
                section: 'actions',
            });
        } else if (active) {
            out.push({
                id: 'exit-select',
                kind: 'context',
                actionId: 'select-exit',
                label: 'Exit file selection',
                keywords: ['bulk'],
                icon: 'x',
                section: 'actions',
            });
        }
        if (
            route &&
            route.name === 'search' &&
            root.prksSearchQueryCodec &&
            typeof root.prksSearchQueryCodec.definitionFromRoute === 'function'
        ) {
            const parsed = root.prksSearchQueryCodec.definitionFromRoute(route);
            if (parsed && parsed.ok) {
                out.push({
                    id: 'save-search-view',
                    kind: 'context',
                    actionId: 'save-search-view',
                    label: 'Save current search as view',
                    keywords: ['saved views', 'smart view'],
                    icon: 'bookmark',
                    section: 'actions',
                });
            }
        }
        if (route && route.name === 'saved-view-detail' && !active) {
            out.push({
                id: 'edit-saved-view',
                kind: 'context',
                actionId: 'edit-saved-view',
                label: 'Edit this Saved View',
                keywords: ['rename'],
                icon: 'pencil',
                section: 'actions',
            });
        }
        if (
            route &&
            (route.name === 'concept-detail' ||
                route.name === 'position-detail' ||
                route.name === 'argument-detail')
        ) {
            let focusType = '';
            let recordId = '';
            if (route.name === 'concept-detail') {
                focusType = 'concept';
                recordId = route.params.conceptId || '';
            } else if (route.name === 'position-detail') {
                focusType = 'position';
                recordId = route.params.positionId || '';
            } else {
                focusType = 'argument';
                recordId = route.params.argumentId || '';
            }
            if (recordId && typeof root.prksGraphFocusHash === 'function') {
                out.push({
                    id: 'view-record-in-graph',
                    kind: 'navigate',
                    label: 'View this record in graph',
                    keywords: ['research', 'graph', 'focus'],
                    icon: 'share-2',
                    hash: root.prksGraphFocusHash(focusType, recordId),
                    section: 'actions',
                });
            }
        }
        return out;
    }

    function isInsidePalette(el) {
        if (!el || !el.closest) return false;
        return !!el.closest('#prks-command-palette');
    }

    function isEditableTarget(el) {
        if (!el) return false;
        if (isInsidePalette(el)) return false;
        const tag = String(el.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
        if (el.isContentEditable) return true;
        const ce = el.getAttribute && el.getAttribute('contenteditable');
        if (ce && String(ce).toLowerCase() !== 'false') return true;
        if (el.closest) {
            if (el.closest('.CodeMirror')) return true;
            if (el.closest('.EasyMDE')) return true;
            if (el.closest('.EasyMDEContainer')) return true;
            if (el.closest('.editor-toolbar')) return true;
        }
        const cls = String(el.className || '');
        if (cls.indexOf('CodeMirror') >= 0) return true;
        if (cls.indexOf('EasyMDE') >= 0) return true;
        return false;
    }

    function isVisibleDialog(el) {
        if (!el) return false;
        if (el.id === 'prks-command-palette') return false;
        if (el.hidden) return false;
        if (el.classList && el.classList.contains('hidden')) return false;
        return true;
    }

    function isBlockingDialogOpen() {
        if (typeof root.prksAnyModalOpen === 'function' && root.prksAnyModalOpen()) return true;
        if (typeof root.prksIsModalUnsavedConfirmOpen === 'function' && root.prksIsModalUnsavedConfirmOpen()) {
            return true;
        }
        const d = doc();
        if (!d) return false;
        const sheet = d.getElementById('prks-bulk-sheet');
        if (sheet && isVisibleDialog(sheet)) return true;
        const nodes = d.querySelectorAll ? d.querySelectorAll('[aria-modal="true"]') : [];
        for (let i = 0; i < nodes.length; i++) {
            if (isVisibleDialog(nodes[i])) return true;
        }
        return false;
    }

    /** Confirm and the unsaved prompt sit above the palette and own Escape. */
    function confirmLayerOpen() {
        if (typeof root.prksIsModalConfirmOpen === 'function' && root.prksIsModalConfirmOpen()) return true;
        if (typeof root.prksIsModalUnsavedConfirmOpen === 'function' && root.prksIsModalUnsavedConfirmOpen()) {
            return true;
        }
        return false;
    }

    function isOpenShortcut(e) {
        if (!e) return false;
        const key = e.key;
        if (key !== 'k' && key !== 'K') return false;
        if (!(e.ctrlKey || e.metaKey)) return false;
        if (e.altKey || e.shiftKey) return false;
        return true;
    }

    function shouldIgnoreShortcut(e) {
        const target = e && (e.target || (doc() && doc().activeElement));
        return isEditableTarget(target);
    }

    function sectionLabel(section) {
        if (section === 'goto') return 'Go to';
        if (section === 'create') return 'Create';
        if (section === 'progress') return 'Progress';
        if (section === 'open') return 'Quick open';
        if (section === 'search') return 'Search';
        if (section === 'actions') return 'Actions';
        if (section === 'open-tabs') return 'Open tabs';
        return '';
    }

    function rowCanBeSecondary(row) {
        if (!row) return false;
        if (row.workspaceTabId) return true;
        if (row.kind === 'modal' || row.kind === 'context') return false;
        if (row.kind === 'search') return false;
        if (row.kind !== 'navigate' && row.kind !== 'open') return false;
        return routeSupportsTile(row.hash);
    }

    /** User-facing examples of tile-capable detail pages (eligibility lives in navigation.js). */
    const SPLIT_DETAIL_EXAMPLES = 'Work, Person, Playlist, Concept, Position, Argument, or Folder';
    const SPLIT_SEARCH_HINT = 'Search for a ' + SPLIT_DETAIL_EXAMPLES + '.';

    function routeSupportsTile(hash) {
        const h = hash ? String(hash) : '';
        if (!h) return false;
        return typeof root.prksRouteSupportsTile === 'function' && !!root.prksRouteSupportsTile(h);
    }

    /** Prefer PRKS_ROUTE_META.title so Progress statuses and role indexes share one label. */
    function labelForRouteHash(hash, fallback) {
        const fb = fallback ? String(fallback) : '';
        if (!hash || typeof root.prksParseRoute !== 'function') return fb;
        const route = root.prksParseRoute(hash);
        if (!route || !route.name) return fb;
        const meta = root.PRKS_ROUTE_META && root.PRKS_ROUTE_META[route.name];
        const title = meta && meta.title ? String(meta.title) : '';
        return title || fb;
    }

    /**
     * When split-mode search matches only non-tileable destinations, name the best match
     * using route metadata + existing COMMANDS / entity caches — not a second allowlist.
     */
    function findMatchedNonTileableDestination(rawQuery) {
        const q = normalizeQuery(rawQuery);
        if (!q) return null;
        let best = null;

        function consider(label, hash, score) {
            /* Require a meaningful match before naming a blocked destination: keyword-only
             * hits (≤300) and very short queries produce misleading labels. */
            if (!(score >= 600) || q.length < 3) return;
            if (!hash || routeSupportsTile(hash)) return;
            const display = labelForRouteHash(hash, label);
            if (!display) return;
            if (!best || score > best.score || (score === best.score && display.length < best.label.length)) {
                best = { label: display, hash: String(hash), score: score };
            }
        }

        for (let i = 0; i < COMMANDS.length; i++) {
            const cmd = COMMANDS[i];
            if (!cmd || (cmd.kind !== 'navigate' && cmd.kind !== 'open')) continue;
            const hash = cmd.hash ? String(cmd.hash) : '';
            if (!hash) continue;
            consider(cmd.label, hash, scoreCommand(cmd, q));
        }

        if (q.length >= MIN_DYNAMIC_LEN) {
            const nonTileCaches = [
                [state.folderCache, 'title', '#/folders/'],
                [state.groupCache, 'title', '#/people/groups/'],
                [state.savedViewCache, 'name', '#/views/'],
            ];
            for (let c = 0; c < nonTileCaches.length; c++) {
                const list = nonTileCaches[c][0];
                const field = nonTileCaches[c][1];
                const prefix = nonTileCaches[c][2];
                if (!list || !list.length) continue;
                for (let i = 0; i < list.length; i++) {
                    const item = list[i];
                    if (!item || !item.id) continue;
                    const label = String(item[field] || item.title || item.name || item.id);
                    const score = bestScore([label], q);
                    if (score <= 0) continue;
                    consider(label, prefix + encodeURIComponent(item.id), score);
                }
            }
        }

        return best;
    }

    function splitPaletteEmptyMessage() {
        const q = normalizeQuery(state.query);
        if (!q) {
            return 'Search for a detail page that supports split — ' + SPLIT_DETAIL_EXAMPLES + '.';
        }
        const blocked = findMatchedNonTileableDestination(state.query);
        if (blocked && blocked.label) {
            return blocked.label + " can’t open in split view. " + SPLIT_SEARCH_HINT;
        }
        return 'No matching pages that can open in split view.';
    }

    function openTabRows(rawQuery) {
        if (typeof root.prksWorkspaceSnapshot !== 'function') return [];
        const snap = root.prksWorkspaceSnapshot();
        if (!snap || !Array.isArray(snap.tabs)) return [];
        const visual =
            typeof root.prksWorkspaceVisualTiled === 'function' ? !!root.prksWorkspaceVisualTiled() : snap.mode === 'tiled';
        const visibleSecondaryIds = visual && typeof root.collectLeafTabIds === 'function' ? root.collectLeafTabIds(snap.secondaryTree) : [];
        const q = normalizeQuery(rawQuery);
        const out = [];
        for (let i = 0; i < snap.tabs.length; i++) {
            const tab = snap.tabs[i];
            if (!tab || tab.id === snap.mainTabId) continue;
            if (visual && visibleSecondaryIds.indexOf(tab.id) !== -1) continue;
            if (!routeSupportsTile(tab.route)) continue;
            if (q && bestScore([String(tab.title || '')], q) <= 0) continue;
            out.push({
                id: 'ws-tab-' + tab.id,
                kind: 'open',
                label: tab.title || 'Page',
                icon: tab.icon || 'file-text',
                hash: tab.route,
                section: 'open-tabs',
                workspaceTabId: tab.id,
            });
        }
        return out;
    }

    function executeTileSelection(row, hash, splitPlacement) {
        const tabId = row && row.workspaceTabId ? row.workspaceTabId : null;
        const placement = splitPlacement || null;
        if (placement && placement.targetLeafTabId && typeof root.prksWorkspaceSplitLeaf === 'function') {
            /* Explicit Split right/down from a specific focused Secondary leaf: reuse the
             * existing tab if the row already resolves to one (parked or otherwise), else
             * create a new one from `hash`. Never duplicates a tab. */
            if (tabId) {
                void root.prksWorkspaceSplitLeaf(placement.targetLeafTabId, placement.axis, {
                    tabId: tabId,
                    placement: placement.placement,
                });
                return;
            }
            if (typeof root.prksWorkspaceFindTabByRoute === 'function') {
                const existing = root.prksWorkspaceFindTabByRoute(hash, {
                    excludeMain: true,
                    excludeVisibleSecondary: true,
                });
                if (existing && existing.id) {
                    void root.prksWorkspaceSplitLeaf(placement.targetLeafTabId, placement.axis, {
                        tabId: existing.id,
                        placement: placement.placement,
                    });
                    return;
                }
            }
            void root.prksWorkspaceSplitLeaf(placement.targetLeafTabId, placement.axis, {
                hash: hash,
                placement: placement.placement,
            });
            return;
        }
        if (tabId && typeof root.prksWorkspaceTileTab === 'function') {
            root.prksWorkspaceTileTab(tabId);
            return;
        }
        if (typeof root.prksWorkspaceFindTabByRoute === 'function') {
            const existing = root.prksWorkspaceFindTabByRoute(hash, {
                excludeMain: true,
                excludeVisibleSecondary: true,
            });
            if (existing && existing.id && typeof root.prksWorkspaceTileTab === 'function') {
                root.prksWorkspaceTileTab(existing.id);
                return;
            }
        }
        if (typeof root.prksNavigate === 'function') {
            root.prksNavigate(hash, { target: 'tile' });
        }
    }

    function assembleResults() {
        const qRaw = state.query;
        const q = normalizeQuery(qRaw);
        const staticCmds = filterCommands(qRaw, { scope: state.scope });
        const ctx = q ? contextCommands().filter(function (c) { return scoreCommand(c, q) > 0; }) : contextCommands();
        const search = state.scope === 'create' ? [] : searchCommands(qRaw);
        let entities = [];
        if (state.scope !== 'create' && q.length >= MIN_DYNAMIC_LEN) {
            entities = entities.concat(state.works);
            if (state.folderCache) {
                entities = entities.concat(
                    entityRows(
                        state.folderCache,
                        q,
                        'folder',
                        function (f) { return [String(f.title || '')]; },
                        function (f) { return '#/folders/' + encodeURIComponent(f.id); },
                        'folder',
                        MAX_FOLDERS
                    )
                );
            }
            if (state.personCache) {
                entities = entities.concat(
                    entityRows(
                        state.personCache,
                        q,
                        'person',
                        personHaystacks,
                        function (p) { return '#/people/' + encodeURIComponent(p.id); },
                        'user',
                        MAX_PERSONS
                    )
                );
            }
            if (state.groupCache) {
                entities = entities.concat(
                    entityRows(
                        state.groupCache,
                        q,
                        'group',
                        function (g) { return [String(g.title || g.name || '')]; },
                        function (g) { return '#/people/groups/' + encodeURIComponent(g.id); },
                        'folders',
                        MAX_GROUPS
                    )
                );
            }
            if (state.playlistCache) {
                entities = entities.concat(
                    entityRows(
                        state.playlistCache,
                        q,
                        'playlist',
                        function (p) { return [String(p.title || '')]; },
                        function (p) { return '#/playlists/' + encodeURIComponent(p.id); },
                        'clapperboard',
                        MAX_PLAYLISTS
                    )
                );
            }
            if (state.savedViewCache) {
                entities = entities.concat(
                    entityRows(
                        state.savedViewCache,
                        q,
                        'saved-view',
                        function (v) { return [String(v.name || '')]; },
                        function (v) { return '#/views/' + encodeURIComponent(v.id); },
                        'bookmark',
                        MAX_SAVED_VIEWS
                    )
                );
            }
            if (state.conceptCache) {
                entities = entities.concat(
                    entityRows(
                        state.conceptCache,
                        q,
                        'concept',
                        conceptHaystacks,
                        function (c) { return '#/concepts/' + encodeURIComponent(c.id); },
                        'network',
                        MAX_CONCEPTS
                    )
                );
            }
            if (state.positionCache) {
                entities = entities.concat(
                    entityRows(
                        state.positionCache,
                        q,
                        'position',
                        function (p) { return [String(p.name || '')]; },
                        function (p) { return '#/positions/' + encodeURIComponent(p.id); },
                        'flag',
                        MAX_POSITIONS
                    )
                );
            }
            if (state.argumentCache) {
                entities = entities.concat(
                    entityRows(
                        state.argumentCache,
                        q,
                        'argument',
                        function (a) { return [String(a.name || '')]; },
                        function (a) { return '#/arguments/' + encodeURIComponent(a.id); },
                        'messages-square',
                        MAX_ARGUMENTS
                    )
                );
            }
        }

        const searchSlots = search.length;
        const budget = Math.max(0, MAX_OPTIONS - searchSlots);
        const combined = [];
        function take(list, remaining) {
            for (let i = 0; i < list.length && combined.length < remaining; i++) combined.push(list[i]);
        }
        take(staticCmds, budget);
        take(entities, budget);
        take(ctx, budget);
        for (let i = 0; i < search.length; i++) combined.push(search[i]);

        state.emptyCreate = state.scope === 'create' && combined.length === 0 && !!q;
        if (state.navigationTarget === 'tile') {
            const openTabs = openTabRows(qRaw);
            const openHashes = Object.create(null);
            for (let i = 0; i < openTabs.length; i++) {
                if (openTabs[i].hash) openHashes[openTabs[i].hash] = true;
            }
            if (typeof root.prksWorkspaceSnapshot === 'function') {
                const snap = root.prksWorkspaceSnapshot();
                if (snap && Array.isArray(snap.tabs)) {
                    for (let t = 0; t < snap.tabs.length; t++) {
                        if (snap.tabs[t] && snap.tabs[t].route) openHashes[snap.tabs[t].route] = true;
                    }
                }
            }
            const filtered = combined.filter(function (row) {
                if (!rowCanBeSecondary(row)) return false;
                if (row.workspaceTabId) return true;
                if (row.hash && openHashes[row.hash]) return false;
                return true;
            });
            return openTabs.concat(filtered);
        }
        return combined;
    }

    /**
     * Loading wins over split-empty guidance. The live region stays mounted in
     * the Vue dialog (never hidden); this string is the only status source.
     */
    function statusText(rows) {
        if (state.fetchFailed) return 'Some quick-open results could not be loaded.';
        if (state.emptyCreate) return 'No create command matches.';
        if (state.worksLoading) return 'Searching library…';
        if (!rows.length && state.navigationTarget === 'tile') return splitPaletteEmptyMessage();
        return '';
    }

    function paletteTitle() {
        if (state.scope === 'create') return 'Create…';
        if (state.navigationTarget === 'new-tab') return 'Open in new tab';
        if (state.navigationTarget === 'tile') return 'Open in split view';
        return 'Search or jump';
    }

    function palettePlaceholder() {
        if (state.scope === 'create') return 'Create…';
        if (state.navigationTarget === 'new-tab') return 'Open in new tab…';
        if (state.navigationTarget === 'tile') return 'Open in split view…';
        return 'Search or jump…';
    }

    function displayRows(rows) {
        const out = [];
        let lastSection = '';
        for (let i = 0; i < rows.length; i++) {
            const row = rows[i];
            const sec = row.section || '';
            out.push({
                index: i,
                id: String(row.id || ''),
                label: String(row.label || ''),
                subtitle: row.subtitle ? String(row.subtitle) : '',
                icon: row.icon ? String(row.icon) : 'search',
                section: sec,
                heading: sec && sec !== lastSection ? sectionLabel(sec) : '',
            });
            if (sec) lastSection = sec;
        }
        return out;
    }

    function currentView() {
        const open = !!state.open;
        const rows = open ? state.results : [];
        const activeDescendant = open && rows.length ? 'prks-palette-opt-' + state.activeIndex : '';
        return {
            open: open,
            title: paletteTitle(),
            placeholder: palettePlaceholder(),
            query: open ? String(state.query || '') : '',
            scope: state.scope === 'create' ? 'create' : 'all',
            navigationTarget: state.navigationTarget,
            createMode: state.scope === 'create',
            activeIndex: state.activeIndex,
            activeDescendant: activeDescendant,
            rows: displayRows(rows),
            status: open ? statusText(rows) : '',
            hints: open && state.scope !== 'create' ? paletteHintsText() : '',
            showHints: open && state.scope !== 'create',
            revision: state.viewRevision,
        };
    }

    function freezeView(view) {
        const rows = [];
        for (let i = 0; i < view.rows.length; i++) rows.push(Object.freeze(view.rows[i]));
        view.rows = Object.freeze(rows);
        return Object.freeze(view);
    }

    function publish(opts) {
        /* A blocked open still accepts a query: callers read the same result
         * list the dialog would have painted. Closing does not reassemble,
         * so the last list stays available and the view simply hides it. */
        if (!opts || opts.assemble !== false) {
            state.results = assembleResults();
            if (state.activeIndex >= state.results.length) {
                state.activeIndex = Math.max(0, state.results.length - 1);
            }
            if (state.activeIndex < 0) state.activeIndex = 0;
        }
        state.viewRevision += 1;
        const view = freezeView(currentView());
        state.view = view;
        for (let i = 0; i < viewListeners.length; i++) viewListeners[i](view);
    }

    function setActive(index) {
        const n = state.results.length;
        if (!n) {
            state.activeIndex = 0;
            publish();
            return;
        }
        let i = index;
        if (i < 0) i = n - 1;
        if (i >= n) i = 0;
        state.activeIndex = i;
        publish();
    }

    function executeRow(row, execOpts) {
        if (!row) return;
        const kind = row.kind;
        if (kind === 'navigate' || kind === 'search' || kind === 'open') {
            const hash = String(row.hash || '');
            if (!hash || hash.charAt(0) !== '#') return;
            const background = !!(execOpts && execOpts.backgroundTab);
            const newTabMode = state.navigationTarget === 'new-tab';
            const tileMode = state.navigationTarget === 'tile' || !!(execOpts && execOpts.tile);
            let target = 'current';
            if (background) target = 'new-tab';
            else if (tileMode) target = 'tile';
            else if (newTabMode) target = 'new-tab';
            const activate = newTabMode && !background && target === 'new-tab';
            /* Snapshot transient palette operation state before closePalette() clears it
             * (splitPlacement is ephemeral session state -- see openPalette). */
            const splitPlacement = state.splitPlacement;
            closePalette({ restoreFocus: false });
            if (target === 'tile') {
                executeTileSelection(row, hash, splitPlacement);
                return;
            }
            if (typeof root.prksNavigate === 'function') {
                if (target === 'new-tab') {
                    root.prksNavigate(hash, { target: 'new-tab', activate: activate });
                } else {
                    const focused =
                        typeof root.prksGetFocusedTabContext === 'function' ? root.prksGetFocusedTabContext() : null;
                    if (focused && focused.tabId) {
                        root.prksNavigate(hash, { target: 'current', tabId: focused.tabId });
                    } else {
                        root.prksNavigate(hash);
                    }
                }
            }
            return;
        }
        if (kind === 'modal') {
            const modalId = String(row.modalId || '');
            if (!ALLOWED_MODALS[modalId]) return;
            closePalette({ restoreFocus: false });
            if (typeof root.openModal === 'function') root.openModal(modalId);
            return;
        }
        if (kind === 'context') {
            const fn = CONTEXT_ACTIONS[row.actionId];
            closePalette({ restoreFocus: true });
            if (typeof fn === 'function') fn();
        }
    }

    function executeActive(execOpts) {
        if (!state.results.length) return;
        executeRow(state.results[state.activeIndex], execOpts);
    }

    function activateFromPointer(index, event) {
        if (!state.open) return;
        const idx = typeof index === 'number' ? index : parseInt(index, 10);
        if (!Number.isFinite(idx)) return;
        const e = event || {};
        state.activeIndex = idx;
        executeActive({
            backgroundTab:
                !!(e.ctrlKey || e.metaKey) &&
                !e.altKey &&
                state.navigationTarget !== 'new-tab' &&
                state.navigationTarget !== 'tile',
            tile: !!e.altKey && state.navigationTarget !== 'new-tab',
        });
    }

    function subscribeView(fn) {
        if (typeof fn !== 'function') return function () {};
        viewListeners.push(fn);
        return function () {
            const at = viewListeners.indexOf(fn);
            if (at >= 0) viewListeners.splice(at, 1);
        };
    }

    function clearCaches() {
        state.folderCache = null;
        state.personCache = null;
        state.groupCache = null;
        state.playlistCache = null;
        state.savedViewCache = null;
        state.conceptCache = null;
        state.positionCache = null;
        state.argumentCache = null;
        state.folderPromise = null;
        state.personPromise = null;
        state.groupPromise = null;
        state.playlistPromise = null;
        state.savedViewPromise = null;
        state.savedViewLoading = false;
        state.savedViewReloadDue = false;
        state.conceptPromise = null;
        state.positionPromise = null;
        state.argumentPromise = null;
        state.fetchFailed = false;
        state.works = [];
        state.worksLoading = false;
    }

    function clearDebounce() {
        if (state.debounceTimer != null) {
            root.clearTimeout(state.debounceTimer);
            state.debounceTimer = null;
        }
    }

    function wrapCatalog(fn, assign) {
        const session = state.sessionGen;
        return Promise.resolve()
            .then(function () {
                return typeof fn === 'function' ? fn() : [];
            })
            .then(function (data) {
                if (!state.open || session !== state.sessionGen) return;
                const list = Array.isArray(data) ? data : [];
                assign(list);
                publish();
                return list;
            })
            .catch(function () {
                if (!state.open || session !== state.sessionGen) return;
                state.fetchFailed = true;
                assign([]);
                publish();
                return [];
            });
    }

    /**
     * Saved Views come from the frontend-app records service, so the palette
     * shares the index's query cache. The list it holds is replaced when a
     * read finishes, never cleared first.
     */
    function loadSavedViews() {
        const records = root.prksSavedViewRecords;
        const listSaved = records && typeof records.list === 'function'
            ? function () { return records.list(); }
            : null;
        const session = state.sessionGen;
        state.savedViewLoading = true;
        state.savedViewReloadDue = false;
        state.savedViewPromise = wrapCatalog(listSaved, function (v) { state.savedViewCache = v; })
            .then(function (list) {
                if (session !== state.sessionGen) return list;
                state.savedViewLoading = false;
                if (state.savedViewReloadDue && state.open) loadSavedViews();
                return list;
            });
    }

    /**
     * A Saved View write anywhere on the page re-reads the open palette's
     * list, so a renamed or deleted view does not linger until it reopens.
     * A write during a read queues one more read after it settles; several
     * such writes still queue one.
     */
    function watchSavedViewWrites() {
        unwatchSavedViewWrites();
        const records = root.prksSavedViewRecords;
        if (!records || typeof records.onWrite !== 'function') return;
        state.savedViewUnwatch = records.onWrite(function () {
            if (!state.open || !state.savedViewPromise) return;
            if (state.savedViewLoading) state.savedViewReloadDue = true;
            else loadSavedViews();
        });
    }

    function unwatchSavedViewWrites() {
        const stop = state.savedViewUnwatch;
        state.savedViewUnwatch = null;
        if (typeof stop === 'function') stop();
    }

    function ensureCatalogs() {
        if (state.scope === 'create') return;
        if (normalizeQuery(state.query).length < MIN_DYNAMIC_LEN) return;
        if (!state.folderPromise) {
            state.folderPromise = wrapCatalog(root.fetchFolders, function (v) { state.folderCache = v; });
        }
        if (!state.personPromise) {
            state.personPromise = wrapCatalog(root.fetchPersons, function (v) { state.personCache = v; });
        }
        if (!state.groupPromise) {
            state.groupPromise = wrapCatalog(root.fetchPersonGroups, function (v) { state.groupCache = v; });
        }
        if (!state.playlistPromise) {
            const fetchPl = root.fetchPlaylists;
            state.playlistPromise = wrapCatalog(fetchPl, function (v) { state.playlistCache = v; });
        }
        if (!state.savedViewPromise) loadSavedViews();
        if (!state.conceptPromise) {
            state.conceptPromise = wrapCatalog(root.fetchConcepts, function (v) { state.conceptCache = v; });
        }
        if (!state.positionPromise) {
            state.positionPromise = wrapCatalog(root.fetchPositions, function (v) { state.positionCache = v; });
        }
        if (!state.argumentPromise) {
            state.argumentPromise = wrapCatalog(root.fetchArguments, function (v) { state.argumentCache = v; });
        }
    }

    function loadWorks(query, gen) {
        const q = String(query || '').trim();
        if (state.scope === 'create' || normalizeQuery(q).length < MIN_DYNAMIC_LEN) {
            state.works = [];
            state.worksLoading = false;
            if (gen === state.queryGen) publish();
            return;
        }
        const fetchSearch = root.fetchSearch;
        if (typeof fetchSearch !== 'function') {
            state.works = [];
            state.worksLoading = false;
            if (gen === state.queryGen) publish();
            return;
        }
        state.worksLoading = true;
        if (gen === state.queryGen) publish();
        Promise.resolve(fetchSearch(q, null, { any: '1' }))
            .then(function (data) {
                if (gen !== state.queryGen) return;
                const list = Array.isArray(data) ? data : [];
                const works = [];
                const n = Math.min(MAX_WORKS, list.length);
                for (let i = 0; i < n; i++) {
                    const w = list[i];
                    if (!w || !w.id) continue;
                    works.push({
                        id: 'open-work-' + w.id,
                        kind: 'open',
                        entity: 'work',
                        entityId: w.id,
                        label: String(w.title || 'Untitled'),
                        subtitle: workSubtitle(w),
                        icon: 'file-text',
                        hash: '#/works/' + encodeURIComponent(w.id),
                        section: 'open',
                    });
                }
                state.works = works;
                state.worksLoading = false;
                publish();
            })
            .catch(function () {
                if (gen !== state.queryGen) return;
                state.fetchFailed = true;
                state.works = [];
                state.worksLoading = false;
                publish();
            });
    }

    function scheduleWorks(query, gen) {
        clearDebounce();
        const wait = debounceMs();
        if (wait <= 0) {
            loadWorks(query, gen);
            return;
        }
        state.debounceTimer = root.setTimeout(function () {
            state.debounceTimer = null;
            loadWorks(query, gen);
        }, wait);
    }

    function applyQuery(raw) {
        state.query = String(raw || '');
        state.queryGen += 1;
        const gen = state.queryGen;
        state.works = [];
        ensureCatalogs();
        publish();
        scheduleWorks(state.query, gen);
    }

    function isMacPlatform() {
        try {
            const nav = root.navigator || {};
            const p = String(nav.platform || nav.userAgent || '');
            return /Mac|iPhone|iPad|iPod/.test(p);
        } catch (_e) {
            return false;
        }
    }

    function paletteHintText() {
        const mod = isMacPlatform() ? '⌘' : 'Ctrl';
        return 'Enter  Open   ' + mod + '+Enter  New tab   Alt+Enter  Split';
    }

    function paletteOrientationText() {
        try {
            if (typeof root.prksWorkspaceSnapshot !== 'function') return '';
            const snap = root.prksWorkspaceSnapshot();
            if (!snap || !Array.isArray(snap.tabs)) return '';
            const focused = snap.tabs.find(function (t) {
                return t && String(t.id) === String(snap.focusedTabId);
            });
            const title = focused && (focused.title || focused.route);
            if (!title) return '';
            return 'Here · ' + String(title);
        } catch (_e) {
            return '';
        }
    }

    function paletteHintsText() {
        const here = paletteOrientationText();
        const keys = paletteHintText();
        return here ? here + '   ·   ' + keys : keys;
    }

    function syncShortcutHint() {
        const d = doc();
        const hint = d && d.querySelector('[data-palette-shortcut-hint]');
        if (hint) hint.textContent = isMacPlatform() ? '⌘ K' : 'Ctrl K';
    }

    function onPaletteKeydown(e) {
        if (!state.open) return;
        const key = e.key;
        if (key === 'ArrowDown') {
            e.preventDefault();
            setActive(state.activeIndex + 1);
            return;
        }
        if (key === 'ArrowUp') {
            e.preventDefault();
            setActive(state.activeIndex - 1);
            return;
        }
        if (key === 'Home') {
            e.preventDefault();
            setActive(0);
            return;
        }
        if (key === 'End') {
            e.preventDefault();
            setActive(state.results.length - 1);
            return;
        }
        if (key === 'Enter') {
            e.preventDefault();
            executeActive({
                backgroundTab: (e.ctrlKey || e.metaKey) && !e.altKey && state.navigationTarget !== 'new-tab',
                tile: (!e.ctrlKey && !e.metaKey && !!e.altKey) || (state.navigationTarget === 'tile' && !(e.ctrlKey || e.metaKey)),
            });
            return;
        }
        if (key === 'Escape') {
            if (confirmLayerOpen()) return;
            e.preventDefault();
            e.stopPropagation();
            closePalette({ restoreFocus: true });
        }
    }

    function onDocumentKeydown(e) {
        if (state.open) {
            if (e.key === 'Escape') {
                if (confirmLayerOpen()) return;
                e.preventDefault();
                e.stopPropagation();
                if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
                closePalette({ restoreFocus: true });
                return;
            }
            if (isOpenShortcut(e)) {
                e.preventDefault();
            }
            return;
        }
        if (!isOpenShortcut(e)) return;
        if (shouldIgnoreShortcut(e)) return;
        if (isBlockingDialogOpen()) return;
        e.preventDefault();
        openPalette({ scope: 'all' });
    }

    function openPalette(options) {
        if (state.open && options && options.scope && options.scope !== state.scope) {
            closePalette({ restoreFocus: false });
        }
        if (isBlockingDialogOpen()) return false;
        const d = doc();
        if (!state.open) {
            state.prevFocus = d && d.activeElement ? d.activeElement : null;
        }
        state.open = true;
        state.sessionGen += 1;
        state.scope = options && options.scope === 'create' ? 'create' : 'all';
        state.navigationTarget =
            options && (options.navigationTarget === 'new-tab' || options.navigationTarget === 'tile') && state.scope !== 'create'
                ? options.navigationTarget
                : 'current';
        /* Split right/down from a specific focused Secondary leaf: {targetLeafTabId, axis,
         * placement}. Only meaningful together with navigationTarget 'tile'; ephemeral, never
         * persisted, cleared on close like every other palette session field. */
        state.splitPlacement =
            state.navigationTarget === 'tile' && options && options.splitPlacement && options.splitPlacement.targetLeafTabId
                ? options.splitPlacement
                : null;
        state.query = '';
        state.activeIndex = 0;
        state.queryGen += 1;
        clearDebounce();
        clearCaches();
        watchSavedViewWrites();
        publish();
        return true;
    }

    function closePalette(opts) {
        const restore = !opts || opts.restoreFocus !== false;
        const prev = state.prevFocus;
        state.sessionGen += 1;
        state.open = false;
        state.scope = 'all';
        state.navigationTarget = 'current';
        state.splitPlacement = null;
        state.query = '';
        state.activeIndex = 0;
        state.prevFocus = null;
        state.emptyCreate = false;
        clearDebounce();
        clearCaches();
        unwatchSavedViewWrites();
        publish({ assemble: false });
        if (restore && prev && typeof prev.focus === 'function') {
            try {
                const d = doc();
                if (!d || !prev) return;
                if (typeof d.contains === 'function' && !d.contains(prev) && prev !== d.body) return;
                prev.focus();
            } catch (_e) {}
        }
    }

    function bindChrome() {
        const d = doc();
        if (!d) return;
        const launch = d.getElementById('prks-command-palette-launch');
        if (launch && launch.getAttribute('data-palette-bound') !== '1') {
            launch.setAttribute('data-palette-bound', '1');
            launch.addEventListener('click', function () {
                openPalette({ scope: 'all' });
            });
        }
        syncShortcutHint();
    }

    function init() {
        if (state.inited) return;
        state.inited = true;
        bindChrome();
        const d = doc();
        if (d && d.addEventListener) d.addEventListener('keydown', onDocumentKeydown, true);
    }

    function resetForTests() {
        closePalette({ restoreFocus: false });
        state.inited = false;
        state.queryGen = 0;
        state.sessionGen = 0;
        state.results = [];
        state.navigationTarget = 'current';
        state.splitPlacement = null;
    }

    const api = {
        PRKS_PALETTE_COMMANDS: COMMANDS,
        PRKS_PALETTE_MAX_OPTIONS: MAX_OPTIONS,
        PRKS_PALETTE_MAX_WORKS: MAX_WORKS,
        PRKS_PALETTE_MAX_FOLDERS: MAX_FOLDERS,
        PRKS_PALETTE_MAX_SAVED_VIEWS: MAX_SAVED_VIEWS,
        prksPaletteNormalizeQuery: normalizeQuery,
        prksPaletteScoreCommand: scoreCommand,
        prksPaletteFilterCommands: filterCommands,
        prksPaletteSearchHash: searchHash,
        prksPaletteSearchCommands: searchCommands,
        prksPaletteShouldIgnoreShortcut: shouldIgnoreShortcut,
        prksPaletteIsBlockedByDialog: isBlockingDialogOpen,
        prksPaletteSplitEmptyMessage: splitPaletteEmptyMessage,
        prksPaletteFindNonTileableMatch: findMatchedNonTileableDestination,
        prksOpenCommandPalette: openPalette,
        prksCloseCommandPalette: function () {
            closePalette({ restoreFocus: true });
        },
        prksCommandPaletteView: function () {
            return state.view || freezeView(currentView());
        },
        prksCommandPaletteSubscribe: subscribeView,
        prksCommandPaletteActivateFromPointer: activateFromPointer,
        prksCommandPaletteIsOpen: function () {
            return !!state.open;
        },
        prksCommandPaletteScope: function () {
            return state.scope;
        },
        prksCommandPaletteNavigationTarget: function () {
            return state.navigationTarget;
        },
        prksCommandPaletteSplitPlacement: function () {
            return state.splitPlacement;
        },
        prksCommandPaletteGetResults: function () {
            return state.results.slice();
        },
        prksCommandPaletteGetActiveIndex: function () {
            return state.activeIndex;
        },
        prksCommandPaletteSetQuery: applyQuery,
        prksCommandPaletteExecuteActive: executeActive,
        prksCommandPaletteHandleKey: onPaletteKeydown,
        prksCommandPaletteHandleDocumentKey: onDocumentKeydown,
        prksInitCommandPalette: init,
        prksCommandPaletteResetForTests: resetForTests,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
