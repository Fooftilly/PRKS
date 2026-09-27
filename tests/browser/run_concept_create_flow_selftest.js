#!/usr/bin/env node
'use strict';

const assert = require('assert');
const path = require('path');

const conceptsPath = path.join(__dirname, '../../frontend/js/components/concepts.js');

function loadApi(root) {
    const prevWindow = global.window;
    const prevDocument = global.document;
    const prevEasyMDE = global.EasyMDE;
    global.window = root;
    global.document = {
        createElement: () => ({ hidden: false, appendChild: () => {} }),
        body: { appendChild: () => {} },
        documentElement: { appendChild: () => {} },
    };
    global.EasyMDE = undefined;
    try {
        const resolved = require.resolve(conceptsPath);
        delete require.cache[resolved];
        return require(conceptsPath);
    } finally {
        global.window = prevWindow;
        global.document = prevDocument;
        global.EasyMDE = prevEasyMDE;
        delete require.cache[require.resolve(conceptsPath)];
    }
}

async function main() {
    {
        const navigate = [];
        const creates = [];
        const root = {
            prksPromptTextDialog: async () => 'Side Concept',
            createConcept: async (body) => {
                creates.push(body);
                return { id: 'C-side' };
            },
            prksNavigate: (hash, opts) => navigate.push({ hash, opts }),
        };
        const api = loadApi(root);
        await api.prksCreateConceptFlow('Side Concept', {
            tabId: 'side',
            generation: 4,
            isCurrent: (g) => g === 4,
        });
        assert.strictEqual(JSON.stringify(creates), JSON.stringify([{ name: 'Side Concept' }]));
        assert.strictEqual(
            JSON.stringify(navigate),
            JSON.stringify([{ hash: '#/concepts/C-side', opts: { tabId: 'side' } }]),
        );
    }

    {
        let current = true;
        const creates = [];
        const navigate = [];
        const root = {
            prksPromptTextDialog: async () => {
                current = false;
                return 'Stale Concept';
            },
            createConcept: async (body) => {
                creates.push(body);
                return { id: 'C-stale' };
            },
            prksNavigate: (hash, opts) => navigate.push({ hash, opts }),
        };
        const api = loadApi(root);
        const created = await api.prksCreateConceptFlow('Stale Concept', {
            tabId: 'side',
            generation: 1,
            isCurrent: () => current,
        });
        assert.strictEqual(created, null);
        assert.strictEqual(creates.length, 0);
        assert.strictEqual(navigate.length, 0);
    }

    {
        let current = true;
        const creates = [];
        const navigate = [];
        const root = {
            prksPromptTextDialog: async () => 'Late Concept',
            createConcept: async (body) => {
                creates.push(body);
                current = false;
                return { id: 'C-late' };
            },
            prksNavigate: (hash, opts) => navigate.push({ hash, opts }),
        };
        const api = loadApi(root);
        const created = await api.prksCreateConceptFlow('Late Concept', {
            tabId: 'side',
            generation: 2,
            isCurrent: () => current,
        });
        assert.strictEqual(JSON.stringify(created), JSON.stringify({ id: 'C-late' }));
        assert.strictEqual(JSON.stringify(creates), JSON.stringify([{ name: 'Late Concept' }]));
        assert.strictEqual(navigate.length, 0);
    }

    console.log('concept create-flow selftest ok');
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
