'use strict';
/* Loads the Dafny model compiled to JS, the shipped sync-reconcile.js and the
 * test packages, as CommonJS modules from a temporary directory. */
const fs = require('fs');
const os = require('os');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');

function loader(generatedPath, prefix) {
    const work = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-difftest-'));
    process.on('exit', () => fs.rmSync(work, { recursive: true, force: true }));
    fs.symlinkSync(path.resolve(prefix), path.join(work, 'node_modules'), 'dir');
    const load = (name, source) => {
        const file = path.join(work, name);
        fs.writeFileSync(file, source);
        return require(file);
    };
    const classicSource = fs.readFileSync(path.join(root, 'frontend/js/sync-reconcile.js'), 'utf8');
    const model = load('model.cjs',
        fs.readFileSync(generatedPath, 'utf8') + '\nmodule.exports = { _dafny, Reconcile, Sequence };\n');
    return {
        load,
        fc: load('fc.cjs', "module.exports = require('fast-check');\n"),
        BigNumber: load('bignumber.cjs', "module.exports = require('bignumber.js');\n"),
        _dafny: model._dafny,
        Reconcile: model.Reconcile,
        Sequence: model.Sequence,
        classicSource,
        classic: load('classic.cjs', classicSource),
        reloadClassic: () => require(path.join(work, 'classic.cjs')),
    };
}

module.exports = { loader };
