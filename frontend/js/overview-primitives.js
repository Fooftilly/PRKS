/**
 * Shared visual-overview / context summary primitives.
 *
 * Dense, flat, information-first strips for classic page headers, Details,
 * and nav attention. Vue route surfaces use `PrksScopeLine` and
 * `PrksRelSummary` for collection scope and relationship strips. Never
 * invent counts: omit unknown parts; unknown is not zero.
 *
 * Parts are typed plain text (and narrowly validated internal links only).
 * No raw HTML hatch — callers must not pass prebuilt markup.
 */
(function (root) {
    'use strict';

    function escapeText(value) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(value);
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /** Allow only same-app hash routes or root-relative paths. */
    function isSafeInternalHref(href) {
        if (typeof href !== 'string') return false;
        const h = href.trim();
        // `\` reads as `/` and tabs/newlines are dropped by the URL parser.
        if (!h || /[\\\u0000-\u001f\u007f]/.test(h)) return false;
        if (h.charAt(0) === '#') {
            return h.indexOf('javascript:') === -1 && h.indexOf('data:') === -1;
        }
        if (h.charAt(0) === '/' && h.charAt(1) !== '/') {
            return h.indexOf('javascript:') === -1 && h.indexOf('data:') === -1;
        }
        return false;
    }

    /**
     * Normalize one summary part.
     * Accepts: string | number | { text, href?, unknown? } | null/undefined/false.
     * Returns escaped HTML fragment or '' (omit).
     * Numbers are only used when Number.isFinite — never coerce NaN/null to 0.
     */
    function normalizePart(part) {
        if (part == null || part === false) return '';
        if (typeof part === 'number') {
            if (!Number.isFinite(part)) return '';
            return escapeText(String(part));
        }
        if (typeof part === 'string') {
            const t = part.trim();
            return t ? escapeText(t) : '';
        }
        if (typeof part !== 'object') return '';
        if (part.unknown === true) return '';
        /* Reject legacy html / raw markup keys — plain text only. */
        if (Object.prototype.hasOwnProperty.call(part, 'html')) return '';
        const text = part.text != null ? String(part.text).trim() : '';
        if (!text) return '';
        const escaped = escapeText(text);
        if (part.href != null && isSafeInternalHref(String(part.href))) {
            return (
                '<a class="prks-summary-link" href="' +
                escapeText(String(part.href).trim()) +
                '">' +
                escaped +
                '</a>'
            );
        }
        return escaped;
    }

    function joinSummaryParts(parts) {
        const list = Array.isArray(parts) ? parts : [];
        const bits = [];
        for (let i = 0; i < list.length; i += 1) {
            const bit = normalizePart(list[i]);
            if (bit) bits.push(bit);
        }
        if (!bits.length) return '';
        const sep = '<span class="prks-summary-sep" aria-hidden="true"> · </span>';
        return bits.join(sep);
    }

    function wrapSummary(className, inner, options) {
        if (!inner) return '';
        const opts = options || {};
        const role = opts.role ? ' role="' + escapeText(opts.role) + '"' : '';
        const aria = opts.ariaLabel
            ? ' aria-label="' + escapeText(opts.ariaLabel) + '"'
            : '';
        const extra = opts.className ? ' ' + String(opts.className).trim() : '';
        return (
            '<p class="' +
            className +
            extra +
            '"' +
            role +
            aria +
            '>' +
            inner +
            '</p>'
        );
    }

    /** Page-header optional summary/count under or beside the title. */
    function prksPageSummaryHtml(options) {
        const opts = options || {};
        const inner = joinSummaryParts(opts.parts);
        return wrapSummary('prks-page-summary', inner, opts);
    }

    /** Compact state summary for Details / right panel (status · type · counts). */
    function prksStateSummaryHtml(options) {
        const opts = options || {};
        const inner = joinSummaryParts(opts.parts);
        return wrapSummary('prks-state-summary', inner, opts);
    }

    /**
     * Restrained nav attention badge. Count must be a finite number > 0 to show.
     * Never renders a "0" badge for unknown inbox/sync state.
     */
    function prksNavAttentionBadgeHtml(options) {
        const opts = options || {};
        if (opts.unknown === true) return '';
        const count = opts.count;
        if (count == null || !Number.isFinite(Number(count))) return '';
        const n = Number(count);
        if (n <= 0) return '';
        const label = opts.label != null ? String(opts.label) : String(n);
        const aria =
            opts.ariaLabel != null
                ? String(opts.ariaLabel)
                : label + (opts.unit ? ' ' + opts.unit : '');
        return (
            '<span class="prks-nav-attention"' +
            (aria ? ' aria-label="' + escapeText(aria) + '"' : '') +
            '>' +
            '<span class="prks-nav-attention__count">' +
            escapeText(String(n)) +
            '</span>' +
            '</span>'
        );
    }

    const api = {
        prksJoinSummaryParts: joinSummaryParts,
        prksPageSummaryHtml: prksPageSummaryHtml,
        prksStateSummaryHtml: prksStateSummaryHtml,
        prksNavAttentionBadgeHtml: prksNavAttentionBadgeHtml,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
