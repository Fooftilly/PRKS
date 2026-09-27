import { afterEach, describe, expect, it, vi } from 'vitest'
import { browserPositionIntents } from './intents'
import type { PositionDetail } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksPromptTextDialog
  delete window.prksAlertDialog
  delete window.prksNavigate
  delete window.prksGraphFocusHash
  delete window.createPosition
})

function position(partial?: Partial<PositionDetail>): PositionDetail {
  return {
    id: 'P1',
    name: 'One',
    description: '',
    arguments: [],
    ...partial,
  }
}

function indexOwner(current = true) {
  return {
    tabId: 'tab-main',
    isCurrent: () => current,
    lastResolvedRoute: { name: 'positions' as const },
  }
}

describe('browserPositionIntents', () => {
  it('creates a Position and navigates the owning tab', async () => {
    window.prksPromptTextDialog = vi.fn(async () => '  Local claim  ')
    const createPosition = vi.fn(async () => ({ id: 'P-new', name: 'Local claim' }))
    window.createPosition = createPosition
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const intents = browserPositionIntents(indexOwner(), 4)
    await intents.create()
    expect(createPosition).toHaveBeenCalledWith({ name: 'Local claim' })
    expect(navigate).toHaveBeenCalledWith('#/positions/P-new', { tabId: 'tab-main' })
  })

  it('does not create after the owning index route is gone', async () => {
    window.prksPromptTextDialog = vi.fn(async () => 'Name')
    const createPosition = vi.fn()
    window.createPosition = createPosition
    const intents = browserPositionIntents(
      { tabId: 't', isCurrent: () => false, lastResolvedRoute: { name: 'positions' } },
      1,
    )
    await intents.create()
    expect(createPosition).not.toHaveBeenCalled()
  })

  it('reports create failure only while the index still owns the route', async () => {
    window.prksPromptTextDialog = vi.fn(async () => 'Name')
    window.createPosition = vi.fn(async () => {
      throw new Error('queue down')
    })
    const alertFn = vi.fn(async () => {})
    window.prksAlertDialog = alertFn
    await browserPositionIntents(indexOwner(), 1).create()
    expect(alertFn).toHaveBeenCalledWith({
      title: 'Could not create Position',
      message: 'queue down',
    })

    alertFn.mockClear()
    let current = true
    window.createPosition = vi.fn(async () => {
      current = false
      throw new Error('late')
    })
    await browserPositionIntents(
      { tabId: 't', isCurrent: () => current, lastResolvedRoute: { name: 'positions' } },
      2,
    ).create()
    expect(alertFn).not.toHaveBeenCalled()
  })

  it('navigates view-in-graph through the canonical hash helper', () => {
    window.prksGraphFocusHash = (kind, id) => `#/graph?focus=${kind}:${id}`
    const navigate = vi.fn()
    window.prksNavigate = navigate
    browserPositionIntents(indexOwner(), 1).viewGraph(position({ id: 'P9' }))
    expect(navigate).toHaveBeenCalledWith('#/graph?focus=position:P9', { tabId: 'tab-main' })
  })
})
