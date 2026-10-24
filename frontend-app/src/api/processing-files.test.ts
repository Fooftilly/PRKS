import { afterEach, describe, expect, it, vi } from 'vitest'
import { processingFileRow } from './processing-files.fixtures'
import {
  importProcessingFile,
  listProcessingFiles,
  parseProcessingFile,
  parseProcessingFileImported,
  parseProcessingFiles,
  updateProcessingFile,
} from './processing-files'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ROW = processingFileRow()

function stub(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls[0] as unknown as [string, RequestInit]
}

describe('Files for Processing client', () => {
  it('parses rows exactly, including null timestamps and thumbnail page', () => {
    expect(parseProcessingFiles([ROW])).toEqual([ROW])
    expect(parseProcessingFile({ ...ROW, thumb_page: 3 })).toEqual({ ...ROW, thumb_page: 3 })
    expect(parseProcessingFileImported({ processing_file_id: 'PF-1', work_id: 'W-1' })).toEqual({
      processing_file_id: 'PF-1',
      work_id: 'W-1',
    })
  })

  it.each([
    ['not a list', ROW],
    ['unknown status', [{ ...ROW, status: 'queued' }]],
    ['unknown draft status', [{ ...ROW, status_draft: 'Done' }]],
    ['numeric title', [{ ...ROW, title: 1 }]],
    ['string exists', [{ ...ROW, exists: 'true' }]],
    ['fractional thumbnail page', [{ ...ROW, thumb_page: 1.5 }]],
    ['missing tags', [{ ...ROW, tags: undefined }]],
    ['role without order', [{ ...ROW, roles: [{ person_id: 'P-1', person_name: 'A', role_type: 'Author' }] }]],
    ['tag without color key', [{ ...ROW, tags: [{ id: 'T-1', name: 'x', created_at: null }] }]],
  ])('refuses a malformed list (%s)', (_label, payload) => {
    expect(() => parseProcessingFiles(JSON.parse(JSON.stringify(payload)))).toThrowError(
      expect.objectContaining({ code: 'invalid_response', message: 'Could not load files for processing.' }),
    )
  })

  it('reads the stored inbox, or rescans it, with the reader signal', async () => {
    const call = stub(200, [ROW])
    const controller = new AbortController()
    await expect(listProcessingFiles({ signal: controller.signal })).resolves.toEqual([ROW])
    expect(call()[0]).toBe('/api/processing-files')
    expect(call()[1].signal).toBe(controller.signal)
    const rescan = stub(200, [])
    await expect(listProcessingFiles({ rescan: true })).resolves.toEqual([])
    expect(rescan()[0]).toBe('/api/processing-files?rescan=1')
  })

  it('patches one file and imports it, surfacing the server refusal', async () => {
    const patch = stub(200, { ...ROW, title: 'Notes' })
    await expect(updateProcessingFile('PF 1', { title: 'Notes', roles: [], tags: [] })).resolves.toMatchObject({
      title: 'Notes',
    })
    expect(patch()[0]).toBe('/api/processing-files/PF%201')
    expect(patch()[1]).toMatchObject({ method: 'PATCH', body: '{"title":"Notes","roles":[],"tags":[]}' })

    const imported = stub(200, { processing_file_id: 'PF-1', work_id: 'W-1' })
    await expect(importProcessingFile('PF-1')).resolves.toEqual({ processing_file_id: 'PF-1', work_id: 'W-1' })
    expect(imported()[0]).toBe('/api/processing-files/PF-1/import')
    expect(imported()[1]).toMatchObject({ method: 'POST', body: '{}' })

    stub(400, { error: 'Unknown folder.' })
    await expect(importProcessingFile('PF-1')).rejects.toMatchObject({ status: 400, message: 'Unknown folder.' })
    stub(200, { processing_file_id: 'PF-1' })
    await expect(importProcessingFile('PF-1')).rejects.toMatchObject({
      code: 'invalid_response',
      message: 'Could not import file.',
    })
    stub(200, { ...ROW, status: 'queued' })
    await expect(updateProcessingFile('PF-1', {})).rejects.toMatchObject({
      code: 'invalid_response',
      message: 'Could not update processing file metadata.',
    })
  })
})
