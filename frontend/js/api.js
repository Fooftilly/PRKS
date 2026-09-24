// Data Fetching

function prksEscapeHtml(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

window.prksEscapeHtml = prksEscapeHtml;

const PRKS_API_ERROR_SOURCES = {
    works: 'works.fetch',
    folders: 'folders.fetch',
    'folder-details': 'folders.details',
    persons: 'persons.fetch',
    'work-details': 'works.details',
    'person-details': 'persons.details',
    'person-groups': 'person-groups.fetch',
    'person-group-details': 'person-groups.details',
    recent: 'recent.fetch',
    'recently-added': 'recently-added.fetch',
    search: 'search.fetch',
    publishers: 'publishers.fetch',
    tags: 'tags.fetch',
    'processing-files': 'processing-files.fetch',
    'saved-views': 'saved-views.fetch',
    'works-bulk': 'works.bulk',
    concepts: 'concepts.fetch',
    positions: 'positions.fetch',
    arguments: 'arguments.fetch',
    'research-graph': 'research-graph.fetch',
    request: 'api',
};

function prksSafeClientSource(value) {
    const raw = String(value || '').trim();
    if (!raw) return 'client';
    if (raw === 'client' || raw === 'api' || raw === 'external') return raw;
    if (Object.prototype.hasOwnProperty.call(PRKS_API_ERROR_SOURCES, raw)) {
        return PRKS_API_ERROR_SOURCES[raw];
    }
    if (/^[a-z][a-z0-9._-]*$/.test(raw)) return raw;
    let path = raw;
    try {
        const url = new URL(raw, window.location.origin);
        path = url.pathname || '';
    } catch (_e) {
        path = raw.split('?')[0].split('#')[0];
    }
    const firstParty = path.startsWith('/js/') || path.startsWith('/vendor/') || path.startsWith('/css/') || path === '/sw.js';
    const base = path.split('/').pop() || '';
    if (firstParty && /^[A-Za-z0-9._-]+\.(js|css|mjs|map|svg|webmanifest)$/.test(base)) {
        return base;
    }
    if (/^[A-Za-z0-9._-]+\.(js|css|mjs)$/.test(raw)) return raw;
    return 'external';
}

function prksApiErrorSource(context) {
    const key = String(context || '');
    return PRKS_API_ERROR_SOURCES[key] || 'api';
}

const __prksApiErrorsByOwner = new WeakMap();

function prksSetApiError(context, message, requestId = '', owner) {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) return;
    __prksApiErrorsByOwner.set(owner, {
        context: String(context || 'request'),
        message: String(message || 'Request failed'),
        requestId: String(requestId || ''),
        at: Date.now(),
    });
}

function prksReportApiClientError(context, requestId = '') {
    prksReportClientError({
        kind: 'api_client_error',
        source: prksApiErrorSource(context),
        request_id: String(requestId || ''),
    });
}

function prksConsumeApiError(owner) {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) return null;
    const payload = __prksApiErrorsByOwner.get(owner) || null;
    __prksApiErrorsByOwner.delete(owner);
    return payload;
}

window.prksConsumeApiError = prksConsumeApiError;

const PRKS_CLIENT_ERROR_DEDUPE_MS = 15000;
const PRKS_CLIENT_ERROR_MAX_QUEUE = 100;
const __prksRecentClientErrors = new Map();

function prksTrimClientErrorText(value, maxLen) {
    if (value == null) return '';
    const text = String(value);
    return text.length > maxLen ? text.slice(0, maxLen) : text;
}

function prksShouldReportClientError(payload) {
    const key = [
        payload.kind || '',
        payload.error_name || '',
        payload.source || '',
        payload.line || '',
        payload.http_status || '',
        payload.request_id || '',
    ].join('|');
    const now = Date.now();
    const prev = __prksRecentClientErrors.get(key) || 0;
    if ((now - prev) < PRKS_CLIENT_ERROR_DEDUPE_MS) {
        return false;
    }
    __prksRecentClientErrors.set(key, now);
    if (__prksRecentClientErrors.size > PRKS_CLIENT_ERROR_MAX_QUEUE) {
        const oldestKey = __prksRecentClientErrors.keys().next().value;
        __prksRecentClientErrors.delete(oldestKey);
    }
    return true;
}

function prksBuildClientErrorPayload(input) {
    const payload = input && typeof input === 'object' ? input : {};
    const out = {
        kind: prksTrimClientErrorText(payload.kind || 'client_error', 64),
        error_name: prksTrimClientErrorText(payload.error_name || 'Error', 64),
        source: prksSafeClientSource(payload.source || 'client'),
        request_id: prksTrimClientErrorText(payload.request_id || '', 64),
    };
    const line = Number(payload.line);
    if (Number.isInteger(line) && line >= 0) {
        out.line = line;
    }
    const column = Number(payload.column);
    if (Number.isInteger(column) && column >= 0) {
        out.column = column;
    }
    const httpStatus = Number(payload.http_status);
    if (Number.isInteger(httpStatus) && httpStatus >= 100 && httpStatus <= 599) {
        out.http_status = httpStatus;
    }
    return out;
}

function prksReportClientError(input) {
    const payload = prksBuildClientErrorPayload(input);
    if (!prksShouldReportClientError(payload)) {
        return;
    }
    fetch('/api/client-errors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        keepalive: true,
    }).catch(() => {});
}

window.prksReportClientError = prksReportClientError;

function prksApiSignal(options) {
    return options && options.signal ? options.signal : undefined;
}

function prksApiErrorOwner(options) {
    const owner = options && (options.errorOwner || options.signal);
    return owner && (typeof owner === 'object' || typeof owner === 'function') ? owner : null;
}

function prksCatalogReadPolicy() {
    const ms = typeof PRKS_REQUEST_BURST_FRESH_MS === 'number' ? PRKS_REQUEST_BURST_FRESH_MS : 1500;
    return { freshForMs: ms };
}

function prksAbortFallback(error) {
    return typeof prksIsAbortError === 'function' && prksIsAbortError(error);
}

/** Parse JSON body when response is OK; otherwise return fallback (same shape callers expect). */
async function prksParseJsonResponse(res, fallback, context = 'request', owner) {
    const requestId = (res && res.headers && res.headers.get('X-Request-ID')) || '';
    if (!res.ok) {
        prksSetApiError(context, `Request failed (${res.status})`, requestId, owner);
        prksReportClientError({
            kind: 'api_http_error',
            source: prksApiErrorSource(context),
            http_status: res.status,
            request_id: requestId,
        });
        return fallback;
    }
    try {
        return await res.json();
    } catch (e) {
        prksSetApiError(context, 'Received invalid server response.', requestId, owner);
        prksReportClientError({
            kind: 'api_parse_error',
            error_name: e && e.name ? String(e.name) : 'Error',
            source: prksApiErrorSource(context),
            request_id: requestId,
        });
        return fallback;
    }
}

async function fetchWorks(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/works', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'works', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('works', 'Could not load files.', '', errorOwner);
        prksReportApiClientError('works');
        return [];
    }
}
/**
 * The Folder hierarchy every picker and dashboard reads.
 *
 * The EFFECTIVE hierarchy: a folder created on this device is a real folder,
 * and a picker that could not offer it -- or a dashboard that did not list it
 * -- would make offline creation useless the moment it succeeded.
 */
