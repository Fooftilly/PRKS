import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksInlineMessage from './PrksInlineMessage.vue'

describe('PrksInlineMessage', () => {
  it('is a neutral in-flow note without a status role', () => {
    const wrapper = mount(PrksInlineMessage, {
      attrs: { 'data-prks-role': 'offline-unavailable' },
      slots: { default: 'This list has not been cached on this device.' },
    })
    const message = wrapper.get('p')
    expect(message.classes()).toEqual(['prks-inline-message'])
    expect(message.attributes('role')).toBeUndefined()
    expect(message.attributes('data-prks-role')).toBe('offline-unavailable')
    expect(message.text()).toBe('This list has not been cached on this device.')
  })

  it('marks an error and announces it only when the caller already did', () => {
    const wrapper = mount(PrksInlineMessage, {
      props: { tone: 'error', status: true },
      attrs: { id: 'publishers-page-create-error', 'data-publishers-create-error': '' },
      slots: { default: 'That publisher name is already in use.' },
    })
    const message = wrapper.get('p')
    expect(message.classes()).toContain('prks-inline-message')
    expect(message.classes()).toContain('prks-inline-message--error')
    expect(message.attributes('role')).toBe('status')
    expect(message.attributes('id')).toBe('publishers-page-create-error')
    expect(message.attributes('aria-live')).toBeUndefined()
  })

  it('keeps an error visually distinct when it is not a live status', () => {
    const wrapper = mount(PrksInlineMessage, {
      props: { tone: 'error' },
      slots: { default: 'Folder not found.' },
    })
    const message = wrapper.get('p')
    expect(message.classes()).toContain('prks-inline-message--error')
    expect(message.attributes('role')).toBeUndefined()
  })
})
