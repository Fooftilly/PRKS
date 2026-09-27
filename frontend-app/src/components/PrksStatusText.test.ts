import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksSectionHeader from './PrksSectionHeader.vue'
import PrksStatusText from './PrksStatusText.vue'

describe('PrksStatusText', () => {
  it('uses the settings hint and a polite live region', () => {
    const wrapper = mount(PrksStatusText, { slots: { default: 'Measurements reset.' } })
    const status = wrapper.get('p')
    expect(status.classes()).toContain('prks-settings-hint')
    expect(status.attributes('aria-live')).toBe('polite')
    expect(status.attributes('role')).toBeUndefined()
    expect(status.text()).toBe('Measurements reset.')
  })

  it('omits the live region when the text is not a status', () => {
    const wrapper = mount(PrksStatusText, {
      props: { live: false },
      slots: { default: 'No API requests measured yet.' },
    })
    expect(wrapper.get('p').attributes('aria-live')).toBeUndefined()
  })
})

describe('PrksSectionHeader', () => {
  it('is the settings section heading', () => {
    const wrapper = mount(PrksSectionHeader, {
      slots: { default: 'Client request coordinator' },
    })
    const heading = wrapper.get('h5')
    expect(heading.classes()).toContain('prks-settings-section__title')
    expect(heading.text()).toBe('Client request coordinator')
  })
})
