import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserConceptIntents } from './intents'
import type { ConceptDetail } from './types'

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
  delete window.putConceptAliases
  delete window.putConceptParents
})

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
})
