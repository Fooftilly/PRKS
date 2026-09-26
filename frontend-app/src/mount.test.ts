import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import Root from './App.vue'
import { mountPrksVue, PRKS_VUE_ROOT_ID } from './mount'

describe('PRKS Vue bootstrap', () => {
  it('renders a hidden ready marker and no visible text', () => {
    const wrapper = mount(Root)
    const marker = wrapper.get('[data-prks-vue-bootstrap="ready"]')
    expect(marker.attributes('hidden')).toBeDefined()
    expect(marker.text()).toBe('')
  })

  it('mounts once on the host element', () => {
    const host = document.createElement('div')
    host.id = PRKS_VUE_ROOT_ID
    document.body.appendChild(host)
    const first = mountPrksVue(host)
    const second = mountPrksVue(host)
    expect(first).not.toBeNull()
    expect(second).toBeNull()
    expect(host.dataset.prksVueMounted).toBe('true')
    expect(host.querySelector('[data-prks-vue-bootstrap="ready"]')).not.toBeNull()
    first?.unmount()
    host.remove()
  })
})
