import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksDisclosureButton from './PrksDisclosureButton.vue'

describe('PrksDisclosureButton', () => {
  it('owns expanded and controls on a native button', () => {
    const wrapper = mount(PrksDisclosureButton, {
      props: { expanded: false, controls: 'graph-filters' },
      attrs: { 'data-prks-role': 'graph-filters-toggle' },
      slots: { default: 'Filters' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('type')).toBe('button')
    expect(button.attributes('aria-expanded')).toBe('false')
    expect(button.attributes('aria-controls')).toBe('graph-filters')
    expect(button.classes()).toContain('prks-btn--secondary')
    expect(button.attributes('data-prks-role')).toBe('graph-filters-toggle')
    expect(button.text()).toBe('Filters')
  })

  it('reflects an open panel', () => {
    const wrapper = mount(PrksDisclosureButton, {
      props: { expanded: true, controls: 'graph-legend' },
      slots: { default: 'Legend' },
    })
    expect(wrapper.get('button').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('button').attributes('aria-controls')).toBe('graph-legend')
  })

  it('emits click from the native button', async () => {
    const wrapper = mount(PrksDisclosureButton, {
      props: { expanded: false, controls: 'panel' },
      slots: { default: 'Filters' },
    })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })
})
