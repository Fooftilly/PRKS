import { VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { performanceSnapshotFixture } from '../../api/performance-diagnostics.test'
import { createPrksQueryClient } from '../../query/client'
import { activatePerformanceDiagnostics, resetPerformanceDiagnosticsActivationForTests } from './activation'
import PerformanceDiagnostics from './PerformanceDiagnostics.vue'

afterEach(() => {
  resetPerformanceDiagnosticsActivationForTests()
  vi.unstubAllGlobals()
})

function installFetch() {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if ((init?.method ?? 'GET') === 'POST') {
        return new Response(JSON.stringify({ status: 'reset' }), { status: 200 })
      }
      return new Response(JSON.stringify(performanceSnapshotFixture()), { status: 200 })
    }),
  )
  return calls
}

function mountDiagnostics(queryClient = createPrksQueryClient()) {
  const wrapper = mount(PerformanceDiagnostics, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  return { wrapper, queryClient }
}

describe('PerformanceDiagnostics', () => {
  it('does not fetch until Diagnostics is activated, then reuses the cache', async () => {
    const calls = installFetch()
    const { wrapper, queryClient } = mountDiagnostics()
    await flushPromises()
    expect(calls).toEqual([])
    expect(wrapper.get('#prks-perf-summary').text()).toBe('Loading measurements…')

    activatePerformanceDiagnostics()
    await flushPromises()
    expect(calls).toEqual(['GET /api/diagnostics/performance'])
    expect(wrapper.get('#prks-perf-summary').text()).toContain('API requests: 2')
    expect(wrapper.get('#prks-perf-routes-body').text()).toContain('GET /api/works')

    const second = mount(PerformanceDiagnostics, {
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    })
    activatePerformanceDiagnostics()
    await flushPromises()
    expect(calls).toEqual(['GET /api/diagnostics/performance'])
    expect(second.get('#prks-perf-summary').text()).toContain('API requests: 2')
    second.unmount()
    wrapper.unmount()
  })

  it('invalidates on Refresh and again after Reset', async () => {
    const calls = installFetch()
    window.prksResetRequestCoordinatorDiagnostics = vi.fn()
    const { wrapper } = mountDiagnostics()
    activatePerformanceDiagnostics()
    await flushPromises()

    await wrapper.get('#prks-perf-refresh-btn').trigger('click')
    await flushPromises()
    expect(calls.filter((call) => call.startsWith('GET'))).toHaveLength(2)

    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(calls).toContain('POST /api/diagnostics/performance/reset')
    expect(calls.filter((call) => call.startsWith('GET'))).toHaveLength(3)
    expect(window.prksResetRequestCoordinatorDiagnostics).toHaveBeenCalled()
    expect(wrapper.get('#prks-perf-status').text()).toBe('Measurements reset.')
    wrapper.unmount()
  })

  it('shows a normalized load error after retryable failures settle', async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ error: 'unavailable', code: 'busy' }), { status: 503 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const queryClient = createPrksQueryClient()
    const defaults = queryClient.getDefaultOptions()
    queryClient.setDefaultOptions({
      ...defaults,
      queries: { ...defaults.queries, retryDelay: 0 },
    })
    const { wrapper } = mountDiagnostics(queryClient)
    activatePerformanceDiagnostics()
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3)
      expect(wrapper.get('#prks-perf-status').text()).toBe('unavailable')
    })
    wrapper.unmount()
  })
})
