import { describe, expect, it, vi } from 'vitest'
import { browserFolderDetailIntents, type FolderDetailIntentOwner } from './intents'

function owner(current = true, entityId: string | null = 'f1'): FolderDetailIntentOwner {
  return {
    tabId: 'tab-1',
    isCurrent: () => current,
    getEntity: () => (entityId == null ? null : { id: entityId }),
  }
}

describe('Folder detail intents', () => {
  it('deletes through the canonical wrapper only while this owner still has the folder', async () => {
    const calls: Array<{ id: string; still: boolean }> = []
    window.prksDeleteFolderFromDetail = async (id, still) => {
      calls.push({ id, still: still ? still() : true })
    }
    await browserFolderDetailIntents(owner(false), 2).remove('f1')
    await browserFolderDetailIntents(owner(true, 'other'), 2).remove('f1')
    expect(calls).toEqual([])
    let live = true
    const pane = owner(true, 'f1')
    pane.isCurrent = () => live
    await browserFolderDetailIntents(pane, 2).remove('f1')
    expect(calls).toEqual([{ id: 'f1', still: true }])
    window.prksDeleteFolderFromDetail = async (_id, still) => {
      live = false
      expect(still?.()).toBe(false)
    }
    await browserFolderDetailIntents(pane, 2).remove('f1')
    delete window.prksDeleteFolderFromDetail
  })

  it('opens the shared new-folder modal for the current folder only', () => {
    const opened: unknown[] = []
    window.prksOpenNewFolderFromDetail = (folder) => {
      opened.push(folder.id)
    }
    browserFolderDetailIntents(owner(false), 1).createChild({ id: 'f1', title: 'Notes' })
    browserFolderDetailIntents(owner(true, 'f1'), 1).createChild({ id: 'f1', title: 'Notes' })
    expect(opened).toEqual(['f1'])
    delete window.prksOpenNewFolderFromDetail
  })

  it('does not read the durable queue', () => {
    const source = browserFolderDetailIntents.toString()
    expect(source).not.toContain('listOperations')
    expect(vi.isMockFunction(window.fetch)).toBe(false)
  })
})
