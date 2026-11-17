import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { COMBOBOX_COLLAPSE_MS, useComboboxPanel } from './combobox-panel'

function harness() {
  let api: ReturnType<typeof useComboboxPanel> | null = null
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useComboboxPanel()
        return () => h('div')
      },
    }),
  )
  return { wrapper, panel: api! as ReturnType<typeof useComboboxPanel> }
}

describe('combobox panel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('closes at once and hides only after the collapse delay', () => {
    const { wrapper, panel } = harness()
    panel.show()
    expect([panel.hidden.value, panel.open.value]).toEqual([false, true])
    panel.hide()
    expect([panel.hidden.value, panel.open.value]).toEqual([false, false])
    vi.advanceTimersByTime(COMBOBOX_COLLAPSE_MS - 1)
    expect(panel.hidden.value).toBe(false)
    vi.advanceTimersByTime(1)
    expect(panel.hidden.value).toBe(true)
    wrapper.unmount()
  })

  it('show() cancels a pending collapse and hideNow() hides without waiting', () => {
    const { wrapper, panel } = harness()
    panel.show()
    panel.hide()
    panel.show()
    vi.advanceTimersByTime(COMBOBOX_COLLAPSE_MS * 2)
    expect([panel.hidden.value, panel.open.value]).toEqual([false, true])
    panel.hide()
    panel.hideNow()
    expect([panel.hidden.value, panel.open.value]).toEqual([true, false])
    // The cancelled timer must not fire later and flip anything.
    panel.show()
    vi.advanceTimersByTime(COMBOBOX_COLLAPSE_MS * 2)
    expect(panel.hidden.value).toBe(false)
    wrapper.unmount()
  })

  it('drops a pending collapse when the owner unmounts', () => {
    const { wrapper, panel } = harness()
    panel.show()
    panel.hide()
    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
