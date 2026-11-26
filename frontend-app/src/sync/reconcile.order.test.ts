import { describe, expect, it } from 'vitest'
import { applyOwnerAck, type FreshRead } from './reconcile'

/*
 * Bounded schedules for a live owner that receives two unproven ACKs for the
 * same Work while both authoritative re-reads are still in flight, and whose
 * held value may be replaced at any point (#391, #1425, #1461): by another
 * copy of the same Work (a reload or warm resume), or by a different Work.
 *
 * Every interleaving of these events is generated, subject to the order the
 * protocol guarantees: ACKs for one target arrive in queue order, and a re-read
 * can only finish after the ACK that started it.
 */

type Work = { id: string; value: string }
type Replace = 'reload' | 'other-work'
type Event = 'ack1' | 'ack2' | 'read1' | 'read2' | Replace

const MUST_FOLLOW: Partial<Record<Event, Event>> = { ack2: 'ack1', read1: 'ack1', read2: 'ack2' }

function interleavings(events: readonly Event[]): Event[][] {
  if (events.length === 0) return [[]]
  const out: Event[][] = []
  for (const event of events) {
    const before = MUST_FOLLOW[event]
    if (before && events.includes(before)) continue
    const rest = events.filter((other) => other !== event)
    for (const tail of interleavings(rest)) out.push([event, ...tail])
  }
  return out
}

const schedules = [
  ...interleavings(['ack1', 'ack2', 'read1', 'read2']),
  ...interleavings(['ack1', 'ack2', 'read1', 'read2', 'reload']),
  ...interleavings(['ack1', 'ack2', 'read1', 'read2', 'other-work']),
]

function deferredRead() {
  let complete!: (value: FreshRead<Work>) => void
  const promise = new Promise<FreshRead<Work>>((resolve) => { complete = resolve })
  return { promise, complete }
}

/* Lets every pending continuation run, however many awaits it takes. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('live owner ACK re-read ordering', () => {
  it('covers every bounded interleaving', () => {
    expect(schedules).toHaveLength(33)
    expect(new Set(schedules.map((order) => order.join())).size).toBe(schedules.length)
  })

  for (const order of schedules) {
    it(order.join(' -> '), async () => {
      const initial: Work = { id: 'W', value: 'initial' }
      const replacements: Record<Replace, Work> = {
        reload: { id: 'W', value: 'reloaded copy' },
        'other-work': { id: 'other-W', value: 'another Work' },
      }
      const fresh = [
        { id: 'W', value: 'server read 1' },
        { id: 'W', value: 'server read 2' },
      ]
      const reads = [deferredRead(), deferredRead()]
      const labels = new Map<Work, string>([
        [initial, 'initial'], [replacements.reload, 'reload'], [replacements['other-work'], 'other-work'],
        [fresh[0], 'fresh1'], [fresh[1], 'fresh2'],
      ])
      const label = (work: Work) => labels.get(work) ?? 'unlabelled'

      let held = initial
      const adopted: string[] = []
      const started: number[] = []

      // The contract in observable terms: a re-read is adopted only while the
      // owner still holds exactly what its own ACK wrote. A replacement value
      // and a later ACK both win over an earlier read. An ACK for a Work the
      // owner no longer holds changes nothing and starts no read, as the
      // production adapters' `get` hides a Work with another id.
      const expectedStarts: number[] = []
      let model = 'initial'
      const modelAdopted: string[] = []
      const wroteByAck: (string | null)[] = [null, null]

      function deliverAck(index: 0 | 1) {
        const ackValue = `ACK ${index + 1}`
        const before = held
        const plan = applyOwnerAck({ ackRevision: index + 1 }, {
          get: () => (held.id === 'W' ? held : null),
          set: (next: Work) => { held = next },
          agrees: (current: Work) => current.value === ackValue,
          patch: (current: Work) => ({ ...current, value: ackValue }),
          read: () => {
            started.push(index + 1)
            return reads[index].promise
          },
          adopted: (next: Work) => { adopted.push(label(next)) },
        })
        if (before.id !== 'W') {
          expect(plan).toBe('unchanged')
          expect(held).toBe(before)
          return
        }
        expect(plan).toBe('unproven')
        expectedStarts.push(index + 1)
        // The user's change shows at once, on the entity the owner held.
        expect(held).not.toBe(before)
        expect(held).toEqual({ id: before.id, value: ackValue })
        labels.set(held, `ack${index + 1}`)
        model = `ack${index + 1}`
        wroteByAck[index] = model
      }

      for (const event of order) {
        switch (event) {
          case 'ack1': deliverAck(0); break
          case 'ack2': deliverAck(1); break
          case 'reload':
          case 'other-work':
            held = replacements[event]
            model = event
            break
          case 'read1':
          case 'read2': {
            const index = event === 'read1' ? 0 : 1
            if (!started.includes(index + 1)) break
            reads[index].complete({ source: 'server', value: fresh[index] })
            await settle()
            if (model === wroteByAck[index]) {
              model = `fresh${index + 1}`
              modelAdopted.push(model)
            }
            break
          }
        }
        expect(label(held), `after ${event}`).toBe(model)
        expect(adopted, `after ${event}`).toEqual(modelAdopted)
      }
      expect(started).toEqual(expectedStarts)
    })
  }
})