async function fetchFolders(options = {}) {
    if (typeof prksEffectiveFolderCatalogue === 'function') {
        try { return await prksEffectiveFolderCatalogue() || []; }
        catch (_e) { return []; }
    }
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/folders', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'folders', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('folders', 'Could not load folders.', '', errorOwner);
        prksReportApiClientError('folders');
        return [];
    }
}
async function fetchFolderDetails(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/folders/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        return await prksParseJsonResponse(res, null, 'folder-details', errorOwner);
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('folder-details', 'Could not load folder details.', '', errorOwner);
        prksReportApiClientError('folder-details');
        return null;
    }
}
async function fetchPersons(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/persons', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'persons', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('persons', 'Could not load people.', '', errorOwner);
        prksReportApiClientError('persons');
        return [];
    }
}
async function fetchWorkDetails(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/works/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        return await prksParseJsonResponse(res, null, 'work-details', errorOwner);
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('work-details', 'Could not load file details.', '', errorOwner);
        prksReportApiClientError('work-details');
        return null;
    }
}
async function fetchPersonDetails(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/persons/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        return await prksParseJsonResponse(res, null, 'person-details', errorOwner);
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('person-details', 'Could not load person details.', '', errorOwner);
        prksReportApiClientError('person-details');
        return null;
    }
}
async function fetchPersonGroups(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/person-groups', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'person-groups', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('person-groups', 'Could not load groups.', '', errorOwner);
        prksReportApiClientError('person-groups');
        return [];
    }
}
async function fetchPersonGroupDetails(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/person-groups/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        return await prksParseJsonResponse(res, null, 'person-group-details', errorOwner);
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('person-group-details', 'Could not load group details.', '', errorOwner);
        prksReportApiClientError('person-group-details');
        return null;
    }
}
async function fetchRecent(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/recent', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'recent', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('recent', 'Could not load recent files.', '', errorOwner);
        prksReportApiClientError('recent');
        return [];
    }
}
async function fetchRecentlyAdded(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/recently-added', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'recently-added', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('recently-added', 'Could not load recently added files.', '', errorOwner);
        prksReportApiClientError('recently-added');
        return [];
    }
}
async function fetchSearch(query, tagName, options = {}) {
    const author = options.author != null ? String(options.author).trim() : '';
    const publisher =
        options.publisher != null ? String(options.publisher).trim() : '';
    const any = options.any != null ? String(options.any).trim() : '';
    const signal = prksApiSignal(options);
    const errorOwner = prksApiErrorOwner(options);
    if (tagName) {
        try {
            const params = new URLSearchParams();
            params.set('tag', tagName);
            if (author) params.set('author', author);
            if (publisher) params.set('publisher', publisher);
            if (any && (any === '1' || any.toLowerCase() === 'true' || any.toLowerCase() === 'yes')) {
                params.set('any', '1');
            }
            const res = await prksRequest('/api/search?' + params.toString(), { signal: signal });
            const data = await prksParseJsonResponse(res, [], 'search', errorOwner);
            return Array.isArray(data) ? data : [];
        } catch (e) {
            if (prksAbortFallback(e)) return [];
            prksSetApiError('search', 'Search request failed.', '', errorOwner);
            prksReportApiClientError('search');
            return [];
        }
    }
    const q = (query || '').trim();
    if (!q && !author && !publisher) return [];
    try {
        const params = new URLSearchParams();
        if (q) params.set('q', q);
        if (author) params.set('author', author);
        if (publisher) params.set('publisher', publisher);
        if (any && (any === '1' || any.toLowerCase() === 'true' || any.toLowerCase() === 'yes')) {
            params.set('any', '1');
        }
        const url = '/api/search?' + params.toString();
        const res = await prksRequest(url, { signal: signal });
        const data = await prksParseJsonResponse(res, [], 'search', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('search', 'Search request failed.', '', errorOwner);
        prksReportApiClientError('search');
        return [];
    }
}
async function fetchPublishersInUse(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/publishers?used=1', { signal: prksApiSignal(options) });
        const data = await prksParseJsonResponse(res, [], 'publishers', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('publishers', 'Could not load publishers.', '', errorOwner);
        prksReportApiClientError('publishers');
        return [];
    }
}

async function fetchTags(options = {}) {
    if (!options.used && typeof prksReadTagsIndex === 'function') {
        try { const result = await prksReadTagsIndex(options); return result.value || []; }
        catch (_) { return []; }
    }
    const errorOwner = prksApiErrorOwner(options);
    const params = new URLSearchParams();
    if (options.used) {
        params.set('used', '1');
    }
    const q = params.toString() ? '?' + params.toString() : '';
    try {
        const res = await prksRequest('/api/tags' + q, { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'tags', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('tags', 'Could not load tags.', '', errorOwner);
        prksReportApiClientError('tags');
        return [];
    }
}

async function fetchProcessingFiles(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    const params = new URLSearchParams();
    if (options && options.rescan) {
        params.set('rescan', '1');
    }
    const q = params.toString() ? '?' + params.toString() : '';
    const policy = options && options.rescan
        ? { dedupe: false, retry: false, freshForMs: 0 }
        : {};
    try {
        const res = await prksRequest('/api/processing-files' + q, { signal: prksApiSignal(options) }, policy);
        const data = await prksParseJsonResponse(res, [], 'processing-files', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (_e) {
        if (prksAbortFallback(_e)) return [];
        prksSetApiError('processing-files', 'Could not load files for processing.', '', errorOwner);
        prksReportApiClientError('processing-files');
        return [];
    }
}

async function patchProcessingFile(processingFileId, fields) {
    const res = await prksRequest('/api/processing-files/' + encodeURIComponent(processingFileId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update processing file metadata.');
    }
    return data;
}

async function importProcessingFile(processingFileId) {
    const res = await prksRequest('/api/processing-files/' + encodeURIComponent(processingFileId) + '/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not import file.');
    }
    // Importing commits a brand-new canonical Work in one request: it is always
    // filed into a folder (the requested one, else "Uncategorized") and can
    // carry staged Author/Editor roles, so this boundary owes the same
    // coherence the /api/works create path publishes.
    prksMarkFoldersDomainChanged();
    prksMarkPeopleDomainChanged();
    prksMarkPersonGroupsDomainChanged();
    // A new Work enters the stable catalog and the top of Recently added, but
    // NOT Recent -- its last_opened_at is still NULL.
    prksMarkWorksBrowseChanged();
    prksMarkRecentlyAddedChanged();
    return data;
}

/** Legacy device-only key; migrated once to server via prksLoadAppSettings. */
const PRKS_LS_ANNOTATION_AUTHOR_LEGACY = 'prks-annotation-author';

let __prksAppSettingsPromise = null;

function prksSetAnnotationAuthorCache(v) {
    window.__prksAnnotationAuthor = (v == null ? '' : String(v)).trim();
}

/** BibTeX field inclusion map from GET/PATCH /api/settings; keys omitted default to included. */
function prksSetBibtexExportFieldsCache(obj) {
    window.__prksBibtexExportFields = obj && typeof obj === 'object' ? { ...obj } : {};
}

/** Display name for new PDF annotations; empty → "You". Synced on the server for all devices. */
function getPrksAnnotationAuthor() {
    const v = (typeof window.__prksAnnotationAuthor === 'string' ? window.__prksAnnotationAuthor : '').trim();
    return v || 'You';
}

function prksLoadAppSettings() {
    if (__prksAppSettingsPromise) return __prksAppSettingsPromise;
    __prksAppSettingsPromise = prksRequest('/api/settings')
        .then((r) => (r.ok ? r.json() : {}))
        .then((data) => {
            const raw =
                data && typeof data.annotation_author === 'string' ? data.annotation_author.trim() : '';
            let author = raw;
            try {
                const legacy = (localStorage.getItem(PRKS_LS_ANNOTATION_AUTHOR_LEGACY) || '').trim();
                if (!author && legacy) {
                    author = legacy;
                    prksRequest('/api/settings', {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ annotation_author: legacy }),
                    }).catch(() => {});
                    localStorage.removeItem(PRKS_LS_ANNOTATION_AUTHOR_LEGACY);
                }
            } catch (_e) {
                /* ignore */
            }
            prksSetAnnotationAuthorCache(author);
            if (data && typeof data.bibtex_export_fields === 'object' && data.bibtex_export_fields !== null) {
                prksSetBibtexExportFieldsCache(data.bibtex_export_fields);
            }
            return data;
        })
        .catch(() => {
            prksSetAnnotationAuthorCache('');
            return {};
        });
    return __prksAppSettingsPromise;
}

async function prksPatchAppSettings(partial) {
    const res = await prksRequest('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(partial),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not save settings.');
    if (data && typeof data.annotation_author === 'string') {
        prksSetAnnotationAuthorCache(data.annotation_author);
    }
    if (data && typeof data.bibtex_export_fields === 'object' && data.bibtex_export_fields !== null) {
        prksSetBibtexExportFieldsCache(data.bibtex_export_fields);
    }
    return data;
}

async function prksReindexPdfText() {
    const res = await prksRequest('/api/works/reindex-pdf-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not rebuild PDF text index.');
    return data;
}

async function prksLinearizeExistingPdfs(unlinearizedOnly = true) {
    const res = await prksRequest('/api/works/linearize-existing-pdfs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unlinearized_only: !!unlinearizedOnly }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not linearize existing PDFs.');
    return data;
}

async function prksGetPerformanceDiagnostics() {
    const res = await prksRequest('/api/diagnostics/performance');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not load performance diagnostics.');
    return data;
}

async function prksResetPerformanceDiagnostics() {
    const res = await prksRequest('/api/diagnostics/performance/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not reset performance diagnostics.');
    return data;
}

async function prksStartBackupProgress(signal) {
    const res = await fetch('/api/backups/progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
        signal,
    });
    if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Backup could not be created.');
    }
    return res.body.getReader();
}

async function prksStageBackup(file) {
    const res = await fetch('/api/backups/stage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Backup could not be verified. Current PRKS data was not changed.');
    return data;
}

async function prksRestoreBackup(token) {
    const res = await fetch('/api/backups/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, confirm: 'RESTORE' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Restore failed. Current PRKS data was not changed.');
    return data;
}

/**
 * Infer pdf vs video for UI. PDFs may have source_url (e.g. original article); explicit source_kind wins.
 */
function prksInferWorkSourceKind(work) {
    if (!work || typeof work !== 'object') return '';
    const sk = String(work.source_kind || '').trim().toLowerCase();
    if (sk === 'video') return 'video';
    if (sk === 'pdf') return 'pdf';
    const fp = String(work.file_path || '').trim();
    if (fp) return 'pdf';
    if (String(work.source_url || '').trim()) return 'video';
    return sk;
}

/**
 * Marker on errors already surfaced by an offline mutation guard, so call
 * sites can skip a second, duplicate alert.
 */
function prksOfflineWasGuardRefusal(err) {
    return !!(err && err.prksOfflineRefused === true);
}

/** One message per refusal the Folder families can produce. */
function prksFolderSaveMessage(error, action) {
    switch (error && error.prksLocalStoreCode) {
        case 'scope_busy':
            return 'Part of this folder is syncing or needs a decision. Try again shortly.';
        case 'entity_deleted':
            return 'This folder is being deleted, so it cannot be changed.';
        case 'dependency_failed':
            return String(error.message || 'A change this one depends on could not be saved.');
        case 'invalid_envelope':
        case 'invalid_base':
            return String(error.message || 'That is not a valid folder change.');
        default:
            return 'Could not ' + action + ' locally. Please retry.';
    }
}

/**
 * Which folder a Work is in, durably.
 *
 * `addWorkToFolder` and `patchWorkFolder` are the same operation seen from two
 * ends -- a Work is in at most ONE folder, so filing, moving and clearing all
 * set the same scalar. Both names are kept so their callers do not change.
 */
async function prksFileWorkInFolder(workId, folderIdOrNull) {
    const observed = await prksAcknowledgedWorkFolder(workId);
    if (!observed) {
        /* Unknown is not empty: without the revision this filing was measured
         * against, it would have to guess 0 and could silently overwrite
         * wherever another device had filed it. */
        const err = new Error(
            'This file cannot be moved offline yet. Open it once while connected to PRKS '
            + 'so its synchronization state is prepared.');
        err.prksFolderUnavailable = true;
        throw err;
    }
    try {
        await prksSetWorkFolderDurably(
            workId, folderIdOrNull == null ? '' : String(folderIdOrNull), observed);
    } catch (error) {
        throw new Error(prksFolderSaveMessage(error, 'move this file'));
    }
}

async function addWorkToFolder(folderId, workId) {
    return prksFileWorkInFolder(workId, folderId);
}

async function patchWorkFolder(workId, folderIdOrNull) {
    return prksFileWorkInFolder(workId, folderIdOrNull);
}

/**
 * Create a folder durably, under an id this device mints.
 *
 * No connectivity guard: the folder is real the moment it is written, and
 * anything filed into it is ordered behind its creation by the generic
 * dependency mechanism. Title uniqueness within a parent stays canonical --
 * only the server sees the whole hierarchy.
 */
async function createFolder(title, description = '', options = {}) {
    const parentIdRaw = options && Object.prototype.hasOwnProperty.call(options, 'parent_id')
        ? options.parent_id
        : '';
    const parentId = parentIdRaw == null ? '' : String(parentIdRaw).trim();
    try {
        const created = await prksCreateFolderDurably({
            title: (title || '').trim() || 'Untitled Folder',
            description: (description || '').trim(),
            parent_id: parentId,
            private_notes: '',
        });
        return created.entity_id;
    } catch (error) {
        throw new Error(prksFolderSaveMessage(error, 'create this folder'));
    }
}

/**
 * Edit a folder's fields durably, sending only what changed.
 *
 * The three concepts stay apart, exactly as the Person editor keeps them: the
 * `updates` are the draft, the acknowledged base comes from the cache and the
 * revisions projection, and the difference is measured against what the caller
 * was SHOWING. Sending every field would let one syncing field refuse the whole
 * form.
 */
async function patchFolder(folderId, updates) {
    const draft = {};
    Object.keys(updates || {}).forEach(function (field) {
        if ((PRKS_FOLDER_FIELDS || []).indexOf(field) === -1) return;
        const value = updates[field];
        draft[field] = value == null || value === false ? '' : String(value);
    });
    if (!Object.keys(draft).length) return;
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const base = await prksAcknowledgedFolderBase(folderId, ops);
    if (!base) {
        const err = new Error(
            'This folder cannot be edited offline yet. Open it once while connected to PRKS '
            + 'so its synchronization state is prepared.');
        err.prksFolderUnavailable = true;
        throw err;
    }
    const changes = prksDirtyFolderFields(folderId, draft, base, ops);
    if (!Object.keys(changes).length) return;
    try {
        await prksSaveFolderFieldsDurably(folderId, changes, base);
    } catch (error) {
        throw new Error(prksFolderSaveMessage(error, 'save this folder'));
    }
}

/* Canonical Tag boundaries. A cached Work detail embeds `work.tags[]` and a
 * cached Folder detail `folder.tags[]`, so deleting or merging a Tag stales
 * both read models. The server reports exactly which entities were linked, so
 * coherence never depends on the active route, the focused tab, or whether
 * this client had ever loaded those relationships. Only acknowledged canonical
 * success publishes anything: a transport failure, HTTP error, validation
 * error or abort leaves every cached snapshot eligible, because nothing
 * canonical changed. */
function prksPublishTagCoherence(data) {
    if (typeof prksOfflineMarkTagsChanged === 'function') prksOfflineMarkTagsChanged();
    const payload = data && typeof data === 'object' ? data : {};
    const folders = Array.isArray(payload.affected_folder_ids) ? payload.affected_folder_ids : [];
    const works = Array.isArray(payload.affected_work_ids) ? payload.affected_work_ids : [];
    if (folders.length) prksMarkFoldersDomainChanged();
    if (works.length && typeof prksOfflineMarkEntityChanged === 'function') {
        works.forEach(function (workId) {
            prksOfflineMarkEntityChanged('work', workId);
        });
    }
    if (typeof prksOfflineMarkEntityChanged === 'function') {
        (payload.affected_tag_options_work_ids || works).forEach(workId => prksOfflineMarkEntityChanged('work-tag-options', workId));
        (payload.affected_tag_options_folder_ids || folders).forEach(folderId =>
            prksOfflineMarkEntityChanged('folder-tag-options', folderId));
    }
    return payload;
}

async function mergeTags(sourceTagId, targetTagId) {
    if (typeof prksMergeTagDurably !== 'function') {
        throw new Error('Tag merge is not available.');
    }
    try {
        await prksMergeTagDurably(sourceTagId, targetTagId);
    } catch (error) {
        if (typeof prksTagVocabularyMessage === 'function') {
            throw new Error(prksTagVocabularyMessage(error, 'merge these tags'));
        }
        throw error;
    }
    return { status: 'queued' };
}

/** Folder deletion, durably. The empty-only rule stays server-enforced. */
async function deleteFolderCanonical(folderId) {
    /* A tombstone, not a destruction: nothing acknowledged is discarded, so a
     * server that refuses -- a folder holding files or subfolders is protected
     * -- restores it by doing nothing. */
    try {
        await prksDeleteFolderDurably(folderId);
    } catch (error) {
        throw new Error(prksFolderSaveMessage(error, 'delete this folder'));
    }
    return { status: 'deleted' };
}

/* Folder-Tag membership is durable: conflict unit (folder, tag), same shape as
 * Work-Tag. The right-panel editor is the ordinary UI path; these wrappers
 * remain for call sites and tests and enqueue through the same store API. */
async function prksEnqueueFolderTag(folderId, tagId, present, knownTag) {
    if (!window.prksSync || !prksSync.store || typeof prksSync.store.coalesceFolderTag !== 'function') {
        throw new Error('Local sync store is not available.');
    }
    if (typeof prksReadFolderTagOptions !== 'function' || typeof prksFolderTagBase !== 'function') {
        throw new Error('Folder Tag sync is not available.');
    }
    const optionsResult = await prksReadFolderTagOptions(folderId);
    if (!optionsResult || !optionsResult.value) {
        const err = new Error(
            'Tag editing not available offline for this Folder yet. Connect once and open the Tags panel to prepare it.'
        );
        err.prksFolderTagUnavailable = true;
        throw err;
    }
    let tag = knownTag || null;
    if (!tag && typeof prksReadTagsIndex === 'function') {
        const catalog = await prksReadTagsIndex();
        const rows = catalog && Array.isArray(catalog.value) ? catalog.value : [];
        tag = rows.find((t) => t && String(t.id) === String(tagId)) || null;
    }
    if (present && !tag) {
        throw new Error('Could not resolve that Tag locally.');
    }
    if (!tag) {
        tag = { id: tagId, name: '', color: null, aliases: [] };
    }
    const base = prksFolderTagBase(optionsResult.value, tagId);
    await prksSync.store.coalesceFolderTag(
        folderId, tagId, present, base.present, base.revision, tag
    );
    if (typeof prksSync.changed === 'function') prksSync.changed();
    return { status: present ? 'added' : 'removed' };
}

async function addTagToFolder(folderId, tagId, knownTag) {
    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const entity = focused && focused.getEntity ? focused.getEntity('folder') : null;
    const editor = focused && focused.getResource ? focused.getResource('folderTagEditor') : null;
    if (
        focused &&
        entity &&
        String(entity.id) === String(folderId) &&
        editor &&
        typeof prksFolderTagEdit === 'function'
    ) {
        await prksFolderTagEdit(focused, tagId, true, knownTag);
        return { status: 'added' };
    }
    return prksEnqueueFolderTag(folderId, tagId, true, knownTag);
}

async function removeTagFromFolder(folderId, tagId) {
    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const entity = focused && focused.getEntity ? focused.getEntity('folder') : null;
    const editor = focused && focused.getResource ? focused.getResource('folderTagEditor') : null;
    if (
        focused &&
        entity &&
        String(entity.id) === String(folderId) &&
        editor &&
        typeof prksFolderTagEdit === 'function'
    ) {
        await prksFolderTagEdit(focused, tagId, false);
        return { status: 'removed' };
    }
    return prksEnqueueFolderTag(folderId, tagId, false);
}

async function bulkUpdateWorks(payload) {
    if (typeof prksOfflineGuardMutation === 'function' &&
        prksOfflineGuardMutation('Bulk organize requires a connection to PRKS.')) {
        const err = new Error('Requires a connection to PRKS.');
        err.prksOfflineRefused = true;
        throw err;
    }
    const res = await prksRequest('/api/works/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(
            (data && data.error) || 'Could not update the selected files.'
        );
        err.httpStatus = res.status;
        throw err;
    }
    if (typeof prksOfflineMarkEntityChanged === 'function' && Array.isArray(payload && payload.work_ids)) {
        payload.work_ids.forEach(function (workId) {
            prksOfflineMarkEntityChanged('work', workId);
            if (['add_tags', 'remove_tags'].includes(payload.action)) prksOfflineMarkEntityChanged('work-tag-options', workId);
        });
    }
    // A cached Person's Work cards display status, so a bulk status change
    // stales them. The other bulk actions -- move_folder, add_tags,
    // remove_tags -- are NOT on those cards and deliberately leave the People
    // cache alone.
    if (payload && payload.action === 'set_status') {
        prksMarkPeopleDomainChanged();
        // Folder Work cards render status too.
        prksMarkFoldersDomainChanged();
        prksMarkWorkBrowseDisplayChanged();
    }
    // Moving files changes both Folder details and every folders:index count.
    if (payload && payload.action === 'move_folder') {
        prksMarkFoldersDomainChanged();
        prksMarkRecentlyAddedChanged();
    }
    return data;
}

