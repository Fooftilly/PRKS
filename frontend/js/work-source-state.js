/**
 * Work SOURCE identity as an aggregate: the SET_WORK_SOURCE family.
 *
 * Every synchronized Work VALUE is a field with its own revision. A video's
 * source is not one value: `source_kind`, `provider`, `provider_id` and
 * `source_url` describe one thing together, and `provider_id` outranks the URL
 * when the viewer builds its embed. Three field-scoped operations would let
 * two ordinary edits reach "the stored URL names video B while the viewer
 * plays video A", and would ask the user to resolve one decision three times.
 *
 * So this module owns the canonical identity parser (mirroring
 * backend/work_source_sync.py), the pending source map rebuilt from the
 * durable queue, the effective source overlay -- which produces ALL FOUR
 * columns together -- and the family's sync handler.
 */
(function (root) {
    'use strict';

    /* Explicit recognized hosts, never substring-matched: that would accept
     * "notyoutube.com" and "youtube.com.example.org". Shared contract with
     * `backend/work_source_sync.YOUTUBE_HOSTS` and works-video.js. */
    const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be']);

    /* The canonical provider-id contract. Mirrors
     * `backend/work_source_sync.MAX_PROVIDER_ID_CHARS` and its alphabet.
     *
     * `provider_id` is an IDENTIFIER, and this is what makes it one. Two
     * promises depend on it being bounded, and they are only simultaneously
     * keepable if it is: a conflict reports the server's identity EXACTLY
     * (a truncated video id names a different video, or none), and a terminal
     * result always fits the durable 2 KiB bound so it can always be stored
     * and therefore always be resolved. Unbounded, those contradict.
     *
     * The alphabet also settles percent-encoding: `searchParams` decodes and
     * `pathname` does not, so a spelling the two sides might read differently
     * contains `%`, which is not a legal identifier character on either. */
    const MAX_PROVIDER_ID_CHARS = 512;
    const PROVIDER_ID_RE = new RegExp('^[A-Za-z0-9_-]{1,' + MAX_PROVIDER_ID_CHARS + '}$');

    /** True for a value this system is willing to call a video identity. */
    function isProviderId(value) {
        return typeof value === 'string' && PROVIDER_ID_RE.test(value);
    }

    /**
     * The video id in any URL spelling PRKS accepts, or ''.
     *
     * Mirrors the server parser exactly. Two parsers that disagree would show
     * up as a Work whose stored URL and stored id name different videos --
     * which is the whole reason this is an aggregate. A URL whose id is not a
     * well-formed identifier has no video id here: it is refused rather than
     * carried, so nothing can hold an identity the protocol cannot report back
     * intact.
     */
    function youtubeVideoId(url) {
        let parsed;
        try {
            parsed = new URL(String(url || '').trim());
        } catch (_e) {
            return '';
        }
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
        const host = String(parsed.hostname || '').toLowerCase();
        if (!YOUTUBE_HOSTS.has(host)) return '';
        const bounded = id => (isProviderId(id) ? id : '');
        if (host === 'youtu.be') {
            return bounded(parsed.pathname.replace(/^\//, '').split('/')[0].trim());
        }
        const v = String(parsed.searchParams.get('v') || '').trim();
        if (v) return bounded(v);
        const parts = parsed.pathname.replace(/^\//, '').split('/');
        if (parts[0] === 'embed' && parts[1]) return bounded(parts[1].trim());
        return '';
    }

    /**
     * User intent -> the canonical identity, or null when it is not one.
     *
     * `provider` and `provider_id` are DERIVED, never taken from a caller: a
     * caller able to assert them could assert an identity its own URL
     * contradicts.
     */
    function canonicalSource(url) {
        const text = String(url == null ? '' : url).trim();
        if (!text) return null;
        const videoId = youtubeVideoId(text);
        if (!videoId) return null;
        return {
            source_kind: 'video',
            provider: 'youtube',
            provider_id: videoId,
            source_url: text,
        };
    }

    /**
     * The identity a conflict result reports, in the SAME spelling
     * `identityOf` produces -- so a base established from a conflict and a
     * base established from a Work record are comparable strings rather than
     * two shapes a caller has to know apart.
     */
    function conflictIdentity(result) {
        if (!result || typeof result.current_provider !== 'string' ||
            typeof result.current_provider_id !== 'string') return '';
        return identityOf({ provider: result.current_provider,
            provider_id: result.current_provider_id });
    }

    /** What makes two sources THE SAME source. Never the URL spelling. */
    function identityOf(source) {
        if (!source) return '';
        return String(source.provider || '').toLowerCase() + ' ' +
            String(source.provider_id || '');
    }

    function sourceOfWork(work) {
        if (!work) return null;
        return {
            source_kind: String(work.source_kind || '').trim().toLowerCase(),
            provider: String(work.provider || '').trim().toLowerCase(),
            provider_id: String(work.provider_id || '').trim(),
            source_url: work.source_url || '',
        };
    }

    /* ---- the pending source map ---- */

    let pendingByWork = new Map();
    let pendingGeneration = 0;

    function sourceOperations(rows, workId) {
        return (rows || []).filter(op => op && op.operation === 'SET_WORK_SOURCE' &&
            op.entity_type === 'work' && op.entity_id === workId &&
            op.status !== 'acknowledged');
    }

    function setPending(rows) {
        const next = new Map();
        (rows || []).filter(op => op && op.operation === 'SET_WORK_SOURCE' &&
            op.entity_type === 'work' && op.status !== 'acknowledged').forEach(op => {
                const source = canonicalSource(op.payload && op.payload.source &&
                    op.payload.source.url);
                if (source) next.set(op.entity_id, source);
            });
        pendingByWork = next;
        pendingGeneration += 1;
        return pendingGeneration;
    }

    async function refreshPending() {
        if (!root.prksSync) return [];
        let rows;
        try {
            rows = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPending(rows);
        return rows;
    }

    /**
     * Acknowledged Work + pending source = what the user should see.
     *
     * ALL FOUR columns move together. Overlaying only `source_url` onto
     * acknowledged provider fields is precisely the inconsistency this
     * operation exists to prevent -- the card and the viewer would disagree
     * about which video this is.
     */
    function effectiveWorkSource(work) {
        if (!work || typeof work.id !== 'string' || !pendingByWork.size) return work;
        const pending = pendingByWork.get(work.id);
        if (!pending) return work;
        return Object.assign({}, work, pending);
    }

    function effectiveWorkSources(rows) {
        if (!Array.isArray(rows) || !pendingByWork.size) return rows;
        return rows.map(row => (row && row.id ? effectiveWorkSource(row) : row));
    }

    /* ---- the sync handler ---- */

    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id) return false;
        const has = key => Object.prototype.hasOwnProperty.call(data, key);
        switch (data.code) {
            case 'ACKNOWLEDGED': {
                if (typeof data.changed !== 'boolean' ||
                    !Number.isSafeInteger(data.server_revision) ||
                    data.server_revision < 0) return false;
                /* The acknowledgement STATES the stored row, and the client
                 * copies it. Two of these columns cannot be derived here at
                 * all -- `urldate` is the server's date, `thumb_url` is
                 * cleared by the write -- and on a convergent write the stored
                 * URL is deliberately NOT the one this operation asked for. */
                const nullableString = v => v === null || typeof v === 'string';
                if (typeof data.source_url !== 'string' ||
                    !nullableString(data.urldate) ||
                    !nullableString(data.thumb_url)) return false;
                /* The echoed columns must be internally coherent: a row whose
                 * URL and whose id name different videos is the exact
                 * contradiction this aggregate exists to prevent, and it is
                 * not made acceptable by arriving from the server. */
                const stored = canonicalSource(data.source_url);
                if (!stored || data.provider !== stored.provider ||
                    data.provider_id !== stored.provider_id ||
                    data.source_kind !== stored.source_kind) return false;
                /* And the server must have converged on the video THIS
                 * operation named. It answers `changed: false` only when the
                 * identities already match, so this holds either way; a
                 * different video is a protocol error, not an acknowledgement.
                 */
                const requested = canonicalSource(op.payload && op.payload.source &&
                    op.payload.source.url);
                return !!requested && identityOf(stored) === identityOf(requested);
            }
            case 'SOURCE_REVISION_CONFLICT': case 'FUTURE_REVISION':
                /* The preview is DISPLAY TEXT -- bounded, and shortened further
                 * whenever the whole result would not fit the durable limit --
                 * so nothing may be derived from it. The IDENTITY is exact and
                 * is what a reapply measures its next edit against. */
                return Number.isSafeInteger(data.current_revision) &&
                    typeof data.current_provider === 'string' &&
                    typeof data.current_provider_id === 'string' &&
                    typeof data.current_preview === 'string' &&
                    Number.isSafeInteger(data.current_bytes) &&
                    Number.isSafeInteger(data.requested_bytes) && !has('source_url');
            case 'ENTITY_NOT_FOUND': case 'UNSUPPORTED_SOURCE_TRANSITION':
                return true;
            default: return false;
        }
    }

    /* Every terminal outcome is the user's to resolve: they chose this video
     * deliberately, so discarding it silently would lose a real decision. */
    function terminal(data) {
        const out = { code: data.code };
        for (const key of ['current_revision', 'current_provider', 'current_provider_id',
            'current_preview', 'current_bytes', 'requested_bytes']) {
            if (Object.prototype.hasOwnProperty.call(data, key)) out[key] = data[key];
        }
        return { conflict: out };
    }

    /** The acknowledged row, as the columns a cached Work carries. */
    function acknowledgedSource(data) {
        return {
            source_kind: data.source_kind,
            provider: data.provider,
            provider_id: data.provider_id,
            source_url: data.source_url,
            thumb_url: data.thumb_url,
            urldate: data.urldate,
        };
    }

    const handler = {
        isResult,
        terminal,
        /* Straight from the acknowledgement, never rebuilt from the local
         * operation: the two differ exactly when it matters, and the server is
         * the one that knows. No second request either -- the ACK already
         * carries the whole row. */
        reconcile: data => root.prksOfflineReconcileWorkSource({
            work_id: data.work_id,
            source: acknowledgedSource(data),
            server_revision: data.server_revision,
        }),
    };

    Object.assign(root, {
        PRKS_YOUTUBE_HOSTS: YOUTUBE_HOSTS,
        PRKS_MAX_PROVIDER_ID_CHARS: MAX_PROVIDER_ID_CHARS,
        prksIsProviderId: isProviderId,
        prksYoutubeVideoId: youtubeVideoId,
        prksCanonicalWorkSource: canonicalSource,
        prksWorkSourceIdentity: identityOf,
        prksWorkSourceConflictIdentity: conflictIdentity,
        prksWorkSourceOf: sourceOfWork,
        prksSetPendingWorkSources: setPending,
        prksRefreshPendingWorkSources: refreshPending,
        prksPendingWorkSourceGeneration: () => pendingGeneration,
        prksWorkSourceOperations: sourceOperations,
        prksEffectiveWorkSource: effectiveWorkSource,
        prksEffectiveWorkSources: effectiveWorkSources,
        prksAcknowledgedWorkSource: acknowledgedSource,
        prksWorkSourceSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
