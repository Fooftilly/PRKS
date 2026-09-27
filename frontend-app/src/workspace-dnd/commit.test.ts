import { afterEach, describe, expect, it } from 'vitest'
import { browserCommitHandlers, commitDropIntent } from './commit'

describe('browserCommitHandlers missing coordinator APIs', () => {
  const keys = [
    'prksWorkspaceReorderTab',
    'prksWorkspaceHideLeaf',
    'prksWorkspaceTileTab',
    'prksWorkspaceMovePane',
    'prksWorkspaceSplitLeaf',
  ] as const
  const saved: Partial<Record<(typeof keys)[number], unknown>> = {}

  afterEach(() => {
    for (const key of keys) {
      if (key in saved) {
        ;(window as unknown as Record<string, unknown>)[key] = saved[key]
        delete saved[key]
      } else {
        delete (window as unknown as Record<string, unknown>)[key]
      }
    }
  })

  function clearApis(): void {
    for (const key of keys) {
      saved[key] = (window as unknown as Record<string, unknown>)[key]
      delete (window as unknown as Record<string, unknown>)[key]
    }
  }

  it('returns ok:false when reorder/move/tile/hide/split APIs are absent', async () => {
    clearApis()
    const handlers = browserCommitHandlers()
    expect(
      (
        await commitDropIntent(
          { kind: 'tab', tabId: 'B' },
          { kind: 'tab-reorder', beforeTabId: null, index: 1 },
          [],
          handlers,
        )
      ).ok,
    ).toBe(false)
    expect(
      (
        await commitDropIntent(
          { kind: 'pane', tabId: 'B' },
          {
            kind: 'secondary-edge',
            tabId: 'C',
            axis: 'left-right',
            placement: 'first',
            zone: 'left',
            valid: true,
            reason: null,
          },
          ['B', 'C'],
          handlers,
        )
      ).ok,
    ).toBe(false)
    expect(
      (
        await commitDropIntent(
          { kind: 'tab', tabId: 'D' },
          { kind: 'secondary-empty', valid: true },
          [],
          handlers,
        )
      ).ok,
    ).toBe(false)
    expect(
      (
        await commitDropIntent(
          { kind: 'pane', tabId: 'B' },
          { kind: 'park' },
          ['B'],
          handlers,
        )
      ).ok,
    ).toBe(false)
  })
})
