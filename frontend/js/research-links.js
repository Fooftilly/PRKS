/**
 * Research-note semantic references: [[concept:Name]] and [[argument:A-ID|Label]].
 * Parser must agree with backend/research_markup.py.
 */
(function (root) {
    'use strict';

    const CONCEPT_REF_MAX = 160;
    const ARGUMENT_LABEL_MAX = 240;
    const ARGUMENT_ID_RE = /^A-[A-Za-z0-9]{1,32}$/;

    function canonicalConceptName(raw) {
        return String(raw == null ? '' : raw)
            .normalize('NFC')
            .trim()
            .replace(/\s+/g, ' ');
    }

    function normalizeConceptKey(raw) {
        return canonicalConceptName(raw).toLocaleLowerCase();
    }

    function atLineStart(text, i) {
        return i === 0 || text.charAt(i - 1) === '\n';
    }

    function openFence(text, i) {
        const n = text.length;
        let j = i;
        let spaces = 0;
        while (spaces < 3 && j < n && text.charAt(j) === ' ') {
            spaces += 1;
            j += 1;
        }
        if (j >= n) return null;
        const ch = text.charAt(j);
        if (ch !== '`' && ch !== '~') return null;
        let k = j;
        while (k < n && text.charAt(k) === ch) k += 1;
        const flen = k - j;
        if (flen < 3) return null;
        while (k < n && text.charAt(k) !== '\n') k += 1;
        if (k < n) k += 1;
        const closer = findFenceClose(text, k, ch, flen);
        return { start: i, closer: closer };
    }

    function findFenceClose(text, start, ch, flen) {
        const n = text.length;
        let i = start;
        while (i < n) {
            if (atLineStart(text, i)) {
                let j = i;
                let spaces = 0;
                while (spaces < 3 && j < n && text.charAt(j) === ' ') {
                    spaces += 1;
                    j += 1;
                }
                let k = j;
                while (k < n && text.charAt(k) === ch) k += 1;
                if (k - j >= flen) {
                    let rest = k;
                    while (rest < n && (text.charAt(rest) === ' ' || text.charAt(rest) === '\t')) rest += 1;
                    if (rest >= n || text.charAt(rest) === '\n') {
                        return rest >= n ? n : rest + 1;
                    }
                }
            }
            i += 1;
        }
        return n;
    }

    function closeInlineCode(text, i) {
        const n = text.length;
        if (text.charAt(i) !== '`') return null;
        let k = i;
        while (k < n && text.charAt(k) === '`') k += 1;
        const run = k - i;
        if (run >= 3 && atLineStart(text, i)) return null;
        let j = k;
        while (j < n) {
            if (text.charAt(j) === '`') {
                let m = j;
                while (m < n && text.charAt(m) === '`') m += 1;
                if (m - j === run) return m;
                j = m;
                continue;
            }
            j += 1;
        }
        return null;
    }

    function liveMask(text) {
        const n = text.length;
        const flags = new Array(n);
        for (let i = 0; i < n; i++) flags[i] = 'Y';
        let i = 0;
        while (i < n) {
            if (atLineStart(text, i)) {
                const fence = openFence(text, i);
                if (fence) {
                    for (let k = fence.start; k < fence.closer; k++) flags[k] = 'N';
                    i = fence.closer;
                    continue;
                }
            }
            if (text.charAt(i) === '`') {
                const end = closeInlineCode(text, i);
                if (end != null) {
                    for (let k = i; k < end; k++) flags[k] = 'N';
                    i = end;
                    continue;
                }
            }
            i += 1;
        }
        return flags.join('');
    }

    function spanLive(live, start, end) {
        for (let i = start; i < end; i++) {
            if (live.charAt(i) !== 'Y') return false;
        }
        return true;
    }

    function parseResearchMarkup(text) {
        const out = { conceptRefs: [], argumentRefs: [] };
        if (!text) return out;
        const live = liveMask(text);
        const n = text.length;
        let i = 0;
        while (i < n) {
            if (live.charAt(i) !== 'Y') {
                i += 1;
                continue;
            }
            if (text.charAt(i) === '\\' && i + 2 < n && text.charAt(i + 1) === '[' && text.charAt(i + 2) === '[') {
                i += 3;
                continue;
            }
            if (text.indexOf('[[concept:', i) === i) {
                const parsed = tryConcept(text, live, i);
                if (parsed) {
                    out.conceptRefs.push(parsed);
                    i = parsed.end;
                    continue;
                }
            }
            if (text.indexOf('[[argument:', i) === i) {
                const parsed = tryArgument(text, live, i);
                if (parsed) {
                    out.argumentRefs.push(parsed);
                    i = parsed.end;
                    continue;
                }
            }
            i += 1;
        }
        return out;
    }

    function tryConcept(text, live, start) {
        const innerStart = start + '[[concept:'.length;
        const close = text.indexOf(']]', innerStart);
        if (close < 0) return null;
        const end = close + 2;
        if (!spanLive(live, start, end)) return null;
        if (text.slice(start, end).indexOf('\n') >= 0) return null;
        const name = canonicalConceptName(text.slice(innerStart, close));
        if (!name || name.length > CONCEPT_REF_MAX) return null;
        return { start: start, end: end, raw: text.slice(start, end), name: name };
    }

    function tryArgument(text, live, start) {
        const innerStart = start + '[[argument:'.length;
        const close = text.indexOf(']]', innerStart);
        if (close < 0) return null;
        const end = close + 2;
        if (!spanLive(live, start, end)) return null;
        if (text.slice(start, end).indexOf('\n') >= 0) return null;
        const inner = text.slice(innerStart, close);
        const pipe = inner.indexOf('|');
        let idRaw;
        let label = '';
        if (pipe >= 0) {
            idRaw = inner.slice(0, pipe).trim();
            label = inner.slice(pipe + 1).trim();
        } else {
            idRaw = inner.trim();
        }
        if (!ARGUMENT_ID_RE.test(idRaw)) return null;
        if (label.length > ARGUMENT_LABEL_MAX) return null;
        return {
            start: start,
            end: end,
            raw: text.slice(start, end),
            argumentId: idRaw,
            label: label,
        };
    }

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function replaceResearchRefs(plainText, refs) {
        if (plainText == null || plainText === '') return plainText;
        const map = refs || {};
        const concepts = {};
        const argumentsMap = {};
        (map.concepts || []).forEach(function (c) {
            if (c && c.id) {
                concepts[normalizeConceptKey(c.name || '')] = c;
                if (Array.isArray(c.aliases)) {
                    c.aliases.forEach(function (a) {
                        concepts[normalizeConceptKey(a)] = c;
                    });
                }
            }
        });
        (map.arguments || []).forEach(function (a) {
            if (a && a.id) argumentsMap[a.id] = a;
        });
        const markup = parseResearchMarkup(plainText);
        const parts = [];
        let cursor = 0;
        const events = [];
        markup.conceptRefs.forEach(function (r) {
            events.push({ start: r.start, end: r.end, kind: 'concept', ref: r });
        });
        markup.argumentRefs.forEach(function (r) {
            events.push({ start: r.start, end: r.end, kind: 'argument', ref: r });
        });
        events.sort(function (a, b) {
            return a.start - b.start;
        });
        events.forEach(function (ev) {
            parts.push(plainText.slice(cursor, ev.start));
            if (ev.kind === 'concept') {
                const rec = concepts[normalizeConceptKey(ev.ref.name)];
                if (rec && rec.id) {
                    parts.push(
                        '<a href="#/concepts/' +
                            encodeURIComponent(rec.id) +
                            '" class="wiki-link-internal">' +
                            esc(ev.ref.name) +
                            '</a>'
                    );
                } else {
                    parts.push(plainText.slice(ev.start, ev.end));
                }
            } else {
                const rec = argumentsMap[ev.ref.argumentId];
                if (rec && rec.id) {
                    const label = ev.ref.label || rec.name || rec.id;
                    parts.push(
                        '<a href="#/arguments/' +
                            encodeURIComponent(rec.id) +
                            '" class="wiki-link-internal">' +
                            esc(label) +
                            '</a>'
                    );
                } else {
                    parts.push(
                        '<span class="wiki-link-unresolved" title="Unresolved argument">' +
                            esc(ev.ref.raw) +
                            '</span>'
                    );
                }
            }
            cursor = ev.end;
        });
        parts.push(plainText.slice(cursor));
        return parts.join('');
    }

    const api = {
        PRKS_CONCEPT_REF_MAX: CONCEPT_REF_MAX,
        PRKS_ARGUMENT_LABEL_MAX: ARGUMENT_LABEL_MAX,
        prksCanonicalConceptName: canonicalConceptName,
        prksNormalizeConceptKey: normalizeConceptKey,
        prksParseResearchMarkup: parseResearchMarkup,
        prksReplaceResearchRefs: replaceResearchRefs,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
