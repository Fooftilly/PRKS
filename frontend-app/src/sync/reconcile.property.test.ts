/** Generated properties for the ACK and edit-base rules (#1472, step 1).
 *
 * `spec` states each rule as an exact table and labels the branch it took.
 * Every property runs against `reconcile.ts` and the built classic
 * `frontend/js/sync-reconcile.js`. Mutants of `reconcile.ts` MUST each fail
 * at least one property, and every spec branch MUST be reached.
 *
 * Production values carry no provenance tags. `spec.pinEditBase` computes the
 * tag from its inputs, and only the untagged pin is compared with production.
 *
 * Set PRKS_FC_SEED for another seed and PRKS_FC_RUNS for another run count.
 * A failure prints the seed and path that reproduce it.
 */
import fc from 'fast-check'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import * as source from './reconcile'
import reconcileSource from './reconcile.ts?raw'
import classicSource from '../../../frontend/js/sync-reconcile.js?raw'

type Core = typeof source

const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {}
const SEED = Number.parseInt(env.PRKS_FC_SEED ?? '', 10)
const RUNS = Number.parseInt(env.PRKS_FC_RUNS ?? '', 10) || 2000
const PARAMS = { numRuns: RUNS, seed: Number.isSafeInteger(SEED) ? SEED : 1472 }
/* Each mutant needs only one failing property, so it gets fewer runs. */
const MUTANT_PARAMS = { ...PARAMS, numRuns: Math.min(RUNS, 500) }
const BRANCH_FLOOR = Math.max(1, Math.floor(RUNS / 400))

const classic = new Function(classicSource + '\nreturn prksSyncReconcile;')() as Core

function transpile(text: string): Core {
  const js = ts.transpileModule(text, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} as Record<string, unknown> }
  new Function('exports', 'module', js)(module.exports, module)
  return module.exports as unknown as Core
}

/* ---- Spec: the tables in #1472 ---- */

type Outcome<T> = { value: T } | { thrown: string }

function outcome<T>(run: () => T): Outcome<T> {
  try {
    return { value: run() }
  } catch (error) {
    return { thrown: error instanceof Error ? error.constructor.name : typeof error }
  }
}

const isRev = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const present = (v: unknown) => v !== undefined && v !== null

function time(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v !== 'string' || v === '') return null
  const t = Date.parse(v)
  return Number.isFinite(t) ? t : null
}

function before(read: unknown, created: unknown): boolean {
  const r = time(read)
  const c = time(created)
  return r !== null && c !== null && r < c
}

type Labelled<T> = { result: T; branch: string } | { thrown: 'TypeError'; branch: string }

