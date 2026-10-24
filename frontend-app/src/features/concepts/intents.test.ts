import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserConceptIntents, changedElsewhereNotice } from './intents'
import type { ConceptDefinitionEdit, ConceptDetail } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksConfirmDestructive
  delete window.prksAlertDialog
  delete window.prksPromptTextDialog
  delete window.deleteConcept
  delete window.prksNavigate
  delete window.prksTabContextOwnsEntityRoute
  delete window.prksCreateConceptFlow
  delete window.updateConcept
  delete window.prksBeginConceptDefinitionEdit
  delete window.putConceptAliases
  delete window.putConceptParents
})

function editSession(value: string, revision: number, opId: string | null = null): ConceptDefinitionEdit {
  return {
    concept_id: 'C1',
    value,
    base: { description: { value, revision } },
    expected: { description: opId },
  }
}

function changedElsewhereError(): Error {
  const err = new Error('changed') as Error & { code?: string }
  err.code = 'changed_elsewhere'
  return err
}

function concept(partial?: Partial<ConceptDetail>): ConceptDetail {
  return {
    id: 'C1',
    name: 'One',
    description: '',
    aliases: [],
    parents: [],
    children: [],
    mentions: [],
    mention_count: 0,
    subconcept_count: 0,
    ...partial,
  }
}

