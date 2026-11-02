import { afterEach, describe, expect, it, vi } from 'vitest'
import { mountWorkDetail } from './detail-lifecycle'

type PresentCall = {
  feature: string
  fields: {
    availability: string
    workId: string
    surface: {
      kind?: string
      video?: { provider: string; providerId: string; sourceUrl: string } | null
    } | null
    attach: (() => void) | null
  }
}

const g = globalThis as Record<string, unknown>

function host(): HTMLElement {
  const node = document.createElement('div')
  document.body.appendChild(node)
  return node
}

afterEach(() => {
  document.body.innerHTML = ''
  for (const name of [
    'prksPresentVueRoute',
    'prksInferWorkSourceKind',
    'prksRefreshPendingWorkSources',
    'prksEffectiveWorkSource',
    'renderVideoViewerPane',
    'prksTabContextIsFocused',
    'updatePanelContent',
    'prksRequest',
    'prksIsAbortError',
  ]) {
    delete g[name]
  }
})

describe('mountWorkDetail', () => {
  it('captures the notes ticket before the pdf import and starts pdf from attach', async () => {
    const order: string[] = []
    const presented: PresentCall[] = []
    g.prksInferWorkSourceKind = () => 'pdf'
    g.prksPresentVueRoute = (_ctx: unknown, _host: unknown, feature: string, fields: PresentCall['fields']) => {
      order.push('present')
      presented.push({ feature, fields })
    }
    const ctx = {
      generation: 4,
      ui: { rightPanelTab: 'annotations' },
      isCurrent: () => true,
      resourceTicket(generation?: number) {
        order.push('ticket:' + generation)
        return 'ticket'
      },
      setEntity() {
        order.push('entity')
      },
      domId: (name: string) => name,
    }
    const painted = await mountWorkDetail(
      ctx,
      host(),
      { id: 'w1', title: 'Paper', file_path: '/files/a.pdf' },
      { generation: 4, sourcePrepared: true, workId: 'w1' },
      async (url) => {
        order.push('import:' + url)
        return {
          initPdfViewerForWork() {
            order.push('pdf')
          },
        }
      },
    )
    expect(painted).toBe(true)
    expect(order[0]).toBe('ticket:4')
    expect(order.indexOf('ticket:4')).toBeLessThan(order.indexOf('import:/js/components/works-pdf.js'))
    expect(order.indexOf('import:/js/components/works-pdf.js')).toBeLessThan(order.indexOf('present'))
    expect(order).not.toContain('pdf')
    const fields = presented[0]?.fields
    expect(presented[0]?.feature).toBe('work')
    expect(fields?.availability).toBe('ready')
    expect(fields?.surface?.kind).toBe('pdf')
    fields?.attach?.()
    expect(order.indexOf('present')).toBeLessThan(order.indexOf('pdf'))
  })

  it('does not present after the generation moves during the viewer import', async () => {
    let current = true
    const presented: unknown[] = []
    g.prksInferWorkSourceKind = () => 'pdf'
    g.prksPresentVueRoute = () => {
      presented.push('present')
    }
    const ctx = {
      generation: 1,
      ui: {},
      isCurrent: () => current,
      resourceTicket: () => 'ticket',
      setEntity() {},
      domId: (name: string) => name,
    }
    const painted = await mountWorkDetail(
      ctx,
      host(),
      { id: 'w1', file_path: '/files/a.pdf', title: 'A' },
      { generation: 1, workId: 'w1', sourcePrepared: true },
      async () => {
        current = false
        return {}
      },
    )
    expect(painted).toBe(false)
    expect(presented).toEqual([])
  })

  it('paints not-found without importing a viewer', async () => {
    const presented: PresentCall[] = []
    let imported = false
    g.prksPresentVueRoute = (_ctx: unknown, _host: unknown, feature: string, fields: PresentCall['fields']) => {
      presented.push({ feature, fields })
    }
    const ctx = {
      generation: 2,
      ui: {},
      isCurrent: () => true,
      resourceTicket: () => 'ticket',
    }
    const painted = await mountWorkDetail(
      ctx,
      host(),
      null,
      { generation: 2, workId: 'gone' },
      async () => {
        imported = true
        return {}
      },
    )
    expect(painted).toBe(true)
    expect(imported).toBe(false)
    expect(presented[0]?.fields.availability).toBe('not-found')
    expect(presented[0]?.fields.workId).toBe('gone')
    expect(presented[0]?.fields.attach).toBeNull()
  })

  it('skips the video source refresh when the route already prepared it', async () => {
    const order: string[] = []
    const presented: PresentCall[] = []
    g.prksInferWorkSourceKind = () => 'video'
    g.prksRefreshPendingWorkSources = async () => {
      order.push('refresh')
    }
    g.renderVideoViewerPane = () => {
      order.push('video-html')
      return '<iframe src="https://evil.example/"></iframe>'
    }
    g.prksPresentVueRoute = (_ctx: unknown, _host: unknown, feature: string, fields: PresentCall['fields']) => {
      order.push('present')
      presented.push({ feature, fields })
    }
    const ctx = {
      generation: 1,
      ui: {},
      isCurrent: () => true,
      resourceTicket: () => 'ticket',
      setEntity() {},
      domId: (name: string) => name,
    }
    await mountWorkDetail(
      ctx,
      host(),
      {
        id: 'v1',
        title: 'Talk',
        provider: 'youtube',
        provider_id: 'abcdefghijk',
        source_url: 'https://www.youtube.com/watch?v=abcdefghijk',
      },
      { generation: 1, workId: 'v1', sourcePrepared: true },
      async (url) => {
        order.push(url)
        return {}
      },
    )
    expect(order).not.toContain('refresh')
    expect(order).not.toContain('video-html')
    expect(order).not.toContain('/js/components/works-video.js')
    expect(order).toEqual(['present'])
    expect(presented[0]?.fields.surface?.kind).toBe('video')
    expect(presented[0]?.fields.surface?.video).toEqual({
      provider: 'youtube',
      providerId: 'abcdefghijk',
      sourceUrl: 'https://www.youtube.com/watch?v=abcdefghijk',
    })
  })

  it('applies the effective video source and does not load a classic viewer', async () => {
    const order: string[] = []
    const presented: PresentCall[] = []
    g.prksInferWorkSourceKind = () => 'video'
    g.prksRefreshPendingWorkSources = async () => {
      order.push('refresh')
    }
    g.prksEffectiveWorkSource = (work: Record<string, unknown>) => ({
      ...work,
      provider: 'youtube',
      provider_id: 'abcdefghijk',
      source_url: 'https://youtu.be/abcdefghijk',
    })
    g.prksPresentVueRoute = (_ctx: unknown, _host: unknown, feature: string, fields: PresentCall['fields']) => {
      order.push('present')
      presented.push({ feature, fields })
    }
    const ctx = {
      generation: 1,
      ui: {},
      isCurrent: () => true,
      resourceTicket: () => 'ticket',
      setEntity() {},
      domId: (name: string) => name,
    }
    await mountWorkDetail(
      ctx,
      host(),
      { id: 'v1', title: 'Talk', source_url: 'https://evil.example/old' },
      { generation: 1, workId: 'v1' },
      async (url) => {
        order.push(url)
        return {}
      },
    )
    expect(order).toEqual(['refresh', 'present'])
    expect(presented[0]?.fields.surface?.video).toEqual({
      provider: 'youtube',
      providerId: 'abcdefghijk',
      sourceUrl: 'https://youtu.be/abcdefghijk',
    })
  })

  it('logs related-folder load failures with the caught error and suppresses aborts', async () => {
    const presented: PresentCall[] = []
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    g.prksInferWorkSourceKind = () => 'pdf'
    g.prksPresentVueRoute = (_ctx: unknown, _host: unknown, feature: string, fields: PresentCall['fields']) => {
      presented.push({ feature, fields })
    }
    g.prksIsAbortError = (err: unknown) =>
      !!(err && typeof err === 'object' && (err as { name?: string }).name === 'AbortError')
    const ctx = {
      generation: 1,
      ui: {},
      isCurrent: () => true,
      resourceTicket: () => 'ticket',
      setEntity() {},
      setTimer() {},
      query: () => null,
      domId: (name: string) => name,
    }
    try {
      const networkErr = new Error('related folders network')
      g.prksRequest = () => Promise.reject(networkErr)
      await mountWorkDetail(
        ctx,
        host(),
        { id: 'w1', title: 'Paper', file_path: '/files/a.pdf' },
        { generation: 1, sourcePrepared: true, workId: 'w1' },
        async () => ({ initPdfViewerForWork() {} }),
      )
      presented[0]?.fields.attach?.()
      await vi.waitFor(() => {
        expect(errorSpy).toHaveBeenCalledWith('related folders fetch failed', networkErr)
      })

      errorSpy.mockClear()
      const abortErr = Object.assign(new Error('aborted'), { name: 'AbortError' })
      g.prksRequest = () => Promise.reject(abortErr)
      presented[0]?.fields.attach?.()
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(errorSpy).not.toHaveBeenCalled()
    } finally {
      errorSpy.mockRestore()
    }
  })
})