async function fetchSavedViews(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/saved-views', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'saved-views', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('saved-views', 'Could not load Saved Views.', '', errorOwner);
        prksReportApiClientError('saved-views');
        return [];
    }
}

async function fetchSavedView(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/saved-views/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        if (res.status === 404) return null;
        const data = await prksParseJsonResponse(res, null, 'saved-views', errorOwner);
        return data && data.id ? data : null;
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('saved-views', 'Could not load Saved View.', '', errorOwner);
        prksReportApiClientError('saved-views');
        return null;
    }
}

function prksGuardSavedViewMutation(message) {
    if (typeof prksOfflineGuardMutation !== 'function') return;
    if (!prksOfflineGuardMutation(
        message || 'Saved Views require a connection to PRKS.')) return;
    const err = new Error('Requires a connection to PRKS.');
    err.prksOfflineRefused = true;
    throw err;
}

async function createSavedView(payload) {
    prksGuardSavedViewMutation('Saving a view requires a connection to PRKS.');
    const res = await prksRequest('/api/saved-views', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error((data && data.error) || 'Could not save view.');
    }
    return data;
}

async function updateSavedView(id, payload) {
    prksGuardSavedViewMutation('Editing a Saved View requires a connection to PRKS.');
    const res = await prksRequest('/api/saved-views/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error((data && data.error) || 'Could not update Saved View.');
    }
    return data;
}

