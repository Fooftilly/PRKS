import { describe, expect, it } from 'vitest'
import {
  acceptWorkMetaField,
  cloneWorkMetaDraft,
  commitWorkMetaBaseline,
  workMetaDraftIsDirty,
  workMetaGroupFields,
  workMetaSessionStill,
  type WorkMetaDraft,
} from './metadata-draft'

function draft(patch: Partial<WorkMetaDraft> = {}): WorkMetaDraft {
  return cloneWorkMetaDraft({
    title: 'Alpha',
    status: 'Not Started',
    doc_type: 'article',
    publisher: 'Held Press',
    ...patch,
  })
}

describe('work metadata draft', () => {
  it('treats the explicit baseline as the leave-guard base, not a later overlay', () => {
    const baseline = draft({ title: 'Alpha', publisher: 'Held Press' })
    const typed = cloneWorkMetaDraft(baseline)
    typed.title = 'Only Work A Draft'
    expect(workMetaDraftIsDirty(typed, baseline)).toBe(true)
    const overlay = cloneWorkMetaDraft(baseline)
    overlay.publisher = 'Server Press'
    expect(workMetaDraftIsDirty(typed, baseline)).toBe(true)
    expect(workMetaDraftIsDirty(cloneWorkMetaDraft(baseline), baseline)).toBe(false)
  })

  it('commits only the saved fields the user has not typed past', () => {
    const baseline = draft()
    const current = cloneWorkMetaDraft(baseline)
    current.title = 'Saved title'
    current.publisher = 'Still typing'
    const snapshot = cloneWorkMetaDraft(current)
    snapshot.publisher = 'Still typing'
    current.publisher = 'Typed after save'
    commitWorkMetaBaseline(current, baseline, snapshot, ['title', 'publisher'])
    expect(baseline.title).toBe('Saved title')
    expect(baseline.publisher).toBe('Held Press')
    expect(workMetaDraftIsDirty(current, baseline)).toBe(true)
  })

  it('keeps a focused field when an acknowledgement arrives', () => {
    const baseline = draft()
    const current = cloneWorkMetaDraft(baseline)
    current.title = 'Typing'
    acceptWorkMetaField(current, baseline, 'title', 'Canonical', 'title')
    expect(current.title).toBe('Typing')
    acceptWorkMetaField(current, baseline, 'publisher', 'Ack Press', '')
    expect(current.publisher).toBe('Ack Press')
    expect(baseline.publisher).toBe('Ack Press')
    expect(baseline.title).toBe('Alpha')
  })

  it('refuses a save once the panel or the session has moved on', () => {
    const open = {
      mode: 'metadata',
      session: 2,
      draftWorkId: 'A',
      entityWorkId: 'A',
      routeName: 'work',
      routeWorkId: 'A',
      panelOwnerTabId: 'main',
      tabId: 'main',
    }
    expect(workMetaSessionStill(open, 'A', 2)).toBe(true)
    expect(workMetaSessionStill({ ...open, entityWorkId: null }, 'A', 2)).toBe(true)
    expect(workMetaSessionStill({ ...open, panelOwnerTabId: 'side' }, 'A', 2)).toBe(false)
    expect(workMetaSessionStill({ ...open, session: 3 }, 'A', 2)).toBe(false)
    expect(workMetaSessionStill({ ...open, entityWorkId: 'B', routeWorkId: 'B' }, 'A', 2)).toBe(false)
    expect(workMetaSessionStill({ ...open, mode: 'view' }, 'A', 2)).toBe(false)
  })

  it('keeps video bibliographic saves to the channel field', () => {
    expect(workMetaGroupFields('bib', 'video')).toEqual(['author_text'])
    expect(workMetaGroupFields('bib', 'pdf')).toContain('abstract')
    expect(workMetaGroupFields('bib', 'pdf')).toContain('source_url')
    expect(workMetaGroupFields('identity', 'pdf')).toEqual(['title', 'doc_type'])
  })
})
