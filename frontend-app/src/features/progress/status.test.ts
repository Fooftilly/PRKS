import { describe, expect, it } from 'vitest'
import {
  PROGRESS_STATUS_VALUES,
  canonicalProgressStatus,
  normalizeProgressStatusParam,
  progressCanonicalHash,
} from './status'

describe('Progress status', () => {
  it('keeps the five canonical statuses', () => {
    expect(PROGRESS_STATUS_VALUES).toEqual(['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'])
  })

  it('normalizes each canonical status and rejects invalid or missing values', () => {
    for (const status of PROGRESS_STATUS_VALUES) {
      expect(normalizeProgressStatusParam(status)).toBe(status)
    }
    expect(normalizeProgressStatusParam(null)).toBeNull()
    expect(normalizeProgressStatusParam(undefined)).toBeNull()
    expect(normalizeProgressStatusParam('')).toBeNull()
    expect(normalizeProgressStatusParam('   ')).toBeNull()
    expect(normalizeProgressStatusParam('Finished')).toBeNull()
    expect(normalizeProgressStatusParam('completed')).toBeNull()
  })

  it('canonicalizes invalid and missing status to Not Started', () => {
    expect(canonicalProgressStatus(null)).toBe('Not Started')
    expect(canonicalProgressStatus(undefined)).toBe('Not Started')
    expect(canonicalProgressStatus('')).toBe('Not Started')
    expect(canonicalProgressStatus('Finished')).toBe('Not Started')
    expect(canonicalProgressStatus('Paused')).toBe('Paused')
    expect(progressCanonicalHash('Not Started')).toBe('#/progress?status=Not%20Started')
    expect(progressCanonicalHash('In Progress')).toBe('#/progress?status=In%20Progress')
  })
})
