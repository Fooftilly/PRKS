import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from './http'
import {
  attachTagToWork,
  createPdfWork,
  isInvalidCreateAnswer,
  parseTagAttached,
  parseWorkCreated,
  type PdfWorkCreateRequest,
} from './works-create'

afterEach(() => {
  vi.unstubAllGlobals()
})

function request(): PdfWorkCreateRequest {
  return {
    title: 'A paper',
    status: 'Not Started',
    doc_type: 'article',
    abstract: '',
    author_text: '',
    year: '',
    folder_id: 'F-1',
    file_b64: 'UERG',
    file_name: 'a.pdf',
    roles: [{ person_id: 'P-1', role_type: 'Author', credit_name: '' }],
    source_kind: 'pdf',
    source_url: '',
    thumb_url: '',
    provider: '',
    published_date: null,
    urldate: '',
    playlist_id: '',
    private_notes: '',
    thumb_page: null,
    publisher: '',
    location: '',
    edition: '',
    journal: '',
    volume: '',
    issue: '',
    pages: '',
    isbn: '',
    doi: '',
  }
}

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('Work create client', () => {
  it('parses the created id exactly', () => {
    expect(parseWorkCreated({ id: 'W-1' })).toEqual({ id: 'W-1' })
    for (const bad of [null, [], {}, { id: '' }, { id: 7 }]) {
      expect(() => parseWorkCreated(bad)).toThrow(PrksApiError)
    }
  })

  it('accepts only an added tag link', () => {
    expect(() => parseTagAttached({ status: 'added' })).not.toThrow()
    expect(() => parseTagAttached({ status: 'removed' })).toThrow(PrksApiError)
    expect(() => parseTagAttached(null)).toThrow(PrksApiError)
  })

  it('posts the PDF create as JSON and returns the new id', async () => {
    const fetchMock = vi.fn(async () => answer(200, { id: 'W-NEW' }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(createPdfWork(request())).resolves.toEqual({ id: 'W-NEW' })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/works')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual(request())
  })

  it('keeps a refusal code and the server message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => answer(404, { error: 'The selected folder no longer exists.', code: 'FOLDER_NOT_FOUND' })),
    )
    await expect(createPdfWork(request())).rejects.toMatchObject({
      status: 404,
      code: 'FOLDER_NOT_FOUND',
      message: 'The selected folder no longer exists.',
    })
  })

  it('marks a 2xx it cannot read as an invalid answer, not a refusal', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => answer(200, { ok: true })))
    const error = await createPdfWork(request()).catch((e: unknown) => e)
    expect(isInvalidCreateAnswer(error)).toBe(true)
    expect(isInvalidCreateAnswer(new PrksApiError('no', 400, 'FOLDER_NOT_FOUND'))).toBe(false)
    expect(isInvalidCreateAnswer(new TypeError('offline'))).toBe(false)
  })

  it('attaches a tag to the encoded Work path', async () => {
    const fetchMock = vi.fn(async () => answer(200, { status: 'added' }))
    vi.stubGlobal('fetch', fetchMock)
    await attachTagToWork('W/1', 'T-1')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/works/W%2F1/tags')
    expect(JSON.parse(String(init.body))).toEqual({ tag_id: 'T-1' })
  })
})
