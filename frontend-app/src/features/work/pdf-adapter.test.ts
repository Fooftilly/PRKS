import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import runtimeSource from '../../../../frontend/js/pdf-work-runtime.js?raw'
import {
  intentCloseWorkPdfAnnotationPopup,
  intentCloseWorkPdfSearch,
  intentDeleteWorkPdfAnnotationPopup,
  intentDeleteWorkPdfAnnotation,
  intentJumpWorkPdfAnnotation,
  intentResizeWorkPdfAnnotationDrawer,
  intentSetWorkPdfAnnotationDrawerPinned,
  intentFlushWorkPdf,
  intentMountWorkPdf,
  intentOpenWorkPdfSearch,
  intentResizeWorkPdf,
  intentSaveWorkPdfAnnotationComment,
  intentSetWorkPdfSearchQuery,
  intentWorkPdfSearchNext,
  intentWorkPdfSearchPrevious,
  readWorkPdf,
  readWorkPdfAnnotationDrawer,
  readWorkPdfAnnotationPopup,
  readWorkPdfSearch,
  registerWorkPdfAdapterBridge,
  workPdfLeaveNeedsConfirm,
  type WorkPdfOwner,
  type WorkPdfRuntime,
} from './pdf-adapter'
import {
  resetWorkPdfAnnotationDrawerForTests,
  syncWorkPdfAnnotationDrawer,
} from './pdf-annotation-drawer'

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
  savePdfAnnotationComment?: (
    owner: WorkPdfOwner,
    text: string,
    captured: { generation: number; annId: string; epoch: number },
  ) => void
  closePdfAnnotationEditor?: (
    owner: WorkPdfOwner,
    options: { annId: string; generation?: number; reason?: string },
  ) => void
  createWorkPdfRuntime: (options: { workId: string }) => WorkPdfRuntime & {
    syncState: { pendingChanges: boolean; inFlight: boolean }
    lastPage: { persistNow: () => void; debounceClear: () => void } | null
    viewer: { resize: () => void } | null
    search?: { epoch: number }
    applySearchResult?: (result: {
      epoch?: number
      total: number
      activeIndex: number
      viewer?: unknown
    }) => boolean
    openAnnotationPopup?: (info: {
      annId: string
      comment?: string
      pageIndex?: number
      generation?: number
    }) => boolean
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
      prksReadWorkPdfSearch: typeof readWorkPdfSearch
      prksIntentOpenWorkPdfSearch: typeof intentOpenWorkPdfSearch
      prksIntentCloseWorkPdfSearch: typeof intentCloseWorkPdfSearch
      prksIntentSetWorkPdfSearchQuery: typeof intentSetWorkPdfSearchQuery
      prksIntentWorkPdfSearchNext: typeof intentWorkPdfSearchNext
      prksIntentWorkPdfSearchPrevious: typeof intentWorkPdfSearchPrevious
      prksReadWorkPdfAnnotationPopup: typeof readWorkPdfAnnotationPopup
      prksIntentSaveWorkPdfAnnotationComment: typeof intentSaveWorkPdfAnnotationComment
      prksIntentCloseWorkPdfAnnotationPopup: typeof intentCloseWorkPdfAnnotationPopup
      prksIntentDeleteWorkPdfAnnotationPopup: typeof intentDeleteWorkPdfAnnotationPopup
    }
    expect(bridge.prksReadWorkPdf).toBe(readWorkPdf)
    expect(bridge.prksIntentMountWorkPdf).toBe(intentMountWorkPdf)
    expect(bridge.prksIntentFlushWorkPdf).toBe(intentFlushWorkPdf)
    expect(bridge.prksIntentResizeWorkPdf).toBe(intentResizeWorkPdf)
    expect(bridge.prksWorkPdfLeaveNeedsConfirm).toBe(workPdfLeaveNeedsConfirm)
    expect(bridge.prksReadWorkPdfSearch).toBe(readWorkPdfSearch)
    expect(bridge.prksIntentOpenWorkPdfSearch).toBe(intentOpenWorkPdfSearch)
    expect(bridge.prksIntentCloseWorkPdfSearch).toBe(intentCloseWorkPdfSearch)
    expect(bridge.prksIntentSetWorkPdfSearchQuery).toBe(intentSetWorkPdfSearchQuery)
    expect(bridge.prksIntentWorkPdfSearchNext).toBe(intentWorkPdfSearchNext)
    expect(bridge.prksIntentWorkPdfSearchPrevious).toBe(intentWorkPdfSearchPrevious)
    expect(bridge.prksReadWorkPdfAnnotationPopup).toBe(readWorkPdfAnnotationPopup)
    expect(bridge.prksIntentSaveWorkPdfAnnotationComment).toBe(intentSaveWorkPdfAnnotationComment)
    expect(bridge.prksIntentCloseWorkPdfAnnotationPopup).toBe(intentCloseWorkPdfAnnotationPopup)
    expect(bridge.prksIntentDeleteWorkPdfAnnotationPopup).toBe(intentDeleteWorkPdfAnnotationPopup)
  })

  it('forwards search to the runtime and drops a stale generation', () => {
    const ctx = mount('search')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' })
    const viewer = {
      commits: [] as string[],
      opens: 0,
      closes: 0,
      resize() {},
      openSearch() { viewer.opens += 1 },
      closeSearch() { viewer.closes += 1 },
      commitSearch(query: string) { viewer.commits.push(query) },
      clearSearchMatches() {},
      searchNext() { return 1 },
      searchPrevious() { return 0 },
    }
    runtime.viewer = viewer
    const token = runtime.viewerSetupToken
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    const captured = { generation: ctx.generation }
    expect(readWorkPdfSearch(ctx).open).toBe(false)
    expect(intentOpenWorkPdfSearch(ctx, captured)).toBe(true)
    expect(intentSetWorkPdfSearchQuery(ctx, 'alpha', captured)).toBe(true)
    expect(intentSetWorkPdfSearchQuery(ctx, 'alpha beta', captured)).toBe(true)
    expect(viewer.commits).toEqual(['alpha', 'alpha beta'])
    expect(readWorkPdfSearch(ctx)).toMatchObject({
      open: true,
      query: 'alpha beta',
      status: 'pending',
    })
    runtime.applySearchResult?.({
      epoch: runtime.search?.epoch,
      total: 0,
      activeIndex: -1,
      viewer,
    })
    expect(readWorkPdfSearch(ctx).status).toBe('empty')
    expect(readWorkPdfSearch(ctx).matchCountLabel).toBe('No matches')
    expect(intentWorkPdfSearchNext(ctx, captured)).toBe(false)
    runtime.setSearchQuery?.('alpha beta')
    runtime.applySearchResult?.({
      epoch: runtime.search?.epoch,
      total: 2,
      activeIndex: 0,
      viewer,
    })
    expect(intentWorkPdfSearchNext(ctx, captured)).toBe(true)
    expect(readWorkPdfSearch(ctx).matchCountLabel).toBe('2 of 2')
    expect(intentWorkPdfSearchPrevious(ctx, captured)).toBe(true)
    expect(runtime.viewer).toBe(viewer)
    expect(runtime.viewerSetupToken).toBe(token)
    expect(intentCloseWorkPdfSearch(ctx, captured)).toBe(true)
    expect(viewer.closes).toBe(1)
    expect(runtime.viewer).toBe(viewer)

    const stale = { generation: ctx.generation }
    ctx.beginRoute({ name: 'work' })
    expect(intentSetWorkPdfSearchQuery(ctx, 'later', stale)).toBe(false)
    expect(intentOpenWorkPdfSearch(ctx, stale)).toBe(false)
  })

  it('does not let a stale annotation popup intent update the next annotation', () => {
    const ctx = mount('popup')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' })
    const viewer = { id: 'viewer', updates: [] as string[], resize() {} }
    runtime.viewer = viewer
    runtime.viewerSetupToken = 3
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    const opened = runtime.openAnnotationPopup?.({
      annId: 'A',
      comment: 'from A',
      pageIndex: 0,
      generation: ctx.generation,
    })
    expect(opened).toBe(true)
    const ticketA = {
      generation: ctx.generation,
      annId: 'A',
      epoch: runtime.readAnnotationPopup?.().epoch ?? -1,
    }
    expect(readWorkPdfAnnotationPopup(ctx)).toMatchObject({
      open: true,
      annId: 'A',
      comment: 'from A',
      epoch: ticketA.epoch,
    })
    const saves: string[] = []
    const closes: string[] = []
    pdfWindow.savePdfAnnotationComment = (_owner, text) => {
      saves.push(text)
    }
    pdfWindow.closePdfAnnotationEditor = (_owner, options) => {
      closes.push(options.annId)
    }
    expect(intentSaveWorkPdfAnnotationComment(ctx, 'typed A', ticketA)).toBe(true)
    runtime.openAnnotationPopup?.({
      annId: 'B',
      comment: 'from B',
      pageIndex: 1,
      generation: ctx.generation,
    })
    expect(intentSaveWorkPdfAnnotationComment(ctx, 'typed A late', ticketA)).toBe(false)
    expect(intentCloseWorkPdfAnnotationPopup(ctx, ticketA)).toBe(false)
    expect(saves).toEqual(['typed A'])
    expect(closes).toEqual([])
    expect(readWorkPdfAnnotationPopup(ctx).annId).toBe('B')
    expect(readWorkPdfAnnotationPopup(ctx).comment).toBe('from B')
    expect(runtime.viewer).toBe(viewer)
    expect(runtime.viewerSetupToken).toBe(3)
  })

  it('does not jump from a stale annotation drawer', () => {
    const ctx = mount('drawer')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' }) as unknown as WorkPdfRuntime & {
      annotationCache: { items: Array<{ id: string }>; listPublished?: boolean }
      openAnnotationDrawer: () => boolean
      closeAnnotationDrawer: () => boolean
      viewer: { id: string }
      viewerSetupToken: number
      destroy: () => void
    }
    const viewer = { id: 'v1' }
    runtime.viewer = viewer
    runtime.viewerSetupToken = 2
    runtime.annotationCache = { items: [{ id: 'ann-a' }], listPublished: true }
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(runtime.openAnnotationDrawer()).toBe(true)
    const read = readWorkPdfAnnotationDrawer(ctx)
    expect(read.published).toBe(true)
    expect(read.items[0]?.wikiLink).toBe('[[pdf:ann-a]]')
    const jumps: string[] = []
    ;(window as unknown as {
      jumpToPdfAnnotationFromDrawer?: (owner: WorkPdfOwner, annId: string) => void
    }).jumpToPdfAnnotationFromDrawer = (_owner, annId) => {
      jumps.push(annId)
    }
    const ticket = {
      generation: ctx.generation,
      epoch: read.epoch,
      viewerToken: read.viewerToken,
    }
    expect(intentJumpWorkPdfAnnotation(ctx, 'ann-a', ticket)).toBe(true)
    expect(runtime.closeAnnotationDrawer()).toBe(true)
    expect(intentJumpWorkPdfAnnotation(ctx, 'ann-a', ticket)).toBe(false)
    expect(runtime.openAnnotationDrawer()).toBe(true)
    expect(intentJumpWorkPdfAnnotation(ctx, 'ann-a', ticket)).toBe(false)
    expect(jumps).toEqual(['ann-a'])
    expect(runtime.viewer).toBe(viewer)
    expect(runtime.viewerSetupToken).toBe(2)
  })

  it('does not pin or resize a stale annotation drawer', () => {
    const ctx = mount('drawer-pin')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' }) as unknown as WorkPdfRuntime & {
      openAnnotationDrawer: () => boolean
      closeAnnotationDrawer: () => boolean
      noteAnnotationDrawerFrame: (frame: { paneWidth: number; mobile: boolean }) => void
      viewer: { id: string }
      viewerSetupToken: number
      destroy: () => void
    }
    const viewer = { id: 'v1' }
    runtime.viewer = viewer
    runtime.viewerSetupToken = 2
    runtime.noteAnnotationDrawerFrame({ paneWidth: 900, mobile: false })
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(runtime.openAnnotationDrawer()).toBe(true)
    const read = readWorkPdfAnnotationDrawer(ctx)
    const ticket = {
      generation: ctx.generation,
      epoch: read.epoch,
      viewerToken: read.viewerToken,
    }
    const layouts: string[] = []
    ;(window as unknown as { prksLayoutAnnotationDrawer?: (owner: WorkPdfOwner) => boolean }).prksLayoutAnnotationDrawer =
      () => {
        layouts.push('layout')
        return true
      }
    expect(intentSetWorkPdfAnnotationDrawerPinned(ctx, true, ticket)).toBe(true)
    expect(intentResizeWorkPdfAnnotationDrawer(ctx, 400, ticket, { persist: false })).toBe(true)
    expect(readWorkPdfAnnotationDrawer(ctx).placement).toBe('pinned')
    expect(readWorkPdfAnnotationDrawer(ctx).width).toBe(400)
    expect(readWorkPdfAnnotationDrawer(ctx).selectedId).toBe('')
    const previews: number[] = []
    ;(window as unknown as {
      prksPreviewAnnotationDrawerWidth?: (owner: WorkPdfOwner, width: number) => boolean
    }).prksPreviewAnnotationDrawerWidth = (_owner, width) => {
      previews.push(width)
      return true
    }
    ;(window as unknown as {
      prksRestoreAnnotationDrawerWidth?: (owner: WorkPdfOwner) => boolean
    }).prksRestoreAnnotationDrawerWidth = () => true
    expect(intentResizeWorkPdfAnnotationDrawer(ctx, 420, ticket, { preview: true })).toBe(true)
    expect(intentResizeWorkPdfAnnotationDrawer(ctx, 352, ticket, { cancel: true })).toBe(true)
    expect(previews).toEqual([420])
    expect(readWorkPdfAnnotationDrawer(ctx).width).toBe(400)
    expect(layouts).toEqual(['layout', 'layout'])
    runtime.closeAnnotationDrawer()
    expect(intentSetWorkPdfAnnotationDrawerPinned(ctx, false, ticket)).toBe(false)
    expect(intentResizeWorkPdfAnnotationDrawer(ctx, 300, ticket)).toBe(false)
    runtime.openAnnotationDrawer()
    expect(readWorkPdfAnnotationDrawer(ctx).pinned).toBe(true)
    expect(readWorkPdfAnnotationDrawer(ctx).width).toBe(400)
    expect(layouts).toEqual(['layout', 'layout'])
    expect(runtime.viewer).toBe(viewer)
    expect(runtime.viewerSetupToken).toBe(2)
    delete (window as unknown as { prksLayoutAnnotationDrawer?: unknown }).prksLayoutAnnotationDrawer
    delete (window as unknown as { prksPreviewAnnotationDrawerWidth?: unknown }).prksPreviewAnnotationDrawerWidth
    delete (window as unknown as { prksRestoreAnnotationDrawerWidth?: unknown }).prksRestoreAnnotationDrawerWidth
  })

  it('returns the in-flight drawer delete', async () => {
    const ctx = mount('drawer-delete')
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' }) as unknown as WorkPdfRuntime & {
      openAnnotationDrawer: () => boolean
      viewerSetupToken: number
      destroy: () => void
    }
    runtime.viewerSetupToken = 2
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(runtime.openAnnotationDrawer()).toBe(true)
    const read = readWorkPdfAnnotationDrawer(ctx)
    let finish: (ok: boolean) => void = () => {}
    const pending = new Promise<boolean>((resolve) => {
      finish = resolve
    })
    ;(window as unknown as {
      deletePdfAnnotationFromList?: (owner: WorkPdfOwner, annId: string) => Promise<boolean>
    }).deletePdfAnnotationFromList = () => pending
    const result = intentDeleteWorkPdfAnnotation(ctx, 'ann-a', {
      generation: ctx.generation,
      epoch: read.epoch,
      viewerToken: read.viewerToken,
    })
    expect(result).toBeInstanceOf(Promise)
    let settled = false
    const done = result.then((ok) => {
      settled = true
      return ok
    })
    await Promise.resolve()
    expect(settled).toBe(false)
    finish(true)
    await expect(done).resolves.toBe(true)
    delete (window as unknown as { deletePdfAnnotationFromList?: unknown }).deletePdfAnnotationFromList
  })

  it('updates data-selected when edit opens and closes', async () => {
    const ctx = mount('drawer-selection')
    const pane = document.createElement('div')
    pane.className = 'work-pdf-pane'
    if (ctx.root instanceof HTMLElement) ctx.root.appendChild(pane)
    const runtime = pdfWindow.createWorkPdfRuntime({ workId: 'work-a' }) as unknown as WorkPdfRuntime & {
      annotationCache: { items: Array<{ id: string }>; listPublished?: boolean }
      openAnnotationDrawer: () => boolean
      openAnnotationPopup?: (info: { annId: string }) => boolean
      closeAnnotationPopup?: (annId: string) => boolean
      destroy: () => void
    }
    runtime.annotationCache = { items: [{ id: 'ann-a' }], listPublished: true }
    ctx.setResource('pdf', runtime, () => runtime.destroy())
    expect(runtime.openAnnotationDrawer()).toBe(true)
    const row = () => document.querySelector('[data-ann-id="ann-a"]')
    syncWorkPdfAnnotationDrawer(ctx)
    await nextTick()
    expect(row()?.getAttribute('data-selected')).toBe('false')
    expect(runtime.openAnnotationPopup?.({ annId: 'ann-a' })).toBe(true)
    syncWorkPdfAnnotationDrawer(ctx)
    await nextTick()
    expect(row()?.getAttribute('data-selected')).toBe('true')
    expect(runtime.closeAnnotationPopup?.('ann-a')).toBe(true)
    syncWorkPdfAnnotationDrawer(ctx)
    await nextTick()
    expect(row()?.getAttribute('data-selected')).toBe('false')
    resetWorkPdfAnnotationDrawerForTests()
  })
})
