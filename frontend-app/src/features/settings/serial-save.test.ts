import { describe, expect, it } from 'vitest'
import { createSerialSave } from './serial-save'

interface Pending {
  value: string
  resolve(): void
  reject(): void
}

function harness(initial = 'apa') {
  const sent: Pending[] = []
  const failed: string[] = []
  const saver = createSerialSave(initial, {
    save: (value) =>
      new Promise<void>((resolve, reject) => {
        sent.push({ value, resolve, reject: () => reject(new Error('offline')) })
      }),
    onFailed: (saved) => failed.push(saved),
  })
  return { saver, sent, failed }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('serial setting save', () => {
  it('sends the newest choice last when choices overlap a running save', async () => {
    const { saver, sent } = harness()
    const done = saver.choose('chicago-author-date')
    void saver.choose('modern-language-association')
    void saver.choose('ieee')
    expect(sent.map((s) => s.value)).toEqual(['chicago-author-date'])
    sent[0].resolve()
    await tick()
    // The two later choices collapse into one request for the newest.
    expect(sent.map((s) => s.value)).toEqual(['chicago-author-date', 'ieee'])
    sent[1].resolve()
    await done
    expect(saver.saved()).toBe('ieee')
    expect(sent).toHaveLength(2)
  })

  it('never lets an older save finish after a newer one', async () => {
    const { saver, sent } = harness()
    void saver.choose('chicago-author-date')
    void saver.choose('ieee')
    // Only one request is ever in flight, so the IEEE save cannot start,
    // and so cannot finish, before the Chicago save settles.
    expect(sent).toHaveLength(1)
    sent[0].resolve()
    await tick()
    expect(sent[1].value).toBe('ieee')
    sent[1].resolve()
    await tick()
    expect(saver.saved()).toBe('ieee')
  })

  it('reverts to the saved value when the newest choice fails', async () => {
    const { saver, sent, failed } = harness()
    const done = saver.choose('ieee')
    sent[0].reject()
    await done
    expect(failed).toEqual(['apa'])
    expect(saver.saved()).toBe('apa')
  })

  it('still sends a newer choice when an older one fails', async () => {
    const { saver, sent, failed } = harness()
    const done = saver.choose('chicago-author-date')
    void saver.choose('ieee')
    sent[0].reject()
    await tick()
    expect(sent.map((s) => s.value)).toEqual(['chicago-author-date', 'ieee'])
    sent[1].resolve()
    await done
    expect(failed).toEqual([])
    expect(saver.saved()).toBe('ieee')
  })

  it('sends a choice again after reset to what the server answered', async () => {
    const { saver, sent } = harness()
    const done = saver.choose('ieee')
    sent[0].resolve()
    await done
    // Another session saved Chicago; the server answered with it.
    saver.reset('chicago-author-date')
    const again = saver.choose('ieee')
    sent[1]?.resolve()
    await again
    expect(sent.map((s) => s.value)).toEqual(['ieee', 'ieee'])
  })

  it('choosing the saved value again sends nothing', async () => {
    const { saver, sent } = harness()
    await saver.choose('apa')
    expect(sent).toHaveLength(0)
  })
})
