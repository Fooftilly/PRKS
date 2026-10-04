import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksDisclosureButton from './PrksDisclosureButton.vue'
import PrksField from './PrksField.vue'
import PrksInlineMessage from './PrksInlineMessage.vue'
import PrksLinkButton from './PrksLinkButton.vue'
import PrksWorkCard from './PrksWorkCard.vue'
import PrksState from './PrksState.vue'

/**
 * Automated a11y contracts for shared primitives (jsdom / Vitest).
 * Storybook addon-a11y is inspection-only. Playwright E2E stays in GitHub CI
 * for route/modal behavior. Do not add a second live region for the same error.
 */
describe('shared primitive a11y contracts', () => {
  it('associates a field label, control, and a single error live region', () => {
    const wrapper = mount(PrksField, {
      props: { label: 'Title', forId: 'meta-title', error: 'Title is required.' },
      slots: {
        default:
          '<input id="meta-title" type="text" aria-invalid="true" aria-describedby="meta-title-error">',
      },
    })
    const input = wrapper.get('#meta-title').element as HTMLInputElement
    const label = wrapper.get('label').element as HTMLLabelElement
    expect(label.htmlFor).toBe('meta-title')
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toBe('meta-title-error')
    const lives = wrapper.findAll('[aria-live]')
    expect(lives).toHaveLength(1)
    expect(lives[0].attributes('id')).toBe('meta-title-error')
    expect(lives[0].attributes('aria-live')).toBe('polite')
  })

  it('composes help then error on the native control', () => {
    const wrapper = mount(PrksField, {
      props: {
        label: 'YouTube URL',
        forId: 'meta-video-url',
        help: 'Replaces which video this file is.',
        error: 'Enter a YouTube URL.',
      },
      slots: { default: '<input id="meta-video-url" type="url">' },
    })
    const input = wrapper.get('#meta-video-url').element as HTMLInputElement
    expect(input.getAttribute('aria-labelledby')).toBe('meta-video-url-label')
    expect(input.getAttribute('aria-describedby')).toBe(
      'meta-video-url-help meta-video-url-error',
    )
  })

  it('does not emit an empty aria-controls', () => {
    const wrapper = mount(PrksDisclosureButton, {
      props: { expanded: false },
      slots: { default: 'Filters' },
    })
    expect(wrapper.get('button').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('button').attributes('aria-controls')).toBeUndefined()
  })

  it('keeps navigation on a real named link', () => {
    const wrapper = mount(PrksLinkButton, {
      props: { href: '#/concepts' },
      slots: { default: 'Back to Concepts' },
    })
    const link = wrapper.get('a').element as HTMLAnchorElement
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/concepts')
    expect(link.textContent).toBe('Back to Concepts')
  })

  it('does not give loading and empty the same live role', () => {
    const loading = mount(PrksState, { props: { kind: 'loading', message: 'Loading…' } })
    const empty = mount(PrksState, { props: { kind: 'empty', heading: 'None yet.' } })
    expect(loading.find('[role="status"]').exists()).toBe(true)
    expect(empty.find('[role="status"]').exists()).toBe(false)
    expect(empty.find('[aria-live]').exists()).toBe(false)
  })

  it('does not add a second live region on an error status message', () => {
    const wrapper = mount(PrksInlineMessage, {
      props: { tone: 'error', status: true },
      slots: { default: 'Could not save.' },
    })
    expect(wrapper.get('[role="status"]').attributes('aria-live')).toBeUndefined()
    expect(wrapper.findAll('[aria-live]')).toHaveLength(0)
  })

  it('keeps a Work card as a named navigation surface', () => {
    const wrapper = mount(PrksWorkCard, {
      props: { work: { id: 'W-1', title: 'Notes' } },
    })
    const card = wrapper.get('.project-card--work-card').element as HTMLElement
    const link = wrapper.get('a.work-card__link').element as HTMLAnchorElement
    expect(card.getAttribute('role')).toBeNull()
    expect(card.getAttribute('tabindex')).toBeNull()
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/works/W-1')
    expect(link.getAttribute('aria-label')).toBe('Notes')
    expect(card.getAttribute('data-prks-route')).toBe('#/works/W-1')
  })
})