const spec = {
  planAck(e: source.AckEvidence): Labelled<source.AckPlan> {
    if (present(e.heldRevision)) {
      if (!isRev(e.ackRevision)) return { thrown: 'TypeError', branch: 'held:invalid-ack' }
      if (isRev(e.heldRevision) && e.ackRevision < e.heldRevision) return { result: 'superseded', branch: 'held:superseded' }
    }
    if (e.agrees === true) return { result: 'unchanged', branch: isRev(e.ackRevision) ? 'agrees' : 'agrees:invalid-ack' }
    if (!isRev(e.ackRevision)) return { thrown: 'TypeError', branch: 'invalid-ack' }
    if (before(e.observedAt, e.operationCreatedAt)) return { result: 'apply', branch: 'time:apply' }
    const equal = time(e.observedAt) !== null && time(e.observedAt) === time(e.operationCreatedAt)
    return { result: 'unproven', branch: equal ? 'time:equal' : 'time:unproven' }
  },

  decideAck(e: source.AckEvidence): Labelled<source.AckDecision> {
    if (!isRev(e.ackRevision)) return { thrown: 'TypeError', branch: 'invalid-ack' }
    if (present(e.heldRevision)) {
      if (!isRev(e.heldRevision)) return { result: 'unproven', branch: 'held:invalid' }
      return e.ackRevision < e.heldRevision
        ? { result: 'superseded', branch: 'held:superseded' }
        : { result: 'apply', branch: 'held:apply' }
    }
    if (e.agrees === true) return { result: 'apply', branch: 'agrees' }
    return before(e.observedAt, e.operationCreatedAt)
      ? { result: 'apply', branch: 'time:apply' }
      : { result: 'unproven', branch: 'time:unproven' }
  },

  /* Tags: `read` and `max-read` pins are pairs the read held; `max-write` and
   * `write-base` pins carry a revision measured by this device's own edit. */
  pinEditBase(
    shown: Record<string, unknown>,
    read: unknown,
    pending: Record<string, unknown> | null | undefined,
  ): { result: source.PinnedBase | null; tags: Record<string, string> } {
    if (!read || typeof read !== 'object') return { result: null, tags: {} }
    const held = read as Record<string, unknown>
    const result: source.PinnedBase = {}
    const tags: Record<string, string> = {}
    const valid = (v: unknown): v is source.FieldBase =>
      !!v && typeof v === 'object' && typeof (v as source.FieldBase).value === 'string' &&
      isRev((v as source.FieldBase).revision)
    const opOf = (own: source.FieldBase) => (typeof own.pendingOp === 'string' ? { pendingOp: own.pendingOp } : {})
    for (const field of Object.keys(shown)) {
      const entry = held[field]
      const own = pending ? pending[field] : undefined
      const value = shown[field] == null ? '' : String(shown[field])
      let pin: source.FieldBase | null = null
      let tag = 'unknown'
      if (!valid(entry)) {
        tag = 'unknown:no-read'
      } else if (own === undefined) {
        if (entry.value === value) { pin = { value, revision: entry.revision }; tag = 'read' }
        else tag = 'unknown:read-differs'
      } else if (!valid(own) || own.value !== value) {
        tag = 'unknown:own-differs'
      } else if (own.value === entry.value) {
        const fromRead = entry.revision >= own.revision
        pin = { value, revision: fromRead ? entry.revision : own.revision, ...opOf(own) }
        tag = fromRead ? 'max-read' : 'max-write'
      } else if (entry.revision === own.revision) {
        pin = { value: entry.value, revision: own.revision, pending: own.value, ...opOf(own) }
        tag = 'write-base'
      } else {
        tag = 'unknown:other-revision'
      }
      result[field] = pin
      tags[field] = tag
    }
    return { result, tags }
  },

  newerEditBase(held: unknown, read: source.FieldBase | null): { result: source.FieldBase | null; branch: string } {
    const valid = (v: unknown): v is source.FieldBase =>
      !!v && typeof v === 'object' && typeof (v as source.FieldBase).value === 'string' &&
      isRev((v as source.FieldBase).revision)
    if (!valid(held) || !valid(read)) return { result: read, branch: 'invalid' }
    if (held.revision > read.revision) return { result: held, branch: 'held-newer' }
    if (held.revision === read.revision && typeof held.ackOp === 'string') {
      return { result: { ...read, ackOp: held.ackOp }, branch: 'equal-stamped' }
    }
    return { result: read, branch: held.revision === read.revision ? 'equal' : 'read-newer' }
  },

  /* The value is the caller's input, as it arrives. Production stamps the raw
   * payload (M0); a canonical stamp is the caller's change, not this rule's. */
  acknowledgeEditBase(
    entry: unknown,
    write: { value: string; baseRevision: unknown; ackRevision: number },
  ): { result: source.FieldBase | null | undefined; branch: string } {
    const e = entry as source.FieldBase
    if (!e || typeof e !== 'object' || typeof e.value !== 'string' || !isRev(e.revision)) {
      return { result: null, branch: 'invalid' }
    }
    if (e.revision > write.ackRevision) return { result: undefined, branch: 'past' }
    if (e.revision === write.ackRevision) {
      return typeof e.ackOp === 'string'
        ? { result: undefined, branch: 'at-ack:stamped' }
        : { result: { value: e.value, revision: e.revision }, branch: 'at-ack:unstamped' }
    }
    return e.revision === write.baseRevision
      ? { result: { value: write.value, revision: write.ackRevision }, branch: 'at-base' }
      : { result: null, branch: 'between' }
  },

  pinnedEdits(
    shown: Record<string, unknown>,
    draft: Record<string, unknown>,
    pinned: source.PinnedBase,
  ): source.PinnedEdits {
    const changes: Record<string, string> = {}
    const base: Record<string, source.FieldBase> = {}
    const unknown: string[] = []
    const text = (v: unknown) => (v == null ? '' : String(v))
    for (const field of Object.keys(shown)) {
      if (pinned[field]) base[field] = pinned[field] as source.FieldBase
      if (!(field in draft) || text(draft[field]) === text(shown[field])) continue
      changes[field] = text(draft[field])
      if (!pinned[field]) unknown.push(field)
    }
    return { changes, base, unknown }
  },
}

/* ---- Inputs: small domains, so equal values, revisions and times collide ---- */

