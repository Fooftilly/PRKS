'use strict';
/* Differential test (#1472): the Dafny model, compiled to JS at test time,
 * against the shipped frontend/js/sync-reconcile.js on generated inputs.
 *
 * Usage: node dafny_differential.cjs <generated reconcile.js> <node_modules prefix>
 *
 * The conversions below are trusted boundary code: they map a JS value to the
 * model's input type, and production output to the model's output type. Tags
 * are dropped from the model output; nothing is read back from production
 * except its plain return value. */
const fs = require('fs');
const os = require('os');
const path = require('path');

const [generatedPath, prefix] = process.argv.slice(2);
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-difftest-'));
process.on('exit', () => fs.rmSync(work, { recursive: true, force: true }));
fs.symlinkSync(path.resolve(prefix), path.join(work, 'node_modules'), 'dir');
function load(name, source) {
    const file = path.join(work, name);
    fs.writeFileSync(file, source);
    return require(file);
}
const fc = load('fc.cjs', "module.exports = require('fast-check');\n");
const BigNumber = load('bignumber.cjs', "module.exports = require('bignumber.js');\n");

const { _dafny, Reconcile: D } = load('model.cjs',
    fs.readFileSync(generatedPath, 'utf8') + '\nmodule.exports = { _dafny, Reconcile };\n');
const M = D.__default;

const root = path.resolve(__dirname, '..', '..');
const classicSource = fs.readFileSync(path.join(root, 'frontend/js/sync-reconcile.js'), 'utf8');
let classic = load('classic.cjs', classicSource);

const RUNS = Number.parseInt(process.env.PRKS_FC_RUNS || '', 10) || 5000;
const SEED = Number.parseInt(process.env.PRKS_FC_SEED || '', 10);
const PARAMS = { numRuns: RUNS, seed: Number.isSafeInteger(SEED) ? SEED : 1472 };

/* ---- JS value -> model input ---- */
const str = s => _dafny.Seq.UnicodeFromString(s);
const nat = n => new BigNumber(n);
const isRev = v => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const opt = v => (typeof v === 'string' ? D.Option.create_Some(str(v)) : D.Option.create_None());
const rev = v => (isRev(v) ? D.Rev.create_Valid(nat(v)) : D.Rev.create_Invalid());
const held = v => (v === undefined || v === null ? D.Held.create_Absent() : D.Held.create_Present(rev(v)));
function instant(v) {
    if (typeof v === 'number') return Number.isFinite(v) ? v : null;
    if (typeof v === 'string' && v !== '') { const p = Date.parse(v); return Number.isFinite(p) ? p : null; }
    return null;
}
function time(v) {
    const t = instant(v);
    return t === null ? D.Time.create_NoTime() : D.Time.create_At(nat(t));
}
const isBase = v => !!v && typeof v === 'object' && typeof v.value === 'string' && isRev(v.revision);
const entry = v => (isBase(v)
    ? D.Entry.create_Ok(str(v.value), nat(v.revision), opt(v.pendingOp), opt(v.ackOp))
    : D.Entry.create_Broken());
function own(v) {
    if (v === undefined) return D.Own.create_NoOwn();
    if (!isBase(v)) return D.Own.create_OwnBroken();
    return D.Own.create_Own(str(v.value), nat(v.revision), opt(v.pendingOp));
}
const evidence = e => D.Evidence.create_Evidence(rev(e.ackRevision), held(e.heldRevision),
    time(e.observedAt), time(e.operationCreatedAt), e.agrees === true);

/* ---- production output -> model output ---- */
const PLANS = { superseded: 'create_Superseded', unchanged: 'create_Unchanged', apply: 'create_Apply', unproven: 'create_Unproven' };
function outcome(run) {
    try {
        const plan = run();
        if (!Object.prototype.hasOwnProperty.call(PLANS, plan)) throw new Error('unknown plan ' + plan);
        return D.Outcome.create_Return(D.Plan[PLANS[plan]]());
    } catch (error) {
        if (error instanceof TypeError) return D.Outcome.create_Throws();
        throw error;
    }
}
function onlyKeys(value, allowed) {
    for (const key of Object.keys(value)) if (!allowed.includes(key)) throw new Error('unexpected key ' + key);
}
function base(pin) {
    if (pin === null) return D.Option.create_None();
    onlyKeys(pin, ['value', 'revision', 'pending', 'pendingOp', 'ackOp']);
    return D.Option.create_Some(D.Base.create_Base(str(pin.value), nat(pin.revision), opt(pin.pending),
        opt(pin.pendingOp), opt(pin.ackOp)));
}
function ackResult(got) {
    if (got === undefined) return D.AckResult.create_Keep();
    if (got === null) return D.AckResult.create_Clear();
    onlyKeys(got, ['value', 'revision']);
    return D.AckResult.create_Pair(str(got.value), nat(got.revision));
}
const asEntry = v => (v === null ? D.Entry.create_Broken() : entry(v));

