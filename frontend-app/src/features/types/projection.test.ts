import { describe, expect, it } from 'vitest'
import {
  acceptTypesIndexRows,
  buildTypeDetailProjection,
  buildTypesIndexProjection,
  typeDetailHref,
  typeFileCountLabel,
} from './projection'

describe('File types projection', () => {
  it('keeps coordinator order and drops non-rows', () => {
    const rows = acceptTypesIndexRows([
      { value: 'book', label: 'Book', count: 2 },
      null,
      { value: '', label: 'Missing', count: 4 },
      { count: 3 },
      { value: 'article', label: 'Article', count: 9 },
    ])
    expect(rows.map((row) => row.value)).toEqual(['book', 'article'])
    expect(rows[0]).toEqual({ value: 'book', label: 'Book', count: 2 })
  })

  it('formats the legacy file count and type path', () => {
    expect(typeFileCountLabel(1)).toBe('1 file')
    expect(typeFileCountLabel(0)).toBe('0 files')
    expect(typeFileCountLabel(2)).toBe('2 files')
    expect(typeDetailHref('mastersthesis')).toBe('#/types/mastersthesis')
    expect(typeDetailHref('a b')).toBe('#/types/a%20b')
  })

  it('records detail provenance without sorting', () => {
    const projection = buildTypeDetailProjection({
      docType: 'book',
      label: 'Book',
      rows: [{ id: 'b', title: 'Zed' }, null, { id: 'a', title: 'Ada' }],
      offlineCached: true,
      generation: 4,
    })
    expect(projection.rows.map((row) => row.id)).toEqual(['b', 'a'])
    expect(projection.offlineCached).toBe(true)
    expect(projection.docType).toBe('book')
    expect(projection.label).toBe('Book')
    expect(projection.generation).toBe(4)
  })

  it('builds an index projection without regrouping', () => {
    const projection = buildTypesIndexProjection({
      rows: [{ value: 'misc', label: 'Misc', count: 1 }],
      generation: 2,
    })
    expect(projection.rows).toEqual([{ value: 'misc', label: 'Misc', count: 1 }])
    expect(projection.generation).toBe(2)
  })
})
