import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from '../../api/http'

const reachability = vi.hoisted(() => ({ noted: [] as unknown[] }))
vi.mock('../../api/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/http')>()),
  noteFinalPrksTransportFailure: (error: unknown) => { reachability.noted.push(error) },
}))
import { processingItemRow } from '../../api/processing-items.fixtures'
import type { ProcessingItem } from '../../api/processing-items'
import { PROCESSING_IMPORT_BATCH_LIMIT, ProcessingQueue, repaintDraft, type ProcessingQueueApi } from './queue'

const NO_DUPLICATES = { items: [], works: [], more_items: false, more_works: false }

function fakeApi(overrides: Partial<ProcessingQueueApi> = {}): ProcessingQueueApi {
  return {
    list: vi.fn(async () => ({ items: [], nextCursor: null })),
    upload: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    importOne: vi.fn(),
    importMany: vi.fn(),
    ...overrides,
  } as ProcessingQueueApi
}

function item(id: string, extra: Partial<ProcessingItem> = {}): ProcessingItem {
  return processingItemRow({ id, original_filename: `${id}.pdf`, ...extra })
}

function refusal(status: number, message: string, current: ProcessingItem | null): PrksApiError {
  return new PrksApiError('REVISION_CONFLICT', status, 'REVISION_CONFLICT', { message, item: current })
}

