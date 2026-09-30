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
    generation: number
    setEntity: (type: string, value: { id: string } | null) => void
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
    ctx.setEntity('work', { id: 'work-a' })
    const captured = { generation: ctx.generation, workId: 'work-a' }
    expect(intentMountWorkPdf(ctx, { id: 'work-a', file_path: '/api/pdfs/a' }, {
      initPdfViewerForWork: (_owner, work) => {
        seen.push(String(work.file_path))
      },
    }, captured)).toBe(true)
    expect(seen).toEqual(['/api/pdfs/a'])
    expect(resourceWrites).toBe(0)
    expect(intentMountWorkPdf(ctx, { id: 'work-a' }, {
      initPdfViewerForWork: () => { seen.push('no-file') },
    }, captured)).toBe(false)
    expect(seen).toEqual(['/api/pdfs/a'])
  })

  it('requires a Work id from a typed caller and still rejects a missing id at runtime', () => {
    type MountArg = NonNullable<Parameters<typeof intentMountWorkPdf>[1]>
    type MountIdIsRequired = {} extends Pick<MountArg, 'id'> ? false : true
    const mountIdIsRequired: MountIdIsRequired = true
    expect(mountIdIsRequired).toBe(true)

    const ctx = mount('typed-id')
    ctx.setEntity('work', { id: 'work-a' })
    const captured = { generation: ctx.generation, workId: 'work-a' }
    const orchestration = { initPdfViewerForWork: () => {} }
    intentMountWorkPdf(
      ctx,
      // @ts-expect-error a typed mount must include the Work id
      { file_path: '/api/pdfs/a' },
      orchestration,
      captured,
    )

    const calls: string[] = []
    const recording = {
      initPdfViewerForWork: () => { calls.push('called') },
    }
    const untypedMount = intentMountWorkPdf as (
      owner: WorkPdfOwner,
      work: { id?: unknown; file_path?: unknown } | null,
      next: typeof recording,
      ownerCapture: typeof captured,
    ) => boolean
    expect(untypedMount(ctx, { file_path: '/api/pdfs/a' }, recording, captured)).toBe(false)
    expect(untypedMount(ctx, { id: '', file_path: '/api/pdfs/a' }, recording, captured)).toBe(false)
    expect(untypedMount(ctx, { id: null, file_path: '/api/pdfs/a' }, recording, captured)).toBe(false)
    expect(intentMountWorkPdf(
      ctx,
      { id: 'work-b', file_path: '/api/pdfs/b' },
      recording,
      captured,
    )).toBe(false)
    expect(calls).toEqual([])
  })

  it('does not mount a stale Work after the same context advances', () => {
    const ctx = mount('stale-owner')
    ctx.setEntity('work', { id: 'work-a' })
    const capturedA = { generation: ctx.generation, workId: 'work-a' }
    ctx.beginRoute({ name: 'work' })
    ctx.setEntity('work', { id: 'work-b' })
    const calls: string[] = []
    const orchestration = {
      initPdfViewerForWork: (_owner: WorkPdfOwner, work: { id?: unknown }) => {
        calls.push(String(work.id))
      },
    }
    expect(intentMountWorkPdf(
      ctx,
      { id: 'work-a', file_path: '/api/pdfs/a' },
      orchestration,
      capturedA,
    )).toBe(false)
    expect(calls).toEqual([])
    expect(intentMountWorkPdf(
      ctx,
      { id: 'work-b', file_path: '/api/pdfs/b' },
      orchestration,
      { generation: ctx.generation, workId: 'work-b' },
    )).toBe(true)
    expect(calls).toEqual(['work-b'])
  })

  it('rejects an older generation of the same Work', () => {
    const ctx = mount('same-work-generation')
    ctx.setEntity('work', { id: 'work-a' })
    const oldGeneration = ctx.generation
    ctx.beginRoute({ name: 'work' })
    ctx.setEntity('work', { id: 'work-a' })
    let calls = 0
    const orchestration = {
      initPdfViewerForWork: () => {
        calls += 1
      },
    }
    expect(intentMountWorkPdf(
      ctx,
      { id: 'work-a', file_path: '/api/pdfs/a' },
      orchestration,
      { generation: oldGeneration, workId: 'work-a' },
    )).toBe(false)
    expect(calls).toBe(0)
    expect(intentMountWorkPdf(
      ctx,
      { id: 'work-a', file_path: '/api/pdfs/a' },
      orchestration,
      { generation: ctx.generation, workId: 'work-a' },
    )).toBe(true)
    expect(calls).toBe(1)
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
