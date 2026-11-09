import { afterEach, describe, expect, it, vi } from 'vitest'
import { processingItemRow } from './processing-items.fixtures'
import {
  newUploadKey,
  deleteProcessingItem,
  importProcessingItems,
  listProcessingItems,
  parseProcessingItem,
  processingErrorMessage,
  processingRefusal,
  updateProcessingItem,
  uploadProcessingItem,
} from './processing-items'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ITEM = processingItemRow()

function stub(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls[0] as unknown as [string, RequestInit]
}

describe('Processing queue client', () => {
  it('parses a ready item and its markers', () => {
    expect(parseProcessingItem(ITEM)).toEqual(ITEM)
    const imported = { ...ITEM, status: 'imported', draft: null, imported_work_id: 'W-1', imported_at: ITEM.created_at }
    expect(parseProcessingItem(imported)).toEqual(imported)
  })

  it.each([
    ['unknown status', { ...ITEM, status: 'queued' }],
    ['ready without a draft', { ...ITEM, draft: null }],
    ['numeric title', { ...ITEM, draft: { ...ITEM.draft, title: 1 } }],
    ['string revision', { ...ITEM, revision: '1' }],
  ])('refuses %s', (_name, value) => {
    expect(parseProcessingItem(value)).toBeNull()
  })

  it('reads one page with its cursor', async () => {
    const call = stub(200, { items: [ITEM], next_cursor: 'c-2' })
    await expect(listProcessingItems({ cursor: 'c-1', limit: 10 })).resolves.toEqual({ items: [ITEM], nextCursor: 'c-2' })
    expect(call()[0]).toBe('/api/processing-items?limit=10&cursor=c-1')
  })

  it('uploads the PDF as the body with its key and encoded filename', async () => {
    const call = stub(201, { item: ITEM, created: true, duplicates: { items: [], works: [], more_items: false, more_works: false } })
    const file = new File([new Uint8Array([37, 80, 68, 70])], 'Čas & co.pdf', { type: 'application/pdf' })
    const uploaded = await uploadProcessingItem(file, 'key-1')
    expect(uploaded.created).toBe(true)
    const [url, init] = call()
    expect(url).toBe('/api/processing-items')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(file)
    const headers = init.headers as Record<string, string>
    expect(headers['Content-Type']).toBe('application/pdf')
    expect(headers['Idempotency-Key']).toBe('key-1')
    expect(headers['PRKS-Filename']).toBe(encodeURIComponent('Čas & co.pdf'))
  })

  it('sends a PDF picked without a type as application/pdf', async () => {
    const call = stub(200, { item: ITEM, created: false, duplicates: {} })
    await uploadProcessingItem(new File(['%PDF'], 'x.pdf'), 'key-2')
    const init = call()[1]
    expect((init.body as Blob).type).toBe('application/pdf')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/pdf')
  })

  it('edits the draft at its revision', async () => {
    const call = stub(200, { ...ITEM, revision: 2 })
    await updateProcessingItem('item-1', 1, { title: 'T', year: '2001' })
    const [url, init] = call()
    expect(url).toBe('/api/processing-items/item-1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({ revision: 1, title: 'T', year: '2001' })
  })

  it('keeps a refusal\'s message and the item as it now is, never the body', async () => {
    const current = { ...ITEM, revision: 3 }
    stub(409, { error: 'REVISION_CONFLICT', code: 'REVISION_CONFLICT', message: 'The item changed since that revision.', item: current, secret: 'x' })
    const error = await updateProcessingItem('item-1', 1, { title: 'T' }).catch((e: unknown) => e)
    expect(processingRefusal(error)).toEqual({ message: 'The item changed since that revision.', item: current })
    expect(processingErrorMessage(error, 'Save failed.')).toBe('The item changed since that revision.')
    expect(JSON.stringify(processingRefusal(error))).not.toContain('secret')
  })

  it('falls back when a refusal carries only a code', async () => {
    stub(503, { error: 'SERVICE_UNAVAILABLE' })
    const error = await deleteProcessingItem('item-1').catch((e: unknown) => e)
    expect(processingErrorMessage(error, 'Could not remove the item.')).toBe('Could not remove the item.')
  })

  it('answers a result per item for a batch', async () => {
    const call = stub(200, { results: [
      { item_id: 'a', ok: true, work_id: 'W-1', created: true, code: null, message: null, item: null },
      { item_id: 'b', ok: false, work_id: null, created: false, code: 'PROCESSING_ITEM_DELETED', message: 'The item has been deleted.', item: null },
    ] })
    const results = await importProcessingItems(['a', 'b'])
    expect(results.map((one) => [one.item_id, one.ok, one.work_id, one.message])).toEqual([
      ['a', true, 'W-1', null],
      ['b', false, null, 'The item has been deleted.'],
    ])
    expect(JSON.parse(call()[1].body as string)).toEqual({ item_ids: ['a', 'b'] })
  })
})

describe('newUploadKey', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('falls back to getRandomValues without randomUUID (an insecure origin)', () => {
    const real = globalThis.crypto
    vi.stubGlobal('crypto', { getRandomValues: (bytes: Uint8Array) => real.getRandomValues(bytes) })
    const key = newUploadKey()
    expect(key).toMatch(/^[0-9a-f]{32}$/)
    expect(newUploadKey()).not.toBe(key)
  })
})
