import { describe, expect, it } from 'vitest'
import { argumentDomScope } from './dom-scope'

describe('argumentDomScope', () => {
  it('derives a stable prefix from the tab id', () => {
    expect(argumentDomScope({ tabId: 'tab-1' })).toBe('prks-arg-tab-1')
    expect(argumentDomScope({ tabId: 'tab-1' })).toBe('prks-arg-tab-1')
    expect(argumentDomScope({ tabId: 'side/pane' })).toBe('prks-arg-side-pane')
    expect(argumentDomScope({ tabId: 'tab-2' })).toBe('prks-arg-tab-2')
  })

  it('keeps one prefix for an owner that has no tab id', () => {
    const owner = {}
    const first = argumentDomScope(owner)
    expect(argumentDomScope(owner)).toBe(first)
    expect(argumentDomScope({})).not.toBe(first)
  })
})
