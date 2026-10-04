import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SavedView, SavedViewDeleted, SavedViewSearch } from './generated/saved-views'
import {
  SAVED_VIEW_DELETED_KEYS,
  SAVED_VIEW_KEYS,
  SAVED_VIEW_MODES,
  SAVED_VIEW_SEARCH_KEYS,
  createSavedView,
  deleteSavedView,
  getSavedView,
  listSavedViews,
  parseSavedView,
  parseSavedViews,
  updateSavedView,
  type SavedViewMode,
} from './saved-views'

type SameKeys<T, Keys extends readonly (keyof T)[]> = [Exclude<keyof T, Keys[number]>, Exclude<Keys[number], keyof T>] extends [
  never,
  never,
]
  ? true
  : false

type SameModes = [Exclude<SavedViewMode, (typeof SAVED_VIEW_MODES)[number]>, Exclude<(typeof SAVED_VIEW_MODES)[number], SavedViewMode>] extends [
  never,
  never,
]
  ? true
  : false

afterEach(() => {
  vi.unstubAllGlobals()
})

const SEARCH = { mode: 'advanced', q: 'culture', tag: '', author: 'Adorno', publisher: '' }
const ROW = { id: 'SV-1', name: 'Adorno', search: SEARCH, created_at: '2026-10-03 10:00:00', updated_at: null }

function stub(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return () => fetchMock.mock.calls[0] as unknown as [string, RequestInit]
}

describe('Saved Views client', () => {
  it('keeps runtime response keys aligned with the generated transport types', () => {
    const view: SameKeys<SavedView, typeof SAVED_VIEW_KEYS> = true
    const search: SameKeys<SavedViewSearch, typeof SAVED_VIEW_SEARCH_KEYS> = true
    const deleted: SameKeys<SavedViewDeleted, typeof SAVED_VIEW_DELETED_KEYS> = true
    const modes: SameModes = true
    expect(view && search && deleted && modes).toBe(true)
  })

  it('parses rows exactly, including a null timestamp', () => {
    expect(parseSavedViews([ROW])).toEqual([ROW])
    expect(parseSavedView(ROW)).toEqual(ROW)
  })

  it.each([
    ['not a list', ROW],
    ['missing search key', [{ ...ROW, search: { ...SEARCH, publisher: undefined } }]],
    ['unknown mode', [{ ...ROW, search: { ...SEARCH, mode: 'regex' } }]],
    ['numeric field', [{ ...ROW, search: { ...SEARCH, q: 1 } }]],
    ['missing timestamp', [{ id: 'SV-1', name: 'Adorno', search: SEARCH, created_at: null }]],
    ['numeric id', [{ ...ROW, id: 1 }]],
  ])('refuses a malformed list (%s)', (_label, payload) => {
    expect(() => parseSavedViews(payload)).toThrowError(
      expect.objectContaining({ code: 'invalid_response', message: 'Could not load Saved Views.' }),
    )
  })

  it('reads the list and one view, and answers null for a missing view', async () => {
    const call = stub(200, [ROW])
    const controller = new AbortController()
    await expect(listSavedViews(controller.signal)).resolves.toEqual([ROW])
    expect(call()[0]).toBe('/api/saved-views')
    expect(call()[1].signal).toBe(controller.signal)

    const one = stub(200, ROW)
    await expect(getSavedView('SV 1')).resolves.toEqual(ROW)
    expect(one()[0]).toBe('/api/saved-views/SV%201')

    stub(404, { error: 'Saved View not found.' })
    await expect(getSavedView('SV-9')).resolves.toBeNull()
    stub(500, null)
    await expect(getSavedView('SV-9')).rejects.toMatchObject({ status: 500 })
  })

  it('creates and updates with the whole definition and keeps the server refusal text', async () => {
    const created = stub(201, ROW)
    await expect(createSavedView({ name: 'Adorno', search: ROW.search as never })).resolves.toEqual(ROW)
    expect(created()[1]).toMatchObject({ method: 'POST', body: JSON.stringify({ name: 'Adorno', search: SEARCH }) })

    const updated = stub(200, ROW)
    await updateSavedView('SV-1', { name: 'Adorno', search: ROW.search as never })
    expect(updated()[0]).toBe('/api/saved-views/SV-1')
    expect(updated()[1].method).toBe('PATCH')

    stub(409, { error: 'A Saved View with that name already exists.' })
    await expect(createSavedView({ name: 'Adorno', search: ROW.search as never })).rejects.toMatchObject({
      status: 409,
      message: 'A Saved View with that name already exists.',
    })
    stub(201, { id: 'SV-1' })
    await expect(createSavedView({ name: 'Adorno', search: ROW.search as never })).rejects.toMatchObject({
      code: 'invalid_response',
      message: 'Could not save view.',
    })
  })

  it('refuses a delete answered with an unexpected status', async () => {
    stub(200, { status: 'deleted' })
    await expect(deleteSavedView('SV-1')).resolves.toBeUndefined()
    stub(200, { status: 'added' })
    await expect(deleteSavedView('SV-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