async function deleteSavedView(id) {
    prksGuardSavedViewMutation('Deleting a Saved View requires a connection to PRKS.');
    const res = await prksRequest('/api/saved-views/' + encodeURIComponent(id), {
        method: 'DELETE',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error((data && data.error) || 'Could not delete Saved View.');
    }
}

async function prksResearchJson(res, fallbackMessage, source) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error((data && data.error) || fallbackMessage);
        err.httpStatus = res.status;
        err.code = data && data.code;
        if (source) prksReportApiClientError(source);
        throw err;
    }
    return data;
}

/**
 * The Concept vocabulary a user should see: what this device holds, with every
 * unsynchronized intent applied. A Concept created here is real, so it is
 * pickable as a parent before any server has heard of it.
 */
async function fetchConcepts(options = {}) {
    if (typeof prksEffectiveConceptCatalogue === 'function') {
        try { return await prksEffectiveConceptCatalogue() || []; }
        catch (_e) { return []; }
    }
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/concepts', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'concepts.fetch', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('concepts', 'Could not load Concepts.', '', errorOwner);
        prksReportApiClientError('concepts.fetch');
        return [];
    }
}

async function fetchConcept(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/concepts/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        if (res.status === 404) return null;
        const data = await prksParseJsonResponse(res, null, 'concepts.fetch', errorOwner);
        return data && data.id ? data : null;
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('concepts', 'Could not load Concept.', '', errorOwner);
        prksReportApiClientError('concepts.fetch');
        return null;
    }
}

