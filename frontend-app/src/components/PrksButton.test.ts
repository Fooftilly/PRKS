import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksButton from './PrksButton.vue'

describe('PrksButton', () => {
  it('is a native button with the secondary contract', () => {
    const wrapper = mount(PrksButton, { slots: { default: 'Refresh' } })
    const button = wrapper.get('button')
    expect(button.element.tagName).toBe('BUTTON')
    expect(button.attributes('type')).toBe('button')
    expect(button.classes()).toContain('prks-btn')
    expect(button.classes()).toContain('prks-btn--secondary')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('aria-busy')).toBeUndefined()
    expect(button.attributes('aria-disabled')).toBeUndefined()
    expect(button.text()).toBe('Refresh')
  })

  it('does not emit click when disabled', async () => {
    const wrapper = mount(PrksButton, {
      props: { disabled: true },
      slots: { default: 'Restore this backup' },
    })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    expect(wrapper.get('button').attributes('disabled')).toBe('')
    expect(wrapper.get('button').attributes('aria-busy')).toBeUndefined()
  })

  it('shows the busy label and blocks another click', async () => {
    const wrapper = mount(PrksButton, {
      props: { busy: true, busyLabel: 'Refreshing…' },
      slots: { default: 'Refresh' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('disabled')).toBe('')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.attributes('aria-disabled')).toBeUndefined()
    expect(button.text()).toBe('Refreshing…')
    await button.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('keeps the idle label when busy without a busy label', () => {
    const wrapper = mount(PrksButton, {
      props: { busy: true },
      slots: { default: 'Refresh' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.attributes('disabled')).toBe('')
    expect(button.text()).toBe('Refresh')
  })

  it('emits click from an enabled button', async () => {
    const wrapper = mount(PrksButton, { slots: { default: 'Copy report' } })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('uses the danger and small classes when asked', () => {
    const wrapper = mount(PrksButton, {
      props: { variant: 'danger', size: 'sm' },
      slots: { default: 'Delete' },
    })
    const button = wrapper.get('button')
    expect(button.classes()).toContain('prks-btn--danger')
    expect(button.classes()).not.toContain('prks-btn--secondary')
    expect(button.classes()).toContain('prks-btn--sm')
  })
})
