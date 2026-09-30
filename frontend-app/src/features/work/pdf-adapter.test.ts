import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import runtimeSource from '../../../../frontend/js/pdf-work-runtime.js?raw'
import {
  intentFlushWorkPdf,
  intentMountWorkPdf,
  intentResizeWorkPdf,
  readWorkPdf,
  registerWorkPdfAdapterBridge,
  workPdfLeaveNeedsConfirm,
  type WorkPdfOwner,
  type WorkPdfRuntime,
} from './pdf-adapter'

type PdfWindow = Window & {
  eval: (code: string) => void
  prksMountTabContext: (tabId: string, host: HTMLElement) => WorkPdfOwner & {
    tabId: string
    setResource: (name: string, value: unknown, disposer?: () => void) => unknown
    getResource: (name: string) => unknown
    beginRoute: (route: { name: string }) => number
    query: (selector: string) => Element | null
  }
  prksDestroyAllTabContexts: () => void
  prksHasPendingWorkAnnotationSync: (ctx?: WorkPdfOwner) => boolean
  createWorkPdfRuntime: (options: { workId: string }) => WorkPdfRuntime & {
    syncState: { pendingChanges: boolean; inFlight: boolean }
    lastPage: { persistNow: () => void; debounceClear: () => void } | null
    viewer: { resize: () => void } | null
    destroy: () => void
  }
}

const pdfWindow = window as unknown as PdfWindow

beforeAll(() => {
  pdfWindow.eval(tabContextSource)
  pdfWindow.eval(runtimeSource)
})

afterEach(() => {
  pdfWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

function mount(tabId: string) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const ctx = pdfWindow.prksMountTabContext(tabId, host)
  ctx.beginRoute({ name: 'work' })
  const viewer = document.createElement('div')
  viewer.setAttribute('data-prks-role', 'pdf-viewer')
  ctx.root instanceof HTMLElement && ctx.root.appendChild(viewer)
  return ctx
}

describe('work PDF adapter', () => {
  it('reads the TabContext pdf runtime and does not keep a copy', () => {
    const ctx = mount('read')
    expect(readWorkPdf(ctx).present).toBe(false)
    expect(readWorkPdf(ctx).paneHost).toBe(true)
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' })
    runtime.viewerSetupToken = 4
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(readWorkPdf(ctx)).toMatchObject({
      present: true,
      workId: 'work-a',
      destroyed: false,
      hasPendingSync: false,
      viewerToken: 4,
      paneHost: true,
    })
    runtime.workId = 'work-b'
    runtime.viewerSetupToken = 5
    expect(readWorkPdf(ctx).workId).toBe('work-b')
    expect(readWorkPdf(ctx).viewerToken).toBe(5)
  })

  it('forwards flush and resize to the runtime and mount to initPdfViewerForWork', () => {
    const ctx = mount('intents')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' })
    let flushed = 0
    let resized = 0
    runtime.lastPage = {
      persistNow: () => {},
      debounceClear: () => {},
    }
    const originalFlush = runtime.flushLastPage
    if (!originalFlush) throw new Error('runtime flush missing')
    runtime.flushLastPage = () => {
      flushed += 1
      originalFlush()
    }
    runtime.viewer = { resize: () => { resized += 1 } }
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(intentFlushWorkPdf(ctx)).toBe(true)
    expect(flushed).toBe(1)
    expect(intentResizeWorkPdf(ctx)).toBe(true)
    expect(resized).toBe(1)

    let resourceWrites = 0
    const originalSet = ctx.setResource.bind(ctx)
    ctx.setResource = (name, value, disposer) => {
      resourceWrites += 1
      return originalSet(name, value, disposer)
    }
    const seen: string[] = []
    expect(intentMountWorkPdf(ctx, { id: 'work-a', file_path: '/api/pdfs/a' }, {
      initPdfViewerForWork: (_owner, work) => {
        seen.push(String(work.file_path))
      },
    })).toBe(true)
    expect(seen).toEqual(['/api/pdfs/a'])
    expect(resourceWrites).toBe(0)
    expect(intentMountWorkPdf(ctx, { id: 'work-a' }, {
      initPdfViewerForWork: () => { seen.push('no-file') },
    })).toBe(false)
    expect(seen).toEqual(['/api/pdfs/a'])
  })

  it('does not flush a destroyed runtime', () => {
    const ctx = mount('destroyed-flush')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' })
    let flushed = 0
    runtime.flushLastPage = () => {
      flushed += 1
    }
    runtime._destroyed = true
    ctx.setResource('pdf', runtime, () => {})
    expect(intentFlushWorkPdf(ctx)).toBe(false)
    expect(flushed).toBe(0)
  })

  it('uses the existing pending-annotation leave check', () => {
    const ctx = mount('leave')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' })
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(workPdfLeaveNeedsConfirm(ctx)).toBe(false)
    runtime.syncState.pendingChanges = true
    expect(workPdfLeaveNeedsConfirm(ctx)).toBe(true)
    expect(pdfWindow.prksHasPendingWorkAnnotationSync(ctx)).toBe(true)
  })

  it('registers the same functions on the window bridge', () => {
    registerWorkPdfAdapterBridge(window)
    const bridge = window as unknown as {
      prksReadWorkPdf: typeof readWorkPdf
      prksIntentMountWorkPdf: typeof intentMountWorkPdf
      prksIntentFlushWorkPdf: typeof intentFlushWorkPdf
      prksIntentResizeWorkPdf: typeof intentResizeWorkPdf
      prksWorkPdfLeaveNeedsConfirm: typeof workPdfLeaveNeedsConfirm
    }
    expect(bridge.prksReadWorkPdf).toBe(readWorkPdf)
    expect(bridge.prksIntentMountWorkPdf).toBe(intentMountWorkPdf)
    expect(bridge.prksIntentFlushWorkPdf).toBe(intentFlushWorkPdf)
    expect(bridge.prksIntentResizeWorkPdf).toBe(intentResizeWorkPdf)
    expect(bridge.prksWorkPdfLeaveNeedsConfirm).toBe(workPdfLeaveNeedsConfirm)
  })
})
