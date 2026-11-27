'use strict';
/* Bounded sequence differential (#1472): every event sequence of length <= 8,
 * with <= 2 other-device writes, <= 1 reload, <= 1 clock step and <= 1 second
 * sender, run on the compiled Sequence model and on an adapter that composes
 * the shipped sync-reconcile.js in the order the Folder and Playlist handlers
 * call it, against a server oracle with the handlers' decision table. After
 * each event the two MUST allow the same events and hold the same observable
 * state. Model-only ghost state (origin, curAtCreate, the pin tag) is not
 * compared.
 *
 * Usage: node dafny_sequences.cjs <generated model.js> <node_modules prefix> */
const { loader } = require('./model_loader.cjs');

const [generatedPath, prefix] = process.argv.slice(2);
const loaded = loader(generatedPath, prefix);
const { BigNumber, _dafny } = loaded;
const S = loaded.Sequence;
const Q = S.__default;

const DEPTH = Number.parseInt(process.env.PRKS_SEQ_DEPTH || '', 10) || 8;
const BUDGET = { other: 2, reload: 1, clock: 1, replay: 1 };
const VALUES = ['A', 'B', 'C', ' B '];
const CLOCK_TO = -60;

/* ---- model values ---- */
const nat = n => new BigNumber(n);
const toVal = v => ({ A: S.Val.create_A(), B: S.Val.create_B(), C: S.Val.create_C(), ' B ': S.Val.create_SpacedB() })[v];
const ofVal = v => (v.is_A ? 'A' : v.is_B ? 'B' : v.is_C ? 'C' : ' B ');
const num = n => n.toNumber();
const some = (o, f) => (o.is_Some ? f(o.dtor_value) : null);
const text = seq => (typeof seq.toVerbatimString === 'function' ? seq.toVerbatimString(false) : Array.from(seq).join(''));

function toEvent(e) {
    switch (e.kind) {
    case 'Read': return S.Event.create_Read(nat(e.r), e.stored);
    case 'ReadProjection': return S.Event.create_ReadProjection(nat(e.r));
    case 'ReadEditBase': return S.Event.create_ReadEditBase(nat(e.r), e.stored);
    case 'Open': return S.Event.create_Open();
    case 'Save': return S.Event.create_Save(toVal(e.v));
    case 'Send': return S.Event.create_Send();
    case 'Discard': return S.Event.create_Discard();
    case 'OtherWrite': return S.Event.create_OtherWrite(toVal(e.v));
    case 'Deliver': return S.Event.create_Deliver();
    case 'Replay': return S.Event.create_Replay(nat(e.id));
    case 'ClockStep': return S.Event.create_ClockStep(new BigNumber(e.to));
    case 'Reload': return S.Event.create_Reload();
    default: throw new Error('unknown event ' + e.kind);
    }
}

function mapEntries(m) {
    const out = [];
    for (const k of m.Keys.Elements) out.push([num(k), m.get(k)]);
    return out.sort((a, b) => a[0] - b[0]);
}

function modelView(s) {
    return {
        history: Array.from(s.dtor_history).map(ofVal),
        now: num(s.dtor_now),
        cache: some(s.dtor_cache, c => ({ value: ofVal(c.dtor_value), observed: num(c.dtor_observed), rev: num(c.dtor_rev) })),
        proj: some(s.dtor_proj, num),
        editBase: s.dtor_editBase.is_Broken ? null : {
            value: ofVal(s.dtor_editBase.dtor_value), revision: num(s.dtor_editBase.dtor_revision),
            ackOp: some(s.dtor_editBase.dtor_ackOp, text),
        },
        op: some(s.dtor_op, o => ({ id: num(o.dtor_id), value: ofVal(o.dtor_value), base: num(o.dtor_base), created: num(o.dtor_created) })),
        refused: s.dtor_refused,
        ledger: mapEntries(s.dtor_ledger).map(([k, v]) => [k, num(v)]),
        form: some(s.dtor_form, f => ({
            shown: ofVal(f.dtor_shown),
            pin: some(f.dtor_pin, b => ({
                value: ofVal(b.dtor_value), revision: num(b.dtor_revision),
                pending: some(b.dtor_pending, ofVal), pendingOp: some(b.dtor_pendingOp, text), ackOp: some(b.dtor_ackOp, text),
            })),
        })),
    };
}

/* ---- implementation adapter ---- */
const canonical = v => v.trim();
const opId = id => String(id);

