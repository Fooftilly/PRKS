import { describe, expect, it } from 'vitest'
import { projectWorkPanelRead, refreshWorkPanelDisplay } from './panel-read'

const server = {
  id: 'w1',
  title: 'Server title',
  status: 'Planned',
  doc_type: 'article',
  year: '1999',
  publisher: 'Server Press',
  folder_id: 'f1',
  folder_title: 'Notes',
  playlist_id: 'p1',
  playlist_title: 'Course',
  file_path: 'files/a.pdf',
  roles: [{ id: 'person-server', role_type: 'Author', first_name: 'Ada', last_name: 'Lovelace' }],
}

describe('projectWorkPanelRead', () => {
  it('keeps the editor base separate from metadata and role overlays', () => {
    const model = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 4,
      workId: 'w1',
      work: server,
      effectiveWork: {
        id: 'w1',
        title: 'Pending title',
        status: 'In Progress',
        publisher: 'Pending Press',
        roles: [{ id: 'person-1', role_type: 'Author', credit_name: 'A. Lovelace', first_name: 'Ada', last_name: 'Lovelace' }],
      },
      tags: [{ id: 't1', name: 'Logic', color: '#fff' }],
      sourceKind: 'pdf',
      publishedDisplay: '',
      docType: { value: 'article', label: 'Article', color: '#3b82f6', border: '#1d4ed8' },
      statusIcon: 'play',
    })

    expect(model.editor.fields.title).toBe('Server title')
    expect(model.editor.fields.publisher).toBe('Server Press')
    expect(model.editor.folderTitle).toBe('Notes')
    expect(model.editor.playlistTitle).toBe('Course')
    expect(model.display.title).toBe('Pending title')
    expect(model.display.publisher).toBe('Pending Press')
    expect(model.display.people[0]?.displayName).toBe('A. Lovelace')
    expect(model.display.people[0]?.canonicalName).toBe('Ada Lovelace')
    expect(model.display.folder?.title).toBe('Notes')
    expect(model.display.playlist?.title).toBe('Course')
    expect(model.display.tags.map((tag) => tag.name)).toEqual(['Logic'])
    expect(model.display.showOriginalUrl).toBe(false)
  })

  it('builds independent models for Main and Secondary', () => {
    const main = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 1,
      workId: 'w-main',
      work: { id: 'w-main', title: 'Main work', folder_title: 'Main folder', folder_id: 'fm' },
      tags: [],
      sourceKind: 'pdf',
    })
    const secondary = projectWorkPanelRead({
      ownerTabId: 'secondary',
      ownerGeneration: 9,
      workId: 'w-side',
      work: { id: 'w-side', title: 'Side work' },
      tags: [{ id: 't2', name: 'Side', color: '' }],
      sourceKind: 'video',
    })
    expect(main.display.title).toBe('Main work')
    expect(secondary.display.title).toBe('Side work')
    expect(main.ownerTabId).not.toBe(secondary.ownerTabId)
    expect(main.display.folder?.title).toBe('Main folder')
    expect(secondary.display.folder).toBeNull()
    expect(secondary.display.tagsCount).toBe(1)
  })

  it('a tags refresh does not replace the editor base or the pending title', () => {
    const current = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 2,
      workId: 'w1',
      work: server,
      effectiveWork: { id: 'w1', title: 'Pending title' },
      tags: [],
      sourceKind: 'pdf',
    })
    const next = refreshWorkPanelDisplay(current, {
      tags: [{ id: 't9', name: 'Added', color: '#111' }],
    })
    expect(next.editor.fields.title).toBe('Server title')
    expect(next.display.title).toBe('Pending title')
    expect(next.display.tags.map((tag) => tag.name)).toEqual(['Added'])
    expect(next.display.folder?.title).toBe('Notes')
  })

  it('a metadata refresh does not copy the overlay onto the editor base', () => {
    const current = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 2,
      workId: 'w1',
      work: server,
      effectiveWork: server,
      tags: [],
      sourceKind: 'pdf',
    })
    const next = refreshWorkPanelDisplay(current, {
      effectiveWork: { title: 'Overlay title', publisher: 'Overlay Press' },
      publishedDisplay: '02/02/2002',
    })
    expect(next.editor.fields.title).toBe('Server title')
    expect(next.editor.fields.publisher).toBe('Server Press')
    expect(next.display.title).toBe('Overlay title')
    expect(next.display.publisher).toBe('Overlay Press')
    expect(next.display.publishedDisplay).toBe('02/02/2002')
    expect(next.editor.fields.author_text).toBe('')
    expect(next.editor.fields.thumb_page).toBe('')
    expect(next.editor.fields.source_url).toBe('')
  })

  it('a metadata refresh keeps pending people and updates type, status, and source url', () => {
    const current = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 2,
      workId: 'w1',
      work: {
        ...server,
        author_text: 'Acknowledged author',
        thumb_page: '2',
        source_url: 'https://example.test/old',
        doc_type: 'article',
        status: 'Planned',
      },
      effectiveWork: {
        id: 'w1',
        title: 'Pending title',
        status: 'Planned',
        doc_type: 'article',
        source_url: 'https://example.test/old',
        author_text: 'Pending author',
        roles: [{ id: 'person-pending', role_type: 'Author', credit_name: 'Pending Person', first_name: 'Pending', last_name: 'Person' }],
      },
      tags: [],
      sourceKind: 'pdf',
      docType: { value: 'article', label: 'Article', color: '#3b82f6', border: '#1d4ed8' },
      statusIcon: 'circle',
    })
    const next = refreshWorkPanelDisplay(current, {
      effectiveWork: {
        title: 'Overlay title',
        status: 'Completed',
        doc_type: 'book',
        source_url: 'https://example.test/new',
        author_text: 'Overlay author',
        thumb_page: '4',
        roles: [{ id: 'person-server', role_type: 'Author', first_name: 'Ada', last_name: 'Lovelace' }],
      },
      docType: { value: 'book', label: 'Book', color: '#a855f7', border: '#6d28d9' },
      statusIcon: 'check',
    })
    expect(next.display.people[0]?.displayName).toBe('Pending Person')
    expect(next.display.peopleCount).toBe(1)
    expect(next.display.docType).toEqual({ value: 'book', label: 'Book', color: '#a855f7', border: '#6d28d9' })
    expect(next.display.status).toBe('Completed')
    expect(next.display.statusIcon).toBe('check')
    expect(next.display.sourceUrl).toBe('https://example.test/new')
    expect(next.display.showOriginalUrl).toBe(true)
    expect(next.editor.fields.author_text).toBe('Acknowledged author')
    expect(next.editor.fields.thumb_page).toBe('2')
    expect(next.editor.fields.source_url).toBe('https://example.test/old')
    expect(next.editor.fields.title).toBe('Server title')
  })

  it('recomputes a changed document type and drops a stale status icon', () => {
    const current = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 2,
      workId: 'w1',
      work: server,
      effectiveWork: { id: 'w1', status: 'Planned', doc_type: 'article' },
      tags: [],
      sourceKind: 'pdf',
      docType: { value: 'article', label: 'Article', color: '#3b82f6', border: '#1d4ed8' },
      statusIcon: 'circle',
    })
    const next = refreshWorkPanelDisplay(current, {
      effectiveWork: { status: 'Completed', doc_type: 'book' },
    })
    expect(next.display.docType.value).toBe('book')
    expect(next.display.docType.label).toBe('book')
    expect(next.display.status).toBe('Completed')
    expect(next.display.statusIcon).toBe('')
  })

  it('does not link a folder that has a title and no id', () => {
    const model = projectWorkPanelRead({
      ownerTabId: 'main',
      ownerGeneration: 1,
      workId: 'w1',
      work: { id: 'w1', title: 'Loose', folder_title: 'Notes only' },
      tags: [],
      sourceKind: 'pdf',
    })
    expect(model.display.folder).toBeNull()
    expect(model.editor.folderId).toBe('')
    expect(model.editor.folderTitle).toBe('')
  })
})
