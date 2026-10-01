import { describe, expect, it } from 'vitest'
import {
  acceptFolderDetail,
  buildFolderDetailProjection,
  effectiveFolderWorks,
} from './projection'

describe('Folder detail projection', () => {
  it('keeps the effective folder and raw works for delete eligibility', () => {
    const folder = acceptFolderDetail({
      id: 'f1',
      title: 'Notes',
      description: '',
      children: [{ id: 'c1', title: 'Child' }],
      works: [{ id: 'raw' }],
    })
    expect(folder?.id).toBe('f1')
    expect(folder?.works).toEqual([{ id: 'raw' }])
    expect(folder?.source.title).toBe('Notes')
  })

  it('applies the folder-domain work overlay and does not replace raw works', () => {
    window.prksEffectiveFolderDetailWorks = (value) => {
      const works = (value as { works?: unknown[] }).works || []
      return works.map((work) => ({ ...(work as object), title: 'Overlay' }))
    }
    const projection = buildFolderDetailProjection({
      folder: { id: 'f1', title: 'Notes', works: [{ id: 'raw', title: 'Ack' }], children: [] },
      offlineCached: true,
      preserveWorkspace: true,
      generation: 3,
    })
    expect(projection.availability).toBe('ready')
    expect(projection.folder?.works).toEqual([{ id: 'raw', title: 'Ack' }])
    expect(projection.effectiveWorks).toEqual([{ id: 'raw', title: 'Overlay' }])
    expect(projection.offlineCached).toBe(true)
    expect(projection.preserveWorkspace).toBe(true)
    expect(projection.folderId).toBe('f1')
    delete window.prksEffectiveFolderDetailWorks
  })

  it('treats a missing folder as not-found and an unavailable read as unavailable', () => {
    expect(buildFolderDetailProjection({ folder: null, folderId: 'gone', generation: 1 }).availability).toBe(
      'not-found',
    )
    const unavailable = buildFolderDetailProjection({
      availability: 'unavailable',
      folder: { id: 'f1', title: 'Cached' },
      generation: 1,
    })
    expect(unavailable.availability).toBe('unavailable')
    expect(unavailable.folder).toBeNull()
    expect(effectiveFolderWorks(null)).toEqual([])
  })
})
