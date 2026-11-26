import { describe, expect, it } from 'vitest'
import { applyOwnerAck, type FreshRead } from './reconcile'

type Work = { id: string; value: string }
type Event = 'old-read' | 'new-read' | 'replace-owner'

function deferredRead() {
  let complete!: (value: FreshRead<Work>) => void
  const promise = new Promise<FreshRead<Work>>((resolve) => { complete = resolve })
  return { promise, complete }
}

const orders: Event[][] = [
  ['old-read', 'new-read', 'replace-owner'],
  ['old-read', 'replace-owner', 'new-read'],
  ['new-read', 'old-read', 'replace-owner'],
  ['new-read', 'replace-owner', 'old-read'],
  ['replace-owner', 'old-read', 'new-read'],
  ['replace-owner', 'new-read', 'old-read'],
  ['old-read', 'new-read'],
  ['new-read', 'old-read'],
]

describe('live owner ACK re-read ordering', () => {
  for (const order of orders) {
    it(order.join(' -> '), async () => {
      const oldRead = deferredRead()
      const newRead = deferredRead()
      const oldFresh = { id: 'W', value: 'old server response' }
      const newFresh = { id: 'W', value: 'new server response' }
      const replacement = { id: 'other-W', value: 'replacement owner' }
      let held: Work = { id: 'W', value: 'initial' }
      const adopted: Work[] = []
      const started: number[] = []

      function deliverAck(revision: number, value: string, read: ReturnType<typeof deferredRead>) {
        return applyOwnerAck({ ackRevision: revision }, {
          get: () => held,
          set: (next: Work) => { held = next },
          agrees: (current: Work) => current.value === value,
          patch: (current: Work) => ({ ...current, value }),
          read: () => {
            started.push(revision)
            return read.promise
          },
          adopted: (next: Work) => { adopted.push(next) },
        })
      }

      expect(deliverAck(1, 'old ACK', oldRead)).toBe('unproven')
      expect(held.value).toBe('old ACK')
      expect(deliverAck(2, 'new ACK', newRead)).toBe('unproven')
      expect(held.value).toBe('new ACK')
      expect(started).toEqual([1, 2])
      const patchedByNewAck = held

      let expected = patchedByNewAck
      const expectedAdoptions: Work[] = []
      let replaced = false
      for (const event of order) {
        switch (event) {
          case 'replace-owner':
            held = replacement
            expected = replacement
            replaced = true
            break
          case 'old-read':
            oldRead.complete({ source: 'server', value: oldFresh })
            await oldRead.promise
            break
          case 'new-read':
            newRead.complete({ source: 'server', value: newFresh })
            await newRead.promise
            if (!replaced) {
              expected = newFresh
              expectedAdoptions.push(newFresh)
            }
            break
        }
        expect(held).toBe(expected)
        expect(adopted).toEqual(expectedAdoptions)
      }
      expect(started).toEqual([1, 2])
    })
  }
})
