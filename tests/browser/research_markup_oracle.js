#!/usr/bin/env node
'use strict';
/* Line-delimited oracle for tests/test_research_markup_differential.py.
 * Each stdin line is a JSON request; each stdout line is its answer:
 *   {"parse": text}        -> refs with code-point offsets (Python's indices)
 *   {"keys": [names...]}   -> prksNormalizeConceptKey of each name
 */
const path = require('path');
const readline = require('readline');

// research-links.js exports its API through module.exports under Node.
const api = require(path.resolve(__dirname, '../../frontend/js/research-links.js'));

function codePoints(text, index) {
    return Array.from(text.slice(0, index)).length;
}

function answer(request) {
    if (Object.prototype.hasOwnProperty.call(request, 'parse')) {
        const text = request.parse;
        const markup = api.prksParseResearchMarkup(text);
        return {
            concepts: markup.conceptRefs.map(function (r) {
                return [codePoints(text, r.start), codePoints(text, r.end), r.name];
            }),
            arguments: markup.argumentRefs.map(function (r) {
                return [codePoints(text, r.start), codePoints(text, r.end), r.argumentId, r.label];
            }),
        };
    }
    return { keys: request.keys.map(function (name) { return api.prksNormalizeConceptKey(name); }) };
}

const lines = readline.createInterface({ input: process.stdin });
lines.on('line', function (line) {
    process.stdout.write(JSON.stringify(answer(JSON.parse(line))) + '\n');
});
