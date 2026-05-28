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

function prksSetApiError(context, message, requestId = '') {
    const payload = {
        context: String(context || 'request'),
        message: String(message || 'Request failed'),
        requestId: String(requestId || ''),
        at: Date.now(),
    };
    window.__prksLastApiError = payload;
    prksReportClientError({
        kind: 'api_client_error',
        message: payload.message,
        source: String(context || 'request'),
        request_id: payload.requestId,
    });
}

function prksConsumeApiError() {
    const payload = window.__prksLastApiError || null;
    window.__prksLastApiError = null;
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
        payload.message || '',
        payload.route || '',
        payload.source || '',
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
    return {
        kind: prksTrimClientErrorText(payload.kind || 'client_error', 64),
        message: prksTrimClientErrorText(payload.message || 'Unknown client error', 2000),
        stack: prksTrimClientErrorText(payload.stack || '', 8000),
        route: prksTrimClientErrorText(payload.route || window.location.hash || '', 512),
        source: prksTrimClientErrorText(payload.source || '', 512),
        request_id: prksTrimClientErrorText(payload.request_id || '', 64),
        client_time: new Date().toISOString(),
    };
}

function prksReportClientError(input) {
    const payload = prksBuildClientErrorPayload(input);
    if (!payload.message || !prksShouldReportClientError(payload)) {
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

/** Parse JSON body when response is OK; otherwise return fallback (same shape callers expect). */
async function prksParseJsonResponse(res, fallback, context = 'request') {
    const requestId = (res && res.headers && res.headers.get('X-Request-ID')) || '';
    if (!res.ok) {
        prksSetApiError(context, `Request failed (${res.status})`, requestId);
        prksReportClientError({
            kind: 'api_http_error',
            message: `HTTP ${res.status} while loading ${context}`,
            source: '/api',
            request_id: requestId,
        });
        return fallback;
    }
    try {
        return await res.json();
    } catch (e) {
        prksSetApiError(context, 'Received invalid server response.', requestId);
        prksReportClientError({
            kind: 'api_parse_error',
            message: `Invalid JSON response while loading ${context}`,
            source: '/api',
            stack: e && e.stack ? String(e.stack) : '',
            request_id: requestId,
        });
        return fallback;
    }
}

let _prksFetchWorksInFlight = null;

async function fetchWorks() {
    if (_prksFetchWorksInFlight) {
        return _prksFetchWorksInFlight;
    }
    _prksFetchWorksInFlight = (async () => {
        try {
            const res = await fetch('/api/works');
            const data = await prksParseJsonResponse(res, [], 'works');
            return Array.isArray(data) ? data : [];
        } catch (e) {
            prksSetApiError('works', 'Could not load files.');
            return [];
        } finally {
            _prksFetchWorksInFlight = null;
        }
    })();
    return _prksFetchWorksInFlight;
}
async function fetchFolders() {
    try {
        const res = await fetch('/api/folders', { cache: 'no-store' });
        const data = await prksParseJsonResponse(res, [], 'folders');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('folders', 'Could not load folders.');
        return [];
    }
}
async function fetchFolderDetails(id) {
    try {
        const res = await fetch('/api/folders/' + encodeURIComponent(id));
        return await prksParseJsonResponse(res, null, 'folder-details');
    } catch (e) {
        prksSetApiError('folder-details', 'Could not load folder details.');
        return null;
    }
}
async function fetchPersons() {
    try {
        const res = await fetch('/api/persons', { cache: 'no-store' });
        const data = await prksParseJsonResponse(res, [], 'persons');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('persons', 'Could not load people.');
        return [];
    }
}
async function fetchWorkDetails(id) {
    try {
        const res = await fetch('/api/works/' + encodeURIComponent(id));
        return await prksParseJsonResponse(res, null, 'work-details');
    } catch (e) {
        prksSetApiError('work-details', 'Could not load file details.');
        return null;
    }
}
async function fetchPersonDetails(id) {
    try {
        const res = await fetch('/api/persons/' + encodeURIComponent(id));
        return await prksParseJsonResponse(res, null, 'person-details');
    } catch (e) {
        prksSetApiError('person-details', 'Could not load person details.');
        return null;
    }
}
async function fetchPersonGroups() {
    try {
        const res = await fetch('/api/person-groups');
        const data = await prksParseJsonResponse(res, [], 'person-groups');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('person-groups', 'Could not load groups.');
        return [];
    }
}
async function fetchPersonGroupDetails(id) {
    try {
        const res = await fetch('/api/person-groups/' + encodeURIComponent(id));
        return await prksParseJsonResponse(res, null, 'person-group-details');
    } catch (e) {
        prksSetApiError('person-group-details', 'Could not load group details.');
        return null;
    }
}
async function fetchRecent() {
    try {
        const res = await fetch('/api/recent');
        const data = await prksParseJsonResponse(res, [], 'recent');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('recent', 'Could not load recent files.');
        return [];
    }
}
async function fetchRecentlyAdded() {
    try {
        const res = await fetch('/api/recently-added');
        const data = await prksParseJsonResponse(res, [], 'recently-added');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('recently-added', 'Could not load recently added files.');
        return [];
    }
}
async function fetchSearch(query, tagName, options = {}) {
    const author = options.author != null ? String(options.author).trim() : '';
    const publisher =
        options.publisher != null ? String(options.publisher).trim() : '';
    const any = options.any != null ? String(options.any).trim() : '';
    if (tagName) {
        try {
            const params = new URLSearchParams();
            params.set('tag', tagName);
            if (author) params.set('author', author);
            if (publisher) params.set('publisher', publisher);
            if (any && (any === '1' || any.toLowerCase() === 'true' || any.toLowerCase() === 'yes')) {
                params.set('any', '1');
            }
            const res = await fetch('/api/search?' + params.toString());
            const data = await prksParseJsonResponse(res, [], 'search');
            return Array.isArray(data) ? data : [];
        } catch (e) {
            prksSetApiError('search', 'Search request failed.');
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
        const res = await fetch(url);
        const data = await prksParseJsonResponse(res, [], 'search');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('search', 'Search request failed.');
        return [];
    }
}
async function fetchPublishersInUse() {
    try {
        const res = await fetch('/api/publishers?used=1');
        const data = await prksParseJsonResponse(res, [], 'publishers');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('publishers', 'Could not load publishers.');
        return [];
    }
}

async function fetchTags(options = {}) {
    const params = new URLSearchParams();
    if (options.used) {
        params.set('used', '1');
    }
    const q = params.toString() ? '?' + params.toString() : '';
    try {
        const res = await fetch('/api/tags' + q);
        const data = await prksParseJsonResponse(res, [], 'tags');
        return Array.isArray(data) ? data : [];
    } catch (e) {
        prksSetApiError('tags', 'Could not load tags.');
        return [];
    }
}

async function fetchProcessingFiles(options = {}) {
    const params = new URLSearchParams();
    if (options && options.rescan) {
        params.set('rescan', '1');
    }
    const q = params.toString() ? '?' + params.toString() : '';
    try {
        const res = await fetch('/api/processing-files' + q);
        const data = await prksParseJsonResponse(res, [], 'processing-files');
        return Array.isArray(data) ? data : [];
    } catch (_e) {
        prksSetApiError('processing-files', 'Could not load files for processing.');
        return [];
    }
}

async function patchProcessingFile(processingFileId, fields) {
    const res = await fetch('/api/processing-files/' + encodeURIComponent(processingFileId), {
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
    const res = await fetch('/api/processing-files/' + encodeURIComponent(processingFileId) + '/import', {
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
    __prksAppSettingsPromise = fetch('/api/settings')
        .then((r) => (r.ok ? r.json() : {}))
        .then((data) => {
            const raw =
                data && typeof data.annotation_author === 'string' ? data.annotation_author.trim() : '';
            let author = raw;
            try {
                const legacy = (localStorage.getItem(PRKS_LS_ANNOTATION_AUTHOR_LEGACY) || '').trim();
                if (!author && legacy) {
                    author = legacy;
                    fetch('/api/settings', {
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
    const res = await fetch('/api/settings', {
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
    const res = await fetch('/api/works/reindex-pdf-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not re-index PDF text.');
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
    const res = await fetch('/api/folders/' + encodeURIComponent(folderId) + '/works', {
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
    const res = await fetch('/api/works/' + encodeURIComponent(workId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: folderIdOrNull }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update folder.');
    }
}

async function createFolder(title, description = '', options = {}) {
    const parentIdRaw = options && Object.prototype.hasOwnProperty.call(options, 'parent_id')
        ? options.parent_id
        : '';
    const parentId = parentIdRaw == null ? null : String(parentIdRaw).trim();
    const res = await fetch('/api/folders', {
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
    const res = await fetch('/api/folders/' + encodeURIComponent(folderId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || 'Could not update folder.');
    }
}

(function prksPrefetchAppSettings() {
    void prksLoadAppSettings();
})();