describe('Processing queue', () => {
  it('pages the queue and reports the ready count', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [item('a')], nextCursor: 'c' })
      .mockResolvedValueOnce({ items: [item('a'), item('b')], nextCursor: null })
    const counts: number[] = []
    const queue = new ProcessingQueue(fakeApi({ list }), (n) => counts.push(n))
    await queue.load()
    await queue.load(true)
    expect(list.mock.calls.map((call) => call[0])).toEqual([{ cursor: null }, { cursor: 'c' }])
    expect(queue.state.items.map((one) => one.id)).toEqual(['a', 'b'])
    expect(queue.state.nextCursor).toBeNull()
    expect(counts).toEqual([1, 2])
  })

  it('keeps a card with unsaved edits through a refresh that no longer lists it', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [item('a')], nextCursor: 'c' })
      .mockResolvedValueOnce({ items: [item('b'), item('c')], nextCursor: null })
      .mockResolvedValueOnce({ items: [item('a')], nextCursor: 'c' })
    const queue = new ProcessingQueue(fakeApi({ list }))
    await queue.load()
    await queue.load(true)
    queue.setDirty('b', true)
    queue.toggle('b', true)
    queue.toggle('c', true)
    await queue.load()
    expect(queue.state.items.map((one) => one.id)).toEqual(['a', 'b'])
    expect([...queue.state.selected]).toEqual(['b'])
    expect(queue.state.dirty.has('b')).toBe(true)
  })

  it('reads after a pending write and reads again when a write overtakes the read', async () => {
    let finishRemove!: (row: ProcessingItem) => void
    const remove = vi.fn(() => new Promise<ProcessingItem>((resolve) => { finishRemove = resolve }))
    let finishList!: () => void
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [item('a'), item('b')], nextCursor: null })
      // Taken before the removal below answers: it still lists 'a'.
      .mockImplementationOnce(() => new Promise((resolve) => {
        finishList = () => resolve({ items: [item('a'), item('b')], nextCursor: null })
      }))
      .mockResolvedValueOnce({ items: [item('b')], nextCursor: null })
    const update = vi.fn(async () => item('b', { revision: 2 }))
    const queue = new ProcessingQueue(fakeApi({ list, remove, update }))
    await queue.load()

    // A refresh while a removal is pending waits for it.
    const removing = queue.remove('a')
    const waiting = queue.load()
    await Promise.resolve()
    expect(list).toHaveBeenCalledTimes(1)
    finishRemove(item('a', { status: 'deleted' }))
    await removing
    await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(2))

    // A write that starts and ends while that read is in flight makes it stale.
    await queue.save('b', { title: 'B' } as never)
    finishList()
    await waiting
    expect(list).toHaveBeenCalledTimes(3)
    expect(queue.state.items.map((one) => one.id)).toEqual(['b'])
  })

  it('splits a large selection into requests within the server batch limit', async () => {
    const many = Array.from({ length: PROCESSING_IMPORT_BATCH_LIMIT + 2 }, (_, i) => item(`i${i}`))
    const importMany = vi.fn(async (ids: readonly string[]) =>
      ids.map((id) => ({ item_id: id, ok: true, work_id: `W-${id}`, created: true, code: null, message: '', item: null })))
    const queue = new ProcessingQueue(fakeApi({ list: vi.fn(async () => ({ items: many, nextCursor: null })), importMany }))
    await queue.load()
    for (const one of many) queue.toggle(one.id, true)
    await queue.importSelected()
    expect(importMany.mock.calls.map((call) => call[0].length)).toEqual([PROCESSING_IMPORT_BATCH_LIMIT, 2])
    expect(queue.state.items).toEqual([])
  })

  it('uploads each PDF with its own key and refuses other files', async () => {
    const keys: string[] = []
    const upload = vi.fn(async (file: File, key: string) => {
      keys.push(key)
      return { item: item(file.name), created: file.name !== 'again.pdf', duplicates: NO_DUPLICATES }
    })
    const queue = new ProcessingQueue(fakeApi({ upload }))
    await queue.upload([
      new File(['%PDF'], 'one.pdf', { type: 'application/pdf' }),
      new File(['x'], 'notes.txt', { type: 'text/plain' }),
      new File(['%PDF'], 'again.pdf'),
    ])
    expect(upload).toHaveBeenCalledTimes(2)
    expect(new Set(keys).size).toBe(2)
    expect(queue.state.uploads.map((one) => [one.filename, one.state, one.message])).toEqual([
      ['one.pdf', 'done', 'Added to the queue.'],
      ['notes.txt', 'failed', 'Only PDF files can be uploaded.'],
      ['again.pdf', 'done', 'Already in the queue.'],
    ])
    expect(queue.state.items.map((one) => one.id)).toEqual(['one.pdf', 'again.pdf'])
  })

  it('shows progress while a file goes up, and no item for a failed upload', async () => {
    let release: (() => void) | null = null
    const seen: number[] = []
    const queue = new ProcessingQueue(fakeApi({
      upload: vi.fn((file: File, _key: string, onProgress: (sent: number, total: number) => void) => {
        onProgress(50, 200)
        seen.push(queue.state.uploads[0].percent)
        if (file.name === 'bad.pdf') return Promise.reject(new PrksApiError('PROCESSING_NOT_A_PDF', 415, 'PROCESSING_NOT_A_PDF', { message: 'The upload is not a PDF.', item: null }))
        return new Promise((resolve) => {
          release = () => resolve({ item: item('good'), created: true, duplicates: NO_DUPLICATES })
        })
      }) as ProcessingQueueApi['upload'],
    }))
    const running = queue.upload([new File(['%PDF'], 'good.pdf', { type: 'application/pdf' }), new File(['x'], 'bad.pdf', { type: 'application/pdf' })])
    await Promise.resolve()
    expect(seen).toEqual([25])
    expect(queue.state.uploads[0].state).toBe('sending')
    expect(queue.state.items).toEqual([])
    release!()
    await running
    expect(queue.state.uploads.map((one) => [one.state, one.percent, one.message])).toEqual([
      ['done', 100, 'Added to the queue.'],
      ['failed', 25, 'The upload is not a PDF.'],
    ])
    expect(queue.state.items.map((one) => one.id)).toEqual(['good'])
  })

  it('names an upload\'s duplicates', async () => {
    const duplicates = { items: [], works: [{ id: 'W-1', title: 'Old' }], more_items: false, more_works: false }
    const queue = new ProcessingQueue(fakeApi({ upload: vi.fn(async () => ({ item: item('a'), created: true, duplicates })) }))
    await queue.upload([new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })])
    expect(queue.state.uploads[0].duplicates).toEqual(duplicates)
  })

  it('saves at the painted revision and repaints a stale item as it now is', async () => {
    const update = vi.fn()
      .mockResolvedValueOnce(item('a', { revision: 2 }))
      .mockRejectedValueOnce(refusal(409, 'The item changed since that revision.', item('a', { revision: 5 })))
    const queue = new ProcessingQueue(fakeApi({
      list: vi.fn(async () => ({ items: [item('a')], nextCursor: null })),
      update,
    }))
    await queue.load()
    await expect(queue.save('a', { title: 'T' })).resolves.toBe(true)
    expect(update.mock.calls[0]).toEqual(['a', 1, { title: 'T' }])
    await expect(queue.save('a', { title: 'U' })).resolves.toBe(false)
    expect(update.mock.calls[1]).toEqual(['a', 2, { title: 'U' }])
    expect(queue.state.items[0].revision).toBe(5)
    expect(queue.state.errors.a).toBe('The item changed since that revision.')
  })

  it('drops an item a refusal says was imported elsewhere', async () => {
    const queue = new ProcessingQueue(fakeApi({
      list: vi.fn(async () => ({ items: [item('a')], nextCursor: null })),
      importOne: vi.fn(async () => { throw refusal(409, 'The item has already been imported.', item('a', { status: 'imported', draft: null })) }),
    }))
    await queue.load()
    await queue.importOne('a')
    expect(queue.state.items).toEqual([])
    expect(queue.state.errors.a).toBeUndefined()
  })

  it('imports one item and links its Work', async () => {
    const counts: number[] = []
    const queue = new ProcessingQueue(fakeApi({
      list: vi.fn(async () => ({ items: [item('a'), item('b')], nextCursor: null })),
      importOne: vi.fn(async () => ({ itemId: 'a', workId: 'W-9', created: true })),
    }), (n) => counts.push(n))
    await queue.load()
    await queue.importOne('a')
    expect(queue.state.items.map((one) => one.id)).toEqual(['b'])
    expect(queue.state.imported).toEqual([{ itemId: 'a', filename: 'a.pdf', workId: 'W-9' }])
    expect(counts.at(-1)).toBe(1)
  })

  it('imports the selected items and keeps each failure with its message', async () => {
    const importMany = vi.fn(async () => [
      { item_id: 'a', ok: true, work_id: 'W-1', created: true, code: null, message: null, item: null },
      { item_id: 'c', ok: false, work_id: null, created: false, code: 'PROCESSING_OBJECT_MISSING', message: 'The item\'s stored file is missing.', item: null },
    ])
    const queue = new ProcessingQueue(fakeApi({
      list: vi.fn(async () => ({ items: [item('a'), item('b'), item('c')], nextCursor: null })),
      importMany,
    }))
    await queue.load()
    queue.toggle('c', true)
    queue.toggle('a', true)
    await queue.importSelected()
    expect(importMany).toHaveBeenCalledWith(['a', 'c'])
    expect(queue.state.items.map((one) => one.id)).toEqual(['b', 'c'])
    expect(queue.state.errors).toEqual({ c: 'The item\'s stored file is missing.' })
    expect(queue.state.imported.map((one) => one.workId)).toEqual(['W-1'])
    expect([...queue.state.selected]).toEqual(['c'])
    expect(queue.state.busy.size).toBe(0)
  })

  it('removes an item', async () => {
    const queue = new ProcessingQueue(fakeApi({
      list: vi.fn(async () => ({ items: [item('a')], nextCursor: null })),
      remove: vi.fn(async () => item('a', { status: 'deleted', draft: null })),
    }))
    await queue.load()
    queue.toggle('a', true)
    await queue.remove('a')
    expect(queue.state.items).toEqual([])
    expect(queue.state.selected.size).toBe(0)
  })

  it('shows a failed read and keeps the painted items', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [item('a')], nextCursor: null })
      .mockRejectedValueOnce(new TypeError('offline'))
    const queue = new ProcessingQueue(fakeApi({ list }))
    await queue.load()
    await queue.load()
    expect(queue.state.items.map((one) => one.id)).toEqual(['a'])
    expect(queue.state.loadError).toBe('Could not load the Processing queue.')
  })

  describe('review regressions', () => {
    afterEach(() => vi.unstubAllGlobals())

    it('never imports a selected card with unsaved edits in a batch', async () => {
      const importMany = vi.fn()
      const importOne = vi.fn()
      const queue = new ProcessingQueue(fakeApi({
        list: vi.fn(async () => ({ items: [item('a'), item('b')], nextCursor: null })),
        importMany,
        importOne,
      }))
      await queue.load()
      queue.toggle('a', true)
      queue.toggle('b', true)
      queue.setDirty('b', true)
      await queue.importSelected()
      await queue.importOne('b')
      expect(importMany).not.toHaveBeenCalled()
      expect(importOne).not.toHaveBeenCalled()
      expect(queue.state.items.map((one) => one.id)).toEqual(['a', 'b'])
      queue.setDirty('b', false)
      importMany.mockResolvedValueOnce([])
      await queue.importSelected()
      expect(importMany).toHaveBeenCalledWith(['a', 'b'])
    })

    it('keeps each batch\'s progress on its own rows when drops overlap', async () => {
      const pending = new Map<string, () => void>()
      const queue = new ProcessingQueue(fakeApi({
        upload: vi.fn((file: File, _key: string, onProgress: (sent: number, total: number) => void) => {
          onProgress(1, 2)
          return new Promise((resolve) => {
            pending.set(file.name, () => resolve({ item: item(file.name), created: true, duplicates: NO_DUPLICATES }))
          })
        }) as ProcessingQueueApi['upload'],
      }))
      const first = queue.upload([new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })])
      await Promise.resolve()
      const second = queue.upload([new File(['%PDF'], 'b.pdf', { type: 'application/pdf' })])
      await Promise.resolve()
      pending.get('a.pdf')!()
      await first
      expect(queue.state.uploads.map((one) => [one.filename, one.state])).toEqual([
        ['a.pdf', 'done'],
        ['b.pdf', 'sending'],
      ])
      pending.get('b.pdf')!()
      await second
      expect(queue.state.uploads.map((one) => one.state)).toEqual(['done', 'done'])
    })

    it('marks the offline caches changed for every import sent, even a failed one', async () => {
      const marks = vi.fn()
      vi.stubGlobal('prksMarkProcessingImportChanged', marks)
      const queue = new ProcessingQueue(fakeApi({
        list: vi.fn(async () => ({ items: [item('a'), item('b')], nextCursor: null })),
        importOne: vi.fn(async () => { throw new TypeError('offline') }),
        importMany: vi.fn(async () => [
          { item_id: 'b', ok: true, work_id: 'W-2', created: true, code: null, message: null, item: null },
        ]),
      }))
      await queue.load()
      await queue.importOne('a')
      queue.toggle('b', true)
      await queue.importSelected()
      expect(marks).toHaveBeenCalledTimes(2)
    })
  })
})

