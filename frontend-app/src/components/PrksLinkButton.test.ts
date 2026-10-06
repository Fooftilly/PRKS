import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksLinkButton from './PrksLinkButton.vue'

describe('PrksLinkButton', () => {
  it('is a native anchor with href and the secondary contract', () => {
    const wrapper = mount(PrksLinkButton, {
      props: { href: '#/concepts' },
      slots: { default: 'Back to Concepts' },
    })
    const link = wrapper.get('a')
    expect(link.element.tagName).toBe('A')
    expect(link.attributes('href')).toBe('#/concepts')
    expect(link.classes()).toContain('prks-btn')
    expect(link.classes()).toContain('prks-btn--secondary')
    expect(link.classes()).not.toContain('prks-btn--primary')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(link.text()).toBe('Back to Concepts')
  })

  it('keeps a small Open link on the secondary family', () => {
    const wrapper = mount(PrksLinkButton, {
      props: { href: '#/views/v1', size: 'sm' },
      slots: { default: 'Open' },
    })
    const link = wrapper.get('a')
    expect(link.attributes('href')).toBe('#/views/v1')
    expect(link.classes()).toContain('prks-btn--sm')
    expect(link.classes()).toContain('prks-btn--secondary')
  })

  it('forwards id and an extra class onto the anchor', () => {
    const wrapper = mount(PrksLinkButton, {
      props: { href: '#/search?q=x' },
      attrs: { id: 'open-as-search', class: 'saved-view-open' },
      slots: { default: 'Open as Search' },
    })
    const link = wrapper.get('a')
    expect(link.attributes('id')).toBe('open-as-search')
    expect(link.classes()).toContain('saved-view-open')
    expect(link.attributes('href')).toBe('#/search?q=x')
  })
})
