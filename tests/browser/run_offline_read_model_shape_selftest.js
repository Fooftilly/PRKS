"use strict";
/* Fast, production-owned read-model shape contracts moved from Chromium
 * (#740). The former E2Es did no offline/cache/UI interaction: they evaluated
 * the pure app.js predicates after launching a server and browser.
 *
 * Extract the EXACT shipped validator functions instead of reimplementing
 * their logic in a test. Fail closed on missing/renamed functions or the
 * optional Person string-field contract.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/js/app.js"), "utf8");
const names = [
  "prksHasUsableRowId",
  "prksIsPositionIndexShape",
  "prksIsPositionShape",
  "prksIsArgumentKind",
  "prksIsArgumentTargetShape",
  "prksIsArgumentAuthorShape",
  "prksIsArgumentSourceShape",
  "prksIsArgumentMentionShape",
  "prksIsArgumentResponseShape",
  "prksIsArgumentIndexRowShape",
  "prksIsArgumentIndexShape",
  "prksIsArgumentShape",
  "prksIsPersonGroupRowShape",
  "prksIsOptionalString",
  "prksIsPersonScalarShape",
  "prksIsPersonWorkRowShape",
  "prksIsAssignedRoleShape",
  "prksIsPeopleIndexRowShape",
  "prksIsPeopleIndexShape",
  "prksIsPersonShape",
  "prksIsPlaylistItemCount",
  "prksIsPlaylistScalarShape",
  "prksIsPlaylistIndexRowShape",
  "prksIsPlaylistsIndexShape",
  "prksIsPlaylistItemShape",
  "prksIsPlaylistShape"
];
function sourceFunction(name) {
  const marker = "function " + name + "(";
  const first = source.indexOf(marker);
  assert.notEqual(first, -1, "missing production validator " + name);
  assert.equal(source.indexOf(marker, first + marker.length), -1,
    "ambiguous production validator " + name);
  const end = source.indexOf("\n}", first);
  assert.notEqual(end, -1, "unclosed production validator " + name);
  return source.slice(first, end + 2);
}
const fieldsMarker = "const PRKS_PERSON_OPTIONAL_STRING_FIELDS = [";
const fieldsStart = source.indexOf(fieldsMarker);
assert.notEqual(fieldsStart, -1, "missing Person scalar contract");
const fieldsEnd = source.indexOf("];", fieldsStart);
assert.notEqual(fieldsEnd, -1, "unclosed Person scalar contract");
const payload = [source.slice(fieldsStart, fieldsEnd + 2),
  ...names.map(sourceFunction),
  "return {" + names.join(", ") + "};"].join("\n");
const predicates = vm.runInThisContext("(function() {\n" + payload + "\n})()",
  {filename:"frontend/js/app.js (pure validator subset)"});
const { prksHasUsableRowId, prksIsPositionIndexShape, prksIsPositionShape, prksIsArgumentKind, prksIsArgumentTargetShape, prksIsArgumentAuthorShape, prksIsArgumentSourceShape, prksIsArgumentMentionShape, prksIsArgumentResponseShape, prksIsArgumentIndexRowShape, prksIsArgumentIndexShape, prksIsArgumentShape, prksIsPersonGroupRowShape, prksIsOptionalString, prksIsPersonScalarShape, prksIsPersonWorkRowShape, prksIsAssignedRoleShape, prksIsPeopleIndexRowShape, prksIsPeopleIndexShape, prksIsPersonShape, prksIsPlaylistItemCount, prksIsPlaylistScalarShape, prksIsPlaylistIndexRowShape, prksIsPlaylistsIndexShape, prksIsPlaylistItemShape, prksIsPlaylistShape } = predicates;
let checks = 0;

// Former test_position_shape_validators_reject_unusable_rows: position index shapes.
{
  const actual = (() => ({
                empty: prksIsPositionIndexShape([]),
                ok: prksIsPositionIndexShape([{ id: 'P-1', name: 'A' }]),
                notArray: prksIsPositionIndexShape({ error: 'boom' }),
                nullValue: prksIsPositionIndexShape(null),
                missingId: prksIsPositionIndexShape([{ name: 'A' }]),
                blankId: prksIsPositionIndexShape([{ id: '   ', name: 'A' }]),
                nestedArrayRow: prksIsPositionIndexShape([['P-1']]),
                oneBadRow: prksIsPositionIndexShape([{ id: 'P-1' }, { id: '' }]),
            }))();
  const expected = {
  "empty": true,
  "ok": true,
  "notArray": false,
  "nullValue": false,
  "missingId": false,
  "blankId": false,
  "nestedArrayRow": false,
  "oneBadRow": false
};
  assert.deepStrictEqual(actual, expected, "position index shapes: production validator regression");
  checks += Object.keys(expected).length;
}

// Former test_position_shape_validators_reject_unusable_rows: position detail shapes.
{
  const actual = (() => ({
                ok: prksIsPositionShape({ id: 'P-1', arguments: [] }, 'P-1'),
                okWithArgs: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ id: 'A-1', name: 'x', kind: 'stance' }] }, 'P-1'),
                argsWithoutDisplayFields: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ id: 'A-1' }] }, 'P-1'),
                wrongId: prksIsPositionShape({ id: 'P-2', arguments: [] }, 'P-1'),
                missingArguments: prksIsPositionShape({ id: 'P-1' }, 'P-1'),
                argumentsNotArray: prksIsPositionShape({ id: 'P-1', arguments: {} }, 'P-1'),
                argumentMissingId: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ name: 'no id' }] }, 'P-1'),
                argumentBlankId: prksIsPositionShape(
                    { id: 'P-1', arguments: [{ id: '  ', name: 'blank' }] }, 'P-1'),
                errorBody: prksIsPositionShape({ error: 'boom' }, 'P-1'),
                arrayBody: prksIsPositionShape([], 'P-1'),
            }))();
  const expected = {
  "ok": true,
  "okWithArgs": true,
  "argsWithoutDisplayFields": true,
  "wrongId": false,
  "missingArguments": false,
  "argumentsNotArray": false,
  "argumentMissingId": false,
  "argumentBlankId": false,
  "errorBody": false,
  "arrayBody": false
};
  assert.deepStrictEqual(actual, expected, "position detail shapes: production validator regression");
  checks += Object.keys(expected).length;
}

// Former test_argument_shape_validators_reject_unusable_rows: argument index shapes.
{
  const actual = (() => {
                const ok = { id: 'A-1', kind: 'argument', targets: [], sources: [] };
                return {
                    empty: prksIsArgumentIndexShape([]),
                    ok: prksIsArgumentIndexShape([ok]),
                    stanceOk: prksIsArgumentIndexShape([{ id: 'A-2', kind: 'stance', targets: [], sources: [] }]),
                    notArray: prksIsArgumentIndexShape({ error: 'boom' }),
                    nullValue: prksIsArgumentIndexShape(null),
                    missingId: prksIsArgumentIndexShape([{ kind: 'argument', targets: [], sources: [] }]),
                    blankId: prksIsArgumentIndexShape([{ id: '  ', kind: 'argument', targets: [], sources: [] }]),
                    badKind: prksIsArgumentIndexShape([{ id: 'A-1', kind: 'claim', targets: [], sources: [] }]),
                    missingKind: prksIsArgumentIndexShape([{ id: 'A-1', targets: [], sources: [] }]),
                    targetsNotArray: prksIsArgumentIndexShape([{ id: 'A-1', kind: 'argument', targets: {}, sources: [] }]),
                    sourcesNotArray: prksIsArgumentIndexShape([{ id: 'A-1', kind: 'argument', targets: [], sources: {} }]),
                    targetMissingId: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [{ type: 'position' }], sources: [] }]),
                    targetMissingType: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [{ id: 'P-1' }], sources: [] }]),
                    targetBadType: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [{ id: 'P-1', type: 'concept' }], sources: [] }]),
                    sourceMissingWorkId: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [], sources: [{ authors: [] }] }]),
                    sourceAuthorsNotArray: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [], sources: [{ work_id: 'W-1' }] }]),
                    authorNull: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: [null] }] }]),
                    authorString: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: ['bad'] }] }]),
                    authorMissingId: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: [{}] }] }]),
                    authorMinimalOk: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument', targets: [],
                          sources: [{ work_id: 'W-1', authors: [{ id: 'P-1' }] }] }]),
                    optionalDisplayFieldsAbsent: prksIsArgumentIndexShape([
                        { id: 'A-1', kind: 'argument',
                          targets: [{ id: 'P-1', type: 'position' }],
                          sources: [{ work_id: 'W-1', authors: [] }] }]),
                };
            })();
  const expected = {
  "empty": true,
  "ok": true,
  "stanceOk": true,
  "notArray": false,
  "nullValue": false,
  "missingId": false,
  "blankId": false,
  "badKind": false,
  "missingKind": false,
  "targetsNotArray": false,
  "sourcesNotArray": false,
  "targetMissingId": false,
  "targetMissingType": false,
  "targetBadType": false,
  "sourceMissingWorkId": false,
  "sourceAuthorsNotArray": false,
  "authorNull": false,
  "authorString": false,
  "authorMissingId": false,
  "authorMinimalOk": true,
  "optionalDisplayFieldsAbsent": true
};
  assert.deepStrictEqual(actual, expected, "argument index shapes: production validator regression");
  checks += Object.keys(expected).length;
}

// Former test_argument_shape_validators_reject_unusable_rows: argument detail shapes.
{
  const actual = (() => {
                const base = () => ({ id: 'A-1', kind: 'argument', targets: [], sources: [],
                                      responses: [], mentions: [], verdicts: [] });
                const withField = (k, v) => { const o = base(); o[k] = v; return o; };
                return {
                    ok: prksIsArgumentShape(base(), 'A-1'),
                    stanceOk: prksIsArgumentShape(withField('kind', 'stance'), 'A-1'),
                    wrongId: prksIsArgumentShape(base(), 'A-2'),
                    badKind: prksIsArgumentShape(withField('kind', 'claim'), 'A-1'),
                    targetsNotArray: prksIsArgumentShape(withField('targets', {}), 'A-1'),
                    sourcesNotArray: prksIsArgumentShape(withField('sources', null), 'A-1'),
                    responsesNotArray: prksIsArgumentShape(withField('responses', {}), 'A-1'),
                    mentionsNotArray: prksIsArgumentShape(withField('mentions', 'x'), 'A-1'),
                    verdictsNotArray: prksIsArgumentShape(withField('verdicts', {}), 'A-1'),
                    targetMissingId: prksIsArgumentShape(
                        withField('targets', [{ type: 'argument' }]), 'A-1'),
                    sourceMissingWorkId: prksIsArgumentShape(
                        withField('sources', [{ authors: [] }]), 'A-1'),
                    authorNull: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [null] }]), 'A-1'),
                    authorString: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: ['bad'] }]), 'A-1'),
                    authorMissingId: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [{}] }]), 'A-1'),
                    authorMinimalOk: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [{ id: 'P-1' }] }]), 'A-1'),
                    blankId: prksIsArgumentShape(
                        { id: '   ', kind: 'argument', targets: [], sources: [],
                          responses: [], mentions: [], verdicts: [] }, '   '),
                    responseMissingId: prksIsArgumentShape(
                        withField('responses', [{ name: 'x' }]), 'A-1'),
                    responseBadKind: prksIsArgumentShape(
                        withField('responses', [{ id: 'A-9', kind: 'claim' }]), 'A-1'),
                    mentionMissingWorkId: prksIsArgumentShape(
                        withField('mentions', [{ title: 'x' }]), 'A-1'),
                    verdictMissingId: prksIsArgumentShape(
                        withField('verdicts', [{ label: 'Supports' }]), 'A-1'),
                    errorBody: prksIsArgumentShape({ error: 'boom' }, 'A-1'),
                    arrayBody: prksIsArgumentShape([], 'A-1'),
                    optionalDisplayFieldsAbsent: prksIsArgumentShape({
                        id: 'A-1', kind: 'argument',
                        targets: [{ id: 'A-9', type: 'argument' }],
                        sources: [{ work_id: 'W-1', authors: [] }],
                        responses: [{ id: 'A-8' }],
                        mentions: [{ work_id: 'W-2' }],
                        verdicts: [{ id: 'supports' }],
                    }, 'A-1'),
                    authorDisplayFieldsAbsent: prksIsArgumentShape(
                        withField('sources', [{ work_id: 'W-1', authors: [
                            { id: 'P-1', first_name: '', last_name: '', credit_name: null },
                        ] }]), 'A-1'),
                };
            })();
  const expected = {
  "ok": true,
  "stanceOk": true,
  "wrongId": false,
  "badKind": false,
  "targetsNotArray": false,
  "sourcesNotArray": false,
  "responsesNotArray": false,
  "mentionsNotArray": false,
  "verdictsNotArray": false,
  "targetMissingId": false,
  "sourceMissingWorkId": false,
  "authorNull": false,
  "authorString": false,
  "authorMissingId": false,
  "authorMinimalOk": true,
  "blankId": false,
  "responseMissingId": false,
  "responseBadKind": false,
  "mentionMissingWorkId": false,
  "verdictMissingId": false,
  "errorBody": false,
  "arrayBody": false,
  "optionalDisplayFieldsAbsent": true,
  "authorDisplayFieldsAbsent": true
};
  assert.deepStrictEqual(actual, expected, "argument detail shapes: production validator regression");
  checks += Object.keys(expected).length;
}

// Former test_people_validator_matches_renderer_scalar_contract: every recorded assertion must hold.
{
  const actual = (() => {
                const scalarFields = [
                    'first_name', 'last_name', 'aliases', 'about', 'image_url',
                    'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep',
                    'links_other', 'birth_date', 'death_date',
                ];
                const badValues = [{}, [], 3, true];
                const allowedValues = [undefined, null, '', 'value'];
                const detail = () => ({ id: 'P-1', works: [], groups: [] });
                const index = () => ({ id: 'P-1', assigned_roles: [], groups: [] });
                const withField = (base, field, value) => Object.assign(base, { [field]: value });
                const workWith = (field, value) => ({
                    id: 'P-1', works: [withField({ id: 'W-1' }, field, value)], groups: [],
                });
                const malformedExamples = {
                    first_name: {}, last_name: [], aliases: [], about: {}, image_url: [],
                    link_wikipedia: 7, link_stanford_encyclopedia: false, link_iep: {},
                    links_other: {}, birth_date: [], death_date: true,
                };
                return {
                    optionalStringContract:
                        prksIsOptionalString(undefined) && prksIsOptionalString(null) &&
                        prksIsOptionalString('') && prksIsOptionalString('value') &&
                        !prksIsOptionalString([]) && !prksIsOptionalString({}) &&
                        !prksIsOptionalString(3) && !prksIsOptionalString(true),
                    everyScalarRejectsEveryBadType: scalarFields.every((field) =>
                        badValues.every((value) =>
                            !prksIsPersonShape(withField(detail(), field, value), 'P-1') &&
                            !prksIsPeopleIndexShape([withField(index(), field, value)])
                        )
                    ),
                    everyScalarAcceptsOptionalStrings: scalarFields.every((field) =>
                        allowedValues.every((value) =>
                            prksIsPersonShape(withField(detail(), field, value), 'P-1') &&
                            prksIsPeopleIndexShape([withField(index(), field, value)])
                        )
                    ),
                    everyAuditedMalformedExampleRejected: Object.entries(malformedExamples).every(
                        ([field, value]) =>
                            !prksIsPersonShape(withField(detail(), field, value), 'P-1') &&
                            !prksIsPeopleIndexShape([withField(index(), field, value)])
                    ),
                    sparseDetailAccepted: prksIsPersonShape({
                        id: 'P-1', first_name: null, last_name: '', aliases: '', about: '',
                        image_url: null, link_wikipedia: null,
                        link_stanford_encyclopedia: null, link_iep: null, links_other: '',
                        birth_date: null, death_date: null, works: [], groups: [],
                    }, 'P-1'),
                    sparseIndexAccepted: prksIsPeopleIndexShape([{
                        id: 'P-1', first_name: null, last_name: '', aliases: '', about: '',
                        image_url: null, link_wikipedia: null,
                        link_stanford_encyclopedia: null, link_iep: null, links_other: '',
                        birth_date: null, death_date: null, assigned_roles: [], groups: [],
                    }]),
                    workScalarsRejectBadTypes: ['year', 'published_date'].every((field) =>
                        badValues.every((value) => !prksIsPersonShape(workWith(field, value), 'P-1'))
                    ),
                    workScalarsAcceptOptionalStrings: ['year', 'published_date'].every((field) =>
                        allowedValues.every((value) => prksIsPersonShape(workWith(field, value), 'P-1'))
                    ),
                };
            })();
  assert.equal(Object.keys(actual).length, 8, "People scalar contract matrix must remain complete");
  for (const [caseName, valid] of Object.entries(actual)) {
    assert.equal(valid, true, "people: " + caseName);
    checks++;
  }
}

// Former test_predicates_protect_every_rendered_field: playlist index + detail shapes.
{
  const actual = (() => ({
                indexEmpty: prksIsPlaylistsIndexShape([]),
                indexOk: prksIsPlaylistsIndexShape([{ id: 'PL-1', title: 'A', item_count: 0 }]),
                indexSparse: prksIsPlaylistsIndexShape([{ id: 'PL-1', item_count: 3 }]),
                indexNotArray: prksIsPlaylistsIndexShape({ error: 'boom' }),
                indexNull: prksIsPlaylistsIndexShape(null),
                indexMissingId: prksIsPlaylistsIndexShape([{ title: 'A', item_count: 0 }]),
                indexBlankId: prksIsPlaylistsIndexShape([{ id: '  ', title: 'A', item_count: 0 }]),
                indexNestedArrayRow: prksIsPlaylistsIndexShape([['PL-1']]),
                indexCountMissing: prksIsPlaylistsIndexShape([{ id: 'PL-1', title: 'A' }]),
                indexCountString: prksIsPlaylistsIndexShape([{ id: 'PL-1', item_count: '3' }]),
                indexCountNegative: prksIsPlaylistsIndexShape([{ id: 'PL-1', item_count: -1 }]),
                indexTitleNumber: prksIsPlaylistsIndexShape([{ id: 'PL-1', title: 7, item_count: 0 }]),
                indexUrlObject: prksIsPlaylistsIndexShape([{ id: 'PL-1', original_url: {}, item_count: 0 }]),

                detailOk: prksIsPlaylistShape({ id: 'PL-1', title: 'A', items: [] }, 'PL-1'),
                detailWithItem: prksIsPlaylistShape({ id: 'PL-1', items: [
                    { id: 'W-1', title: 'V', author_text: 'C', published_date: '2020-01-01', position: 0 }] }, 'PL-1'),
                detailSparseItem: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', position: 0 }] }, 'PL-1'),
                detailWrongId: prksIsPlaylistShape({ id: 'PL-2', items: [] }, 'PL-1'),
                detailNull: prksIsPlaylistShape(null, 'PL-1'),
                detailArray: prksIsPlaylistShape([], 'PL-1'),
                detailItemsMissing: prksIsPlaylistShape({ id: 'PL-1' }, 'PL-1'),
                detailItemsNotArray: prksIsPlaylistShape({ id: 'PL-1', items: {} }, 'PL-1'),
                detailItemNull: prksIsPlaylistShape({ id: 'PL-1', items: [null] }, 'PL-1'),
                detailItemBlankId: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: '', position: 0 }] }, 'PL-1'),
                detailItemNestedArray: prksIsPlaylistShape({ id: 'PL-1', items: [['W-1']] }, 'PL-1'),
                detailItemBadTitle: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', title: 3, position: 0 }] }, 'PL-1'),
                detailItemBadChannel: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', author_text: [], position: 0 }] }, 'PL-1'),
                detailItemBadDate: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', published_date: 2020, position: 0 }] }, 'PL-1'),
                detailItemNoPosition: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1' }] }, 'PL-1'),
                detailItemFractionalPosition: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', position: 1.5 }] }, 'PL-1'),
                detailItemNegativePosition: prksIsPlaylistShape({ id: 'PL-1', items: [{ id: 'W-1', position: -1 }] }, 'PL-1'),
                detailOneBadItem: prksIsPlaylistShape({ id: 'PL-1', items: [
                    { id: 'W-1', position: 0 }, { id: 'W-2' }] }, 'PL-1'),
            }))();
  const expected = {
  "indexEmpty": true,
  "indexOk": true,
  "indexSparse": true,
  "indexNotArray": false,
  "indexNull": false,
  "indexMissingId": false,
  "indexBlankId": false,
  "indexNestedArrayRow": false,
  "indexCountMissing": false,
  "indexCountString": false,
  "indexCountNegative": false,
  "indexTitleNumber": false,
  "indexUrlObject": false,
  "detailOk": true,
  "detailWithItem": true,
  "detailSparseItem": true,
  "detailWrongId": false,
  "detailNull": false,
  "detailArray": false,
  "detailItemsMissing": false,
  "detailItemsNotArray": false,
  "detailItemNull": false,
  "detailItemBlankId": false,
  "detailItemNestedArray": false,
  "detailItemBadTitle": false,
  "detailItemBadChannel": false,
  "detailItemBadDate": false,
  "detailItemNoPosition": false,
  "detailItemFractionalPosition": false,
  "detailItemNegativePosition": false,
  "detailOneBadItem": false
};
  assert.deepStrictEqual(actual, expected, "playlist index + detail shapes: production validator regression");
  checks += Object.keys(expected).length;
}

assert.equal(checks, 102, "all four migrated E2E matrices must remain complete");
console.log("All " + checks + " offline read-model shape checks passed");
