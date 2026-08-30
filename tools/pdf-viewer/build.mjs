import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as esbuild from 'esbuild';
import { applyEmbedpdfPatches } from './scripts/apply-embedpdf-patches.mjs';
import { runGuards } from './scripts/guard.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, '..', '..');
const outDir = join(repoRoot, 'frontend', 'vendor', 'prks-pdf-viewer');
const wasmSrc = join(here, 'node_modules', '@embedpdf', 'pdfium', 'dist', 'pdfium.wasm');
const localWasmUrl = '/vendor/prks-pdf-viewer/pdfium.wasm';
const cdnWasmNeedle = 'https://cdn.jsdelivr.net/npm/@embedpdf/pdfium@2.15.0/dist/pdfium.wasm';

function sha256(buf) {
    return createHash('sha256').update(buf).digest('hex');
}

function copyLicense(pkgName, destName) {
    const dir = join(here, 'node_modules', ...pkgName.split('/'));
    for (const name of ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'LICENSE.pdfium']) {
        const p = join(dir, name);
        try {
            copyFileSync(p, join(outDir, 'LICENSES', destName + '-' + name));
            return;
        } catch {
            /* next */
        }
    }
}

const pkg = JSON.parse(readFileSync(join(here, 'package.json'), 'utf8'));
mkdirSync(join(outDir, 'LICENSES'), { recursive: true });

const patchRecords = applyEmbedpdfPatches();
runGuards(here);
execFileSync(join(here, 'node_modules', 'typescript', 'bin', 'tsc'), ['--noEmit'], {
    cwd: here,
    stdio: 'inherit',
});

const result = await esbuild.build({
    absWorkingDir: here,
    entryPoints: ['src/index.ts'],
    bundle: true,
    format: 'esm',
    platform: 'browser',
    target: ['es2022'],
    outfile: join(outDir, 'prks-pdf-viewer.js'),
    jsx: 'automatic',
    sourcemap: false,
    metafile: true,
    minify: true,
    logLevel: 'info',
    define: {
        'process.env.NODE_ENV': '"production"',
    },
    loader: { '.css': 'css' },
});

const meta = result.metafile;
const reactInputs = Object.keys(meta.inputs).filter(
    (p) => p.includes('node_modules/react/') || p.includes('node_modules/react-dom/'),
);
const outputImports = Object.values(meta.outputs).flatMap((out) => out.imports || []);
try {
    unlinkSync(join(outDir, 'esbuild-meta.json'));
} catch {
    /* optional leftover */
}

copyFileSync(wasmSrc, join(outDir, 'pdfium.wasm'));

copyLicense('@embedpdf/core', 'embedpdf-core');
copyLicense('@embedpdf/engines', 'embedpdf-engines');
copyLicense('@embedpdf/pdfium', 'embedpdf-pdfium');
copyLicense('@embedpdf/pdfium', 'pdfium');
copyLicense('@embedpdf/plugin-annotation', 'embedpdf-plugin-annotation');
copyLicense('react', 'react');
copyLicense('react-dom', 'react-dom');

let jsText = readFileSync(join(outDir, 'prks-pdf-viewer.js'), 'utf8');
if (jsText.includes(cdnWasmNeedle)) {
    jsText = jsText.split(cdnWasmNeedle).join(localWasmUrl);
    writeFileSync(join(outDir, 'prks-pdf-viewer.js'), jsText);
}
const jsBuf = Buffer.from(jsText);
const cssPath = join(outDir, 'prks-pdf-viewer.css');
let cssHash = '';
try {
    cssHash = sha256(readFileSync(cssPath));
} catch {
    writeFileSync(cssPath, '/* bundled beside JS */\n');
}

const thirdParty = [
    '@embedpdf/core ' + pkg.dependencies['@embedpdf/core'],
    '@embedpdf/engines ' + pkg.dependencies['@embedpdf/engines'],
    '@embedpdf/pdfium ' + pkg.dependencies['@embedpdf/pdfium'],
    'react ' + pkg.dependencies.react,
    'react-dom ' + pkg.dependencies['react-dom'],
].join('\n');
writeFileSync(join(outDir, 'THIRD_PARTY.md'), thirdParty + '\n');

const manifest = {
    embedpdf: '2.15.0',
    react: pkg.dependencies.react,
    reactDom: pkg.dependencies['react-dom'],
    fontFallback: null,
    tiling: false,
    pdfiumWasm: 'pdfium.wasm',
    esbuild: esbuild.version,
    reactMetafileInputs: reactInputs,
    outputSha256: {
        js: sha256(jsBuf),
        css: cssHash,
        wasm: sha256(readFileSync(join(outDir, 'pdfium.wasm'))),
    },
    builtAt: new Date().toISOString(),
    embedpdfPatches: patchRecords.map((r) => ({
        package: r.package,
        version: r.version,
        patch: r.patch,
        sha256: r.sha256,
    })),
};
writeFileSync(join(outDir, 'BUILD-MANIFEST.json'), JSON.stringify(manifest, null, 2) + '\n');
writeFileSync(
    join(outDir, 'VERSION'),
    `embedpdf 2.15.0\nreact ${pkg.dependencies.react}\nreact-dom ${pkg.dependencies['react-dom']}\nfontFallback null\n`,
);

if (/cdn\.jsdelivr\.net.*react/.test(jsText) || /unpkg\.com.*react/.test(jsText)) {
    throw new Error('bundle references CDN React');
}
if (
    outputImports.some(
        (imp) => imp.external && (imp.path === 'react' || imp.path === 'react-dom'),
    )
) {
    throw new Error('esbuild left react as external');
}
if (!reactInputs.length) {
    throw new Error('esbuild metafile has no React inputs (React was not bundled)');
}

console.log('wrote', outDir);