describe('repaintDraft', () => {
  it('keeps a field edited while the save was in flight and takes the saved value for the rest', () => {
    const submitted = { title: 'Saved title', year: '2020' }
    const shown = { title: 'Saved title', year: '2021' } // the year was edited after Save
    const incoming = { title: 'Saved title', year: '2020' }
    expect(repaintDraft(shown, submitted, incoming)).toEqual({ title: 'Saved title', year: '2021' })
  })

  it('takes the item as it now is when nothing was submitted (a refusal or another change)', () => {
    expect(repaintDraft({ title: 'Mine' }, null, { title: 'Theirs' })).toEqual({ title: 'Theirs' })
  })
})

describe('queue reachability', () => {
  it('reports each failed queue call to PRKS reachability', async () => {
    reachability.noted.length = 0
    const down = () => new TypeError('PRKS API request failed.')
    const queue = new ProcessingQueue(fakeApi({
      list: vi.fn()
        .mockResolvedValueOnce({ items: [item('a'), item('b')], nextCursor: null })
        .mockRejectedValueOnce(down()),
      upload: vi.fn(async () => { throw down() }),
      update: vi.fn(async () => { throw down() }),
      importMany: vi.fn(async () => { throw down() }),
    }))
    await queue.load()
    await queue.load()
    await queue.upload([new File(['%PDF'], 'x.pdf', { type: 'application/pdf' })])
    await queue.save('a', { title: 'T' })
    queue.toggle('b', true)
    await queue.importSelected()
    expect(reachability.noted).toHaveLength(4)
  })
})
