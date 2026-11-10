#!/usr/bin/env node
'use strict';

/**
 * Same document under a new storage key, or a different document.
 *
 * On PostgreSQL every PDF write is stored under a fresh immutable key. When
 * the server confirms it stored exactly the bytes this viewer exported, the
 * open document is that object and the viewer stays mounted
 * (`prksStoredExportMatches`); the loaded path is not rewritten, and the new
 * key is recorded for that viewer instance only
 * (`prksViewerBoundToManagedPath`). Anything else remounts.
 *
 * Drives the real helpers from works-pdf.js.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { createHash } = require('crypto');
const strict = require('assert/strict');

let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

const rootDir = path.resolve(__dirname, '../..');
global.window = global;

let pdfSource = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works-pdf.js'), 'utf8');
pdfSource = pdfSource.replace(
    "import { prksPdfCacheInstallOutcome, prksPostPdfCacheInstall } from '/js/pdf-cache-install.js';\nimport { createPrksPdfViewer } from '/js/pdf-viewer-runtime.js';\n",
    'const prksPdfCacheInstallOutcome = null;\nconst prksPostPdfCacheInstall = null;\nconst createPrksPdfViewer = null;\n'
);
pdfSource = pdfSource.replace('export function initPdfViewerForWork', 'function initPdfViewerForWork');
const scriptDir = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-pdf-stored-export-'));
const scriptFile = path.join(scriptDir, 'works-pdf.cjs');
fs.writeFileSync(scriptFile, pdfSource + '\nmodule.exports = { prksStoredExportMatches, prksViewerBoundToManagedPath };\n');
const {
    prksStoredExportMatches: matches,
    prksViewerBoundToManagedPath: bound,
} = require(scriptFile);

function bytes(text) {
    return new TextEncoder().encode(text).buffer;
}

function sha256(buffer) {
    return createHash('sha256').update(Buffer.from(buffer)).digest('hex');
}

async function withoutWebCrypto(fn) {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
    Object.defineProperty(globalThis, 'crypto', { value: undefined, configurable: true, writable: true });
    try {
        return await fn();
    } finally {
        Object.defineProperty(globalThis, 'crypto', original);
    }
}

async function storedExport() {
    const exported = bytes('%PDF-1.7 exported by this viewer');
    const other = bytes('%PDF-1.7 a different document!!!');
    assert.equal(exported.byteLength, other.byteLength);

    // The server's SHA-256 of what it stored decides.
    assert.equal(await matches({ content_sha256: sha256(exported), byte_size: exported.byteLength }, exported), true);
    assert.equal(await matches({ content_sha256: sha256(exported).toUpperCase() }, exported), true);
    assert.equal(await matches({ content_sha256: sha256(other), byte_size: other.byteLength }, exported), false,
        'other bytes of the same size are another document');

    // Linearization rewrote the stored bytes: the export fields still name
    // this viewer's upload, and they decide over the stored file's hash.
    const linearized = { content_sha256: sha256(other), byte_size: other.byteLength + 9 };
    assert.equal(await matches(Object.assign({ export_sha256: sha256(exported),
        export_byte_size: exported.byteLength }, linearized), exported), true);
    assert.equal(await matches(Object.assign({ export_sha256: sha256(other),
        export_byte_size: other.byteLength }, { content_sha256: sha256(exported) }), exported), false,
        'an export that is not these bytes is not rescued by the stored hash');

    // No answer to compare against is not a match: the caller remounts.
    assert.equal(await matches({ byte_size: exported.byteLength }, exported), false);
    assert.equal(await matches({}, exported), false);
    assert.equal(await matches(null, exported), false);
    assert.equal(await matches({ content_sha256: sha256(exported) }, null), false);

    // Without Web Crypto (plain-HTTP LAN origin) this request's own size stands in.
    await withoutWebCrypto(async () => {
        assert.equal(await matches({ content_sha256: sha256(exported), byte_size: exported.byteLength }, exported), true);
        assert.equal(await matches({ content_sha256: sha256(exported), byte_size: exported.byteLength + 1 }, exported), false);
        assert.equal(await matches({ content_sha256: sha256(exported) }, exported), false);
        assert.equal(await matches({ byte_size: exported.byteLength }, exported), false);
        assert.equal(await matches(Object.assign({ export_sha256: sha256(exported),
            export_byte_size: exported.byteLength }, linearized), exported), true);
        assert.equal(await matches(Object.assign({ export_sha256: sha256(exported),
            export_byte_size: exported.byteLength + 9 }, { byte_size: exported.byteLength }), exported), false);
    });
}

function boundPath() {
    const viewer = { id: 'mounted' };
    const loaded = '/api/pdfs/works/W-1/object-a.pdf';
    const stored = '/api/pdfs/works/W-1/object-b.pdf';
    const runtime = { viewer, viewerFilePath: loaded + '?v=1' };

    assert.equal(bound(runtime, loaded), true, 'the loaded path');
    assert.equal(bound(runtime, stored), false, 'an unconfirmed new key');

    // The confirmed export: bound for this viewer, loaded path unchanged.
    runtime.viewerStoredExport = { viewer, path: stored };
    assert.equal(bound(runtime, stored + '?v=2'), true);
    assert.equal(bound(runtime, loaded), true);
    assert.equal(runtime.viewerFilePath, loaded + '?v=1');
    assert.equal(bound(runtime, '/api/pdfs/works/W-1/object-c.pdf'), false);
    assert.equal(bound(runtime, '/files/object-b.pdf'), false, 'only managed PDF paths');

    // It belongs to the viewer instance, not the runtime: a replacement
    // viewer does not inherit it.
    runtime.viewer = { id: 'replacement' };
    assert.equal(bound(runtime, stored), false);
    runtime.viewer = null;
    assert.equal(bound(runtime, loaded), false);
}

async function main() {
    await storedExport();
    boundPath();
    console.log(`PDF stored-export selftest: ${checks} checks passed`);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
