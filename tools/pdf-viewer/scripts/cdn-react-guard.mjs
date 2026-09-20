// Supply-chain guard: the vendored viewer must bundle React locally, never
// pull it from a public CDN at runtime.
//
// Bundled URLs reach this check in several shapes, and a guard that only
// recognises one of them is a guard that silently stops working:
//
//   https://unpkg.com/react@18/umd/react.js   absolute
//   //unpkg.com/react@18/umd/react.js         protocol-relative
//   unpkg.com/react@18/umd/react.js           bare
//   https:\/\/unpkg.com\/react@18\/react.js   escaped, inside a string literal
//   https://cdn.jsdelivr.net/npm/lib?dep=react   carried in the query
//
// So: normalise the escapes, extract every host-like token with a generic
// (host-agnostic) pattern, and resolve each token through the URL parser.
// The decision itself stays an exact hostname comparison, so a lookalike
// host such as "unpkg.com.example.org" or "evil-unpkg.com" cannot match.

const CDN_REACT_HOSTNAMES = new Set(['cdn.jsdelivr.net', 'unpkg.com']);

// Dot-separated labels, then a path. Labels cannot contain '.', so each
// repetition is unambiguous and the pattern cannot backtrack quadratically.
const HOST_LIKE = /[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?\/[^\s"'`)\\<>]*/gi;

// Bounded repetition: escaped separators nest at most a couple of levels in
// practice, and a bounded quantifier keeps this linear.
const ESCAPED_SLASH = /\\{1,4}\//g;

/**
 * Report whether a built bundle references React on a public CDN.
 *
 * @param {string} text Bundle source to scan.
 * @returns {boolean} True when a supported CDN host serves a React asset.
 */
export function bundleReferencesCdnReact(text) {
    const normalised = String(text).replace(ESCAPED_SLASH, '/');
    for (const match of normalised.matchAll(HOST_LIKE)) {
        let url;
        try {
            // The token never carries a scheme (the pattern starts at the
            // host), so supplying one makes it parseable without changing
            // which host it names.
            url = new URL('https://' + match[0]);
        } catch {
            continue;
        }
        if (!CDN_REACT_HOSTNAMES.has(url.hostname.toLowerCase())) {
            continue;
        }
        if ((url.pathname + url.search).toLowerCase().includes('react')) {
            return true;
        }
    }
    return false;
}
