import { VueQueryPlugin, onlineManager } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { performanceSnapshotFixture } from '../../api/performance-diagnostics.test'
import { createPrksQueryClient } from '../../query/client'
import { activatePerformanceDiagnostics, resetPerformanceDiagnosticsActivationForTests } from './activation'
import PerformanceDiagnostics from './PerformanceDiagnostics.vue'

afterEach(() => {
  resetPerformanceDiagnosticsActivationForTests()
  onlineManager.setOnline(true)
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

  it('reports Reset reachability through the mutation cache', async () => {
    const success = vi.fn()
    const failure = vi.fn()
    vi.stubGlobal('prksOfflineNoteRequestSuccess', success)
    vi.stubGlobal('prksOfflineNoteRequestFailure', failure)
    window.prksResetRequestCoordinatorDiagnostics = vi.fn()
    let postMode: 'network' | 'abort' | 'http' | 'domain' | 'ok' = 'network'
    const posts: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        if (method === 'POST') {
          posts.push(String(url))
          if (postMode === 'network') throw new TypeError('Failed to fetch')
          if (postMode === 'abort') throw new DOMException('Aborted', 'AbortError')
          if (postMode === 'http') {
            return new Response(JSON.stringify({ error: 'no', code: 'invalid_request' }), { status: 400 })
          }
          if (postMode === 'domain') return new Response(JSON.stringify({ status: 'nope' }), { status: 200 })
          return new Response(JSON.stringify({ status: 'reset' }), { status: 200 })
        }
        return new Response(JSON.stringify(performanceSnapshotFixture()), { status: 200 })
      }),
    )
    const { wrapper } = mountDiagnostics()
    activatePerformanceDiagnostics()
    await flushPromises()
    success.mockClear()
    failure.mockClear()

    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(posts).toEqual(['/api/diagnostics/performance/reset'])
    expect(failure).toHaveBeenCalledTimes(1)
    expect(success).not.toHaveBeenCalled()
    expect(wrapper.get('#prks-perf-status').text()).toBe('Could not reset measurements.')
    expect(window.prksResetRequestCoordinatorDiagnostics).not.toHaveBeenCalled()

    postMode = 'abort'
    failure.mockClear()
    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(posts).toHaveLength(2)
    expect(failure).not.toHaveBeenCalled()
    expect(success).not.toHaveBeenCalled()

    postMode = 'http'
    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(failure).not.toHaveBeenCalled()
    expect(success).toHaveBeenCalledTimes(1)
    expect(wrapper.get('#prks-perf-status').text()).toBe('no')

    postMode = 'domain'
    success.mockClear()
    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(failure).not.toHaveBeenCalled()
    expect(success).toHaveBeenCalledTimes(1)
    expect(wrapper.get('#prks-perf-status').text()).toBe('Could not reset performance diagnostics.')

    postMode = 'ok'
    success.mockClear()
    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(posts).toHaveLength(5)
    expect(failure).not.toHaveBeenCalled()
    expect(success).toHaveBeenCalledTimes(2)
    expect(window.prksResetRequestCoordinatorDiagnostics).toHaveBeenCalledTimes(1)
    expect(wrapper.get('#prks-perf-status').text()).toBe('Measurements reset.')
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

  it('still probes PRKS when TanStack OnlineManager is offline', async () => {
    onlineManager.setOnline(false)
    const calls = installFetch()
    window.prksResetRequestCoordinatorDiagnostics = vi.fn()
    const { wrapper } = mountDiagnostics()
    activatePerformanceDiagnostics()
    await flushPromises()
    expect(calls).toEqual(['GET /api/diagnostics/performance'])
    expect(wrapper.get('#prks-perf-summary').text()).toContain('API requests: 2')

    await wrapper.get('#prks-perf-reset-btn').trigger('click')
    await flushPromises()
    expect(calls).toContain('POST /api/diagnostics/performance/reset')
    expect(wrapper.get('#prks-perf-status').text()).toBe('Measurements reset.')
    expect(window.prksResetRequestCoordinatorDiagnostics).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('a second Reset click does not start another reset', async () => {
    let releasePost: (response: Response) => void = () => {}
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        calls.push(`${method} ${url}`)
        if (method === 'POST') {
          return await new Promise<Response>((resolve) => {
            releasePost = resolve
          })
        }
        return new Response(JSON.stringify(performanceSnapshotFixture()), { status: 200 })
      }),
    )
    window.prksResetRequestCoordinatorDiagnostics = vi.fn()
    const { wrapper } = mountDiagnostics()
    activatePerformanceDiagnostics()
    await flushPromises()
    expect(calls).toEqual(['GET /api/diagnostics/performance'])

    const reset = wrapper.get('#prks-perf-reset-btn')
    const refresh = wrapper.get('#prks-perf-refresh-btn')
    const first = reset.trigger('click')
    const second = reset.trigger('click')
    await flushPromises()
    expect(calls.filter((call) => call.startsWith('POST'))).toEqual(['POST /api/diagnostics/performance/reset'])
    expect(reset.attributes('disabled')).toBe('')
    expect(reset.attributes('aria-busy')).toBe('true')
    expect(reset.text()).toBe('Resetting…')
    expect(refresh.attributes('disabled')).toBe('')
    expect(refresh.attributes('aria-busy')).toBe('true')
    expect(refresh.text()).toBe('Refresh')

    releasePost(new Response(JSON.stringify({ status: 'reset' }), { status: 200 }))
    await Promise.all([first, second])
    await flushPromises()
    expect(calls).toEqual([
      'GET /api/diagnostics/performance',
      'POST /api/diagnostics/performance/reset',
      'GET /api/diagnostics/performance',
    ])
    expect(window.prksResetRequestCoordinatorDiagnostics).toHaveBeenCalledTimes(1)
    expect(reset.attributes('disabled')).toBeUndefined()
    expect(reset.attributes('aria-busy')).toBeUndefined()
    expect(reset.text()).toBe('Reset')
    expect(refresh.attributes('disabled')).toBeUndefined()
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