const MAX = Number.MAX_SAFE_INTEGER
const validRevision = fc.oneof(fc.nat({ max: 6 }), fc.constantFrom(MAX - 1, MAX))
const invalidRevision = fc.constantFrom(-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, MAX + 1, '3', null, undefined, {})
const anyRevision = fc.oneof({ weight: 4, arbitrary: validRevision }, { weight: 1, arbitrary: invalidRevision })
const instant = fc.integer({ min: 0, max: 4 }).map((n) => Date.UTC(2026, 10, 27, 12, 0, n))
const anyTime = fc.oneof(
  { weight: 3, arbitrary: instant },
  { weight: 2, arbitrary: instant.map((t) => new Date(t).toISOString()) },
  { weight: 1, arbitrary: fc.constantFrom(undefined, null, '', 'later', Number.NaN, Number.POSITIVE_INFINITY, {}) },
)
const evidence: fc.Arbitrary<source.AckEvidence> = fc.record(
  {
    ackRevision: anyRevision,
    heldRevision: anyRevision,
    observedAt: anyTime,
    operationCreatedAt: anyTime,
    agrees: fc.constantFrom(true, false, undefined),
  },
  { requiredKeys: ['ackRevision'] },
)

const FIELDS = ['a', 'b', 'c']
const text = fc.constantFrom('', 'A', 'B', ' A ')
const opId = fc.constantFrom('op-1', 'op-2')
const fieldBase = fc.record(
  { value: text, revision: fc.nat({ max: 3 }), pending: text, pendingOp: opId, ackOp: opId },
  { requiredKeys: ['value', 'revision'] },
) as fc.Arbitrary<source.FieldBase>
const brokenBase = fc.constantFrom<unknown>(null, undefined, {}, { value: 'A' }, { value: 1, revision: 0 },
  { value: 'A', revision: -1 }, { value: 'A', revision: 0.5 })
const entry = fc.oneof({ weight: 5, arbitrary: fieldBase }, { weight: 1, arbitrary: brokenBase })
const fieldMap = <T>(value: fc.Arbitrary<T>) =>
  fc.dictionary(fc.constantFrom(...FIELDS), value, { maxKeys: 3 })
const shownMap = fieldMap(fc.oneof(text, fc.constantFrom<unknown>(null, undefined, 0)))
const readMap = fc.oneof({ weight: 6, arbitrary: fieldMap(entry) },
  { weight: 1, arbitrary: fc.constantFrom<unknown>(null, undefined, 'read') })
const pendingMap = fc.option(fieldMap(entry), { nil: undefined })
/* One field where the form, the read and this device's own edit often hold
 * the same text: the max and write-base branches need that. */
const coherentField = fc.record({
  shown: text, readSame: fc.boolean(), readRevision: fc.nat({ max: 3 }),
  own: fc.boolean(), ownRevision: fc.nat({ max: 3 }), pendingOp: fc.option(opId, { nil: undefined }), other: text,
}).map((f) => ({
  shown: f.shown,
  read: { value: f.readSame ? f.shown : f.other, revision: f.readRevision },
  own: f.own ? { value: f.shown, revision: f.ownRevision, ...(f.pendingOp ? { pendingOp: f.pendingOp } : {}) } : undefined,
}))
const coherentInput = fc.uniqueArray(fc.constantFrom(...FIELDS)).chain((names) =>
  fc.tuple(...names.map(() => coherentField)).map((parts) => {
    const shown: Record<string, unknown> = {}
    const read: Record<string, unknown> = {}
    const pending: Record<string, unknown> = {}
    names.forEach((name, i) => {
      shown[name] = parts[i].shown
      read[name] = parts[i].read
      if (parts[i].own) pending[name] = parts[i].own
    })
    return [shown, read, pending] as [Record<string, unknown>, unknown, Record<string, unknown> | undefined]
  }))
const pinInput = fc.oneof(fc.tuple(shownMap, readMap, pendingMap), coherentInput)
const write = fc.record({ value: text, baseRevision: anyRevision, ackRevision: fc.nat({ max: 3 }) })

/* ---- Properties, each a boolean so mutants can be run through them ---- */

type Property = { name: string; build: (core: Core) => fc.IProperty<never> }
const branches = new Map<string, number>()
const hit = (fn: string, branch: string) => branches.set(fn + ':' + branch, (branches.get(fn + ':' + branch) ?? 0) + 1)

const same = (a: unknown, b: unknown) => {
  try {
    expect(a).toStrictEqual(b)
    return true
  } catch {
    return false
  }
}

function matches<T>(want: Labelled<T>, got: Outcome<T>): boolean {
  return 'thrown' in want ? 'thrown' in got && got.thrown === want.thrown : 'value' in got && got.value === want.result
}

const property = (name: string, build: Property['build']): Property => ({ name, build })

