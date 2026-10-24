import { describe, expect, it } from 'vitest'
import { describeWorkDetail, peopleCountForWork } from './detail-model'

describe('describeWorkDetail', () => {
  it('hides the header when a PDF has a file', () => {
    const described = describeWorkDetail(
      { id: 'w1', title: '  Lecture  ', file_path: '/files/a.pdf', tags: ['a', 'b'] },
      'pdf',
      { roles: [{ id: 'p1' }] },
    )
    expect(described).toMatchObject({
      workId: 'w1',
      kind: 'pdf',
      hasFile: true,
      pdfViewerActive: true,
      showHeader: false,
      title: 'Lecture',
      peopleCount: 1,
      tagCount: 2,
    })
  })

  it('keeps a header for video and for a PDF with no file', () => {
    expect(describeWorkDetail({ id: 'v', title: 'Talk', file_path: 'https://example.test' }, 'video').kind).toBe('video')
    expect(describeWorkDetail({ id: 'v', title: 'Talk' }, 'video').showHeader).toBe(true)
    const emptyPdf = describeWorkDetail({ id: 'p', title: '' }, 'pdf')
    expect(emptyPdf).toMatchObject({
      kind: 'pdf',
      hasFile: false,
      pdfViewerActive: false,
      showHeader: true,
      title: 'Document',
      tagCount: null,
    })
  })

  it('reads folder fields from the nested folder when the flat fields are empty', () => {
    const described = describeWorkDetail(
      { id: 'n', title: 'Note', folder: { id: 'f1', title: 'Shelf' } },
      '',
    )
    expect(described.kind).toBe('empty')
    expect(described.folderId).toBe('f1')
    expect(described.folderTitle).toBe('Shelf')
  })

  it('prefers the effective role overlay over the editor roles', () => {
    expect(peopleCountForWork({ roles: [{}, {}] }, { roles: [{}] })).toBe(2)
    expect(peopleCountForWork(null, { roles: [{}] })).toBe(1)
    expect(peopleCountForWork(undefined, {})).toBeNull()
  })
})
