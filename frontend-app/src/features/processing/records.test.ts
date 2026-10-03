import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from '../../api/http'
import { processingFileRow } from '../../api/processing-files.fixtures'
import { createPrksQueryClient } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'
import type { ProcessingFileDraft } from './projection'
import { processingActionMessage, processingRecords, processingUpdateFromDraft } from './records'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksReportClientError
  delete window.prksMarkProcessingImportChanged
  delete window.prksParsePublishedDateInput
})

const ROW = processingFileRow()
const IMPORTED = { processing_file_id: 'PF-1', work_id: 'W-1' }

function draft(overrides: Partial<ProcessingFileDraft> = {}): ProcessingFileDraft {
  return {
    title: 'Notes',
    status_draft: 'Planned',
    abstract: '',
    source_url: '',
    published_date: '03.10.2026',
    year: '2026',
    publisher: '',
    location: '',
    edition: '',
    journal: '',
    volume: '',
    issue: '',
    pages: '',
    isbn: '',
    doi: '',
    doc_type: 'article',
    private_notes: '',
    thumb_page: '2',
    target_folder_id: 'F-1',
    roles: [{ person_id: 'P-1', person_name: 'Ada Lovelace', role_type: 'Author' }],
    tags: [{ id: 'T-1', name: 'logic' }],
    ...overrides,
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fetch that answers each request only when the test releases it. */
function heldFetch() {
  const calls: Array<{ url: string; init: RequestInit; release: (response: Response) => void }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (url: string, init: RequestInit = {}) =>
        new Promise<Response>((resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          calls.push({ url, init, release: resolve })
        }),
    ),
  )
  return calls
}

