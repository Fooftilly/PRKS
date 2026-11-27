#!/usr/bin/env node
'use strict';

/*
 * Deterministic clipboard regression against the shipped Work-detail code.
 * A 200/empty BibTeX response is not proof of a successful copy (#1076).
 * Import the real module; do not reimplement or eval its handlers.
 */
const assert = require('node:assert/strict');
global.window = global;
const { copyBibTeX, prksCopyBibTeXToClipboard } =
    require('../../frontend/js/components/works.js');

const savedSetTimeout = global.setTimeout;
global.setTimeout = (fn, delay) => {
    const handle = savedSetTimeout(fn, delay);
    if (handle && typeof handle.unref === 'function') handle.unref();
    return handle;
};
global.prksIcon = () => '';
global.prksRefreshIcons = () => {};

function button() {
    return {
        dataset: {},
        innerHTML: 'Copy BibTeX',
        style: {},
        getAttribute() { return ''; },
        setAttribute() {},
    };
}

async function scenario(name, options) {
    const copied = [];
    const visited = [];
    global.prksRequest = async (path) => {
        visited.push(path);
        return { ok: options.httpOk !== false, text: async () => options.text };
    };
    global.prksCopyTextToClipboard = async (text) => {
        if (options.rejectCopy) throw new Error('Clipboard denied');
        copied.push(text);
    };
    const btn = button();
    await copyBibTeX('W-1', btn);
    assert.deepEqual(visited, ['/api/bibtex/W-1'], name + ': expected one export request');
    assert.deepEqual(copied, options.expectedCopies, name + ': clipboard contents/call count');
    if (options.success) {
        assert.match(btn.innerHTML, /BibTeX copied!/, name);
    } else {
        assert.doesNotMatch(btn.innerHTML, /BibTeX copied!/, name);
        assert.match(btn.innerHTML, options.errorLabel, name);
    }
}

async function main() {
    await scenario('normal BibTeX copy', {
        text: '@book{W1, title={A}}\n', expectedCopies: ['@book{W1, title={A}}\n'], success: true,
    });
    await scenario('deleted Work returns 200/empty', {
        text: '', expectedCopies: [], errorLabel: /No BibTeX available.*refresh/,
    });
    await scenario('whitespace-only response', {
        text: ' \n\t', expectedCopies: [], errorLabel: /No BibTeX available.*refresh/,
    });
    await scenario('HTTP error', {
        httpOk: false, text: '@book{W1}', expectedCopies: [], errorLabel: /Copy failed/,
    });
    await scenario('real clipboard failure', {
        text: '@book{W1}', expectedCopies: [], rejectCopy: true, errorLabel: /Copy failed/,
    });
    /* Toggles still being saved are sent first: the GET reads the saved profile. */
    {
        const order = [];
        let release;
        global.prksCitations = { settleBibtexProfile: () => {
            order.push('settle');
            return new Promise(resolve => { release = resolve; });
        } };
        global.prksRequest = async (path) => {
            order.push(path);
            return { ok: true, text: async () => '@book{W1}' };
        };
        global.prksCopyTextToClipboard = async () => {};
        const btn = button();
        const copying = copyBibTeX('W-1', btn);
        await new Promise(resolve => savedSetTimeout(resolve, 0));
        assert.deepEqual(order, ['settle'], 'no export request while a toggle save is pending');
        release(true);
        await copying;
        assert.deepEqual(order, ['settle', '/api/bibtex/W-1'], 'the export follows the saved profile');
        assert.match(btn.innerHTML, /BibTeX copied!/);
        global.prksCitations = { settleBibtexProfile: async () => false };
        order.length = 0;
        const refused = button();
        await copyBibTeX('W-1', refused);
        assert.deepEqual(order, [], 'a failed toggle save sends no export request');
        assert.match(refused.innerHTML, /Export settings not saved/);
        delete global.prksCitations;
    }
    let attempted = 0;
    global.prksCopyTextToClipboard = async () => { attempted++; };
    await assert.rejects(prksCopyBibTeXToClipboard(''), /No BibTeX export available/);
    await assert.rejects(prksCopyBibTeXToClipboard('  '), /No BibTeX export available/);
    assert.equal(attempted, 0, 'empty helper calls never write to clipboard');
    delete global.prksCopyTextToClipboard;
    await assert.rejects(prksCopyBibTeXToClipboard('@book{W1}'), /Clipboard helper unavailable/);
    console.log('BibTeX clipboard: 10 passed, 0 failed');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