function initial() {
    return { history: ['A'], now: 0, cache: null, proj: null, editBase: null, op: null, refused: false, ops: {}, ledger: {}, form: null, nextId: 0 };
}
const cur = s => s.history.length - 1;
const inLedger = (s, id) => Object.prototype.hasOwnProperty.call(s.ledger, id);

function implEnabled(mode, s, e) {
    switch (e.kind) {
    case 'Read': case 'ReadProjection': case 'ReadEditBase': return e.r <= cur(s);
    case 'Open': return s.op !== null || s.cache !== null;
    /* A queued edit that is in flight or in conflict makes a Save scope_busy. */
    case 'Save': return s.form !== null && (s.op === null || (!inLedger(s, s.op.id) && !s.refused));
    case 'Send': return s.op !== null && !inLedger(s, s.op.id) && !s.refused && s.op.base <= cur(s);
    case 'Discard': return s.refused;
    case 'OtherWrite': case 'Reload': return true;
    case 'Deliver': return s.op !== null && inLedger(s, s.op.id);
    case 'Replay': return mode === 'M0' && inLedger(s, e.id) && Object.prototype.hasOwnProperty.call(s.ops, e.id);
    case 'ClockStep': return mode === 'M0';
    default: throw new Error('unknown event ' + e.kind);
    }
}

/* SET_FOLDER_FIELD and SET_PLAYLIST_FIELD: a stale base conflicts unless
 * the server already holds the value; an unchanged value is a no-op ACK. */
function serve(s, o) {
    const desired = canonical(o.value);
    const revision = cur(s);
    const current = s.history[revision];
    if (o.base < revision && current !== desired) return { code: 'REVISION_CONFLICT' };
    if (current === desired) return { code: 'ACKNOWLEDGED', revision, changed: false };
    s.history.push(desired);
    return { code: 'ACKNOWLEDGED', revision: revision + 1, changed: true };
}

function reconcile(mode, s, o, ack, C, stats) {
    /* reconcileFolderField: the projection decides first. */
    if (s.proj !== null && C.decideAck({ ackRevision: ack, heldRevision: s.proj }) === 'superseded') {
        stats.hit('reconcile:superseded');
        return;
    }
    if (s.proj !== null) s.proj = ack;
    const value = canonical(o.value);
    /* patchProvenEntity keeps the entry's read time. */
    if (s.cache !== null) {
        const plan = C.planAck({ ackRevision: ack, agrees: s.cache.value === value,
            observedAt: s.cache.observedAt, operationCreatedAt: o.created });
        stats.hit('cache:' + plan);
        if (plan === 'apply') s.cache = { value, observedAt: s.cache.observedAt, revision: ack };
        else if (plan !== 'unchanged') s.cache = null;
    }
    /* patchEditBase, stamped with the payload (M0) or the stored value (M1). */
    const got = C.acknowledgeEditBase(s.editBase,
        { value: mode === 'M0' ? o.value : value, baseRevision: o.base, ackRevision: ack });
    stats.hit('editBase:' + (got === undefined ? 'keep' : got === null ? 'clear' : 'pair'));
    if (got === null) s.editBase = null;
    else if (got !== undefined) s.editBase = Object.assign({}, got, { ackOp: opId(o.id) });
}

/* settlePinnedPlaylistBase: only the edit base the shown edit's own ACK
 * stamped moves the pin, once that edit has left the queue. */
function settle(s, existing, pin) {
    const open = existing !== null && opId(existing.id) === pin.pendingOp;
    const now = s.editBase;
    if (typeof pin.pending !== 'string' || open || !now || now.ackOp !== pin.pendingOp || !(now.revision > pin.revision)) return pin;
    return { value: pin.pending, revision: now.revision };
}

/* local-store fieldWriteRevision. A Save cannot run while an edit is in
 * flight, so no ACKNOWLEDGED row of the shown edit is in the queue here. */
function writeRevision(existing, pin, desired, stats) {
    if (desired !== pin.value) return pin.revision;
    const original = existing !== null && (typeof pin.pending !== 'string'
        ? existing.base === pin.revision : opId(existing.id) === pin.pendingOp);
    if (original || typeof pin.pending !== 'string' || pin.pending === pin.value) return null;
    stats.hit('save:revert-queued');
    return pin.revision;
}

