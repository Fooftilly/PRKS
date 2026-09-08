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

async function addWorkToFolder(folderId, workId) {
    const res = await prksRequest('/api/folders/' + encodeURIComponent(folderId) + '/works', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: workId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not add file to folder.');
    }
}

async function patchWorkFolder(workId, folderIdOrNull) {
    const res = await prksRequest('/api/works/' + encodeURIComponent(workId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: folderIdOrNull }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update folder.');
    }
    return typeof prksOfflineMarkEntityChanged === 'function'
        ? prksOfflineMarkEntityChanged('work', workId)
        : null;
}

async function createFolder(title, description = '', options = {}) {
    const parentIdRaw = options && Object.prototype.hasOwnProperty.call(options, 'parent_id')
        ? options.parent_id
        : '';
    const parentId = parentIdRaw == null ? null : String(parentIdRaw).trim();
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
    return data.id;
}

async function patchFolder(folderId, updates) {
    const res = await prksRequest('/api/folders/' + encodeURIComponent(folderId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update folder.');
    }
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

async function createConcept(payload) {
    const res = await prksRequest('/api/concepts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    return prksResearchJson(res, 'Could not create Concept.', 'concepts.create');
}

async function updateConcept(id, payload) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    return prksResearchJson(res, 'Could not update Concept.', 'concepts.update');
}

async function deleteConcept(id) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id), { method: 'DELETE' });
    return prksResearchJson(res, 'Could not delete Concept.', 'concepts.delete');
}

async function putConceptParents(id, parentIds) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id) + '/parents', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_ids: parentIds || [] }),
    });
    return prksResearchJson(res, 'Could not update Concept parents.', 'concepts.parents');
}

async function putConceptAliases(id, aliases) {
    const res = await prksRequest('/api/concepts/' + encodeURIComponent(id) + '/aliases', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aliases: aliases || [] }),
    });
    return prksResearchJson(res, 'Could not update Concept aliases.', 'concepts.aliases');
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
    return prksResearchJson(res, 'Could not create Position.', 'positions.create');
}

async function updatePosition(id, payload) {
    const res = await prksRequest('/api/positions/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    return prksResearchJson(res, 'Could not update Position.', 'positions.update');
}

async function deletePosition(id) {
    const res = await prksRequest('/api/positions/' + encodeURIComponent(id), { method: 'DELETE' });
    return prksResearchJson(res, 'Could not delete Position.', 'positions.delete');
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

async function createArgument(payload) {
    const res = await prksRequest('/api/arguments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    return prksResearchJson(res, 'Could not create Argument.', 'arguments.create');
}

async function updateArgument(id, payload) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    return prksResearchJson(res, 'Could not update Argument.', 'arguments.update');
}

async function deleteArgument(id) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id), { method: 'DELETE' });
    return prksResearchJson(res, 'Could not delete Argument.', 'arguments.delete');
}

async function putArgumentSources(id, sources) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id) + '/sources', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sources: sources || [] }),
    });
    return prksResearchJson(res, 'Could not update Argument sources.', 'arguments.sources');
}

async function putArgumentTargets(id, targets) {
    const res = await prksRequest('/api/arguments/' + encodeURIComponent(id) + '/targets', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targets: targets || [] }),
    });
    return prksResearchJson(res, 'Could not update Argument targets.', 'arguments.targets');
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
