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
async function fetchFolders(options = {}) {
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
 * Shared refusal boundary for the canonical Folder wrappers. The guard itself
 * tells the user why, so the thrown error carries a marker letting call sites
 * skip a second, duplicate alert.
 */
function prksGuardFolderMutation(message) {
    if (typeof prksOfflineGuardMutation !== 'function') return;
    if (!prksOfflineGuardMutation(message)) return;
    const err = new Error('Requires a connection to PRKS.');
    err.prksOfflineRefused = true;
    throw err;
}

/** True for an error thrown by prksGuardFolderMutation (already surfaced). */
function prksOfflineWasGuardRefusal(err) {
    return !!(err && err.prksOfflineRefused === true);
}

async function addWorkToFolder(folderId, workId) {
    prksGuardFolderMutation('Changing a file\'s folder requires a connection to PRKS.');
    const res = await prksRequest('/api/folders/' + encodeURIComponent(folderId) + '/works', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: workId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not add file to folder.');
    }
    prksMarkFoldersDomainChanged();
    // Only the Recently-added projection carries `folder_id` (it filters
    // locally over the folder title); the stable catalog deliberately does
    // not, because #/progress and #/types never render a folder.
    prksMarkRecentlyAddedChanged();
    return typeof prksOfflineMarkEntityChanged === 'function'
        ? prksOfflineMarkEntityChanged('work', workId)
        : null;
}

async function patchWorkFolder(workId, folderIdOrNull) {
    prksGuardFolderMutation('Changing a file\'s folder requires a connection to PRKS.');
    const res = await prksRequest('/api/works/' + encodeURIComponent(workId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: folderIdOrNull }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update folder.');
    }
    prksMarkFoldersDomainChanged();
    // Only the Recently-added projection carries `folder_id` (it filters
    // locally over the folder title); the stable catalog deliberately does
    // not, because #/progress and #/types never render a folder.
    prksMarkRecentlyAddedChanged();
    return typeof prksOfflineMarkEntityChanged === 'function'
        ? prksOfflineMarkEntityChanged('work', workId)
        : null;
}

async function createFolder(title, description = '', options = {}) {
    const parentIdRaw = options && Object.prototype.hasOwnProperty.call(options, 'parent_id')
        ? options.parent_id
        : '';
    const parentId = parentIdRaw == null ? null : String(parentIdRaw).trim();
    prksGuardFolderMutation('Creating a folder requires a connection to PRKS.');
    const res = await prksRequest('/api/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            title: (title || '').trim() || 'Untitled Folder',
            description: (description || '').trim(),
            parent_id: parentId || null,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not create folder.');
    }
    if (!data.id) {
        throw new Error('Could not create folder.');
    }
    prksMarkFoldersDomainChanged();
    return data.id;
}

async function patchFolder(folderId, updates) {
    prksGuardFolderMutation('Editing a folder requires a connection to PRKS.');
    const res = await prksRequest('/api/folders/' + encodeURIComponent(folderId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update folder.');
    }
    prksMarkFoldersDomainChanged();
    // Only a rename can stale a member Work's own cached detail (it embeds
    // folder_title). The canonical response reports exactly those members, so
    // description/private-notes/parent-only edits evict nothing extra and this
    // never depends on which page happened to be focused.
    if (typeof prksOfflineMarkEntityChanged === 'function' && Array.isArray(data.member_work_ids)) {
        data.member_work_ids.forEach(function (workId) {
            prksOfflineMarkEntityChanged('work', workId);
        });
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
    const payload = data && typeof data === 'object' ? data : {};
    const folders = Array.isArray(payload.affected_folder_ids) ? payload.affected_folder_ids : [];
    const works = Array.isArray(payload.affected_work_ids) ? payload.affected_work_ids : [];
    if (folders.length) prksMarkFoldersDomainChanged();
    if (works.length && typeof prksOfflineMarkEntityChanged === 'function') {
        works.forEach(function (workId) {
            prksOfflineMarkEntityChanged('work', workId);
        });
    }
    return payload;
}

async function deleteTag(tagId) {
    prksGuardFolderMutation('Deleting a tag requires a connection to PRKS.');
    const res = await prksRequest('/api/tags/' + encodeURIComponent(tagId), { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(data.error || 'Could not delete tag.');
        err.httpStatus = res.status;
        throw err;
    }
    return prksPublishTagCoherence(data);
}

async function mergeTags(sourceTagId, targetTagId) {
    prksGuardFolderMutation('Merging tags requires a connection to PRKS.');
    const res = await prksRequest('/api/tags/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_tag_id: sourceTagId, target_tag_id: targetTagId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(data.error || 'Could not merge tags.');
        err.httpStatus = res.status;
        throw err;
    }
    return prksPublishTagCoherence(data);
}

/** Canonical Folder deletion boundary (empty folders only, server-enforced). */
async function deleteFolderCanonical(folderId) {
    prksGuardFolderMutation('Deleting a folder requires a connection to PRKS.');
    const res = await prksRequest('/api/folders/' + encodeURIComponent(folderId), {
        method: 'DELETE',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(data.error || 'Could not delete folder.');
        err.httpStatus = res.status;
        throw err;
    }
    prksMarkFoldersDomainChanged();
    return data;
}

/* Folder tag membership is rendered by the Folder detail right panel, so both
 * directions are Folder-domain coherence boundaries. */
async function addTagToFolder(folderId, tagId) {
    prksGuardFolderMutation('Editing folder tags requires a connection to PRKS.');
    const res = await prksRequest('/api/folders/' + encodeURIComponent(folderId) + '/tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: tagId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not add tag.');
    prksMarkFoldersDomainChanged();
    return data;
}

async function removeTagFromFolder(folderId, tagId) {
    prksGuardFolderMutation('Editing folder tags requires a connection to PRKS.');
    const res = await prksRequest(
        '/api/folders/' + encodeURIComponent(folderId) + '/tags/' + encodeURIComponent(tagId),
        { method: 'DELETE' }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not remove tag.');
    prksMarkFoldersDomainChanged();
    return data;
}

async function bulkUpdateWorks(payload) {
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

async function createSavedView(payload) {
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
    const res = await prksRequest('/api/saved-views/' + encodeURIComponent(id), {
        method: 'DELETE',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error((data && data.error) || 'Could not delete Saved View.');
    }
}

/* Group operations publish coherence only after acknowledged canonical success. */
async function createPersonGroup(payload) {
    if (typeof prksOfflineGuardMutation === 'function' &&
        prksOfflineGuardMutation('Creating a Person Group requires a connection to PRKS.')) {
        return { ok: false, data: { error: 'Requires a connection to PRKS.' } };
    }
    const res = await prksRequest('/api/person-groups', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) prksMarkPersonGroupsDomainChanged();
    return { ok: res.ok, data };
}

async function updatePersonGroup(groupId, payload) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) {
        return { ok: false, data: { error: 'Requires a connection to PRKS.' } };
    }
    const res = await prksRequest('/api/person-groups/' + encodeURIComponent(groupId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    // Conservative: a rename directly stales the Group chips embedded in
    // People, and a description/hierarchy-only edit invalidating too is an
    // acceptable Phase-1 cost against field-diffing this shape.
    if (res.ok) {
        prksMarkPeopleDomainChanged();
        prksMarkPersonGroupsDomainChanged();
    }
    return { ok: res.ok, data: data };
}

async function deletePersonGroup(groupId) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) {
        return { ok: false, data: { error: 'Requires a connection to PRKS.' } };
    }
    const res = await prksRequest('/api/person-groups/' + encodeURIComponent(groupId), {
        method: 'DELETE',
    });
    const data = await res.json().catch(() => ({}));
    // Deleting a Group removes memberships from every Person that was in it.
    if (res.ok) {
        prksMarkPeopleDomainChanged();
        prksMarkPersonGroupsDomainChanged();
    }
    return { ok: res.ok, data: data };
}

async function addPersonGroupMember(groupId, personId) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) {
        return { ok: false, data: { error: 'Requires a connection to PRKS.' } };
    }
    const res = await prksRequest('/api/person-groups/' + encodeURIComponent(groupId) + '/members', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ person_id: personId }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        prksMarkPeopleDomainChanged();
        prksMarkPersonGroupsDomainChanged();
    }
    return { ok: res.ok, data: data };
}

async function removePersonGroupMember(groupId, personId) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) {
        return { ok: false, data: { error: 'Requires a connection to PRKS.' } };
    }
    const res = await prksRequest(
        '/api/person-groups/' +
            encodeURIComponent(groupId) +
            '/members/' +
            encodeURIComponent(personId),
        { method: 'DELETE' }
    );
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        prksMarkPeopleDomainChanged();
        prksMarkPersonGroupsDomainChanged();
    }
    return { ok: res.ok, data: data };
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

async function fetchConcepts(options = {}) {
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
 * though no Position record moved. Phase 1 is deliberately conservative: any
 * successful call to one of those helpers invalidates the whole Positions
 * domain rather than working out which Positions were actually affected.
 * Independent of the Concepts domain by construction -- see AGENTS.md
 * "Offline coherence domains".
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
 * See AGENTS.md, "Offline browse catalogs".
 * ------------------------------------------------------------------------ */

/**
 * Canonical "the user opened this Work" event.
 *
 * `GET /api/works/:id` is a pure read, so this is the ONLY thing that
 * reorders Recent. Call it for genuine foreground Work navigation and never
 * for an internal refresh -- a post-save reload, a folder/playlist/tag/role
 * refresh or a cache revalidation must not make a Work look "recently
 * opened" to the user, nor stale `recent:index` behind their back.
 *
 * Best-effort by design: failing to record an open must never break opening
 * the Work. Recent coherence is published only on acknowledged success.
 */
async function markWorkOpened(workId) {
    const id = String(workId || '').trim();
    if (!id) return false;
    let res;
    try {
        // The POST dispatcher requires a JSON content type (CSRF posture), so
        // send an empty object even though the event carries no payload.
        res = await prksRequest('/api/works/' + encodeURIComponent(id) + '/opened', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
    } catch (e) {
        return false;
    }
    if (!res || !res.ok) return false;
    prksMarkRecentChanged();
    return true;
}

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
 * stale it too -- see AGENTS.md, "Offline coherence domains".
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
function prksMarkWorkRoleChanged(workId, roleType) {
    prksMarkPersonGroupsDomainChanged();
    const token =
        typeof prksOfflineMarkEntityChanged === 'function'
            ? prksOfflineMarkEntityChanged('work', workId)
            : null;
    prksMarkPeopleDomainChanged();
    const role = String(roleType || '').trim();
    if (role === 'Author') {
        prksMarkResearchGraphPeopleChanged();
        prksMarkArgumentsDomainChanged();
    }
    // A Folder Work card's credit line is linked_authors -> author_text ->
    // primary_editor, so Editor changes stale Folders even though they touch
    // neither Arguments nor the Graph. Other roles are not rendered there.
    if (role === 'Author' || role === 'Editor') {
        prksMarkFoldersDomainChanged();
        prksMarkWorkBrowseDisplayChanged();
    }
    return token;
}

/** Former name of prksMarkWorkRoleChanged; kept as a thin delegate only. */
function prksMarkWorkAuthorDisplayChanged(workId, roleType) {
    return prksMarkWorkRoleChanged(workId, roleType);
}

async function createConcept(payload) {
    const res = await prksRequest('/api/concepts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await prksResearchJson(res, 'Could not create Concept.', 'concepts.create');
    prksMarkConceptsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function updateConcept(id, payload) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await prksResearchJson(res, 'Could not update Concept.', 'concepts.update');
    prksMarkConceptsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function deleteConcept(id) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id), { method: 'DELETE' });
    const data = await prksResearchJson(res, 'Could not delete Concept.', 'concepts.delete');
    prksMarkConceptsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function putConceptParents(id, parentIds) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id) + '/parents', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_ids: parentIds || [] }),
    });
    const data = await prksResearchJson(res, 'Could not update Concept parents.', 'concepts.parents');
    prksMarkConceptsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function putConceptAliases(id, aliases) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id) + '/aliases', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aliases: aliases || [] }),
    });
    // Aliases affect note resolution, not Graph labels or explicit hierarchy.
    const data = await prksResearchJson(res, 'Could not update Concept aliases.', 'concepts.aliases');
    prksMarkConceptsDomainChanged();
    return data;
}