/**
 * Concept read models span many canonical records: renaming one Concept changes
 * its own detail, the Concept index, and every cached relative that displays its
 * name, while a parent change moves subconcept lists and counts on both sides.
 * Per-entity invalidation cannot express that, so a successful Concept mutation
 * invalidates the whole Concepts offline domain at this canonical boundary --
 * independent of the current route, focused pane, ctx generation, or panel
 * ownership. Canonical success alone controls coherence. See AGENTS.md
 * "Offline / PWA".
 */
function prksMarkConceptsDomainChanged() {
    if (typeof prksOfflineMarkConceptsChanged !== 'function') return null;
    return prksOfflineMarkConceptsChanged();
}

/**
 * Coherence hook for EVERY acknowledged canonical Work-title change, wherever
 * the edit surface lives (the metadata editor, the Playlist inline video
 * rename, anything added later). A cached Concept detail lists the titles of
 * the Works that mention it, so a renamed Work stales the Concept read model
 * even though no Concept record changed. Routing every title save through one
 * helper is what stops a new title-edit surface from silently reopening that
 * hole. Returns the Work's coherence token so the caller can still gate a
 * follow-up complete Work GET.
 */
/**
 * Coherence hook for a canonical change to the Positions read model. A cached
 * Position detail embeds derived Argument/Stance summaries (name, kind,
 * verdict) and its whole targeting list, so Argument-side changes stale it even
 * Positions domain policy is deliberately conservative: any
 * successful call to one of those helpers invalidates the whole Positions
 * domain rather than working out which Positions were actually affected.
 * Independent of the Concepts domain by construction -- see
 * docs/agent-rules/offline-pwa.md "Offline coherence domains".
 */
function prksMarkPositionsDomainChanged() {
    if (typeof prksOfflineMarkPositionsChanged !== 'function') return null;
    return prksOfflineMarkPositionsChanged();
}

/**
 * Coherence hook for a canonical change to the Arguments read model. That model
 * is the widest in PRKS: an Argument/Stance embeds its targets' Position and
 * Argument names, its source Works' titles AND those Works' Author names and
 * credit names, its incoming responses, and its research-note mentions. So a
 * lot of canonical callers outside `arguments` itself invalidate this domain --
 * each documented in AGENTS.md with the field it can stale. Stances live here
 * too: they are Arguments with `kind: 'stance'`, not a separate domain.
 */
function prksMarkArgumentsDomainChanged() {
    if (typeof prksOfflineMarkArgumentsChanged !== 'function') return null;
    return prksOfflineMarkArgumentsChanged();
}

/** Graph dependencies are separate from the entity-detail coherence domains. */
function prksMarkResearchGraphCoreChanged() {
    // Start both invalidations synchronously; never await the first sweep.
    const core = typeof prksOfflineMarkResearchGraphCoreChanged === 'function'
        ? prksOfflineMarkResearchGraphCoreChanged() : null;
    prksMarkResearchGraphPeopleChanged();
    return core;
}

function prksMarkResearchGraphPeopleChanged() {
    return typeof prksOfflineMarkResearchGraphPeopleChanged === 'function'
        ? prksOfflineMarkResearchGraphPeopleChanged() : null;
}

/**
 * Coherence hook for a canonical change to the People read model. A cached
 * Person carries full Work-card summaries for every Work it is linked to, the
 * role assignments themselves, and its Group memberships -- so Work metadata,
 * Work-role and Group mutations all stale it, not just Person edits. See
 * AGENTS.md for the dependency table and the deliberate exclusions.
 */
function prksMarkPersonGroupsDomainChanged() {
    if (typeof prksOfflineMarkPersonGroupsChanged !== 'function') return null;
    return prksOfflineMarkPersonGroupsChanged();
}

function prksMarkPlaylistsDomainChanged() {
    if (typeof prksOfflineMarkPlaylistsChanged !== 'function') return null;
    return prksOfflineMarkPlaylistsChanged();
}

/* ---------------------------------------------------------------------------
 * Browse-projection coherence.
 *
 * Three independent domains, one semantic helper each. Components must call
 * these rather than touching list keys directly: when offline mutations land,
 * the sync coordinator needs ONE place to change "discard the projection" into
 * "apply the pending operation to it optimistically". A scattered
 * deleteList('works-browse:index') would have to be rewritten everywhere.
 * See docs/agent-rules/offline-pwa.md, "Offline browse catalogs".
 * ------------------------------------------------------------------------ */

/** The stable Work catalog behind #/progress, #/types and #/types/:type. */
function prksMarkWorksBrowseChanged() {
    if (typeof prksOfflineMarkWorksBrowseChanged !== 'function') return null;
    return prksOfflineMarkWorksBrowseChanged();
}

/** #/recent -- top-N by last_opened_at. Deliberately separate so that merely
 *  OPENING a Work does not cost the other browse caches. */
function prksMarkRecentChanged() {
    if (typeof prksOfflineMarkRecentChanged !== 'function') return null;
    return prksOfflineMarkRecentChanged();
}

/** Home -> Recently added -- top-N by created_at. */
function prksMarkRecentlyAddedChanged() {
    if (typeof prksOfflineMarkRecentlyAddedChanged !== 'function') return null;
    return prksOfflineMarkRecentlyAddedChanged();
}

/**
 * Every browse projection that embeds a Work's rendered card fields. Work
 * cards in all three show title, status, doc type, credit line, year and file
 * size, so a display change stales all three -- but an *open* (Recent only) and
 * a *create* (Recently added + catalog) deliberately do not come through here.
 */
function prksMarkWorkBrowseDisplayChanged() {
    prksMarkWorksBrowseChanged();
    prksMarkRecentChanged();
    prksMarkRecentlyAddedChanged();
}

/**
 * Coherence hook for the Folders read model. Both cached Folder surfaces live
 * in one domain because Folder relationships are never local to a single
 * Folder: moving a Work changes two Folder details AND both `work_count`s in
 * `folders:index`, and reparenting changes the hierarchy for every ancestor.
 * A cached Folder detail also embeds whole Work cards, so Work display changes
 * stale it too -- see docs/agent-rules/offline-pwa.md, "Offline coherence domains".
 */
function prksMarkFoldersDomainChanged() {
    if (typeof prksOfflineMarkFoldersChanged !== 'function') return null;
    return prksOfflineMarkFoldersChanged();
}

function prksMarkPeopleDomainChanged() {
    if (typeof prksOfflineMarkPeopleChanged !== 'function') return null;
    return prksOfflineMarkPeopleChanged();
}

/**
 * Every canonical change to a Work's *displayed* metadata. A Work title shows
 * in cached Concept mentions and cached Argument sources/mentions, and a cached
 * Person embeds whole Work cards (title, status, doc type, year, author text,
 * thumbnail metadata, file size). Graph Work nodes also consume title/doc_type,
 * so the same conservative save hook invalidates both projection snapshots.
 */
function prksMarkWorkTitleChanged(workId) {
    const token =
        typeof prksOfflineMarkEntityChanged === 'function'
            ? prksOfflineMarkEntityChanged('work', workId)
            : null;
    prksMarkConceptsDomainChanged();
    prksMarkArgumentsDomainChanged();
    prksMarkPeopleDomainChanged();
    // A cached Playlist detail renders each item's title, author_text and
    // published_date, so a metadata save can stale it. This helper is already
    // deliberately conservative (a date-only edit invalidates Concepts too),
    // and routing Playlists through it is what makes the Playlist inline Work
    // rename inherit the dependency without its own hook.
    prksMarkPlaylistsDomainChanged();
    // A cached Folder detail renders the same Work cards (title, status, doc
    // type, year, credit line, file size), so this conservative hook covers
    // Folders for exactly the same reason it covers Playlists.
    prksMarkFoldersDomainChanged();
    // Every browse projection embeds the same Work card (title, status, doc
    // type, credit line, year, file size), so a display change stales all
    // three. An *open* and a *create* deliberately do not come through here.
    prksMarkWorkBrowseDisplayChanged();
    prksMarkResearchGraphCoreChanged();
    return token;
}

