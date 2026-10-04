import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksState from './PrksState.vue'

describe('PrksState', () => {
  it('announces a loading message', () => {
    const wrapper = mount(PrksState, {
      props: { kind: 'loading', message: 'Loading publishers…' },
      attrs: { 'data-publishers-loading': '' },
    })
    const region = wrapper.get('div')
    expect(region.classes()).toContain('prks-state')
    expect(region.classes()).toContain('prks-state--loading')
    expect(region.attributes('role')).toBe('status')
    expect(region.attributes('data-publishers-loading')).toBe('')
    expect(region.get('p').classes()).toContain('prks-state__body')
    expect(region.text()).toBe('Loading publishers…')
    expect(region.find('.prks-state__heading').exists()).toBe(false)
  })

  it('shows an error and a retry action', async () => {
    const wrapper = mount(PrksState, {
      props: { kind: 'error', message: 'Could not load Saved Views.' },
      attrs: { 'data-saved-views-load-error': '' },
      slots: {
        default: '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm">Try again</button>',
      },
    })
    const region = wrapper.get('[role="status"]')
    expect(region.attributes('role')).toBe('status')
    expect(region.classes()).toContain('prks-state--error')
    expect(region.get('p').text()).toBe('Could not load Saved Views.')
    const retry = region.get('button')
    expect(retry.text()).toBe('Try again')
    await retry.trigger('click')
    expect(retry.attributes('disabled')).toBeUndefined()
  })

  it('uses a heading for an empty collection', () => {
    const wrapper = mount(PrksState, {
      props: { kind: 'empty', heading: 'No files with this progress status yet.' },
    })
    const region = wrapper.get('div')
    expect(region.attributes('role')).toBeUndefined()
    expect(region.classes()).toContain('prks-state--empty')
    expect(region.get('p').classes()).toContain('prks-state__heading')
    expect(region.find('.prks-state__body').exists()).toBe(false)
    expect(region.text()).toBe('No files with this progress status yet.')
  })

  it('keeps a long error inside the status region', () => {
    const message = 'Could not load publishers. '.repeat(12).trim()
    const wrapper = mount(PrksState, {
      props: { kind: 'error', message },
    })
    expect(wrapper.get('.prks-state__body').text()).toBe(message)
    expect(wrapper.get('[role="status"]').classes()).toContain('prks-state--error')
  })
})
