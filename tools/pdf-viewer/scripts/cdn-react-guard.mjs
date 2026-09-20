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
//   https://unpkg.com./react@18/react.js      trailing-dot FQDN
//   https://cdn.jsdelivr.net/npm/lib?dep=react   carried in the query
//
// So: normalise the escapes, extract every host-like token with a generic
// (host-agnostic) pattern, and resolve each token through the URL parser.
// The decision itself stays an exact hostname comparison, so a lookalike
// host such as "unpkg.com.example.org" or "evil-unpkg.com" cannot match.

const CDN_REACT_HOSTNAMES = new Set(['cdn.jsdelivr.net', 'unpkg.com']);

// Dot-separated labels, an optional root dot, an optional port, then a path.
//
// The leading lookbehind is load-bearing for performance, not just for
// correctness. Without it, a long dotted token that never reaches the
// required '/' is re-scanned from every offset inside itself, which is
// quadratic in the token length: a synthetic 32k-label chain took ~11.6s.
// Rejecting any start position that sits mid-token makes those retries O(1),
// so only the first offset does real work.
const HOST_LIKE = /(?<![a-z0-9.-])[a-z0-9-]+(?:\.[a-z0-9-]+)+\.?(?::\d+)?\/[^\s"'`)\\<>]*/gi;

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
        // "unpkg.com." is the same DNS host as "unpkg.com"; the URL parser
        // keeps the root dot, so drop it before the exact comparison.
        const hostname = url.hostname.toLowerCase().replace(/\.$/, '');
        if (!CDN_REACT_HOSTNAMES.has(hostname)) {
            continue;
        }
        if ((url.pathname + url.search).toLowerCase().includes('react')) {
            return true;
        }
    }
    return false;
}