const PROPERTIES: Property[] = [
  property('A planAck follows its table', (core) => fc.property(evidence, (e) => {
    const want = spec.planAck(e)
    hit('planAck', want.branch)
    return matches(want, outcome(() => core.planAck(e)))
  }) as never),
  property('A decideAck follows its table', (core) => fc.property(evidence, (e) => {
    const want = spec.decideAck(e)
    hit('decideAck', want.branch)
    return matches(want, outcome(() => core.decideAck(e)))
  }) as never),
  property('A planAck and decideAck differ only with a held revision or agreement', (core) =>
    fc.property(evidence, (e) => {
      const plan = outcome(() => core.planAck(e))
      const decision = outcome(() => core.decideAck(e))
      if (present(e.heldRevision) || e.agrees === true) return true
      return same(plan, decision)
    }) as never),
  property('A no proof means no apply', (core) => fc.property(evidence, (e) => {
    const plan = outcome(() => core.planAck(e))
    if (!('value' in plan) || plan.value !== 'apply') return true
    return e.agrees !== true && before(e.observedAt, e.operationCreatedAt) &&
      !(isRev(e.heldRevision) && isRev(e.ackRevision) && e.ackRevision < e.heldRevision)
  }) as never),
  property('B2 pinEditBase follows its table', (core) => fc.property(pinInput, ([shown, read, pending]) => {
    const want = spec.pinEditBase(shown, read, pending)
    Object.values(want.tags).forEach((tag) => hit('pinEditBase', tag))
    return same(core.pinEditBase(shown, read as never, pending), want.result)
  }) as never),
  property('B2 a read-tagged pin is a pair the read held', (core) => fc.property(pinInput, ([shown, read, pending]) => {
    const { tags } = spec.pinEditBase(shown, read, pending)
    const pins = core.pinEditBase(shown, read as never, pending)
    return Object.entries(tags).every(([field, tag]) => {
      if (tag !== 'read' && tag !== 'max-read') return true
      const held = (read as Record<string, source.FieldBase>)[field]
      const pin = pins?.[field]
      return !!pin && pin.value === held.value && pin.revision === held.revision
    })
  }) as never),
  property('B1 pinnedEdits sends exactly the changed fields', (core) =>
    fc.property(pinInput, shownMap, ([shown, read, pending], draft) => {
      const pinned = core.pinEditBase(shown, read as never, pending) ?? {}
      const got = core.pinnedEdits(shown, draft, pinned)
      if (!same(got, spec.pinnedEdits(shown, draft, pinned))) return false
      return Object.keys(got.changes).every((f) => f in got.base || got.unknown.includes(f)) &&
        got.unknown.every((f) => f in got.changes && !pinned[f])
    }) as never),
  property('B3 newerEditBase follows its table', (core) => fc.property(entry, fc.option(fieldBase, { nil: null }), (held, read) => {
    const want = spec.newerEditBase(held, read)
    hit('newerEditBase', want.branch)
    return same(core.newerEditBase(held, read), want.result)
  }) as never),
  property('B3 a read never removes newer acknowledged state', (core) => fc.property(fieldBase, fieldBase, (held, read) => {
    const got = core.newerEditBase(held, read)
    if (!got || got.revision !== Math.max(held.revision, read.revision)) return false
    return !(held.revision >= read.revision && typeof held.ackOp === 'string') || got.ackOp === held.ackOp
  }) as never),
  property('C1 acknowledgeEditBase follows its table', (core) => fc.property(entry, write, (e, w) => {
    const want = spec.acknowledgeEditBase(e, w)
    hit('acknowledgeEditBase', want.branch)
    return same(core.acknowledgeEditBase(e, w), want.result)
  }) as never),
  property('C1 the acknowledged pair is the write value at the ACK revision', (core) =>
    fc.property(fieldBase, write, (e, w) => {
      const got = core.acknowledgeEditBase(e, w)
      if (got === undefined) return e.revision > w.ackRevision || (e.revision === w.ackRevision && typeof e.ackOp === 'string')
      if (got === null) return e.revision < w.ackRevision && e.revision !== w.baseRevision
      return got.revision === w.ackRevision && got.value === (e.revision === w.ackRevision ? e.value : w.value)
    }) as never),
]

/* ---- Mutants of reconcile.ts (function group) ---- */

