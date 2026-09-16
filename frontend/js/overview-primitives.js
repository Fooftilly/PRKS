/**
 * Shared visual-overview / context summary primitives.
 *
 * Dense, flat, information-first strips for page headers, identity, Details,
 * collections, and nav attention. Never invent counts: omit unknown parts;
 * unknown is not zero.
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

    /**
     * Normalize one summary part.
     * Accepts: string | number | { text, href?, html?, unknown? } | null/undefined/false.
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
        if (part.html != null && String(part.html).trim()) return String(part.html);
        const text = part.text != null ? String(part.text).trim() : '';
        if (!text) return '';
        const escaped = escapeText(text);
        if (part.href) {
            const href = String(part.href);
            if (href.charAt(0) === '#' || href.indexOf('/') === 0) {
                return (
                    '<a class="prks-summary-link" href="' +
                    escapeText(href) +
                    '">' +
                    escaped +
                    '</a>'
                );
            }
        }
        return escaped;
    }

    function joinSummaryParts(parts, sepHtml) {
        const list = Array.isArray(parts) ? parts : [];
        const bits = [];
        for (let i = 0; i < list.length; i += 1) {
            const bit = normalizePart(list[i]);
            if (bit) bits.push(bit);
        }
        if (!bits.length) return '';
        const sep =
            sepHtml != null
                ? String(sepHtml)
                : '<span class="prks-summary-sep" aria-hidden="true"> · </span>';
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

    /**
     * Collection filter / result scope: "12 of 48 matching" / "48 Concepts".
     * When total is unknown, omit rather than inventing 0.
     */
    function prksScopeLineHtml(options) {
        const opts = options || {};
        const parts = [];
        const label = opts.label != null ? String(opts.label).trim() : '';
        const totalKnown = opts.total != null && Number.isFinite(Number(opts.total));
        const shownKnown = opts.shown != null && Number.isFinite(Number(opts.shown));
        const filterOn = !!(opts.filter && String(opts.filter).trim());

        if (shownKnown && totalKnown && filterOn) {
            const shown = Number(opts.shown);
            const total = Number(opts.total);
            parts.push(shown + ' of ' + total + ' matching');
        } else if (totalKnown) {
            const total = Number(opts.total);
            if (label) parts.push(total + ' ' + label);
            else parts.push(String(total));
        } else if (shownKnown && filterOn) {
            parts.push(Number(opts.shown) + ' matching');
        } else if (opts.unavailable) {
            parts.push(opts.unavailableText || 'Not available offline');
        }

        if (opts.extra) {
            const extras = Array.isArray(opts.extra) ? opts.extra : [opts.extra];
            for (let i = 0; i < extras.length; i += 1) parts.push(extras[i]);
        }

        const inner = joinSummaryParts(parts);
        return wrapSummary('prks-scope-line', inner, {
            className: opts.className,
            role: 'status',
            ariaLabel: opts.ariaLabel,
        });
    }

    /** Relationship strip near entity identity (folder · people · tags). */
    function prksRelSummaryHtml(options) {
        const opts = options || {};
        const inner = joinSummaryParts(opts.parts);
        return wrapSummary('prks-rel-summary', inner, opts);
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

    /** Paint a stable `[data-prks-role="index-scope-host"]` with a scope line. */
    function prksPaintScopeHost(root, options) {
        if (!root || !root.querySelector) return;
        const host =
            root.getAttribute && root.getAttribute('data-prks-role') === 'index-scope-host'
                ? root
                : root.querySelector('[data-prks-role="index-scope-host"]');
        if (!host) return;
        host.innerHTML = prksScopeLineHtml(options);
    }

    const api = {
        prksJoinSummaryParts: joinSummaryParts,
        prksPageSummaryHtml: prksPageSummaryHtml,
        prksScopeLineHtml: prksScopeLineHtml,
        prksRelSummaryHtml: prksRelSummaryHtml,
        prksStateSummaryHtml: prksStateSummaryHtml,
        prksNavAttentionBadgeHtml: prksNavAttentionBadgeHtml,
        prksPaintScopeHost: prksPaintScopeHost,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
