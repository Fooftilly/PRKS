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
    expect(wrapper.get('label').attributes('id')).toBe('meta-title-label')
    expect(wrapper.get('label').text()).toBe('Title')
    expect(wrapper.get('label').classes()).toContain('prks-field__label')
    const input = wrapper.get('input')
    expect(input.element.tagName).toBe('INPUT')
    expect(input.attributes('id')).toBe('meta-title')
    expect(input.attributes('aria-labelledby')).toBe('meta-title-label')
    expect(input.attributes('aria-describedby')).toBeUndefined()
    expect(wrapper.find('.prks-field__error').exists()).toBe(false)
  })

  it('reserves an error live region when error is an empty string', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Title', forId: 'meta-title', error: '' },
      slots: {
        default: '<input id="meta-title" type="text">',
      },
    })
    const error = wrapper.get('#meta-title-error')
    expect(error.classes()).toContain('prks-field__error')
    expect(error.attributes('aria-live')).toBe('polite')
    expect(error.text()).toBe('')
    expect(wrapper.classes()).not.toContain('prks-field--error')
    expect(wrapper.get('input').attributes('aria-describedby')).toBe('meta-title-error')
  })

  it('marks the field invalid and shows the error text', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Title', forId: 'meta-title', error: 'Title is required.' },
      slots: {
        default: '<input id="meta-title" type="text" aria-invalid="true">',
      },
    })
    expect(wrapper.classes()).toContain('prks-field--error')
    expect(wrapper.get('#meta-title-error').text()).toBe('Title is required.')
    expect(wrapper.get('input').attributes('aria-invalid')).toBe('true')
    expect(wrapper.get('input').attributes('aria-describedby')).toBe('meta-title-error')
  })

  it('points the control at help-only describedby', () => {
    const wrapper = mount(PrksField, {
      props: {
        label: 'Location (place of publication)',
        forId: 'meta-location',
        help: 'Separate multiple places with semicolons.',
      },
      slots: { default: '<input id="meta-location" type="text">' },
    })
    expect(wrapper.get('#meta-location-help').text()).toBe(
      'Separate multiple places with semicolons.',
    )
    expect(wrapper.get('input').attributes('aria-describedby')).toBe('meta-location-help')
    expect(wrapper.find('.prks-field__error').exists()).toBe(false)
  })

  it('composes help then error ids instead of replacing one with the other', () => {
    const wrapper = mount(PrksField, {
      props: {
        label: 'YouTube URL',
        forId: 'meta-video-url',
        help: 'Replaces which video this file is.',
        error: 'Enter a YouTube URL.',
      },
      slots: { default: '<input id="meta-video-url" type="url">' },
    })
    expect(wrapper.get('input').attributes('aria-describedby')).toBe(
      'meta-video-url-help meta-video-url-error',
    )
    expect(wrapper.get('#meta-video-url-help').text()).toBe('Replaces which video this file is.')
    expect(wrapper.get('#meta-video-url-error').text()).toBe('Enter a YouTube URL.')
  })
})
