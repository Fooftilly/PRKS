import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksResearchSectionHead from './PrksResearchSectionHead.vue'

describe('PrksResearchSectionHead', () => {
  it('renders the heading id used by the section', () => {
    const wrapper = mount(PrksResearchSectionHead, {
      props: { title: 'Definition', headingId: 'prks-concept-def-h' },
    })
    expect(wrapper.get('h3').attributes('id')).toBe('prks-concept-def-h')
    expect(wrapper.get('h3').text()).toBe('Definition')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('shows count and an Edit action on a PrksButton', async () => {
    const wrapper = mount(PrksResearchSectionHead, {
      props: {
        title: 'Parent concepts',
        headingId: 'prks-concept-parents-h',
        count: 2,
        actionId: 'prks-concept-edit-parents',
        actionLabel: 'Edit',
        actionRole: 'concept-mutation-control',
        sub: '2 parents',
      },
    })
    expect(wrapper.get('.research-entity__section-count').text()).toBe('2')
    const button = wrapper.get('button')
    expect(button.attributes('id')).toBe('prks-concept-edit-parents')
    expect(button.attributes('data-prks-role')).toBe('concept-mutation-control')
    expect(button.text()).toBe('Edit')
    expect(wrapper.get('.research-entity__section-sub').text()).toBe('2 parents')
    await button.trigger('click')
    expect(wrapper.emitted('action')).toHaveLength(1)
  })
})
