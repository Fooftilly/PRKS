import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksButton from './PrksButton.vue'

describe('PrksButton', () => {
  it('is a native button with the secondary contract', () => {
    const wrapper = mount(PrksButton, { slots: { default: 'Refresh' } })
    const button = wrapper.get('button')
    expect(button.element.tagName).toBe('BUTTON')
    expect(button.attributes('type')).toBe('button')
    expect(button.classes()).toContain('prks-btn')
    expect(button.classes()).toContain('prks-btn--secondary')
    expect(button.classes()).not.toContain('prks-btn--primary')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('aria-busy')).toBeUndefined()
    expect(button.attributes('aria-disabled')).toBeUndefined()
    expect(button.text()).toBe('Refresh')
  })

  it('maps primary, ghost, danger, and quiet-danger without the secondary class', () => {
    const cases = [
      ['primary', 'prks-btn--primary'],
      ['ghost', 'prks-btn--ghost'],
      ['danger', 'prks-btn--danger'],
      ['quiet-danger', 'prks-btn--quiet-danger'],
    ] as const
    for (const [variant, className] of cases) {
      const wrapper = mount(PrksButton, {
        props: { variant },
        slots: { default: 'Action' },
      })
      const button = wrapper.get('button')
      expect(button.classes()).toContain(className)
      expect(button.classes()).not.toContain('prks-btn--secondary')
      expect(button.text()).toBe('Action')
    }
  })

  it('uses the danger and small classes when asked', () => {
    const wrapper = mount(PrksButton, {
      props: { variant: 'danger', size: 'sm' },
      slots: { default: 'Delete' },
    })
    const button = wrapper.get('button')
    expect(button.classes()).toContain('prks-btn--danger')
    expect(button.classes()).not.toContain('prks-btn--secondary')
    expect(button.classes()).not.toContain('prks-btn--quiet-danger')
    expect(button.classes()).toContain('prks-btn--sm')
  })

  it('keeps quiet-danger distinct from danger', () => {
    const wrapper = mount(PrksButton, {
      props: { variant: 'quiet-danger' },
      attrs: { class: 'prks-page-action--destructive', id: 'prks-concept-delete' },
      slots: { default: 'Delete' },
    })
    const button = wrapper.get('button')
    expect(button.classes()).toContain('prks-btn--quiet-danger')
    expect(button.classes()).toContain('prks-page-action--destructive')
    expect(button.classes()).not.toContain('prks-btn--danger')
    expect(button.classes()).not.toContain('prks-btn--ghost')
    expect(button.attributes('id')).toBe('prks-concept-delete')
    expect(button.text()).toBe('Delete')
  })

  it('forwards id, data, aria, and an extra class onto the button', () => {
    const wrapper = mount(PrksButton, {
      attrs: {
        id: 'search-run-btn',
        class: 'search-advanced__submit',
        'data-prks-role': 'graph-fit',
        'aria-pressed': 'true',
        title: 'Pin annotations',
      },
      slots: { default: 'Search' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('id')).toBe('search-run-btn')
    expect(button.classes()).toContain('search-advanced__submit')
    expect(button.classes()).toContain('prks-btn--secondary')
    expect(button.attributes('data-prks-role')).toBe('graph-fit')
    expect(button.attributes('aria-pressed')).toBe('true')
    expect(button.attributes('title')).toBe('Pin annotations')
    expect(button.attributes('type')).toBe('button')
  })

  it('keeps a submit type', () => {
    const wrapper = mount(PrksButton, {
      props: { type: 'submit', variant: 'primary' },
      slots: { default: 'Save' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('type')).toBe('submit')
    expect(button.classes()).toContain('prks-btn--primary')
    expect(button.text()).toBe('Save')
  })

  it('does not emit click when disabled', async () => {
    const wrapper = mount(PrksButton, {
      props: { disabled: true },
      slots: { default: 'Restore this backup' },
    })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    expect(wrapper.get('button').attributes('disabled')).toBe('')
    expect(wrapper.get('button').attributes('aria-busy')).toBeUndefined()
    expect(wrapper.get('button').text()).toBe('Restore this backup')
  })

  it('shows the busy label and blocks another click', async () => {
    const wrapper = mount(PrksButton, {
      props: { busy: true, busyLabel: 'Refreshing…' },
      slots: { default: 'Refresh' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('disabled')).toBe('')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.attributes('aria-disabled')).toBeUndefined()
    expect(button.text()).toBe('Refreshing…')
    await button.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('keeps the idle label when busy without a busy label', async () => {
    const wrapper = mount(PrksButton, {
      props: { busy: true },
      slots: { default: 'Refresh' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.attributes('disabled')).toBe('')
    expect(button.text()).toBe('Refresh')
    await button.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('emits click from an enabled button', async () => {
    const wrapper = mount(PrksButton, { slots: { default: 'Copy report' } })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('focuses the native button without a key handler', () => {
    const wrapper = mount(PrksButton, {
      attachTo: document.body,
      slots: { default: 'Close' },
    })
    const button = wrapper.get('button').element
    ;(wrapper.vm as { focus: () => void }).focus()
    expect(document.activeElement).toBe(button)
    expect(button.tagName).toBe('BUTTON')
    wrapper.unmount()
  })
})
