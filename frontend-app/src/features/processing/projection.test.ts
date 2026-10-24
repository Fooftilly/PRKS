import { describe, expect, it } from 'vitest'
import {
  buildProcessingProjection,
  filterProcessingFolders,
  filterProcessingPeople,
  filterProcessingTags,
  normalizeProcessingFile,
  processingFoldersAfterCreate,
  processingPeopleAfterCreate,
  processingVisibleCount,
  processingWidgetPrefix,
  type ProcessingFolder,
  type ProcessingPerson,
} from './projection'

describe('Processing inbox projection', () => {
  it('marks missing and error rows as not importable and hides a missing preview', () => {
    const missing = normalizeProcessingFile({
      id: 'a',
      filename: 'a.pdf',
      rel_path: 'inbox/a.pdf',
      status: 'missing',
      exists: false,
    })
    const ready = normalizeProcessingFile({
      id: 'b',
      filename: 'b.pdf',
      status: 'pending',
      exists: true,
      doc_type: 'book',
      roles: [{ person_id: 'p1', person_name: 'Ada', role_type: 'Author' }],
      tags: [{ id: 't1', name: 'Draft' }],
    })
    expect(missing?.canImport).toBe(false)
    expect(missing?.canPreview).toBe(false)
    expect(missing?.sourceHint).toBe('Source file missing from for_processing.')
    expect(ready?.canImport).toBe(true)
    expect(ready?.canPreview).toBe(true)
    expect(ready?.draft.doc_type).toBe('book')
    expect(ready?.draft.roles).toEqual([{ person_id: 'p1', person_name: 'Ada', role_type: 'Author' }])
    expect(ready?.draft.tags).toEqual([{ id: 't1', name: 'Draft' }])
  })

  it('keeps a requested window and otherwise shows the first page', () => {
    expect(processingVisibleCount(undefined, 40)).toBe(25)
    expect(processingVisibleCount(30, 40)).toBe(30)
    expect(processingVisibleCount(80, 40)).toBe(40)
    const projection = buildProcessingProjection({
      files: [{ id: 'a', exists: true }, { id: '', exists: true }],
      people: [{ id: 'p', first_name: 'Ada', last_name: 'Lovelace' }],
      folders: [{ id: 'f', title: 'Inbox' }],
      roleTypes: ['Author', ''],
      domPrefix: 'prks-pf-main',
      generation: 3,
      resume: { visibleCount: 10 },
    })
    expect(projection.files.map((file) => file.id)).toEqual(['a'])
    expect(projection.people[0]?.name).toBe('Ada Lovelace')
    expect(projection.folders).toEqual([{ id: 'f', title: 'Inbox' }])
    expect(projection.roleTypes).toEqual(['Author'])
    expect(projection.visibleCount).toBe(1)
    expect(projection.generation).toBe(3)
  })

  it('sanitizes widget ids and filters catalogs without a second role list', () => {
    expect(processingWidgetPrefix('prks pf', 'a/b', 'status')).toBe('prks_pf-status-a_b')
    expect(filterProcessingPeople(
      [{ id: 'p', name: 'Ada Lovelace', raw: {} }],
      'ada',
    ).map((person) => person.id)).toEqual(['p'])
    expect(filterProcessingFolders([{ id: 'f', title: 'Notes' }], 'no')).toEqual([{ id: 'f', title: 'Notes' }])
    const tags = filterProcessingTags(
      [{ id: 't', name: 'Draft', aliases: [] }],
      'new tag',
      new Set(['t']),
    )
    expect(tags.tags).toEqual([])
    expect(tags.canCreate).toBe(true)
  })
})

const ada: ProcessingPerson = { id: 'ada', name: 'Ada Lovelace', raw: { id: 'ada' } }
const grace: ProcessingPerson = { id: 'grace', name: 'Grace Hopper', raw: { id: 'grace' } }
const library: ProcessingFolder = { id: 'lib', title: 'Library' }
const archive: ProcessingFolder = { id: 'arc', title: 'Archive' }

describe('Processing catalogue after quick-create', () => {
  it('keeps painted people when the returned catalogue is empty or only the new person', () => {
    const created = { id: 'new', name: 'New Person' }
    expect(processingPeopleAfterCreate([ada, grace], created).map((person) => person.id)).toEqual([
      'ada',
      'grace',
      'new',
    ])
    expect(processingPeopleAfterCreate([ada, grace], { id: 'ada', name: 'Ada Lovelace' })).toEqual([ada, grace])
  })

  it('keeps painted folders when the folder read is empty or only the new folder', () => {
    const created = { id: 'created', title: 'Created' }
    expect(processingFoldersAfterCreate([library, archive], [], created).map((folder) => folder.id)).toEqual([
      'lib',
      'arc',
      'created',
    ])
    expect(processingFoldersAfterCreate([library, archive], [created], created).map((folder) => folder.title)).toEqual([
      'Library',
      'Archive',
      'Created',
    ])
    expect(processingFoldersAfterCreate([library], null, created).map((folder) => folder.id)).toEqual(['lib', 'created'])
  })
})
