/**
 * Cached loader for the isolated PRKS PDF viewer bundle.
 * Production UI stays vanilla JS; React lives only inside the vendor file.
 */

const DEFAULT_ASSET_BASE = '/vendor/prks-pdf-viewer/';
const loaders = new Map();

function normalizeBase(assetBaseUrl) {
    const raw = assetBaseUrl || DEFAULT_ASSET_BASE;
    return String(raw).replace(/\/?$/, '/');
}

function ensureViewerCss(base) {
    if (typeof document === 'undefined') return;
    const href = base + 'prks-pdf-viewer.css';
    if (document.querySelector('link[data-prks-pdf-viewer-css="' + href + '"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.setAttribute('data-prks-pdf-viewer-css', href);
    document.head.appendChild(link);
}

export function loadPrksPdfViewerModule(assetBaseUrl) {
    const base = normalizeBase(assetBaseUrl);
    if (!loaders.has(base)) {
        ensureViewerCss(base);
        loaders.set(base, import(base + 'prks-pdf-viewer.js'));
    }
    return loaders.get(base);
}

export async function createPrksPdfViewer(options) {
    const opts = options && typeof options === 'object' ? options : {};
    const assetBaseUrl = normalizeBase(opts.assetBaseUrl || DEFAULT_ASSET_BASE);
    const mod = await loadPrksPdfViewerModule(assetBaseUrl);
    return mod.createPrksPdfViewer({
        ...opts,
        assetBaseUrl,
    });
}
