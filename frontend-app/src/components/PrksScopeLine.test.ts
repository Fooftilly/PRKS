import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksScopeLine from './PrksScopeLine.vue'
import { scopeLineParts } from './scopeLine'

describe('scopeLineParts', () => {
  it('matches the classic filter wording', () => {
    expect(scopeLineParts({ shown: 2, total: 8, filter: 'ag', label: 'Concepts' })).toEqual([
      '2 of 8 matching',
    ])
    expect(scopeLineParts({ total: 8, label: 'Concepts' })).toEqual(['8 Concepts'])
    expect(scopeLineParts({ shown: 2, filter: 'ag' })).toEqual(['2 matching'])
  })

  it('does not invent zero when total is unknown', () => {
    expect(scopeLineParts({ label: 'People' })).toEqual([])
  })
})

describe('PrksScopeLine', () => {
  it('is a status line with the shared class', () => {
    const wrapper = mount(PrksScopeLine, { props: { total: 1, label: 'result' } })
    const line = wrapper.get('p')
    expect(line.classes()).toContain('prks-scope-line')
    expect(line.attributes('role')).toBe('status')
    expect(line.text()).toBe('1 result')
    expect(line.element.textContent).toBe('1 result')
  })
})