const MUTANTS: Array<{ name: string; edits: Array<[string, string]> }> = [
  { name: 'constant unproven', edits: [['export function planAck(evidence: AckEvidence): AckPlan {', 'export function planAck(evidence: AckEvidence): AckPlan {\n  return \'unproven\'']] },
  { name: '<= for <', edits: [['return read !== null && created !== null && read < created', 'return read !== null && created !== null && read <= created']] },
  { name: 'sibling revision proves apply', edits: [["decideAck({ ackRevision: evidence.ackRevision, heldRevision: evidence.heldRevision }) === 'superseded') {\n    return 'superseded'\n  }",
    "decideAck({ ackRevision: evidence.ackRevision, heldRevision: evidence.heldRevision }) === 'superseded') {\n    return 'superseded'\n  }\n  if (isRevision(evidence.heldRevision)) return 'apply'"]] },
  { name: 'planAck validates nothing', edits: [['export function planAck(evidence: AckEvidence): AckPlan {', 'export function planAck(evidence: AckEvidence): AckPlan {\n  if (!isRevision(evidence.ackRevision)) return \'unproven\'']] },
  { name: 'max case keeps entry.revision', edits: [['revision: Math.max(entry.revision, own.revision),', 'revision: entry.revision,']] },
  { name: 'max case keeps own.revision', edits: [['revision: Math.max(entry.revision, own.revision),', 'revision: own.revision,']] },
  { name: 'pinEditBase drops pendingOp', edits: [["value: entry.value, revision: Math.max(entry.revision, own.revision),\n          ...(typeof own.pendingOp === 'string' ? { pendingOp: own.pendingOp } : {}),",
    'value: entry.value, revision: Math.max(entry.revision, own.revision),']] },
  { name: 'stamp replaced at equal revisions', edits: [["if (held.revision === read.revision && typeof held.ackOp === 'string') return { ...read, ackOp: held.ackOp }", '']] },
  { name: 'acknowledged stamp replaced', edits: [["return typeof entry.ackOp === 'string' ? undefined : { value: entry.value, revision: entry.revision }", 'return { value: entry.value, revision: entry.revision }']] },
]

function mutate(edits: Array<[string, string]>): Core {
  let text = reconcileSource
  for (const [from, to] of edits) {
    const at = text.indexOf(from)
    if (at < 0 || text.indexOf(from, at + 1) >= 0) throw new Error('mutant edit must match exactly once: ' + from)
    text = text.slice(0, at) + to + text.slice(at + from.length)
  }
  return transpile(text)
}

describe('reconcile properties (#1472)', () => {
  const cores: Array<[string, Core]> = [['reconcile.ts', source], ['sync-reconcile.js', classic]]
  for (const [label, core] of cores) {
    describe(label, () => {
      for (const p of PROPERTIES) it(p.name, () => expect(() => fc.assert(p.build(core), PARAMS)).not.toThrow())
    })
  }

  it('the transpiled source agrees with the module, so mutants start from production', () => {
    for (const p of PROPERTIES) expect(() => fc.assert(p.build(mutate([])), MUTANT_PARAMS)).not.toThrow()
  })

  it('every spec branch is reached', () => {
    const want = [
      'planAck:held:invalid-ack', 'planAck:held:superseded', 'planAck:agrees', 'planAck:agrees:invalid-ack',
      'planAck:invalid-ack', 'planAck:time:apply', 'planAck:time:equal', 'planAck:time:unproven',
      'decideAck:invalid-ack', 'decideAck:held:invalid', 'decideAck:held:superseded', 'decideAck:held:apply',
      'decideAck:agrees', 'decideAck:time:apply', 'decideAck:time:unproven',
      'pinEditBase:read', 'pinEditBase:max-read', 'pinEditBase:max-write', 'pinEditBase:write-base',
      'pinEditBase:unknown:no-read', 'pinEditBase:unknown:read-differs', 'pinEditBase:unknown:own-differs',
      'pinEditBase:unknown:other-revision',
      'newerEditBase:invalid', 'newerEditBase:held-newer', 'newerEditBase:equal-stamped', 'newerEditBase:equal',
      'newerEditBase:read-newer',
      'acknowledgeEditBase:invalid', 'acknowledgeEditBase:past', 'acknowledgeEditBase:at-ack:stamped',
      'acknowledgeEditBase:at-ack:unstamped', 'acknowledgeEditBase:at-base', 'acknowledgeEditBase:between',
    ]
    const low = want.filter((b) => (branches.get(b) ?? 0) < BRANCH_FLOOR)
    expect(low, 'branches below ' + BRANCH_FLOOR + ' hits').toEqual([])
  })

  describe('mutants', () => {
    for (const mutant of MUTANTS) {
      it(mutant.name + ' fails a property', () => {
        const core = mutate(mutant.edits)
        const killed = PROPERTIES.filter((p) => fc.check(p.build(core), MUTANT_PARAMS).failed)
        expect(killed.map((p) => p.name)).not.toEqual([])
      })
    }
  })
})
