import { describe, expect, it } from 'vitest'
import { workCardThumbOptions } from '../../components/work-card'
import {
  acceptEffectiveRows,
  progressCardSubtitle,
  progressFileCountLabel,
  progressRowsForStatus,
  progressVisibleRows,
} from './rows'
import { WORK_STATUSES } from '../../domain/work-status'
import type { ProgressStatus } from './status'

describe('Progress effective rows', () => {
  const catalog = [
    { id: 'c', title: 'zeta', status: 'Planned', abstract_excerpt: 'from the projection' },
    { id: 'a', title: 'Alpha', status: 'Planned', abstract_excerpt: '' },
    { id: 'b', title: 'beta', status: 'Completed', abstract_excerpt: 'done' },
    { id: 'd', title: 'c', status: 'In Progress' },
  ]

  it('filters each canonical status without mutating the handed rows', () => {
    const frozen = catalog.map((row) => Object.freeze({ ...row }))
    const snapshot = Object.freeze(frozen.slice())
    for (const status of WORK_STATUSES as readonly ProgressStatus[]) {
      const visible = progressVisibleRows(snapshot, status)
      expect(visible.every((row) => row.status === status)).toBe(true)
      expect(snapshot.map((row) => row.id)).toEqual(['c', 'a', 'b', 'd'])
    }
    expect(progressVisibleRows(snapshot, 'Paused').map((row) => row.id)).toEqual([])
    expect(progressVisibleRows(snapshot, 'Not Started').map((row) => row.id)).toEqual([])
    expect(progressVisibleRows(snapshot, 'Planned').map((row) => row.id)).toEqual(['a', 'c'])
    expect(progressVisibleRows(snapshot, 'Completed').map((row) => row.id)).toEqual(['b'])
    expect(progressVisibleRows(snapshot, 'In Progress').map((row) => row.id)).toEqual(['d'])
  })

  it('sorts titles alphabetically with base sensitivity', () => {
    const rows = progressVisibleRows(
      [
        { id: '1', title: 'b', status: 'Paused' },
        { id: '2', title: 'A', status: 'Paused' },
        { id: '3', title: 'c', status: 'Paused' },
      ],
      'Paused',
    )
    expect(rows.map((row) => row.title)).toEqual(['A', 'b', 'c'])
  })

  it('moves a work when the effective status changes and ignores any other status field', () => {
    const rows = [
      { id: 'W-S', title: 'Paper', status: 'Completed', acknowledgedStatus: 'Planned' },
      { id: 'W-STAY', title: 'Stay', status: 'Planned' },
    ]
    expect(progressRowsForStatus(rows, 'Planned').map((row) => row.id)).toEqual(['W-STAY'])
    expect(progressRowsForStatus(rows, 'Completed').map((row) => row.id)).toEqual(['W-S'])
    const moved = rows.map((row) => (row.id === 'W-S' ? { ...row, status: 'Paused' } : row))
    expect(progressRowsForStatus(moved, 'Completed').map((row) => row.id)).toEqual([])
    expect(progressRowsForStatus(moved, 'Paused').map((row) => row.id)).toEqual(['W-S'])
    expect(progressRowsForStatus(moved, 'Planned').map((row) => row.id)).toEqual(['W-STAY'])
  })

  it('uses the bounded excerpt and does not treat an empty excerpt as the full abstract', () => {
    expect(progressCardSubtitle({ abstract_excerpt: 'bounded' })).toBe('bounded…')
    expect(progressCardSubtitle({ abstract_excerpt: '' , abstract: 'should not be used' })).toBe('')
    window.prksAbstractExcerpt = (value) => `excerpt:${String(value)}`
    expect(progressCardSubtitle({ abstract: 'full text' })).toBe('excerpt:full text…')
    delete window.prksAbstractExcerpt
  })

  it('labels an empty group and a single file', () => {
    expect(progressFileCountLabel(0)).toBe('0 files')
    expect(progressFileCountLabel(1)).toBe('1 file')
    expect(progressFileCountLabel(2)).toBe('2 files')
  })

  it('drops non-rows and suppresses cached thumbnails only when offline', () => {
    expect(acceptEffectiveRows(null)).toEqual([])
    expect(acceptEffectiveRows([{ id: 'ok', status: 'Planned' }, null, 'no', []])).toEqual([
      { id: 'ok', status: 'Planned' },
    ])
    expect(workCardThumbOptions(true, { subtitle: 'sub' })).toEqual({ subtitle: 'sub', suppressThumbnail: true })
    expect(workCardThumbOptions(false, { subtitle: 'sub' })).toEqual({ subtitle: 'sub' })
  })
})