async function fetchPositions(options = {}) {
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

async function createPosition(payload) {
    const res = await prksRequest('/api/positions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await prksResearchJson(res, 'Could not create Position.', 'positions.create');
    prksMarkPositionsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function updatePosition(id, payload) {
    const res = await prksRequest('/api/positions/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const data = await prksResearchJson(res, 'Could not update Position.', 'positions.update');
    prksMarkPositionsDomainChanged();
    // A cached Argument's targets embed the Position's name, so a rename stales
    // Arguments too. Create/delete do not: a brand-new Position cannot already
    // be targeted, and a targeted Position cannot be deleted.
    prksMarkArgumentsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function deletePosition(id) {
    const res = await prksRequest('/api/positions/' + encodeURIComponent(id), { method: 'DELETE' });
    const data = await prksResearchJson(res, 'Could not delete Position.', 'positions.delete');
    prksMarkPositionsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function fetchArguments(kind, options = {}) {
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

/* Argument mutations below additionally invalidate the POSITIONS domain: a
 * cached Position detail embeds its targeting Arguments/Stances by name, kind
 * and verdict, so those cached Positions go stale whenever an Argument is
 * created, edited, retargeted or deleted. The Arguments domain itself is
 * invalidated alongside it in each mutation below. `putArgumentSources` marks
 * Arguments but deliberately NOT Positions -- source Works are not part of the
 * Position read model. */

async function createArgument(payload) {
    const res = await prksRequest('/api/arguments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    // A create payload may already carry Position targets.
    const data = await prksResearchJson(res, 'Could not create Argument.', 'arguments.create');
    prksMarkArgumentsDomainChanged();
    prksMarkPositionsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function updateArgument(id, payload) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    // name/kind are both displayed in a Position's Arguments & Stances list --
    // and in every other Argument that targets or responds to this one, which
    // is why the whole Arguments domain goes rather than one row.
    const data = await prksResearchJson(res, 'Could not update Argument.', 'arguments.update');
    prksMarkArgumentsDomainChanged();
    prksMarkPositionsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function deleteArgument(id) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id), { method: 'DELETE' });
    // A deleted Argument must stop appearing in a cached Position's list, and in
    // any cached Argument that targeted or was answered by it.
    const data = await prksResearchJson(res, 'Could not delete Argument.', 'arguments.delete');
    prksMarkArgumentsDomainChanged();
    prksMarkPositionsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function putArgumentSources(id, sources) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id) + '/sources', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sources: sources || [] }),
    });
    // Arguments only: source Works (and their authors) are part of the Argument
    // read model, and deliberately NOT of the Position one.
    const data = await prksResearchJson(res, 'Could not update Argument sources.', 'arguments.sources');
    prksMarkArgumentsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
}

async function putArgumentTargets(id, targets) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id) + '/targets', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targets: targets || [] }),
    });
    // Changes Position membership and per-Position verdict, and on the Argument
    // side both this Argument's targets and the target's responses list.
    const data = await prksResearchJson(res, 'Could not update Argument targets.', 'arguments.targets');
    prksMarkArgumentsDomainChanged();
    prksMarkPositionsDomainChanged();
    prksMarkResearchGraphCoreChanged();
    return data;
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
window.prksMarkPeopleDomainChanged = prksMarkPeopleDomainChanged;
window.prksMarkPersonGroupsDomainChanged = prksMarkPersonGroupsDomainChanged;
window.prksMarkPlaylistsDomainChanged = prksMarkPlaylistsDomainChanged;
window.prksMarkFoldersDomainChanged = prksMarkFoldersDomainChanged;
window.prksMarkWorksBrowseChanged = prksMarkWorksBrowseChanged;
window.prksMarkRecentChanged = prksMarkRecentChanged;
window.markWorkOpened = markWorkOpened;
window.prksMarkRecentlyAddedChanged = prksMarkRecentlyAddedChanged;
window.prksMarkWorkBrowseDisplayChanged = prksMarkWorkBrowseDisplayChanged;
window.prksOfflineWasGuardRefusal = prksOfflineWasGuardRefusal;
window.deleteFolderCanonical = deleteFolderCanonical;
window.addTagToFolder = addTagToFolder;
window.removeTagFromFolder = removeTagFromFolder;
window.deleteTag = deleteTag;
window.mergeTags = mergeTags;
window.createPersonGroup = createPersonGroup;
window.updatePersonGroup = updatePersonGroup;
window.deletePersonGroup = deletePersonGroup;
window.addPersonGroupMember = addPersonGroupMember;
window.removePersonGroupMember = removePersonGroupMember;
window.prksMarkWorkRoleChanged = prksMarkWorkRoleChanged;
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
