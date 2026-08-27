/**
 * Research-notes Markdown preview sanitizer.
 *
 * Last HTML-producing transform before EasyMDE inserts preview markup.
 * Raw Markdown stays canonical storage; this module only sanitizes rendered HTML.
 */
(function () {
    'use strict';

    var PRKS_PREVIEW_UNAVAILABLE_HTML =
        '<p class="prks-inline-message prks-inline-message--error">Markdown preview unavailable.</p>';

    var PRKS_WIKI_CLASS_A = Object.freeze({
        'wiki-link-internal': true,
        'wiki-link-pdf-ann': true,
    });
    var PRKS_WIKI_CLASS_SPAN = Object.freeze({
        'wiki-link-unresolved': true,
    });
    var PRKS_SAFE_SCHEMES_A = Object.freeze({ http: true, https: true, mailto: true });
    var PRKS_SAFE_SCHEMES_IMG = Object.freeze({ http: true, https: true });

    var PRKS_PREVIEW_SANITIZE_CONFIG = Object.freeze({
        ALLOWED_TAGS: Object.freeze([
            'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'span',
            'blockquote', 'pre', 'code', 'em', 'strong', 'del', 'ul', 'ol', 'li', 'a', 'img',
            'table', 'thead', 'tbody', 'tr', 'th', 'td',
        ]),
        ALLOWED_ATTR: Object.freeze([
            'href', 'title', 'src', 'alt', 'target', 'rel', 'align',
            'class', 'data-pdf-ann-id',
        ]),
        ALLOWED_NAMESPACES: Object.freeze(['http://www.w3.org/1999/xhtml']),
        ALLOW_DATA_ATTR: false,
        ALLOW_ARIA_ATTR: false,
        ALLOW_UNKNOWN_PROTOCOLS: false,
        FORBID_TAGS: Object.freeze([
            'style', 'form', 'input', 'button', 'textarea', 'select', 'option', 'template',
        ]),
        FORBID_ATTR: Object.freeze(['style', 'id', 'name', 'srcdoc', 'formaction']),
        SANITIZE_DOM: true,
        RETURN_DOM_FRAGMENT: false,
        IN_PLACE: false,
    });

    function prksTrimUriControls(value) {
        var s = String(value == null ? '' : value);
        var start = 0;
        var end = s.length;
        while (start < end && s.charCodeAt(start) <= 32) start += 1;
        while (end > start && s.charCodeAt(end - 1) <= 32) end -= 1;
        return s.slice(start, end);
    }

    function prksStripControls(value) {
        var out = '';
        for (var i = 0; i < value.length; i += 1) {
            if (value.charCodeAt(i) > 32) out += value.charAt(i);
        }
        return out;
    }

    function prksIsAuthorityForm(trimmed) {
        return (
            trimmed.indexOf('//') === 0 ||
            trimmed.indexOf('\\\\') === 0 ||
            trimmed.indexOf('/\\') === 0 ||
            trimmed.indexOf('\\/') === 0
        );
    }

    function prksUriScheme(trimmed) {
        var colon = trimmed.indexOf(':');
        if (colon <= 0) return '';
        var scheme = prksStripControls(trimmed.slice(0, colon)).toLowerCase();
        if (!/^[a-z][a-z0-9+.-]*$/.test(scheme)) return '';
        return scheme;
    }

    function prksUriIsAllowed(value, allowedSchemes) {
        var trimmed = prksTrimUriControls(value);
        if (!trimmed) return false;
        if (prksIsAuthorityForm(trimmed)) return false;
        var scheme = prksUriScheme(trimmed);
        if (!scheme) return true;
        return allowedSchemes[scheme] === true;
    }

    function prksClassTokens(node) {
        var raw = node.getAttribute('class');
        if (!raw) return [];
        return String(raw).split(/\s+/).filter(Boolean);
    }

    function prksHasClass(node, token) {
        var tokens = prksClassTokens(node);
        for (var i = 0; i < tokens.length; i += 1) {
            if (tokens[i] === token) return true;
        }
        return false;
    }

    function prksAfterSanitizeAttributesHook(node) {
        if (!node || node.nodeType !== 1) return;
        var tag = String(node.nodeName || '').toLowerCase();

        if (tag !== 'a' && node.hasAttribute('href')) node.removeAttribute('href');
        if (tag !== 'img' && node.hasAttribute('src')) node.removeAttribute('src');
        if (tag !== 'a' && node.hasAttribute('target')) node.removeAttribute('target');
        if (tag !== 'a' && node.hasAttribute('rel')) node.removeAttribute('rel');

        if (tag === 'a' && node.hasAttribute('href')) {
            if (!prksUriIsAllowed(node.getAttribute('href'), PRKS_SAFE_SCHEMES_A)) {
                node.removeAttribute('href');
            }
        }
        if (tag === 'img' && node.hasAttribute('src')) {
            if (!prksUriIsAllowed(node.getAttribute('src'), PRKS_SAFE_SCHEMES_IMG)) {
                node.removeAttribute('src');
            }
        }

        var allowedClass = tag === 'a' ? PRKS_WIKI_CLASS_A : tag === 'span' ? PRKS_WIKI_CLASS_SPAN : null;
        if (node.hasAttribute('class')) {
            var kept = [];
            if (allowedClass) {
                var tokens = prksClassTokens(node);
                for (var i = 0; i < tokens.length; i += 1) {
                    if (allowedClass[tokens[i]]) kept.push(tokens[i]);
                }
            }
            if (kept.length) node.setAttribute('class', kept.join(' '));
            else node.removeAttribute('class');
        }

        if (node.hasAttribute('data-pdf-ann-id')) {
            if (!(tag === 'a' && prksHasClass(node, 'wiki-link-pdf-ann'))) {
                node.removeAttribute('data-pdf-ann-id');
            }
        }

        if (tag === 'a' && node.hasAttribute('target')) {
            if (node.getAttribute('target') !== '_blank') {
                node.removeAttribute('target');
            } else {
                node.setAttribute('rel', 'noopener noreferrer');
            }
        }
    }

    var purifier = null;
    if (
        typeof window !== 'undefined' &&
        window.DOMPurify &&
        window.DOMPurify.isSupported === true &&
        typeof window.DOMPurify.sanitize === 'function' &&
        typeof window.DOMPurify.addHook === 'function'
    ) {
        try {
            window.DOMPurify.addHook('afterSanitizeAttributes', prksAfterSanitizeAttributesHook);
            purifier = window.DOMPurify;
        } catch (_e) {
            purifier = null;
        }
    }

    window.prksSanitizeMarkdownPreviewHtml = function (html) {
        if (html == null || html === '') return '';
        if (!purifier) return PRKS_PREVIEW_UNAVAILABLE_HTML;
        try {
            return purifier.sanitize(html, PRKS_PREVIEW_SANITIZE_CONFIG);
        } catch (_e) {
            return PRKS_PREVIEW_UNAVAILABLE_HTML;
        }
    };
})();