function implStep(mode, s0, e, C, stats) {
    const s = structuredClone(s0);
    switch (e.kind) {
    case 'Read':
        if (e.stored) s.cache = { value: s.history[e.r], observedAt: s.now, revision: e.r };
        break;
    case 'ReadProjection': s.proj = e.r; break;
    case 'ReadEditBase':
        if (e.stored) s.editBase = C.newerEditBase(s.editBase, { value: s.history[e.r], revision: e.r });
        break;
    case 'Open': {
        const shown = s.op !== null ? s.op.value : s.cache.value;
        const own = s.op !== null ? { x: { value: s.op.value, revision: s.op.base, pendingOp: opId(s.op.id) } } : undefined;
        s.form = { shown, pin: C.pinEditBase({ x: shown }, { x: s.editBase }, own).x };
        break;
    }
    case 'Save': {
        const f = s.form;
        s.form = null;
        if (f.pin === null || e.v === f.shown) { stats.hit(f.pin === null ? 'save:refused' : 'save:unchanged'); break; }
        /* setPlaylistFields: an unsent edit to another value is deleted
         * first; the write then gets its base revision, or none. */
        const existing = s.op;
        if (existing !== null && existing.value === e.v) { stats.hit('save:kept'); break; }
        s.op = null;
        const base = writeRevision(existing, settle(s, existing, f.pin), e.v, stats);
        if (base === null) { stats.hit(existing ? 'save:cancelled' : 'save:nothing'); break; }
        stats.hit('save:queued');
        s.op = { id: s.nextId, value: e.v, base, created: s.now };
        s.ops[s.nextId] = s.op;
        s.nextId += 1;
        break;
    }
    case 'Send': {
        const answer = serve(s, s.op);
        stats.hit('server:' + (answer.code === 'REVISION_CONFLICT' ? 'conflict' : answer.changed ? 'apply' : 'no-op'));
        if (answer.code === 'REVISION_CONFLICT') s.refused = true;
        else s.ledger[s.op.id] = answer.revision;
        break;
    }
    case 'Discard': s.op = null; s.refused = false; break;
    case 'OtherWrite': s.history.push(canonical(e.v)); break;
    case 'Deliver': reconcile(mode, s, s.op, s.ledger[s.op.id], C, stats); s.op = null; break;
    case 'Replay': reconcile(mode, s, s.ops[e.id], s.ledger[e.id], C, stats); break;
    case 'ClockStep': case 'Reload': break;
    default: throw new Error('unknown event ' + e.kind);
    }
    s.now = e.kind === 'ClockStep' ? e.to : s0.now + 1;
    return s;
}

const stringOr = v => (typeof v === 'string' ? v : null);

function implView(s) {
    const pin = s.form && s.form.pin;
    return {
        history: s.history.slice(),
        now: s.now,
        cache: s.cache && { value: s.cache.value, observed: s.cache.observedAt, rev: s.cache.revision },
        proj: s.proj,
        editBase: s.editBase && { value: s.editBase.value, revision: s.editBase.revision, ackOp: stringOr(s.editBase.ackOp) },
        op: s.op && { id: s.op.id, value: s.op.value, base: s.op.base, created: s.op.created },
        refused: s.refused,
        ledger: Object.keys(s.ledger).map(Number).sort((a, b) => a - b).map(k => [k, s.ledger[k]]),
        form: s.form && {
            shown: s.form.shown,
            pin: pin ? {
                value: pin.value, revision: pin.revision,
                pending: typeof pin.pending === 'string' ? pin.pending : null,
                pendingOp: stringOr(pin.pendingOp), ackOp: stringOr(pin.ackOp),
            } : null,
        },
    };
}

/* ---- enumeration ---- */
function candidates(s, budget) {
    const out = [];
    const top = cur(s);
    for (let r = 0; r <= top + 1; r += 1) {
        out.push({ kind: 'Read', r, stored: true }, { kind: 'Read', r, stored: false });
        out.push({ kind: 'ReadProjection', r });
        out.push({ kind: 'ReadEditBase', r, stored: true }, { kind: 'ReadEditBase', r, stored: false });
    }
    out.push({ kind: 'Open' }, { kind: 'Send' }, { kind: 'Discard' }, { kind: 'Deliver' });
    for (const v of VALUES) out.push({ kind: 'Save', v });
    if (budget.other > 0) for (const v of VALUES) out.push({ kind: 'OtherWrite', v });
    if (budget.reload > 0) out.push({ kind: 'Reload' });
    if (budget.clock > 0) out.push({ kind: 'ClockStep', to: CLOCK_TO });
    if (budget.replay > 0) for (let id = 0; id <= s.nextId; id += 1) out.push({ kind: 'Replay', id });
    return out;
}
const spend = (budget, e) => {
    const key = { OtherWrite: 'other', Reload: 'reload', ClockStep: 'clock', Replay: 'replay' }[e.kind];
    return key ? Object.assign({}, budget, { [key]: budget[key] - 1 }) : budget;
};
const show = e => e.kind + (e.r !== undefined ? '(' + e.r + (e.stored === undefined ? '' : ',' + e.stored) + ')' : '')
    + (e.v !== undefined ? '(' + JSON.stringify(e.v) + ')' : '') + (e.id !== undefined ? '(' + e.id + ')' : '')
    + (e.to !== undefined ? '(' + e.to + ')' : '');

