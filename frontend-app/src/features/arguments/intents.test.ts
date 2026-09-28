import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  argumentSourcePickerItems,
  argumentTargetPickerItems,
  browserArgumentIntents,
  type ArgumentIntentOwner,
} from './intents'
import type { ArgumentDetail, ArgumentEditorDraft } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.prksPromptTextDialog
  delete window.prksNavigate
  delete window.prksAlertDialog
  delete window.prksConfirmDestructive
  delete window.prksPrepareArgumentEdit
  delete window.prksCommitArgumentEditorDraft
  delete window.prksDeleteArgumentDurably
  delete window.prksArgumentSaveMessage
  delete window.prksTabContextOwnsEntityRoute
  delete window.prksOpenResearchPicker
  delete window.prksGraphFocusHash
  delete window.createArgument
  delete window.fetchArguments
  delete window.fetchPositions
  delete window.fetchWorks
})

function argument(partial: Partial<ArgumentDetail> = {}): ArgumentDetail {
  return {
    id: 'A1',
    name: 'Base',
    kind: 'argument',
    main_text: 'Body',
    targets: [],
    sources: [],
    responses: [],
    mentions: [],
    verdicts: [{ id: 'supports', label: 'Supports' }],
    ...partial,
  }
}

function detailOwner(overrides: Partial<ArgumentIntentOwner> = {}): ArgumentIntentOwner {
  return {
    tabId: 'tab-main',
    isCurrent: () => true,
    lastResolvedRoute: { name: 'argument-detail' },
    getEntity: () => ({ id: 'A1' }),
    ui: { argumentEditing: false },
    ...overrides,
  }
}

const draft: ArgumentEditorDraft = {
  name: 'Base',
  kind: 'argument',
  main_text: 'Body',
  targets: [],
  sources: [],
}