describe('Processing records', () => {
  it('shares concurrent inbox reads, asks again for each new read, and keeps rescan apart', async () => {
    const fetchMock = vi.fn(async () => json([ROW]))
    vi.stubGlobal('fetch', fetchMock)
    const records = processingRecords(createPrksQueryClient())
    await Promise.all([records.inbox(), records.inbox()])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await expect(records.inbox()).resolves.toEqual([ROW])
    await Promise.all([records.inbox({ rescan: true }), records.inbox({ rescan: true })])
    expect(fetchMock.mock.calls.map((call) => (call as unknown as [string])[0])).toEqual([
      '/api/processing-files',
      '/api/processing-files',
      '/api/processing-files?rescan=1',
    ])
  })

  it('reports a failed read once under the classic source and does not retry a rescan', async () => {
    const report = vi.fn()
    window.prksReportClientError = report
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', failing)
    const records = processingRecords(createPrksQueryClient())
    await expect(records.inbox({ rescan: true })).rejects.toBeInstanceOf(TypeError)
    expect(failing).toHaveBeenCalledTimes(1)
    vi.stubGlobal('fetch', vi.fn(async () => json({ rows: [] })))
    await expect(records.inbox({ rescan: true })).rejects.toMatchObject({ code: 'invalid_response' })
    expect(report.mock.calls).toEqual(
      Array.from({ length: 2 }, () => [{ kind: 'api_client_error', source: 'processing-files.fetch' }]),
    )
  })

  it('rejects an aborted reader and cancels the request only when the last reader leaves', async () => {
    const calls = heldFetch()
    const records = processingRecords(createPrksQueryClient())
    const first = new AbortController()
    const second = new AbortController()
    const a = records.inbox({ rescan: true, signal: first.signal })
    const b = records.inbox({ rescan: true, signal: second.signal })
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    first.abort()
    await expect(a).rejects.toMatchObject({ name: 'AbortError' })
    expect(calls[0].init.signal?.aborted).toBe(false)
    second.abort()
    await expect(b).rejects.toMatchObject({ name: 'AbortError' })
    await vi.waitFor(() => expect(calls[0].init.signal?.aborted).toBe(true))
  })

  it('never answers a read asked after an import with a rescan that began before it', async () => {
    const calls = heldFetch()
    const records = processingRecords(createPrksQueryClient())
    const before = records.inbox({ rescan: true })
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const imported = records.importFile('PF-1')
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].release(json(IMPORTED))
    await expect(imported).resolves.toEqual(IMPORTED)
    const after = records.inbox({ rescan: true })
    calls[0].release(json([ROW]))
    await expect(before).resolves.toEqual([ROW])
    await vi.waitFor(() => expect(calls).toHaveLength(3))
    expect(calls[2].url).toBe('/api/processing-files?rescan=1')
    calls[2].release(json([]))
    await expect(after).resolves.toEqual([])
  })

  it('holds a read asked for while an import is in flight until the import settles', async () => {
    const calls = heldFetch()
    const records = processingRecords(createPrksQueryClient())
    const imported = records.importFile('PF-1')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const during = records.inbox({ rescan: true })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(calls).toHaveLength(1)
    calls[0].release(json(IMPORTED))
    await expect(imported).resolves.toEqual(IMPORTED)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].url).toBe('/api/processing-files?rescan=1')
    calls[1].release(json([]))
    await expect(during).resolves.toEqual([])
  })

  it('saves the normalized draft and invalidates the domain, without retrying a write', async () => {
    window.prksParsePublishedDateInput = (raw: string) => (raw === '03.10.2026' ? '2026-10-03' : '')
    const fetchMock = vi.fn(async () => json({ ...ROW, title: 'Notes' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = createPrksQueryClient()
    client.setQueryData(prksQueryKeys.processingFiles.inbox('stored'), [ROW])
    await expect(processingRecords(client).save('PF-1', draft())).resolves.toMatchObject({ title: 'Notes' })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/processing-files/PF-1')
    expect(JSON.parse(String(init.body))).toMatchObject({
      title: 'Notes',
      published_date: '2026-10-03',
      thumb_page: '2',
      target_folder_id: 'F-1',
      roles: [{ person_id: 'P-1', role_type: 'Author' }],
      tags: [{ id: 'T-1' }],
    })
    expect(client.getQueryState(prksQueryKeys.processingFiles.inbox('stored'))?.isInvalidated).toBe(true)

    client.setQueryData(prksQueryKeys.processingFiles.inbox('stored'), [ROW])
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', failing)
    await expect(processingRecords(client).save('PF-1', draft())).rejects.toBeInstanceOf(TypeError)
    expect(failing).toHaveBeenCalledTimes(1)
    expect(client.getQueryState(prksQueryKeys.processingFiles.inbox('stored'))?.isInvalidated).toBe(true)
  })

  it('marks the offline Work-create caches after every import that was sent', async () => {
    const marks = vi.fn()
    window.prksMarkProcessingImportChanged = marks
    vi.stubGlobal('fetch', vi.fn(async () => json(IMPORTED)))
    const records = processingRecords(createPrksQueryClient())
    await records.importFile('PF-1')
    expect(marks).toHaveBeenCalledTimes(1)
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'Inbox file no longer present.' }, 400)))
    await expect(records.importFile('PF-1')).rejects.toMatchObject({ message: 'Inbox file no longer present.' })
    expect(marks).toHaveBeenCalledTimes(2)
  })

  it('narrows the draft to the PATCH body and sends an unparseable date empty', () => {
    window.prksParsePublishedDateInput = () => ''
    const update = processingUpdateFromDraft(draft())
    expect(update.published_date).toBe('')
    expect(Object.keys(update)).not.toContain('author_text')
    expect(update.roles).toEqual([{ person_id: 'P-1', role_type: 'Author' }])
  })

  it('uses the server refusal for an action message and the fallback otherwise', () => {
    expect(processingActionMessage(new PrksApiError('Unknown folder.', 400), 'Save failed.')).toBe('Unknown folder.')
    expect(processingActionMessage(new PrksApiError('PRKS could not complete the request.', 500), 'Save failed.')).toBe(
      'Save failed.',
    )
    expect(processingActionMessage(new TypeError('Failed to fetch'), 'Save failed.')).toBe('Save failed.')
  })
})
