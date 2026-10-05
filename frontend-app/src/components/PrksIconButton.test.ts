import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksIconButton from './PrksIconButton.vue'

describe('PrksIconButton', () => {
  it('is a named icon button', () => {
    const wrapper = mount(PrksIconButton, {
      attachTo: document.body,
      props: { label: 'Edit aliases for History', title: 'Aliases', size: 'sm' },
      attrs: { 'data-tag-alias-edit': 'tag-1' },
      slots: { default: '<span class="work-html-slot"></span>' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('type')).toBe('button')
    expect(button.classes()).toContain('prks-icon-btn')
    expect(button.classes()).toContain('prks-icon-btn--sm')
    expect(button.classes()).not.toContain('prks-icon-btn--ghost')
    expect(button.attributes('aria-label')).toBe('Edit aliases for History')
    expect(button.attributes('title')).toBe('Aliases')
    expect(button.attributes('data-tag-alias-edit')).toBe('tag-1')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('tabindex')).toBeUndefined()
    button.element.focus()
    expect(document.activeElement).toBe(button.element)
    wrapper.unmount()
  })

  it('does not emit click when disabled', async () => {
    const wrapper = mount(PrksIconButton, {
      props: { label: 'Close', disabled: true },
      slots: { default: '×' },
    })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    expect(wrapper.get('button').attributes('disabled')).toBe('')
  })

  it('activates from click and keeps the native button for Enter and Space', async () => {
    const wrapper = mount(PrksIconButton, {
      props: { label: 'Pane actions', title: 'Pane actions', variant: 'ghost' },
      attrs: { class: 'prks-tile-header__menu', 'aria-haspopup': 'menu', 'aria-expanded': 'false' },
      slots: { default: '<span></span>' },
    })
    const button = wrapper.get('button')
    expect(button.classes()).toContain('prks-icon-btn--ghost')
    expect(button.classes()).toContain('prks-tile-header__menu')
    expect(button.attributes('aria-haspopup')).toBe('menu')
    await button.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('click')).toBeUndefined()
    await button.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    const event = wrapper.emitted('click')?.[0]?.[0]
    expect(event).toBeInstanceOf(MouseEvent)
  })

  it('replaces the icon and the accessible name while removing', async () => {
    const wrapper = mount(PrksIconButton, {
      props: { label: 'Remove alias', variant: 'danger', size: 'sm', busy: true, busyLabel: 'Removing…' },
      attrs: { class: 'tags-page-alias-remove--busy', 'data-alias-remove': 'alias' },
      slots: { default: '<span class="work-html-slot"></span>' },
    })
    const button = wrapper.get('button')
    expect(button.classes()).toContain('prks-icon-btn--danger')
    expect(button.classes()).toContain('tags-page-alias-remove--busy')
    expect(button.attributes('disabled')).toBe('')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.attributes('aria-label')).toBe('Removing…')
    expect(button.text()).toBe('Removing…')
    expect(button.find('.work-html-slot').exists()).toBe(false)
    await button.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('keeps the icon when busy without a busy label', () => {
    const wrapper = mount(PrksIconButton, {
      props: { label: 'Close', busy: true },
      slots: { default: '×' },
    })
    const button = wrapper.get('button')
    expect(button.attributes('aria-label')).toBe('Close')
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.text()).toBe('×')
  })
})
