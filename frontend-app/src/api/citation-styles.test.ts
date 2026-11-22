import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from './http'
import { deleteImportedStyle, importStyle, listImportedStyles, readImportedStyle } from './citation-styles'

const ID = `custom-${'a'.repeat(32)}`

afterEach(() => {
  vi.unstubAllGlobals()
})

function answering(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function call(fetchMock: ReturnType<typeof answering>, index = 0): [string, RequestInit] {
  return fetchMock.mock.calls[index] as unknown as [string, RequestInit]
}

describe('imported citation styles client', () => {
  it('lists, reads, imports and deletes', async () => {
    let fetchMock = answering({ styles: [{ id: ID, title: 'House', created_at: '2026-11-21T10:00:00+00:00' }] })
    await expect(listImportedStyles()).resolves.toEqual([{ id: ID, title: 'House', created_at: '2026-11-21T10:00:00+00:00' }])
    expect(call(fetchMock)[0]).toBe('/api/citation-styles')

    fetchMock = answering({ id: ID, title: 'House', csl: '<style/>' })
    await expect(readImportedStyle(ID)).resolves.toEqual({ id: ID, title: 'House', csl: '<style/>' })
    expect(call(fetchMock)[0]).toBe(`/api/citation-styles/${ID}`)

    fetchMock = answering({ id: ID, title: 'House', created_at: 'now' }, 201)
    await expect(importStyle('<style/>')).resolves.toMatchObject({ id: ID, title: 'House' })
    expect(call(fetchMock)[1].method).toBe('POST')
    expect(JSON.parse(String(call(fetchMock)[1].body))).toEqual({ csl: '<style/>' })

    fetchMock = answering({ deleted: ID })
    await deleteImportedStyle(ID)
    expect(call(fetchMock)).toEqual([`/api/citation-styles/${ID}`, expect.objectContaining({ method: 'DELETE' })])
  })

  it('carries the server reason for a refused file and refuses a malformed answer', async () => {
    answering({ error: 'This is a dependent style: it only points to another style. Import that style instead.' }, 400)
    await expect(importStyle('<style/>')).rejects.toMatchObject({
      status: 400, message: 'This is a dependent style: it only points to another style. Import that style instead.',
    })
    answering({ styles: [{ id: 'apa', title: 'APA', created_at: 'x' }] })
    await expect(listImportedStyles()).rejects.toBeInstanceOf(PrksApiError)
    const fetchMock = answering({})
    await expect(readImportedStyle('../settings')).rejects.toMatchObject({ status: 404 })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