function tally() {
    const counts = new Map();
    return { counts, hit: k => counts.set(k, (counts.get(k) || 0) + 1) };
}

/* Explores every sequence; a state pair already explored with at least as
 * many steps and budget left is not explored again. */
function explore(mode, C) {
    const m = S.Model['create_' + mode]();
    const stats = tally();
    const seen = new Map();
    let steps = 0;
    let failure = null;
    function visit(ms, is, depth, budget, trace) {
        if (failure || depth === 0) return;
        const key = JSON.stringify([is, budget]);
        if ((seen.get(key) ?? -1) >= depth) return;
        seen.set(key, depth);
        for (const e of candidates(is, budget)) {
            const de = toEvent(e);
            const modelOn = Q.Enabled(m, ms, de);
            const implOn = implEnabled(mode, is, e);
            if (modelOn !== implOn) {
                failure = { trace: trace.concat(show(e)), reason: `enabled: model ${modelOn}, implementation ${implOn}` };
                return;
            }
            if (!modelOn) continue;
            steps += 1;
            stats.hit('event:' + e.kind);
            const nextModel = Q.Step(m, ms, de);
            const nextImpl = implStep(mode, is, e, C, stats);
            const a = JSON.stringify(modelView(nextModel));
            const b = JSON.stringify(implView(nextImpl));
            if (a !== b) {
                failure = { trace: trace.concat(show(e)), reason: `state:\n  model          ${a}\n  implementation ${b}` };
                return;
            }
            visit(nextModel, nextImpl, depth - 1, spend(budget, e), trace.concat(show(e)));
            if (failure) return;
        }
    }
    visit(Q.Init(), initial(), DEPTH, BUDGET, []);
    return { failure, steps, states: seen.size, stats: stats.counts };
}

const REQUIRED = [
    'event:Read', 'event:ReadProjection', 'event:ReadEditBase', 'event:Open', 'event:Save', 'event:Send',
    'event:OtherWrite', 'event:Deliver', 'event:Discard', 'event:Reload',
    'save:queued', 'save:refused', 'save:unchanged', 'save:cancelled', 'save:revert-queued', 'server:apply', 'server:no-op', 'server:conflict',
    'reconcile:superseded', 'cache:apply', 'cache:unchanged', 'cache:unproven',
    'editBase:keep', 'editBase:pair',
];
const REQUIRED_M0 = ['event:Replay', 'event:ClockStep'];

let failed = 0;
for (const mode of ['M0', 'M1']) {
    const run = explore(mode, loaded.classic);
    const want = mode === 'M0' ? REQUIRED.concat(REQUIRED_M0) : REQUIRED;
    const missing = want.filter(k => !run.stats.get(k));
    if (run.failure) {
        console.log(`FAIL ${mode} after ${run.failure.trace.join(' ')}\n${run.failure.reason}`);
        failed += 1;
    } else if (missing.length) {
        console.log(`FAIL ${mode} never reached: ${missing.join(', ')}`);
        failed += 1;
    } else {
        console.log(`ok   ${mode}: ${run.steps} steps over ${run.states} states, depth ${DEPTH}`);
    }
}

/* Control: an adapter that stamps the payload in M1 MUST diverge from the model. */
const rawStamp = Object.assign({}, loaded.classic, {
    acknowledgeEditBase: (entry, write) => loaded.classic.acknowledgeEditBase(entry,
        Object.assign({}, write, { value: write.value === 'B' ? ' B ' : write.value })),
});
if (!explore('M1', rawStamp).failure) {
    console.log('FAIL control: an M1 adapter that stamps " B " for B agreed with the model');
    failed += 1;
}
process.exit(failed ? 1 : 0);
