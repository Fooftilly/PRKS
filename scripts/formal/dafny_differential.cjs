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
const path = require('path');
const { createRequire } = require('module');

const [generatedPath, prefix] = process.argv.slice(2);
const requireFrom = createRequire(path.join(path.resolve(prefix), 'noop.js'));
const fc = requireFrom('fast-check');
const BigNumber = requireFrom('bignumber.js');

const generated = fs.readFileSync(generatedPath, 'utf8') + '\nmodule.exports = { _dafny, Reconcile };\n';
const loaded = { exports: {} };
new Function('require', 'module', 'exports', generated)(requireFrom, loaded, loaded.exports);
const { _dafny, Reconcile: D } = loaded.exports;
const M = D.__default;

const root = path.resolve(__dirname, '..', '..');
const classic = new Function(fs.readFileSync(path.join(root, 'frontend/js/sync-reconcile.js'), 'utf8') +
    '\nreturn prksSyncReconcile;')();

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
function time(v) {
    let t = null;
    if (typeof v === 'number') t = Number.isFinite(v) ? v : null;
    else if (typeof v === 'string' && v !== '') { const p = Date.parse(v); t = Number.isFinite(p) ? p : null; }
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
const instant = fc.integer({ min: 0, max: 4 }).map(n => Date.UTC(2026, 10, 27, 12, 0, n));
const anyTime = fc.oneof({ weight: 3, arbitrary: instant },
    { weight: 2, arbitrary: instant.map(t => new Date(t).toISOString()) },
    { weight: 1, arbitrary: fc.constantFrom(undefined, null, '', 'later', Number.NaN, Number.POSITIVE_INFINITY, {}) });
const ack = fc.record({ ackRevision: anyRevision, heldRevision: anyRevision, observedAt: anyTime,
    operationCreatedAt: anyTime, agrees: fc.constantFrom(true, false, undefined) }, { requiredKeys: ['ackRevision'] });
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

/* ---- properties ---- */
const same = (a, b) => _dafny.areEqual(a, b);
const checks = [
    ['planAck', fc.property(ack, e => same(outcome(() => classic.planAck(e)), M.PlanAck(evidence(e))))],
    ['decideAck', fc.property(ack, e => same(outcome(() => classic.decideAck(e)), M.DecideAck(evidence(e))))],
    ['pinEditBase (per field)', fc.property(pinField, f => {
        const shown = f.shown == null ? '' : String(f.shown);
        const got = classic.pinEditBase({ x: f.shown }, { x: f.read }, f.own === undefined ? undefined : { x: f.own });
        return same(base(got.x), M.PinField(str(shown), entry(f.read), own(f.own))[0]);
    })],
    ['newerEditBase', fc.property(anyEntry, fc.option(fieldBase, { nil: null }), (h, r) =>
        same(asEntry(classic.newerEditBase(h, r)), M.NewerEditBase(entry(h), asEntry(r))))],
    ['acknowledgeEditBase', fc.property(anyEntry, write, (e, w) =>
        same(ackResult(classic.acknowledgeEditBase(e, w)),
            M.AcknowledgeEditBase(entry(e), str(w.value), rev(w.baseRevision), nat(w.ackRevision))))],
];

let failed = 0;
for (const [name, property] of checks) {
    const result = fc.check(property, PARAMS);
    console.log((result.failed ? 'FAIL ' : 'ok   ') + name + (result.failed ? '\n' + fc.defaultReportMessage(result) : ''));
    if (result.failed) failed += 1;
}
console.log(`${checks.length - failed} of ${checks.length} functions agree on ${RUNS} runs each (seed ${PARAMS.seed})`);
process.exit(failed ? 1 : 0);