/**
 * Coherence hook for every Work-role surface (link, unlink, credit-name edit).
 *
 * People is staled by EVERY role type: the People index carries
 * `assigned_roles`, a cached Person lists the Work with its role_type/
 * order_index/credit_name, and the server may append a non-empty credit name to
 * `persons.aliases` -- which feeds Person display and People search. Arguments
 * is staled only by Author changes, because a cached Argument source lists just
 * that Work's Authors. Routing every role surface through one helper is what
 * stops a new one from silently reopening either gap.
 */
/**
 * Everything OTHER than the Work record that a role change stales.
 *
 * Split out from `prksMarkWorkRoleChanged` because the durable path patches
 * the Work, the browse catalogs and the embedded summaries with the exact new
 * values rather than evicting them -- reconciling and then invalidating would
 * throw away the patch. What remains here are the projections whose author
 * rendering is not in the reference-shape registry, so the exact new value
 * cannot be written into them and re-reading is the honest answer.
 */
function prksMarkWorkRoleDependenciesChanged(roleType) {
    prksMarkPersonGroupsDomainChanged();
    prksMarkPeopleDomainChanged();
    const role = String(roleType || '').trim();
    if (role === 'Author') {
        /* NOT the Graph's People snapshot: a role acknowledgement patches that
         * edge exactly -- the node and edge shapes are fully determined -- and
         * invalidating here would throw the patch away and leave the Graph
         * unavailable offline for a change PRKS could draw. Cached Argument
         * source authors have no such exact projection, so they are re-read. */
        prksMarkArgumentsDomainChanged();
    }
    // A Folder Work card's credit line is linked_authors -> author_text ->
    // primary_editor, so Editor changes stale Folders even though they touch
    // neither Arguments nor the Graph. Other roles are not rendered there.
    if (role === 'Author' || role === 'Editor') {
        prksMarkFoldersDomainChanged();
        prksMarkWorkBrowseDisplayChanged();
    }
}

function prksMarkWorkRoleChanged(workId, roleType) {
    const token =
        typeof prksOfflineMarkEntityChanged === 'function'
            ? prksOfflineMarkEntityChanged('work', workId)
            : null;
    prksMarkWorkRoleDependenciesChanged(roleType);
    return token;
}

/** Former name of prksMarkWorkRoleChanged; kept as a thin delegate only. */
function prksMarkWorkAuthorDisplayChanged(workId, roleType) {
    return prksMarkWorkRoleChanged(workId, roleType);
}

/* --- Durable Concept mutations --------------------------------------------
 * Every production Concept write goes through these, so there is exactly one
 * boundary per operation. None of them guards connectivity: a Concept change
 * is a semantic operation with a revision and a defined conflict, so it is
 * written to the durable queue and is as real offline as online.
 *
 * What a caller can still be told is that the BASE is unknown -- a Concept this
 * device has never read has no revision to measure an edit against, and
 * guessing would silently overwrite whatever another device wrote. That is a
 * different refusal from "no connection", and it is the only one this layer
 * makes. */
function prksConceptSaveMessage(error, action) {
    switch (error && error.prksLocalStoreCode) {
        case 'scope_busy':
            return 'Part of this concept is syncing or needs a decision. Try again shortly.';
        case 'entity_deleted':
            return 'This concept is being deleted, so it cannot be changed.';
        case 'dependency_failed':
            return String(error.message || 'A change this one depends on could not be saved.');
        case 'invalid_envelope':
        case 'invalid_base':
            return String(error.message || 'That is not a valid concept change.');
        default:
            return 'Could not ' + action + ' locally. Please retry.';
    }
}

function prksConceptBaseUnavailable() {
    const err = new Error(
        'This concept cannot be edited offline yet. Open it once while connected to '
        + 'PRKS so its synchronization state is prepared.');
    err.prksConceptUnavailable = true;
    return err;
}

/**
 * Create a Concept durably, under an id this device mints.
 *
 * Unlike a Folder or a Playlist there is no placeholder name: a Concept's name
 * is its identity. Uniqueness over the name-or-alias space stays canonical --
 * only the server sees the whole vocabulary — so a taken name comes back as a
 * named refusal rather than being guessed at here.
 */
async function createConcept(payload) {
    const src = payload && typeof payload === 'object' ? payload : {};
    let op;
    try {
        op = await prksCreateConceptDurably({
            name: src.name == null ? '' : String(src.name),
            description: src.description == null ? '' : String(src.description),
        });
    } catch (error) {
        throw new Error(prksConceptSaveMessage(error, 'create this concept'));
    }
    return op ? { id: op.entity_id, name: op.payload.name,
                  description: op.payload.description, aliases: [],
                  parents: [], children: [] } : null;
}

/**
 * Edit a Concept, sending only what changed.
 *
 * `name` and `description` arrive together from the ordinary PATCH shape but
 * belong to two different families: the definition is a scalar FIELD, and the
 * name is half of the identity aggregate, because renaming keeps the old name
 * reachable as an alias.
 */
async function updateConcept(id, payload) {
    const src = payload && typeof payload === 'object' ? payload : {};
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    if (Object.prototype.hasOwnProperty.call(src, 'description')) {
        const base = await prksAcknowledgedConceptFields(id, ops);
        if (!base) throw prksConceptBaseUnavailable();
        const draft = { description: src.description == null ? '' : String(src.description) };
        const changes = prksDirtyConceptFields(id, draft, base, ops);
        if (Object.keys(changes).length) {
            try {
                await prksSaveConceptFieldsDurably(id, changes, base);
            } catch (error) {
                throw new Error(prksConceptSaveMessage(error, 'save this concept'));
            }
        }
    }
    if (Object.prototype.hasOwnProperty.call(src, 'name')) {
        const observed = await prksAcknowledgedConceptIdentity(id, ops);
        if (!observed) throw prksConceptBaseUnavailable();
        /* The alias set travels unchanged: the server adds the old name to it
         * when the identity actually moves, exactly as the ordinary PATCH
         * always did. */
        try {
            await prksSetConceptIdentityDurably(
                id, String(src.name == null ? '' : src.name), observed.aliases, observed);
        } catch (error) {
            throw new Error(prksConceptSaveMessage(error, 'rename this concept'));
        }
    }
    return { id: id };
}

/** Delete a Concept durably. A tombstone: nothing acknowledged is destroyed. */
async function deleteConcept(id) {
    try {
        await prksDeleteConceptDurably(id);
    } catch (error) {
        throw new Error(prksConceptSaveMessage(error, 'delete this concept'));
    }
    return { status: 'deleted' };
}

/** The whole parent set, as one structural judgement. */
async function putConceptParents(id, parentIds) {
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const observed = await prksAcknowledgedConceptParents(id, ops);
    if (!observed) throw prksConceptBaseUnavailable();
    try {
        await prksSetConceptParentsDurably(id, parentIds || [], observed);
    } catch (error) {
        throw new Error(prksConceptSaveMessage(error, 'reparent this concept'));
    }
    return { id: id };
}

/**
 * The alias set -- which is half of the identity, not a list of its own.
 *
 * The name travels unchanged, so an alias edit and a rename are the same
 * operation seen from two ends and cannot overwrite each other's half.
 */
async function putConceptAliases(id, aliases) {
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const observed = await prksAcknowledgedConceptIdentity(id, ops);
    if (!observed) throw prksConceptBaseUnavailable();
    try {
        await prksSetConceptIdentityDurably(id, observed.name, aliases || [], observed);
    } catch (error) {
        throw new Error(prksConceptSaveMessage(error, 'save these aliases'));
    }
    return { id: id };
}

/**
 * The Position list a user should see: what this device holds, with every
 * unsynchronized intent applied. A Position created here is real, so it is
 * pickable as an Argument target before any server has heard of it.
 */
