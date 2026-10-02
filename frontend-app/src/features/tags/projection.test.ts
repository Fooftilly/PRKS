import { describe, expect, it } from 'vitest'
import {
  acceptTagRows,
  buildTagsProjection,
  filterMergeCandidates,
  safeTagCssColor,
  tagCloudScale,
  tagSearchHash,
} from './projection'

describe('Tags projection', () => {
  it('keeps coordinator order and drops rows without an id', () => {
    const rows = acceptTagRows([
      { id: 'b', name: 'Beta', work_count: 1, folder_count: 0, color: '#abc' },
      null,
      { name: 'Missing' },
      { id: '', name: 'Blank' },
      { id: 'a', name: 'Ada', work_count: 0, folder_count: 2, aliases: ['A', 3, 'Ada'] },
    ])
    expect(rows.map((row) => row.id)).toEqual(['b', 'a'])
    expect(rows[1]?.aliases).toEqual(['A', 'Ada'])
    expect(rows[0]?.color).toBe('#abc')
  })

  it('uses the legacy log scale and refuses unsafe colors', () => {
    expect(tagCloudScale(4, 4, 4)).toBe(1)
    expect(tagCloudScale(0, 0, 99)).toBeCloseTo(0.85)
    expect(tagCloudScale(99, 0, 99)).toBeCloseTo(1.85)
    const rows = acceptTagRows([
      { id: 'small', name: 'Small', work_count: 0, folder_count: 0, color: 'red;background:url(https://evil)' },
      { id: 'large', name: 'Large', work_count: 99, folder_count: 0, color: 'ReD' },
    ])
    expect(rows[0]).toMatchObject({ scale: '0.850', borderWidth: '3.40px', color: '#6d6cf7' })
    expect(rows[1]).toMatchObject({ scale: '1.850', borderWidth: '7.40px', color: 'ReD' })
    expect(safeTagCssColor('#aabbccdd')).toBe('#aabbccdd')
    expect(safeTagCssColor('#ab')).toBe('#6d6cf7')
    expect(safeTagCssColor('navy')).toBe('navy')
  })

  it('builds the search hash and merge candidates without the source', () => {
    expect(tagSearchHash('a b')).toBe('#/search?tag=a%20b')
    const rows = acceptTagRows([
      { id: '1', name: 'Alpha', work_count: 1, folder_count: 0 },
      { id: '2', name: 'beta', work_count: 1, folder_count: 0 },
      { id: '3', name: 'Alpine', work_count: 1, folder_count: 0 },
    ])
    expect(rows[0]?.searchHash).toBe('#/search?tag=Alpha')
    expect(rows[0]?.encodedName).toBe('Alpha')
    expect(filterMergeCandidates(rows, '1', '').map((row) => row.id)).toEqual(['2', '3'])
    expect(filterMergeCandidates(rows, '1', 'ALP').map((row) => row.id)).toEqual(['3'])
    expect(filterMergeCandidates(rows, '1', 'nope')).toEqual([])
  })

  it('reopens the alias dialog only for a tag that is still in the list', () => {
    const open = buildTagsProjection({
      tags: [{ id: 't1', name: 'Alpha', work_count: 1, folder_count: 0 }],
      generation: 4,
      resume: { aliasTagId: 't1' },
    })
    expect(open.openAliasTagId).toBe('t1')
    const closed = buildTagsProjection({
      tags: [{ id: 't1', name: 'Alpha', work_count: 1, folder_count: 0 }],
      generation: 4,
      resume: { aliasTagId: 'missing' },
    })
    expect(closed.openAliasTagId).toBeNull()
  })
})
