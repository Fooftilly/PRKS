import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksResearchRow from './PrksResearchRow.vue'

describe('PrksResearchRow', () => {
  it('is a native row link with title and meta', () => {
    const wrapper = mount(PrksResearchRow, {
      props: {
        href: '#/concepts/C1',
        title: 'Agency',
        meta: ['Top-level concept', '0 subconcepts'],
      },
    })
    const link = wrapper.get('a')
    expect(link.classes()).toContain('prks-list-row')
    expect(link.classes()).toContain('prks-research-row')
    expect(link.attributes('href')).toBe('#/concepts/C1')
    expect(wrapper.get('.prks-research-row__title').text()).toBe('Agency')
    expect(wrapper.find('.prks-research-row__kind').exists()).toBe(false)
    expect(wrapper.findAll('.prks-research-row__meta-item').map((el) => el.text())).toEqual([
      'Top-level concept',
      '0 subconcepts',
    ])
  })

  it('renders duplicate metadata values as separate items', () => {
    const wrapper = mount(PrksResearchRow, {
      props: {
        href: '#/concepts/C1',
        title: 'Agency',
        meta: ['supports', 'supports'],
      },
    })
    expect(wrapper.findAll('.prks-research-row__meta-item').map((el) => el.text())).toEqual([
      'supports',
      'supports',
    ])
  })

  it('shows a kind badge when present', () => {
    const wrapper = mount(PrksResearchRow, {
      props: {
        href: '#/arguments/A1',
        title: 'A1',
        kind: 'Stance',
        meta: ['0 responses'],
      },
    })
    expect(wrapper.get('.prks-research-row__kind').text()).toBe('Stance')
  })
})
