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

  it('does not render links for unsafe hrefs', () => {
    const wrapper = mount(PrksRelSummary, {
      props: {
        parts: [{ text: '1 parent', href: 'javascript:alert(1)' } as never],
      },
    })
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('1 parent')
  })

  it('links a same-app route and keeps the text escaped', () => {
    const wrapper = mount(PrksRelSummary, {
      props: {
        parts: [{ text: 'Folder <b>', href: '#/folders/f1' }, 'Author: A & B'],
      },
    })
    const link = wrapper.get('a.prks-summary-link')
    expect(link.attributes('href')).toBe('#/folders/f1')
    expect(link.text()).toBe('Folder <b>')
    expect(link.find('b').exists()).toBe(false)
    expect(wrapper.text()).toContain('Author: A & B')
  })

  it('does not link protocol-relative or external hrefs', () => {
    const wrapper = mount(PrksRelSummary, {
      props: {
        parts: [
          { text: 'a', href: '//evil.example' },
          { text: 'b', href: 'https://evil.example' },
          { text: 'c', href: '#/x?data:1' },
        ],
      },
    })
    expect(wrapper.find('a').exists()).toBe(false)
  })
})