async function fetchPositions(options = {}) {
    if (typeof prksEffectivePositionCatalogue === 'function') {
        try { return await prksEffectivePositionCatalogue() || []; }
        catch (_e) { return []; }
    }
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/positions', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'positions.fetch', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('positions', 'Could not load Positions.', '', errorOwner);
        prksReportApiClientError('positions.fetch');
        return [];
    }
}

async function fetchPosition(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/positions/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        if (res.status === 404) return null;
        const data = await prksParseJsonResponse(res, null, 'positions.fetch', errorOwner);
        return data && data.id ? data : null;
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('positions', 'Could not load Position.', '', errorOwner);
        prksReportApiClientError('positions.fetch');
        return null;
    }
}

/* --- Durable Position mutations -------------------------------------------
 * Every production Position write goes through these. None guards
 * connectivity: a Position change is a semantic operation with a per-field
 * revision and a defined conflict. What a caller can still be told is that the
 * BASE is unknown -- a Position this device has never read has no revision to
 * measure an edit against. */
function prksPositionSaveMessage(error, action) {
    switch (error && error.prksLocalStoreCode) {
        case 'scope_busy':
            return 'Part of this position is syncing or needs a decision. Try again shortly.';
        case 'entity_deleted':
            return 'This position is being deleted, so it cannot be changed.';
        case 'dependency_failed':
            return String(error.message || 'A change this one depends on could not be saved.');
        case 'invalid_envelope':
        case 'invalid_base':
            return String(error.message || 'That is not a valid position change.');
        default:
            return 'Could not ' + action + ' locally. Please retry.';
    }
}

function prksPositionBaseUnavailable() {
    const err = new Error(
        'This position cannot be edited offline yet. Open it once while connected to '
        + 'PRKS so its synchronization state is prepared.');
    err.prksPositionUnavailable = true;
    return err;
}

/**
 * Create a Position durably, under an id this device mints.
 *
 * Permanent and distributed, so a Position created offline can be the target
 * of an Argument before any server has heard of either.
 */
async function createPosition(payload) {
    const src = payload && typeof payload === 'object' ? payload : {};
    let op;
    try {
        op = await prksCreatePositionDurably({
            name: src.name == null ? '' : String(src.name),
            description: src.description == null ? '' : String(src.description),
        });
    } catch (error) {
        throw new Error(prksPositionSaveMessage(error, 'create this position'));
    }
    return op ? { id: op.entity_id, name: op.payload.name,
                  description: op.payload.description } : null;
}

/**
 * Edit a Position, sending only what changed.
 *
 * The two fields are INDEPENDENT, so a description edit here never conflicts
 * with a rename elsewhere -- and each is measured against the acknowledged
 * base rather than against whatever the page happens to be showing.
 */
async function updatePosition(id, payload) {
    const src = payload && typeof payload === 'object' ? payload : {};
    const draft = {};
    (PRKS_POSITION_FIELDS || []).forEach(function (field) {
        if (!Object.prototype.hasOwnProperty.call(src, field)) return;
        const value = src[field];
        draft[field] = value == null || value === false ? '' : String(value);
    });
    if (!Object.keys(draft).length) return { id: id };
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const base = await prksAcknowledgedPositionBase(id, ops);
    if (!base) throw prksPositionBaseUnavailable();
    const changes = prksDirtyPositionFields(id, draft, base, ops);
    if (!Object.keys(changes).length) return { id: id };
    try {
        await prksSavePositionFieldsDurably(id, changes, base);
    } catch (error) {
        throw new Error(prksPositionSaveMessage(error, 'save this position'));
    }
    return { id: id };
}

/** Delete a Position durably. A tombstone: a refusal brings it back. */
async function deletePosition(id) {
    try {
        await prksDeletePositionDurably(id);
    } catch (error) {
        throw new Error(prksPositionSaveMessage(error, 'delete this position'));
    }
    return { status: 'deleted' };
}

