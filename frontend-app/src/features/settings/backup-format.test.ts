import { describe, expect, it } from 'vitest'
import { bibtexExportSummary } from './bibtex-summary'
import { backupPhaseLabel, backupSummaryRows, textIndexSummary } from './backup-format'

describe('settings copy', () => {
  it('counts included BibTeX fields from the toggle flags', () => {
    expect(bibtexExportSummary([true, false, true])).toBe('2 of 3 fields included')
  })

  it('keeps the backup phase and text-index sentences', () => {
    expect(backupPhaseLabel({ phase: 'archiving', files_done: 1, files_total: 4 })).toBe(
      'Packing files… 1 of 4 files.',
    )
    expect(backupPhaseLabel({ phase: 'failed', error: 'Disk full.' })).toBe('Disk full.')
    expect(
      backupSummaryRows({ works: 0, persons: '', created_at: '' }).map((row) => row.value),
    ).toEqual(['0'])
    expect(textIndexSummary({ processed: 1, updated: 1 })).toBe('Done. 1 PDF checked: 1 rebuilt.')
  })
})
