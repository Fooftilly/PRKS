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
    expect(list.attributes('aria-orientation')).toBe('horizontal')
    const tabs = list.findAll('button')
    expect(tabs).toHaveLength(3)
    expect(tabs.map((tab) => tab.attributes('role'))).toEqual(['tab', 'tab', 'tab'])
    expect(tabs.map((tab) => tab.attributes('data-arg-filter'))).toEqual(['all', 'argument', 'stance'])
    expect(tabs.map((tab) => tab.attributes('aria-selected'))).toEqual(['false', 'true', 'false'])
    expect(tabs.map((tab) => tab.attributes('aria-controls'))).toEqual([
      'prks-argument-rows',
      'prks-argument-rows',
      'prks-argument-rows',
    ])
    expect(tabs.map((tab) => tab.attributes('tabindex'))).toEqual(['-1', '0', '-1'])
    expect(tabs.map((tab) => tab.classes().includes('is-active'))).toEqual([false, true, false])
    const panel = wrapper.get('#prks-argument-rows')
    expect(panel.attributes('role')).toBe('tabpanel')
    expect(panel.attributes('aria-labelledby')).toBe('prks-argument-kind-argument')

    await tabs[0]!.trigger('click')
    await tabs[2]!.trigger('click')
    expect(filterKind.mock.calls).toEqual([['all'], ['stance']])

    await wrapper.setProps({ projection: projection('stance') })
    const updated = list.findAll('button')
    expect(updated.map((tab) => tab.attributes('aria-selected'))).toEqual(['false', 'false', 'true'])
    expect(updated.map((tab) => tab.attributes('tabindex'))).toEqual(['-1', '-1', '0'])
    expect(updated.map((tab) => tab.classes().includes('is-active'))).toEqual([false, false, true])
    expect(panel.attributes('aria-labelledby')).toBe('prks-argument-kind-stance')
    wrapper.unmount()
  })

  it('moves the kind with arrow keys and keeps one tab stop', async () => {
    const filterKind = vi.fn()
    const wrapper = mount(ArgumentsIndexRoute, {
      attachTo: document.body,
      props: { projection: projection('argument') },
      global: {
        provide: {
          [argumentIntentsKey as symbol]: intents(filterKind),
        },
      },
    })
    const argument = wrapper.get('#prks-argument-kind-argument')
    const argumentEl = argument.element as HTMLElement
    argumentEl.focus()
    await argument.trigger('keydown', { key: 'ArrowRight' })
    expect(filterKind.mock.calls).toEqual([['stance']])
    expect(document.activeElement).toBe(wrapper.get('#prks-argument-kind-stance').element)

    await wrapper.setProps({ projection: projection('stance') })
    await wrapper.get('#prks-argument-kind-stance').trigger('keydown', { key: 'ArrowRight' })
    expect(filterKind.mock.calls).toEqual([['stance'], ['all']])
    expect(document.activeElement).toBe(wrapper.get('#prks-argument-kind-all').element)

    await wrapper.setProps({ projection: projection('all') })
    await wrapper.get('#prks-argument-kind-all').trigger('keydown', { key: 'ArrowLeft' })
    expect(filterKind.mock.calls.at(-1)).toEqual(['stance'])

    await wrapper.setProps({ projection: projection('stance') })
    filterKind.mockClear()
    await wrapper.get('#prks-argument-kind-stance').trigger('keydown', { key: 'Home' })
    expect(filterKind.mock.calls).toEqual([['all']])
    expect(document.activeElement).toBe(wrapper.get('#prks-argument-kind-all').element)

    await wrapper.setProps({ projection: projection('all') })
    filterKind.mockClear()
    await wrapper.get('#prks-argument-kind-all').trigger('keydown', { key: 'End' })
    expect(filterKind.mock.calls).toEqual([['stance']])

    await wrapper.setProps({ projection: projection('stance') })
    filterKind.mockClear()
    await wrapper.get('#prks-argument-kind-stance').trigger('keydown', { key: 'End' })
    await wrapper.get('#prks-argument-kind-stance').trigger('keydown', { key: 'ArrowUp' })
    expect(filterKind).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
