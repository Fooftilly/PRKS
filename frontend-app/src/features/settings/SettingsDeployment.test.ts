import { VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseDeploymentSettings } from '../../api/deployment-settings'
import { createPrksQueryClient } from '../../query/client'
import { activateSettingsCategory, openSettingsToLastCategory, resetSettingsNavigationForTests } from './navigation'
import SettingsDeployment from './SettingsDeployment.vue'

const BODY = {
  settings: [
    { key: 'PRKS_HTTP_THREADS', summary: 'Requests at once.', value: '8', source: 'environment', default: '16' },
    { key: 'PRKS_DATABASE_URL', summary: 'Connection.', value: '(set, hidden)', source: 'environment', default: 'none' },
    { key: 'PRKS_AUTH', summary: 'Sign-in mode.', value: 'session', source: 'default', default: 'session' },
  ],
  editable_in_browser: false,
}

const mounted: VueWrapper[] = []

afterEach(() => {
  for (const wrapper of mounted.splice(0)) wrapper.unmount()
  resetSettingsNavigationForTests()
  vi.unstubAllGlobals()
})

function answer(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function mountPanel(): VueWrapper {
  const wrapper = mount(SettingsDeployment, {
    global: { plugins: [[VueQueryPlugin, { queryClient: createPrksQueryClient() }]] },
  })
  mounted.push(wrapper)
  return wrapper
}

describe('SettingsDeployment', () => {
  it('reads nothing until the Deployment category opens', async () => {
    const fetchMock = answer(200, BODY)
    mountPanel()
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('lists each setting read-only, with its source', async () => {
    const fetchMock = answer(200, BODY)
    activateSettingsCategory('deployment')
    const wrapper = mountPanel()
    await flushPromises()
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/api/deployment-settings')
    const rows = wrapper.findAll('[data-prks-setting]')
    expect(rows.map((row) => row.attributes('data-prks-setting'))).toEqual([
      'PRKS_HTTP_THREADS', 'PRKS_DATABASE_URL', 'PRKS_AUTH',
    ])
    expect(rows[0]?.text()).toContain('8')
    expect(rows[0]?.text()).toContain('Set')
    expect(rows[2]?.text()).toContain('Default')
    expect(wrapper.get('#prks-deployment-summary').text()).toBe('2 of 3 settings are set; the rest have their defaults.')
    // Read-only: no input and no Save.
    expect(wrapper.findAll('input, select, textarea')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Save')
  })

  it('tells a member who is not the owner why nothing shows', async () => {
    answer(403, { error: 'forbidden', code: 'forbidden' })
    activateSettingsCategory('deployment')
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.text()).toContain('Only the Library owner can see how this PRKS is deployed.')
  })

  it('refuses a body that claims the browser may edit', () => {
    expect(() => parseDeploymentSettings({ ...BODY, editable_in_browser: true })).toThrow()
    expect(() => parseDeploymentSettings({ settings: [{ ...BODY.settings[0], source: 'browser' }], editable_in_browser: false })).toThrow()
  })

  it('reads again when the category reopens, so a restart with new values shows', async () => {
    const after = { ...BODY, settings: [{ ...BODY.settings[0], value: '4' }, ...BODY.settings.slice(1)] }
    let body: unknown = BODY
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    activateSettingsCategory('deployment')
    const wrapper = mountPanel()
    await flushPromises()
    const threads = () => wrapper.get('[data-prks-setting="PRKS_HTTP_THREADS"] .prks-deployment-list__value').text()
    expect(threads()).toBe('8')
    activateSettingsCategory('diagnostics')
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    body = after
    activateSettingsCategory('deployment')
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(threads()).toBe('4')
  })
  it('reads again when Settings reopens on Deployment without a category change', async () => {
    const after = { ...BODY, settings: [{ ...BODY.settings[0], value: '4' }, ...BODY.settings.slice(1)] }
    let body: unknown = BODY
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    activateSettingsCategory('deployment')
    const wrapper = mountPanel()
    await flushPromises()
    const threads = () => wrapper.get('[data-prks-setting="PRKS_HTTP_THREADS"] .prks-deployment-list__value').text()
    expect(threads()).toBe('8')
    // Settings closes (nothing unmounts, the category stays), the host restarts PRKS, Settings reopens.
    body = after
    openSettingsToLastCategory()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(threads()).toBe('4')
  })
})
