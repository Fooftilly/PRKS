import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksField from './PrksField.vue'

describe('PrksField', () => {
  it('associates a visible native control with its label', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Title', forId: 'meta-title' },
      slots: {
        default: '<input id="meta-title" type="text">',
      },
    })
    expect(wrapper.get('label').attributes('for')).toBe('meta-title')
    expect(wrapper.get('label').text()).toBe('Title')
    expect(wrapper.get('label').classes()).toContain('prks-field__label')
    const input = wrapper.get('input')
    expect(input.element.tagName).toBe('INPUT')
    expect(input.attributes('id')).toBe('meta-title')
    expect(wrapper.find('.prks-field__error').exists()).toBe(false)
  })

  it('reserves an error live region when error is an empty string', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Title', forId: 'meta-title', error: '' },
      slots: {
        default:
          '<input id="meta-title" type="text" aria-describedby="meta-title-error">',
      },
    })
    const error = wrapper.get('#meta-title-error')
    expect(error.classes()).toContain('prks-field__error')
    expect(error.attributes('aria-live')).toBe('polite')
    expect(error.text()).toBe('')
    expect(wrapper.classes()).not.toContain('prks-field--error')
  })

  it('marks the field invalid and shows the error text', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Title', forId: 'meta-title', error: 'Title is required.' },
      slots: {
        default:
          '<input id="meta-title" type="text" aria-invalid="true" aria-describedby="meta-title-error">',
      },
    })
    expect(wrapper.classes()).toContain('prks-field--error')
    expect(wrapper.get('#meta-title-error').text()).toBe('Title is required.')
    expect(wrapper.get('input').attributes('aria-invalid')).toBe('true')
  })

  it('keeps a required marker out of the accessible name', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Name', forId: 'prks-arg-name', required: true },
      slots: { default: '<input id="prks-arg-name" type="text">' },
    })
    const marker = wrapper.get('.prks-field__required')
    expect(marker.text()).toBe('Required')
    expect(marker.attributes('aria-hidden')).toBe('true')
  })
})