/* ---- inputs (the same small domains as reconcile.property.test.ts) ---- */
const MAX = Number.MAX_SAFE_INTEGER;
const validRevision = fc.oneof(fc.nat({ max: 6 }), fc.constantFrom(MAX - 1, MAX));
const invalidRevision = fc.constantFrom(-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, MAX + 1, '3', null, undefined, {});
const anyRevision = fc.oneof({ weight: 4, arbitrary: validRevision }, { weight: 1, arbitrary: invalidRevision });
const moment = fc.integer({ min: 0, max: 4 }).map(n => Date.UTC(2026, 10, 27, 12, 0, n));
const anyTime = fc.oneof({ weight: 3, arbitrary: moment },
    { weight: 2, arbitrary: moment.map(t => new Date(t).toISOString()) },
    { weight: 1, arbitrary: fc.constantFrom(undefined, null, '', 'later', Number.NaN, Number.POSITIVE_INFINITY, {}) });
const ackWith = ackRevision => fc.record({ ackRevision, heldRevision: anyRevision, observedAt: anyTime,
    operationCreatedAt: anyTime, agrees: fc.constantFrom(true, false, undefined) }, { requiredKeys: ['ackRevision'] });
const ack = ackWith(anyRevision);
const text = fc.constantFrom('', 'A', 'B', ' A ');
const opId = fc.constantFrom('op-1', 'op-2');
const fieldBase = fc.record({ value: text, revision: fc.nat({ max: 3 }), pendingOp: opId, ackOp: opId },
    { requiredKeys: ['value', 'revision'] });
const broken = fc.constantFrom(null, undefined, {}, { value: 'A' }, { value: 1, revision: 0 },
    { value: 'A', revision: -1 }, { value: 'A', revision: 0.5 });
const anyEntry = fc.oneof({ weight: 5, arbitrary: fieldBase }, { weight: 1, arbitrary: broken });
const shownValue = fc.oneof(text, fc.constantFrom(null, undefined, 0));
const coherent = fc.record({ shown: text, readSame: fc.boolean(), readRevision: fc.nat({ max: 3 }),
    hasOwn: fc.boolean(), ownRevision: fc.nat({ max: 3 }), pendingOp: fc.option(opId, { nil: undefined }), other: text })
    .map(f => ({ shown: f.shown, read: { value: f.readSame ? f.shown : f.other, revision: f.readRevision },
        own: f.hasOwn ? Object.assign({ value: f.shown, revision: f.ownRevision }, f.pendingOp ? { pendingOp: f.pendingOp } : {}) : undefined }));
const pinField = fc.oneof(
    fc.record({ shown: shownValue, read: anyEntry, own: fc.option(anyEntry, { nil: undefined }) }), coherent);
const write = fc.record({ value: text, baseRevision: anyRevision, ackRevision: fc.nat({ max: 3 }) });

/* ---- branch labels: the model's case split, from the model's inputs ---- */
const FLOOR = 20;
const MIN_RUNS = 1000;
const ACK_TIME = ['time:before', 'time:equal', 'time:after', 'time:missing'];
function timing(e) {
    const o = instant(e.observedAt);
    const c = instant(e.operationCreatedAt);
    if (o === null || c === null) return 'time:missing';
    return o < c ? 'time:before' : o === c ? 'time:equal' : 'time:after';
}
const heldOf = e => (e.heldRevision === undefined || e.heldRevision === null ? 'absent'
    : isRev(e.heldRevision) ? 'valid' : 'invalid');
function planAckLabel(e) {
    const valid = isRev(e.ackRevision);
    const held = heldOf(e);
    if (held !== 'absent' && !valid) return 'held:invalid-ack';
    if (held === 'valid' && e.ackRevision < e.heldRevision) return 'held:superseded';
    if (e.agrees === true) return valid ? 'agrees' : 'agrees:invalid-ack';
    if (!valid) return 'invalid-ack';
    return timing(e);
}
function decideAckLabel(e) {
    const held = heldOf(e);
    if (!isRev(e.ackRevision)) return 'invalid-ack';
    if (held === 'invalid') return 'held:invalid';
    if (held === 'valid') return e.ackRevision < e.heldRevision ? 'held:superseded' : 'held:apply';
    if (e.agrees === true) return 'agrees';
    return timing(e);
}
const TAGS = ['ReadPair', 'MaxRead', 'MaxWrite', 'WriteBase', 'Unknown'];
function pinLabel(f) {
    const shown = f.shown == null ? '' : String(f.shown);
    const tag = M.PinField(str(shown), entry(f.read), own(f.own))[1];
    const name = TAGS.find(n => _dafny.areEqual(tag, D.Tag['create_' + n]()));
    return name === 'Unknown' && !isBase(f.read) ? 'Unknown:broken' : name;
}
function newerLabel(h, r) {
    if (!isBase(h) || r === null) return 'broken';
    if (h.revision > r.revision) return 'held-newer';
    return h.revision === r.revision && typeof h.ackOp === 'string' ? 'equal-stamped' : 'read';
}
function acknowledgeLabel(e, w) {
    if (!isBase(e)) return 'broken';
    if (e.revision > w.ackRevision) return 'newer-kept';
    if (e.revision === w.ackRevision) return typeof e.ackOp === 'string' ? 'equal-stamped-kept' : 'equal-pair';
    return isRev(w.baseRevision) && e.revision === w.baseRevision ? 'base-pair' : 'clear';
}