describe('Argument intents', () => {
  it('creates an Argument or Stance and a response with the parent target', async () => {
    window.prksPromptTextDialog = vi.fn(async () => 'Reply')
    window.createArgument = vi.fn(async () => ({ id: 'A9' }))
    window.prksNavigate = vi.fn()
    window.prksTabContextOwnsEntityRoute = () => true
    const owner = detailOwner()
    await browserArgumentIntents(owner, 4).createResponse(argument())
    expect(window.createArgument).toHaveBeenCalledWith({
      name: 'Reply',
      kind: 'argument',
      targets: [{ type: 'argument', id: 'A1', verdict_id: 'opposes' }],
    })
    expect(window.prksNavigate).toHaveBeenCalledWith('#/arguments/A9', { tabId: 'tab-main' })

    window.prksPromptTextDialog = vi.fn(async () => 'Local stance')
    const index = browserArgumentIntents(
      { tabId: 'tab-main', isCurrent: () => true, lastResolvedRoute: { name: 'arguments' } },
      1,
    )
    await index.create('stance')
    expect(window.createArgument).toHaveBeenLastCalledWith({ name: 'Local stance', kind: 'stance' })
  })

  it('shows the create API message instead of mapping it again', async () => {
    const useful = 'That part of this Argument is syncing or needs a decision. Try again shortly.'
    window.prksPromptTextDialog = vi.fn(async () => 'Reply')
    window.createArgument = vi.fn(async () => {
      throw new Error(useful)
    })
    window.prksArgumentSaveMessage = () => 'Could not create this Argument locally. Please retry.'
    const alertFn = vi.fn(async () => {})
    window.prksAlertDialog = alertFn
    window.prksTabContextOwnsEntityRoute = () => true
    await browserArgumentIntents(detailOwner(), 4).createResponse(argument())
    expect(alertFn).toHaveBeenCalledWith({ title: 'Could not create response', message: useful })

    alertFn.mockClear()
    window.prksPromptTextDialog = vi.fn(async () => 'Named')
    await browserArgumentIntents(
      { tabId: 'tab-main', isCurrent: () => true, lastResolvedRoute: { name: 'arguments' } },
      1,
    ).create('stance')
    expect(alertFn).toHaveBeenCalledWith({ title: 'Could not create Stance', message: useful })
  })

  it('does not create a response after the pane leaves while the prompt is open', async () => {
    let release = (_name: string | null) => {}
    window.prksPromptTextDialog = () =>
      new Promise((resolve) => {
        release = resolve
      })
    window.createArgument = vi.fn(async () => ({ id: 'A9' }))
    window.prksNavigate = vi.fn()
    let current = true
    window.prksTabContextOwnsEntityRoute = () => current
    const pending = browserArgumentIntents(detailOwner({ isCurrent: () => current }), 4).createResponse(
      argument(),
    )
    current = false
    release('Reply')
    await pending
    expect(window.createArgument).not.toHaveBeenCalled()
    expect(window.prksNavigate).not.toHaveBeenCalled()
  })

  it('saves through the commit API and ignores a stale completion', async () => {
    const commit = vi.fn(async () => ({ id: 'A1' }))
    window.prksCommitArgumentEditorDraft = commit
    window.prksNavigate = vi.fn()
    window.prksTabContextOwnsEntityRoute = () => true
    const owner = detailOwner()
    const saved = await browserArgumentIntents(owner, 2).save('A1', {
      ...draft,
      name: 'Renamed',
    })
    expect(saved).toBe(true)
    expect(commit).toHaveBeenCalledWith('A1', expect.objectContaining({ name: 'Renamed' }))
    expect(owner.ui?.argumentEditing).toBe(false)
    expect(window.prksNavigate).toHaveBeenCalledWith('#/arguments/A1', {
      replace: true,
      tabId: 'tab-main',
    })

    let current = true
    window.prksTabContextOwnsEntityRoute = () => current
    window.prksCommitArgumentEditorDraft = vi.fn(async () => {
      current = false
      return { id: 'A1' }
    })
    const navigate = vi.fn()
    window.prksNavigate = navigate
    const stale = await browserArgumentIntents(detailOwner({ isCurrent: () => current }), 3).save(
      'A1',
      draft,
    )
    expect(stale).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('reports save and delete failure only while the generation still owns the route', async () => {
    window.prksCommitArgumentEditorDraft = vi.fn(async () => {
      throw new Error('revision')
    })
    window.prksArgumentSaveMessage = (_err, fallback) => `mapped ${fallback}`
    const alertFn = vi.fn(async () => {})
    window.prksAlertDialog = alertFn
    window.prksTabContextOwnsEntityRoute = () => true
    const failed = await browserArgumentIntents(detailOwner(), 1).save('A1', draft)
    expect(failed).toBe(false)
    expect(alertFn).toHaveBeenCalledWith({ title: 'Could not save', message: 'mapped save this Argument' })

    alertFn.mockClear()
    window.prksCommitArgumentEditorDraft = vi.fn(async () => {
      throw new Error('late')
    })
    await browserArgumentIntents(detailOwner({ isCurrent: () => false }), 9).save('A1', draft)
    expect(alertFn).not.toHaveBeenCalled()

    window.prksTabContextOwnsEntityRoute = () => true
    window.prksConfirmDestructive = vi.fn(async () => false)
    window.prksDeleteArgumentDurably = vi.fn()
    await browserArgumentIntents(detailOwner(), 1).remove(argument())
    expect(window.prksConfirmDestructive).toHaveBeenCalledWith(
      expect.objectContaining({ confirmLabel: 'Delete Argument', title: 'Delete Argument?' }),
    )
    expect(window.prksDeleteArgumentDurably).not.toHaveBeenCalled()

    await browserArgumentIntents(detailOwner(), 1).remove(argument({ kind: 'stance' }))
    expect(window.prksConfirmDestructive).toHaveBeenLastCalledWith(
      expect.objectContaining({ confirmLabel: 'Delete Stance', title: 'Delete Stance?' }),
    )

    window.prksConfirmDestructive = vi.fn(async () => true)
    window.prksDeleteArgumentDurably = vi.fn(async () => {
      throw new Error('in use')
    })
    await browserArgumentIntents(detailOwner(), 1).remove(argument())
    expect(alertFn).toHaveBeenCalledWith({ title: 'Cannot delete', message: 'in use' })

    window.prksNavigate = vi.fn()
    window.prksDeleteArgumentDurably = vi.fn(async () => ({}))
    await browserArgumentIntents(detailOwner(), 1).remove(argument())
    expect(window.prksNavigate).toHaveBeenCalledWith('#/arguments', {
      replace: true,
      tabId: 'tab-main',
    })
  })

  it('excludes the Argument being edited from the target picker and uses the Work picker for sources', async () => {
    const items = argumentTargetPickerItems(
      [
        { id: 'A1', name: 'Self', kind: 'argument' },
        { id: 'A2', name: 'Other', kind: 'stance' },
      ],
      [{ id: 'P1', name: 'Position' }],
      'A1',
    )
    expect(items.map((item) => item.id)).toEqual(['P1', 'A2'])
    expect(argumentSourcePickerItems([{ id: 'W1', title: 'Work' }])[0]?.pickType).toBe('work')

    window.fetchArguments = vi.fn(async () => [{ id: 'A2', name: 'Other', kind: 'argument' }])
    window.fetchPositions = vi.fn(async () => [])
    window.fetchWorks = vi.fn(async () => [{ id: 'W9', title: 'Cited' }])
    window.prksOpenResearchPicker = vi.fn()
    const editing = detailOwner({ ui: { argumentEditing: true } })
    await browserArgumentIntents(editing, 1).pickTarget('A1', () => {})
    await browserArgumentIntents(editing, 1).pickSource('A1', () => {})
    expect(window.prksOpenResearchPicker).toHaveBeenCalledTimes(2)
    expect(window.fetchWorks).toHaveBeenCalledTimes(1)
  })

  it('starts the Positions catalogue without waiting for Arguments', async () => {
    const open = vi.fn()
    window.prksOpenResearchPicker = open
    let releaseArgs = () => {}
    let positionsStarted = false
    window.fetchArguments = () =>
      new Promise((resolve) => {
        releaseArgs = () => resolve([{ id: 'A2', name: 'Other', kind: 'argument' }])
      })
    window.fetchPositions = () => {
      positionsStarted = true
      return Promise.resolve([{ id: 'P1', name: 'Position' }])
    }
    const pending = browserArgumentIntents(detailOwner({ ui: { argumentEditing: true } }), 1).pickTarget(
      'A1',
      () => {},
    )
    expect(positionsStarted).toBe(true)
    releaseArgs()
    await pending
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('does not open a picker when edit ends before the catalogue resolves', async () => {
    const open = vi.fn()
    window.prksOpenResearchPicker = open
    let releaseArgs = () => {}
    let releaseWorks = () => {}
    window.fetchArguments = () =>
      new Promise((resolve) => {
        releaseArgs = () => resolve([])
      })
    window.fetchPositions = async () => []
    window.fetchWorks = () =>
      new Promise((resolve) => {
        releaseWorks = () => resolve([])
      })

    const cancelled = detailOwner({ ui: { argumentEditing: true } })
    const targetPending = browserArgumentIntents(cancelled, 1).pickTarget('A1', () => {})
    cancelled.ui!.argumentEditing = false
    releaseArgs()
    await targetPending

    const sourceOwner = detailOwner({ ui: { argumentEditing: true } })
    const sourcePending = browserArgumentIntents(sourceOwner, 1).pickSource('A1', () => {})
    sourceOwner.ui!.argumentEditing = false
    releaseWorks()
    await sourcePending

    let current = true
    const left = detailOwner({
      ui: { argumentEditing: true },
      isCurrent: () => current,
    })
    let releaseLeft = () => {}
    window.fetchArguments = () =>
      new Promise((resolve) => {
        releaseLeft = () => resolve([])
      })
    const leftPending = browserArgumentIntents(left, 4).pickTarget('A1', () => {})
    current = false
    releaseLeft()
    await leftPending

    expect(open).not.toHaveBeenCalled()
  })

  it('does not apply a picker choice after the editor closes', async () => {
    const open = vi.fn()
    window.prksOpenResearchPicker = open
    window.fetchArguments = async () => []
    window.fetchPositions = async () => [{ id: 'P1', name: 'Position' }]
    window.fetchWorks = async () => [{ id: 'W1', title: 'Work' }]
    const owner = detailOwner({ ui: { argumentEditing: true } })
    const onTarget = vi.fn()
    await browserArgumentIntents(owner, 1).pickTarget('A1', onTarget)
    owner.ui!.argumentEditing = false
    const targetOpen = open.mock.calls[0]?.[0] as { onPick: (id: string, pickType: string) => void }
    targetOpen.onPick('P1', 'position')
    expect(onTarget).not.toHaveBeenCalled()

    owner.ui!.argumentEditing = true
    const onSource = vi.fn()
    await browserArgumentIntents(owner, 1).pickSource('A1', onSource)
    owner.ui!.argumentEditing = false
    const sourceOpen = open.mock.calls[1]?.[0] as { onPick: (id: string) => void }
    sourceOpen.onPick('W1')
    expect(onSource).not.toHaveBeenCalled()
  })

  it('does not read the server when entering edit of a caller-owned draft flag', async () => {
    const prepare = vi.fn(async () => {})
    window.prksPrepareArgumentEdit = prepare
    window.prksTabContextOwnsEntityRoute = () => true
    const owner = detailOwner()
    const entered = await browserArgumentIntents(owner, 1).enterEdit('A1')
    expect(entered).toBe(true)
    expect(owner.ui?.argumentEditing).toBe(true)
    expect(prepare).toHaveBeenCalledWith('A1')
    browserArgumentIntents(owner, 1).cancelEdit()
    expect(owner.ui?.argumentEditing).toBe(false)
  })
})
