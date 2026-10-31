import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ArgumentsIndexRoute from './ArgumentsIndexRoute.vue'
import { argumentIntentsKey, type ArgumentIntents } from './intents'
import type { ArgumentIndexProjection } from './projection'
import type { ArgumentKindFilter } from './types'

afterEach(() => {
  document.body.innerHTML = ''
})

function intents(filterKind: ArgumentIntents['filterKind']): ArgumentIntents {
  return {
    create: async () => {},
    filterKind,
    viewGraph: () => {},
    enterEdit: async () => false,
    cancelEdit: () => {},
    save: async () => false,
    createResponse: async () => {},
    remove: async () => {},
    pickTarget: async () => {},
    pickSource: async () => {},
  }
}

function projection(kind: ArgumentKindFilter): ArgumentIndexProjection {
  return {
    availability: 'ready',
    kind,
    items: [],
    generation: 1,
  }
}

describe('Arguments kind selector tabs', () => {
  it('exposes tab roles, the active kind, and the existing click filter', async () => {
    const filterKind = vi.fn()
    const wrapper = mount(ArgumentsIndexRoute, {
      props: { projection: projection('argument') },
      global: {
        provide: {
          [argumentIntentsKey as symbol]: intents(filterKind),
        },
      },
    })

    const list = wrapper.get('[role="tablist"]')
    expect(list.attributes('aria-label')).toBe('Argument kind')
    const tabs = list.findAll('button')
    expect(tabs).toHaveLength(3)
    expect(tabs.map((tab) => tab.attributes('role'))).toEqual(['tab', 'tab', 'tab'])
    expect(tabs.map((tab) => tab.attributes('data-arg-filter'))).toEqual(['all', 'argument', 'stance'])
    expect(tabs.map((tab) => tab.attributes('aria-selected'))).toEqual(['false', 'true', 'false'])
    expect(tabs.map((tab) => tab.classes().includes('is-active'))).toEqual([false, true, false])

    await tabs[0]!.trigger('click')
    await tabs[2]!.trigger('click')
    expect(filterKind.mock.calls).toEqual([['all'], ['stance']])

    await wrapper.setProps({ projection: projection('stance') })
    const updated = list.findAll('button')
    expect(updated.map((tab) => tab.attributes('aria-selected'))).toEqual(['false', 'false', 'true'])
    expect(updated.map((tab) => tab.classes().includes('is-active'))).toEqual([false, false, true])
    wrapper.unmount()
  })
})