/* ---- properties ---- */
const same = (a, b) => _dafny.areEqual(a, b);
const checks = [
    { name: 'planAck', arbs: [ack], label: planAckLabel,
        branches: ['held:invalid-ack', 'held:superseded', 'agrees', 'agrees:invalid-ack', 'invalid-ack', ...ACK_TIME],
        agree: e => same(outcome(() => classic.planAck(e)), M.PlanAck(evidence(e))) },
    { name: 'decideAck', arbs: [ack], label: decideAckLabel,
        branches: ['invalid-ack', 'held:invalid', 'held:superseded', 'held:apply', 'agrees', ...ACK_TIME],
        agree: e => same(outcome(() => classic.decideAck(e)), M.DecideAck(evidence(e))) },
    { name: 'pinEditBase (per field)', arbs: [pinField], label: pinLabel,
        branches: [...TAGS, 'Unknown:broken'],
        agree: f => {
            const shown = f.shown == null ? '' : String(f.shown);
            const got = classic.pinEditBase({ x: f.shown }, { x: f.read }, f.own === undefined ? undefined : { x: f.own });
            return same(base(got.x), M.PinField(str(shown), entry(f.read), own(f.own))[0]);
        } },
    { name: 'newerEditBase', arbs: [anyEntry, fc.option(fieldBase, { nil: null })], label: newerLabel,
        branches: ['broken', 'held-newer', 'equal-stamped', 'read'],
        agree: (h, r) => same(asEntry(classic.newerEditBase(h, r)), M.NewerEditBase(entry(h), asEntry(r))) },
    { name: 'acknowledgeEditBase', arbs: [anyEntry, write], label: acknowledgeLabel,
        branches: ['broken', 'newer-kept', 'equal-stamped-kept', 'equal-pair', 'base-pair', 'clear'],
        agree: (e, w) => same(ackResult(classic.acknowledgeEditBase(e, w)),
            M.AcknowledgeEditBase(entry(e), str(w.value), rev(w.baseRevision), nat(w.ackRevision))) },
];

function counted(check, counts, arbs = check.arbs) {
    return fc.property(...arbs, (...args) => {
        const label = check.label(...args);
        counts.set(label, (counts.get(label) || 0) + 1);
        return check.agree(...args);
    });
}
function coverage(check, counts) {
    const low = check.branches.filter(b => (counts.get(b) || 0) < FLOOR);
    const unknown = [...counts.keys()].filter(k => !check.branches.includes(k));
    return low.map(b => `${b} hit ${counts.get(b) || 0} < ${FLOOR}`).concat(unknown.map(k => `unlisted branch ${k}`));
}

if (RUNS < MIN_RUNS) {
    console.log(`FAIL ${RUNS} runs is below the minimum of ${MIN_RUNS}`);
    process.exit(1);
}

/* Controls: a known-wrong build MUST fail, and a generator without invalid
 * ACK revisions MUST miss the branch floor. */
const strictTime = 'read !== null && created !== null && read < created';
if (classicSource.split(strictTime).length !== 2) throw new Error('control mutation does not match exactly once');
classic = load('mutant.cjs', classicSource.replace(strictTime, 'read !== null && created !== null && read <= created'));
if (!fc.check(counted(checks[0], new Map()), PARAMS).failed) {
    console.log('FAIL control: a <= mutant of sync-reconcile.js passed the planAck check');
    process.exit(1);
}
classic = require(path.join(work, 'classic.cjs'));
const narrow = new Map();
fc.check(counted(checks[0], narrow, [ackWith(validRevision)]), PARAMS);
if (!coverage(checks[0], narrow).some(gap => gap.startsWith('invalid-ack '))) {
    console.log('FAIL control: a generator without invalid ACK revisions met the planAck branch floor');
    process.exit(1);
}

let failed = 0;
for (const check of checks) {
    const counts = new Map();
    const result = fc.check(counted(check, counts), PARAMS);
    const gaps = result.failed ? [] : coverage(check, counts);
    const hits = check.branches.map(b => `${b}=${counts.get(b) || 0}`).join(' ');
    if (result.failed) console.log('FAIL ' + check.name + '\n' + fc.defaultReportMessage(result));
    else if (gaps.length) console.log('FAIL ' + check.name + ' coverage: ' + gaps.join(', '));
    else console.log('ok   ' + check.name + ': ' + hits);
    if (result.failed || gaps.length) failed += 1;
}
console.log(`${checks.length - failed} of ${checks.length} functions agree on ${RUNS} runs each with every branch hit at least ${FLOOR} times (seed ${PARAMS.seed})`);
process.exit(failed ? 1 : 0);
