#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.resolve(__dirname, '../../frontend/js/ui.js'), 'utf8');

function between(start, end) {
    const a = src.indexOf(start);
    const b = src.indexOf(end, a + start.length);
    assert(a >= 0 && b > a, 'missing renderer source: ' + start);
    return src.slice(a, b);
}

// Exercise the actual classic helpers without initializing the browser app.
const context = vm.createContext({ window: {}, URL, encodeURIComponent });
vm.runInContext(between('function escapeHtml(s) {', 'function prksEscapeAttr(s) {'), context);

for (const good of ['#abc', '#aBcD', '#abcdef', '#12345678']) {
    assert.equal(context.prksSafeTagColorCss(good), good);
}
for (const bad of [
    'red;position:fixed;inset:0;z-index:9999', 'x;background:url(https://evil.example/beacon)',
    'red', '#abcde', '#abcdefg', 'var(--accent)', ''
]) {
    assert.equal(context.prksSafeTagColorCss(bad), '#6d6cf7');
}
for (const safe of ['https://example.test/a.pdf', 'http://localhost/doc']) {
    assert.equal(context.prksSafeHttpUrl(safe), safe);
    assert.match(context.prksExternalUrlRowHtml('Original URL', safe), /<a href=/);
}
for (const bad of ['javascript:alert(1)', 'data:text/html,<script>', 'file:///etc/passwd', 'relative/file.pdf']) {
    assert.equal(context.prksSafeHttpUrl(bad), '');
    const html = context.prksExternalUrlRowHtml('Original URL', bad);
    assert(!html.includes('<a '), 'unsafe scheme became link: ' + bad);
    assert(!html.includes('<script>'), 'unsafe URL became markup');
}

// Cover the real classic folder and work chip renderers, not just the sanitizer.
vm.runInContext(
    between('function renderFolderTagsChipsHtml(folder) {', 'window.renderFolderTagsChipsHtml ='),
    context
);
vm.runInContext(
    between('function renderWorkTagsChips(work, options = {}) {', 'async function prksReloadEntityTagsUI('),
    context
);
const poisoned = { id: 't', name: 'Poison', color: 'red;position:fixed;inset:0;z-index:9999' };
const folderHtml = context.renderFolderTagsChipsHtml({ id: 'f', tags: [poisoned] });
const workHtml = context.renderWorkTagsChips({ id: 'w', tags: [poisoned] });
for (const html of [folderHtml, workHtml]) {
    assert.match(html, /style="--tag-accent:#6d6cf7;"/);
    assert(!html.includes('position:fixed'));
}

// The playlist render path must use the guarded helper too.
vm.runInContext(
    between('function renderPlaylistSummarySidebarHtml(pl, editing) {',
            'function prksWork'),
    context
);
const playlistHtml = context.renderPlaylistSummarySidebarHtml({
    title: 'Playlist', original_url: 'javascript:alert(1)', items: []
}, false);
assert(!playlistHtml.includes('<a href="javascript:'), 'playlist emitted an unsafe href');
assert(playlistHtml.includes('javascript:alert(1)'), 'unsafe URL should remain visible as text');

// Guard the two Work metadata source sinks, which share the same helper.
const workSource = between('function prksWorkPanelViewShellHtml(', 'function prksWorkPanel');
assert(workSource.includes("prksExternalUrlRowHtml('Original URL', parts.originalUrlPdf)"));
assert(src.includes("prksExternalUrlRowHtml('Original URL', originalUrlPdf)"));

console.log('Legacy untrusted-render safety regression checks passed.');
