import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
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

  it('mounts Settings into the shell without fetching diagnostics', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const host = document.createElement('div')
    host.id = PRKS_VUE_ROOT_ID
    const settings = document.createElement('div')
    settings.id = 'settings-modal'
    settings.className = 'modal hidden'
    settings.innerHTML = '<div id="prks-settings-root"></div>'
    document.body.append(host, settings)
    const app = mountPrksVue(host)
    await nextTick()
    await nextTick()
    const perf = document.getElementById('prks-settings-perf-root')
    expect(perf?.querySelector('#prks-perf-refresh-btn')).not.toBeNull()
    expect(settings.contains(perf)).toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
    app?.unmount()
    host.remove()
    settings.remove()
    vi.unstubAllGlobals()
  })
})
