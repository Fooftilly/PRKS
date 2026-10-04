import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksRelSummary from './PrksRelSummary.vue'

describe('PrksRelSummary', () => {
  it('omits empty parts and joins with the shared separator', () => {
    const wrapper = mount(PrksRelSummary, {
      props: { parts: [null, '1 parent', '2 note mentions'] },
    })
    const line = wrapper.get('.prks-rel-summary')
    expect(line.text()).toContain('1 parent')
    expect(line.text()).toContain('2 note mentions')
    expect(line.findAll('.prks-summary-sep')).toHaveLength(1)
  })

  it('does not render links even when a part object carries href', () => {
    const wrapper = mount(PrksRelSummary, {
      props: {
        parts: [{ text: '1 parent', href: 'javascript:alert(1)' } as never],
      },
    })
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('1 parent')
  })
})