describe('browserConceptIntents', () => {
  it('does not delete when the destructive confirm helper is missing', async () => {
    const deleteConcept = vi.fn()
    window.deleteConcept = deleteConcept
    const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 1)
    await intents.remove(concept())
    expect(deleteConcept).not.toHaveBeenCalled()
  })

  it('maps concept_in_use delete failures to the named alert', async () => {
    window.prksConfirmDestructive = vi.fn(async () => true)
    window.deleteConcept = vi.fn(async () => {
      const err = new Error('in use') as Error & { code?: string }
      err.code = 'concept_in_use'
      throw err
    })
    const alerts: Array<{ title: string; message: string }> = []
    window.prksAlertDialog = vi.fn(async (opts) => {
      alerts.push(opts)
    })
    const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 2)
    await intents.remove(concept())
    expect(alerts).toHaveLength(1)
    expect(alerts[0]?.title).toBe('Cannot delete Concept')
    expect(alerts[0]?.message).toContain('still referenced in research notes')
  })

  it('does not navigate after mutation when the owner generation is stale', async () => {
    window.prksConfirmDestructive = vi.fn(async () => true)
    window.deleteConcept = vi.fn(async () => undefined)
    const navigate = vi.fn()
    window.prksNavigate = navigate
    window.prksTabContextOwnsEntityRoute = () => false
    const intents = browserConceptIntents(
      { tabId: 't1', isCurrent: () => false },
      3,
    )
    await intents.remove(concept())
    expect(window.deleteConcept).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('passes owning tab context into createConceptFlow', async () => {
    const flow = vi.fn(async () => null)
    window.prksCreateConceptFlow = flow
    const owner = { tabId: 'secondary', isCurrent: (g: number) => g === 9 }
    const intents = browserConceptIntents(owner, 9)
    await intents.create('Fresh')
    expect(flow).toHaveBeenCalledWith('Fresh', {
      tabId: 'secondary',
      generation: 9,
      isCurrent: expect.any(Function),
    })
  })

  it('fails closed when updateConcept is missing after an edit prompt', async () => {
    window.prksPromptTextDialog = vi.fn(async () => 'Updated definition')
    window.prksBeginConceptDefinitionEdit = vi.fn(async () => editSession('old', 1))
    window.prksTabContextOwnsEntityRoute = () => true
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const alerts: Array<{ title: string; message: string }> = []
    window.prksAlertDialog = vi.fn(async (opts) => {
      alerts.push(opts)
    })
    delete window.updateConcept
    const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 4)
    await intents.editDefinition(concept({ description: 'old' }))
    expect(navigate).not.toHaveBeenCalled()
    expect(alerts).toHaveLength(1)
    expect(alerts[0]?.title).toBe('Could not save the definition')
    expect(alerts[0]?.message).toContain('updateConcept is unavailable')
  })

  it('fails closed when deleteConcept is missing after confirm', async () => {
    window.prksConfirmDestructive = vi.fn(async () => true)
    delete window.deleteConcept
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const alerts: Array<{ title: string; message: string }> = []
    window.prksAlertDialog = vi.fn(async (opts) => {
      alerts.push(opts)
    })
    const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 5)
    await intents.remove(concept())
    expect(navigate).not.toHaveBeenCalled()
    expect(alerts).toHaveLength(1)
    expect(alerts[0]?.title).toBe('Cannot delete Concept')
    expect(alerts[0]?.message).toContain('deleteConcept is unavailable')
  })

  describe('editDefinition (#568)', () => {
    it('opens on the text its base was read with and saves against that session', async () => {
      const session = editSession('Stored elsewhere', 4)
      window.prksBeginConceptDefinitionEdit = vi.fn(async () => session)
      const prompts: Array<{ defaultValue?: string }> = []
      window.prksPromptTextDialog = vi.fn(async (opts) => {
        prompts.push(opts)
        return 'Mine'
      })
      window.prksTabContextOwnsEntityRoute = () => true
      window.prksNavigate = vi.fn()
      const update = vi.fn(async () => ({ id: 'C1' }))
      window.updateConcept = update
      const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 6)
      // The page still paints an older definition; the editor does not open on it.
      await intents.editDefinition(concept({ description: 'Painted before' }))
      expect(prompts).toHaveLength(1)
      expect(prompts[0]?.defaultValue).toBe('Stored elsewhere')
      expect(update).toHaveBeenCalledWith('C1', { description: 'Mine' }, { definitionEdit: session })
      expect(window.prksNavigate).toHaveBeenCalledTimes(1)
    })

    it('keeps the draft and shows the newer text when the definition changed elsewhere', async () => {
      const first = editSession('Original', 2)
      const fresh = editSession('Def A', 3)
      window.prksBeginConceptDefinitionEdit = vi
        .fn<(id: string) => Promise<ConceptDefinitionEdit | null>>()
        .mockResolvedValueOnce(first)
        .mockResolvedValueOnce(fresh)
      const prompts: Array<{ defaultValue?: string; message?: string }> = []
      window.prksPromptTextDialog = vi.fn(async (opts) => {
        prompts.push(opts)
        return prompts.length === 1 ? 'Def B' : 'Def B, after reading A'
      })
      window.prksTabContextOwnsEntityRoute = () => true
      window.prksNavigate = vi.fn()
      const update = vi
        .fn()
        .mockRejectedValueOnce(changedElsewhereError())
        .mockResolvedValueOnce({ id: 'C1' })
      window.updateConcept = update
      const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 7)
      await intents.editDefinition(concept({ description: 'Original' }))

      expect(prompts).toHaveLength(2)
      expect(prompts[1]?.defaultValue).toBe('Def B')
      expect(prompts[1]?.message).toContain('changed in another tab')
      expect(prompts[1]?.message).toContain('Def A')
      expect(update).toHaveBeenNthCalledWith(1, 'C1', { description: 'Def B' }, { definitionEdit: first })
      expect(update).toHaveBeenNthCalledWith(
        2,
        'C1',
        { description: 'Def B, after reading A' },
        { definitionEdit: fresh },
      )
      expect(window.prksNavigate).toHaveBeenCalledTimes(1)
    })

    it('saves nothing more when the user cancels after a stale save, and repaints', async () => {
      window.prksBeginConceptDefinitionEdit = vi
        .fn<(id: string) => Promise<ConceptDefinitionEdit | null>>()
        .mockResolvedValueOnce(editSession('Original', 2))
        .mockResolvedValueOnce(editSession('Def A', 3))
      let calls = 0
      window.prksPromptTextDialog = vi.fn(async () => (++calls === 1 ? 'Def B' : null))
      window.prksTabContextOwnsEntityRoute = () => true
      window.prksNavigate = vi.fn()
      const update = vi.fn().mockRejectedValueOnce(changedElsewhereError())
      window.updateConcept = update
      const alerts: unknown[] = []
      window.prksAlertDialog = vi.fn(async (opts) => {
        alerts.push(opts)
      })
      const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 8)
      await intents.editDefinition(concept({ description: 'Original' }))
      expect(update).toHaveBeenCalledTimes(1)
      expect(alerts).toHaveLength(0)
      expect(window.prksNavigate).toHaveBeenCalledTimes(1)
    })

    it('does not open an editor whose base cannot be known', async () => {
      window.prksBeginConceptDefinitionEdit = vi.fn(async () => null)
      const prompt = vi.fn()
      window.prksPromptTextDialog = prompt
      window.prksTabContextOwnsEntityRoute = () => true
      const alerts: Array<{ title: string; message: string }> = []
      window.prksAlertDialog = vi.fn(async (opts) => {
        alerts.push(opts)
      })
      const intents = browserConceptIntents({ tabId: 't1', isCurrent: () => true }, 9)
      await intents.editDefinition(concept())
      expect(prompt).not.toHaveBeenCalled()
      expect(alerts[0]?.title).toBe('Could not edit the definition')
      expect(alerts[0]?.message).toContain('cannot be edited offline yet')
    })

    it('bounds the newer text it quotes', () => {
      const notice = changedElsewhereNotice('x'.repeat(1000))
      expect(notice).toContain('x'.repeat(280) + '…')
      expect(notice).not.toContain('x'.repeat(281))
      expect(changedElsewhereNotice('  ')).toContain('(empty)')
    })
  })
})