async function fetchArguments(kind, options = {}) {
    if (typeof prksEffectiveArgumentCatalogue === 'function') {
        try {
            const rows = await prksEffectiveArgumentCatalogue();
            return typeof prksFilterArgumentsByKind === 'function'
                ? prksFilterArgumentsByKind(rows || [], kind || '') : (rows || []);
        } catch (_e) { return []; }
    }
    const errorOwner = prksApiErrorOwner(options);
    try {
        const q = kind ? '?kind=' + encodeURIComponent(kind) : '';
        const res = await prksRequest('/api/arguments' + q, { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'arguments.fetch', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        prksSetApiError('arguments', 'Could not load Arguments.', '', errorOwner);
        prksReportApiClientError('arguments.fetch');
        return [];
    }
}

async function fetchArgument(id, options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/arguments/' + encodeURIComponent(id), { signal: prksApiSignal(options) });
        if (res.status === 404) return null;
        const data = await prksParseJsonResponse(res, null, 'arguments.fetch', errorOwner);
        return data && data.id ? data : null;
    } catch (e) {
        if (prksAbortFallback(e)) return null;
        prksSetApiError('arguments', 'Could not load Argument.', '', errorOwner);
        prksReportApiClientError('arguments.fetch');
        return null;
    }
}

async function fetchArgumentVerdicts(options = {}) {
    const errorOwner = prksApiErrorOwner(options);
    try {
        const res = await prksRequest('/api/argument-verdicts', { signal: prksApiSignal(options) }, prksCatalogReadPolicy());
        const data = await prksParseJsonResponse(res, [], 'arguments.fetch', errorOwner);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (prksAbortFallback(e)) return [];
        return [];
    }
}

/* --- Durable Argument mutations ------------------------------------------
 * Construction is one atomic operation carrying scalar state, sources and
 * targets. Later fields and the two ordered aggregates are independent
 * conflict units. No connectivity guard belongs here: lack of a known
 * acknowledged revision is reported as unknown base, never disguised as an
 * offline policy. */

function prksArgumentSaveMessage(error, action) {
    if (error && error.prksArgumentUnavailable) return String(error.message || '');
    const code = error && (error.prksLocalStoreCode || error.code);
    switch (code) {
        case 'scope_busy':
            return 'That part of this Argument is syncing or needs a decision. Try again shortly.';
        case 'entity_deleted':
            return 'This Argument is being deleted, so it cannot be changed.';
        case 'dependency_failed':
        case 'DEPENDENCY_FAILED':
            return String(error.message || 'A change this one depends on could not be saved.');
        case 'invalid_envelope':
        case 'invalid_base':
            return String(error.message || 'That is not a valid Argument change.');
        case 'WORK_NOT_FOUND': return 'One source Work no longer exists on PRKS.';
        case 'POSITION_NOT_FOUND': return 'One target Position no longer exists on PRKS.';
        case 'TARGET_NOT_FOUND': return 'One target Argument no longer exists on PRKS.';
        case 'INVALID_VERDICT': return 'One selected verdict is no longer available.';
        case 'ARGUMENT_CYCLE': return 'Those targets would create an Argument response cycle.';
        case 'ARGUMENT_IN_USE': return 'Research notes still mention this Argument.';
        case 'ARGUMENT_TARGETED': return 'Another Argument still targets this Argument.';
        case 'ENTITY_NOT_FOUND': return 'This Argument no longer exists on PRKS.';
        case 'REVISION_CONFLICT': return 'This part changed on another device and needs a decision.';
        case 'FUTURE_REVISION': return 'This device has a newer revision than PRKS can accept.';
        default: return 'Could not ' + action + ' locally. Please retry.';
    }
}

function prksArgumentBaseUnavailable(what) {
    const err = new Error(
        'This Argument\u2019s ' + what + ' cannot be safely saved because this device does not '
        + 'know its revision. Open it once while connected to PRKS and try again.');
    err.prksArgumentUnavailable = true;
    return err;
}

async function createArgument(payload) {
    const src = payload && typeof payload === 'object' ? payload : {};
    try {
        const op = await prksCreateArgumentDurably({
            name: src.name == null ? '' : String(src.name),
            kind: src.kind === 'stance' ? 'stance' : 'argument',
            main_text: src.main_text == null ? '' : String(src.main_text),
            sources: Array.isArray(src.sources) ? src.sources : [],
            targets: Array.isArray(src.targets) ? src.targets : [],
        });
        return op ? { id: op.entity_id, name: op.payload.name, kind: op.payload.kind,
            main_text: op.payload.main_text, sources: op.payload.sources,
            targets: op.payload.targets } : null;
    } catch (error) {
        throw new Error(prksArgumentSaveMessage(error, 'create this Argument'));
    }
}

async function updateArgument(id, payload, options) {
    const src = payload && typeof payload === 'object' ? payload : {};
    const draft = {};
    (PRKS_ARGUMENT_FIELDS || []).forEach(function (field) {
        if (Object.prototype.hasOwnProperty.call(src, field)) {
            draft[field] = src[field] == null ? '' : String(src[field]);
        }
    });
    if (!Object.keys(draft).length) return { id: id };
    const opts = options || {};
    const ops = Array.isArray(opts.operations) ? opts.operations
        : await prksDurableOperationsOrNone();
    const wholeBase = opts.base || await prksAcknowledgedArgumentBase(id, ops);
    if (!wholeBase) throw prksArgumentBaseUnavailable('fields');
    const fieldBase = wholeBase.fields || wholeBase;
    const changes = prksDirtyArgumentFields(id, draft, fieldBase, ops);
    try {
        for (const field of Object.keys(changes)) {
            await prksSaveArgumentFieldsDurably(id,
                { [field]: changes[field] }, { [field]: fieldBase[field] });
        }
    } catch (error) {
        throw new Error(prksArgumentSaveMessage(error, 'save this Argument'));
    }
    return { id: id };
}

async function deleteArgument(id) {
    try { await prksDeleteArgumentDurably(id); }
    catch (error) { throw new Error(prksArgumentSaveMessage(error, 'delete this Argument')); }
    return { status: 'deleted' };
}

async function putArgumentSources(id, sources, options) {
    const opts = options || {};
    const ops = Array.isArray(opts.operations) ? opts.operations
        : await prksDurableOperationsOrNone();
    const base = opts.base || await prksAcknowledgedArgumentBase(id, ops);
    if (!base || !base.sources) throw prksArgumentBaseUnavailable('sources');
    if (typeof prksDirtyArgumentSources === 'function' &&
        !prksDirtyArgumentSources(id, sources || [], base.sources, ops)) return { id: id };
    try { await prksSetArgumentSourcesDurably(id, sources || [], base.sources); }
    catch (error) { throw new Error(prksArgumentSaveMessage(error, 'save these sources')); }
    return { id: id };
}

async function putArgumentTargets(id, targets, options) {
    const opts = options || {};
    const ops = Array.isArray(opts.operations) ? opts.operations
        : await prksDurableOperationsOrNone();
    const base = opts.base || await prksAcknowledgedArgumentBase(id, ops);
    if (!base || !base.targets) throw prksArgumentBaseUnavailable('targets');
    if (typeof prksDirtyArgumentTargets === 'function' &&
        !prksDirtyArgumentTargets(id, targets || [], base.targets, ops)) return { id: id };
    try { await prksSetArgumentTargetsDurably(id, targets || [], base.targets); }
    catch (error) { throw new Error(prksArgumentSaveMessage(error, 'save these targets')); }
    return { id: id };
}

window.bulkUpdateWorks = bulkUpdateWorks;
async function fetchResearchGraph(opts) {
    const people = !!(opts && opts.people);
    const url = people ? '/api/research-graph?people=1' : '/api/research-graph';
    const errorOwner = prksApiErrorOwner(opts);
    try {
        const res = await prksRequest(url, { signal: prksApiSignal(opts) });
        if (res.status === 413) {
            const data = await res.json().catch(function () {
                return {};
            });
            const err = new Error('Graph is too large to render as a single snapshot.');
            err.httpStatus = 413;
            err.code = (data && data.code) || 'graph_too_large';
            err.node_count = data && data.node_count;
            err.edge_count = data && data.edge_count;
            throw err;
        }
        const data = await prksParseJsonResponse(res, null, 'research-graph.fetch', errorOwner);
        if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) {
            throw new Error('Could not load Research Graph.');
        }
        return data;
    } catch (e) {
        if (prksAbortFallback(e)) throw e;
        if (e && e.code === 'graph_too_large') throw e;
        prksSetApiError('research-graph', 'Could not load Research Graph.', '', errorOwner);
        prksReportApiClientError('research-graph.fetch');
        throw e;
    }
}

window.fetchFolders = fetchFolders;
window.fetchTags = fetchTags;
window.fetchSavedViews = fetchSavedViews;
window.fetchSavedView = fetchSavedView;
window.createSavedView = createSavedView;
window.updateSavedView = updateSavedView;
window.deleteSavedView = deleteSavedView;
window.prksMarkResearchGraphCoreChanged = prksMarkResearchGraphCoreChanged;
window.prksMarkResearchGraphPeopleChanged = prksMarkResearchGraphPeopleChanged;
window.prksMarkConceptsDomainChanged = prksMarkConceptsDomainChanged;
window.prksMarkWorkTitleChanged = prksMarkWorkTitleChanged;
window.prksMarkPositionsDomainChanged = prksMarkPositionsDomainChanged;
window.prksMarkArgumentsDomainChanged = prksMarkArgumentsDomainChanged;
window.prksArgumentSaveMessage = prksArgumentSaveMessage;
window.prksMarkPeopleDomainChanged = prksMarkPeopleDomainChanged;
window.prksMarkPersonGroupsDomainChanged = prksMarkPersonGroupsDomainChanged;
window.prksMarkPlaylistsDomainChanged = prksMarkPlaylistsDomainChanged;
window.prksMarkFoldersDomainChanged = prksMarkFoldersDomainChanged;
window.prksMarkWorksBrowseChanged = prksMarkWorksBrowseChanged;
window.prksMarkRecentChanged = prksMarkRecentChanged;
window.prksMarkRecentlyAddedChanged = prksMarkRecentlyAddedChanged;
window.prksMarkWorkBrowseDisplayChanged = prksMarkWorkBrowseDisplayChanged;
window.prksOfflineWasGuardRefusal = prksOfflineWasGuardRefusal;
window.deleteFolderCanonical = deleteFolderCanonical;
window.prksFolderSaveMessage = prksFolderSaveMessage;
window.prksFileWorkInFolder = prksFileWorkInFolder;
window.addTagToFolder = addTagToFolder;
window.removeTagFromFolder = removeTagFromFolder;
window.mergeTags = mergeTags;
window.prksMarkWorkRoleChanged = prksMarkWorkRoleChanged;
window.prksMarkWorkRoleDependenciesChanged = prksMarkWorkRoleDependenciesChanged;
window.prksMarkWorkAuthorDisplayChanged = prksMarkWorkAuthorDisplayChanged;
window.fetchConcepts = fetchConcepts;
window.fetchConcept = fetchConcept;
window.createConcept = createConcept;
window.updateConcept = updateConcept;
window.deleteConcept = deleteConcept;
window.putConceptParents = putConceptParents;
window.putConceptAliases = putConceptAliases;
window.fetchPositions = fetchPositions;
window.fetchPosition = fetchPosition;
window.createPosition = createPosition;
window.updatePosition = updatePosition;
window.deletePosition = deletePosition;
window.fetchArguments = fetchArguments;
window.fetchArgument = fetchArgument;
window.fetchArgumentVerdicts = fetchArgumentVerdicts;
window.createArgument = createArgument;
window.updateArgument = updateArgument;
window.deleteArgument = deleteArgument;
window.putArgumentSources = putArgumentSources;
window.putArgumentTargets = putArgumentTargets;
window.fetchResearchGraph = fetchResearchGraph;

(function prksPrefetchAppSettings() {
    void prksLoadAppSettings();
})();
