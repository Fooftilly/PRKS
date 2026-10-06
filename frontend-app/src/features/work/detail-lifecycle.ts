/**
 * Work-detail mount sequence.
 * Presentation is the `work` route surface. This module owns the order:
 * notes ticket, viewer modules, shell, then PDF / Research Notes / panel.
 * It is not a second Work store and does not write annotations.
 */
import type { RelSummaryPart } from '../../components/relSummary'
import { workCardCreditText } from '../../components/work-card'
import { describeWorkDetail } from './detail-model'
import type { WorkMainSurfaceModel } from './main-surface'
import { presentWorkResearchNotes } from './research-note-session'

export interface WorkDetailMountRequest {
  generation?: number
  signal?: AbortSignal
  sourcePrepared?: boolean
  workId?: string
}

type Classic = (...args: unknown[]) => unknown

interface WorkDetailMountOwner {
  tabId?: unknown
  destroyed?: boolean
  ui?: { rightPanelTab?: string } | null
  root?: ParentNode | null
  generation?: number
  isCurrent?: (generation: number) => boolean
  getEntity?: (type: string) => { id?: unknown } | null
  registerCleanup?: (fn: () => void) => void
  domId?: (name: string) => string
  setEntity?: (type: string, value: Record<string, unknown> | null) => void
  resourceTicket?: (generation?: number) => unknown
  query?: (selector: string) => Element | null
  setResource?: (name: string, value: unknown) => unknown
  setTimer?: (name: string, timerId: ReturnType<typeof setTimeout>) => unknown
  clearTimer?: (name: string) => void
  timers?: { get: (name: string) => unknown }
  getResource?: (name: string) => unknown
}

function classic(name: string): Classic | null {
  const fn = (globalThis as Record<string, unknown>)[name]
  return typeof fn === 'function' ? (fn as Classic) : null
}

async function loadViewerModule(url: string): Promise<Record<string, unknown>> {
  const imported = await import(/* @vite-ignore */ url)
  return imported as Record<string, unknown>
}

function relSummaryParts(
  work: Record<string, unknown>,
  described: ReturnType<typeof describeWorkDetail>,
): RelSummaryPart[] {
  if (described.pdfViewerActive) return []
  const credit = workCardCreditText(work)
  const folderPart: RelSummaryPart = described.folderTitle
    ? described.folderId
      ? { text: described.folderTitle, href: '#/folders/' + encodeURIComponent(described.folderId) }
      : described.folderTitle
    : null
  const peopleN = described.peopleCount
  const tagsN = described.tagCount
  return [
    folderPart,
    credit || null,
    peopleN != null && peopleN > 0 ? peopleN + (peopleN === 1 ? ' person' : ' people') : null,
    tagsN != null && tagsN > 0 ? tagsN + (tagsN === 1 ? ' tag' : ' tags') : null,
  ]
}

function presentWork(
  ctx: WorkDetailMountOwner,
  contentDiv: HTMLElement,
  fields: {
    availability: 'ready' | 'not-found'
    workId: string
    generation: number
    surface: WorkMainSurfaceModel | null
    attach: (() => void) | null
  },
): void {
  const present = classic('prksPresentVueRoute')
  if (!present) return
  present(ctx as never, contentDiv as never, 'work' as never, fields as never)
}

/**
 * Mount one Work into this pane.
 * The notes ticket is captured before any await. A later generation does not
 * paint, and a rejected surface does not fall back to a legacy shell.
 */
export async function mountWorkDetail(
  ctx: WorkDetailMountOwner | null | undefined,
  contentDiv: HTMLElement | null | undefined,
  work: Record<string, unknown> | null,
  request?: WorkDetailMountRequest,
  loadModule: (url: string) => Promise<Record<string, unknown>> = loadViewerModule,
): Promise<boolean> {
  if (!ctx || !contentDiv || typeof ctx.isCurrent !== 'function') return false
  const currentCheck = ctx.isCurrent
  const generation = typeof request?.generation === 'number' ? request.generation : Number(ctx.generation)
  const routeSignal = request?.signal
  const isCurrent = () => currentCheck(generation)
  if (!isCurrent()) return false
  /* Research Notes setup runs after awaits and a deferred timer. Capture the
   * owner ticket when this mount begins so a cold park, route change, or
   * replaced owner rejects the late EasyMDE. */
  const notesTicket = typeof ctx.resourceTicket === 'function' ? ctx.resourceTicket(generation) : null
  const requestedId = String(request?.workId || (work && work.id) || '')

  if (!work) {
    presentWork(ctx, contentDiv, {
      availability: 'not-found',
      workId: requestedId,
      generation,
      surface: null,
      attach: null,
    })
    return true
  }

  const remember = classic('prksRememberWorkNotesCanonical')
  if (remember) remember(ctx as never, work as never)
  const bindNotes = classic('prksBindWorkNotesSync')
  if (bindNotes) bindNotes(ctx as never)

  const infer = classic('prksInferWorkSourceKind')
  let current = work
  const acknowledgedKind = infer ? String(infer(current as never) || '') : ''
  const sourcePrepared = request?.sourcePrepared === true
  if (!sourcePrepared && acknowledgedKind === 'video') {
    const refreshSources = classic('prksRefreshPendingWorkSources')
    if (refreshSources) {
      await refreshSources()
      if (!isCurrent()) return false
    }
  }
  if (acknowledgedKind === 'video') {
    const effectiveSource = classic('prksEffectiveWorkSource')
    if (effectiveSource) {
      const next = effectiveSource(current as never)
      if (next && typeof next === 'object') current = next as Record<string, unknown>
    }
  }

  const inferredKind = infer ? String(infer(current as never) || '') : ''
  let pdfModule: Record<string, unknown> | null = null
  if (inferredKind === 'pdf' && current.file_path) {
    pdfModule = await loadModule('/js/components/works-pdf.js')
  }
  if (!isCurrent()) return false
  let videoReady = false
  if (inferredKind === 'video') {
    await loadModule('/js/components/works-video.js')
    videoReady = true
  }
  if (!isCurrent()) return false

  const rolesFn = classic('prksEffectiveWorkDetailRoles')
  const rolesForRel = rolesFn ? rolesFn(current as never) : current
  const described = describeWorkDetail(
    current,
    inferredKind,
    rolesForRel && typeof rolesForRel === 'object' ? (rolesForRel as Record<string, unknown>) : null,
  )
  const renderVideo = classic('renderVideoViewerPane')
  let viewerHtml = ''
  if (inferredKind === 'video') {
    viewerHtml = videoReady && renderVideo
      ? String(renderVideo(current as never) || '')
      : '<div class="work-pdf-pane work-pdf-pane--empty"><p class="work-pdf-empty">Video viewer unavailable.</p></div>'
  }
  if (typeof ctx.setEntity === 'function') ctx.setEntity('work', current)
  const badge = classic('prksDocTypeBadgeHtml')
  const shell: WorkMainSurfaceModel = {
    workId: described.workId,
    generation,
    kind: described.kind,
    hasFile: described.hasFile,
    showHeader: described.showHeader,
    title: described.title,
    docTypeHtml: badge ? String(badge(current.doc_type as never) || '') : '',
    relSummaryParts: relSummaryParts(current, described),
    viewerHtml,
    editorRegionId: typeof ctx.domId === 'function' ? ctx.domId('work-notes-editor-region') : 'work-notes-editor-region',
  }

  const attach = () => {
    if (!isCurrent()) return
    const notesTextFor = classic('prksResearchNotesTextForWork')
    const notesTa = typeof ctx.query === 'function' ? ctx.query('[data-prks-role="research-notes-editor"]') : null
    if (notesTa && notesTextFor && 'value' in notesTa) {
      ;(notesTa as HTMLTextAreaElement).value = String(
        notesTextFor(current.id as never, current.text_content as never, ctx as never) || '',
      )
    }
    contentDiv.querySelectorAll('.prks-person-chip').forEach((el) => {
      ;(el as HTMLElement).style.cursor = 'pointer'
    })
    const focused = classic('prksTabContextIsFocused')
    const isFocused = focused ? focused(ctx as never) !== false : true
    if (isFocused) {
      const panelTab = (ctx.ui && ctx.ui.rightPanelTab) || 'details'
      const updatePanel = classic('updatePanelContent')
      if (updatePanel) updatePanel(panelTab as never)
      const syncTabs = classic('prksSyncRightPanelTabStrip')
      if (syncTabs) syncTabs(panelTab as never)
      const editBtn = document.getElementById('edit-metadata-btn')
      const toggle = classic('toggleWorkMetaEdit')
      if (editBtn && toggle) editBtn.onclick = () => { toggle(true as never) }
    }
    const initPdf = pdfModule && pdfModule.initPdfViewerForWork
    if (current.file_path && typeof initPdf === 'function' && isCurrent()) {
      initPdf(ctx, current)
    }
    const setupTimer = globalThis.setTimeout(() => {
      void (async () => {
        if (ctx.timers && ctx.timers.get('workDeferredSetup') === setupTimer && ctx.clearTimer) {
          ctx.clearTimer('workDeferredSetup')
        }
        if (!isCurrent()) return
        const wsEarly = contentDiv.querySelector('.work-workspace')
        if (wsEarly) {
          const key = 'prks.workNotesCollapsed.' + String(current.id ?? '')
          const saved = localStorage.getItem(key)
          const small = classic('prksIsSmallScreen')
          const defaultCollapsed = saved == null && !!small && small() === true
          if (saved === '1' || defaultCollapsed) wsEarly.classList.add('work-workspace--notes-collapsed')
        }
        const fetchWorks = classic('fetchWorks')
        try {
          const works = fetchWorks ? await fetchWorks({ signal: routeSignal } as never) : []
          if (!isCurrent()) return
          const titleMap = classic('prksBuildWorkTitleLowerToIdMap')
          const wikiList = classic('prksBuildWikiAutocompleteWorkList')
          if (ctx.setResource && titleMap && wikiList) {
            ctx.setResource('wikiTitleMap', titleMap(works as never))
            ctx.setResource('wikiWorkList', wikiList(works as never))
          }
        } catch {
          if (!isCurrent()) return
          if (ctx.setResource) {
            ctx.setResource('wikiTitleMap', {})
            ctx.setResource('wikiWorkList', [])
          }
        }
        const fetchConcepts = classic('fetchConcepts')
        try {
          if (fetchConcepts) {
            const concepts = await fetchConcepts({ signal: routeSignal } as never)
            if (!isCurrent()) return
            if (ctx.setResource) ctx.setResource('conceptHintList', concepts)
          }
        } catch {
          if (!isCurrent()) return
          if (ctx.setResource) ctx.setResource('conceptHintList', [])
        }
        const fetchArguments = classic('fetchArguments')
        try {
          if (fetchArguments) {
            const argumentsList = await fetchArguments(undefined as never, { signal: routeSignal } as never)
            if (!isCurrent()) return
            if (ctx.setResource) ctx.setResource('argumentHintList', argumentsList)
          }
        } catch {
          if (!isCurrent()) return
          if (ctx.setResource) ctx.setResource('argumentHintList', [])
        }
        if (!isCurrent()) return
        const refreshNotes = classic('prksRefreshPendingWorkNotes')
        if (refreshNotes) {
          await refreshNotes()
          if (!isCurrent()) return
        }
        const ensureBase = classic('prksEnsureWorkNotesBase')
        if (ensureBase) {
          const canonical = ctx.getResource ? ctx.getResource('workNotesCanonical') : null
          await ensureBase(ctx as never, (canonical || current) as never)
          if (!isCurrent()) return
        }
        const readNotes = classic('prksResearchNotesTextForWork')
        const notesText = readNotes
          ? String(readNotes(current.id as never, current.text_content as never, ctx as never) || '')
          : ''
        const vueNotes = presentWorkResearchNotes(ctx, current, notesText)
        if (!vueNotes) {
          const notesTaLive = ctx.query ? ctx.query('[data-prks-role="research-notes-editor"]') : null
          if (
            notesTaLive &&
            notesTaLive instanceof HTMLTextAreaElement &&
            !notesTaLive.dataset.prksNotesBound
          ) {
            notesTaLive.value = notesText
          }
        }
        const initNotes = classic('initEasyMDE')
        if (initNotes) initNotes(ctx as never, current as never, notesTicket as never)
        const split = classic('setupWorkNotesSplitResize')
        if (split) split(ctx as never, current.id as never)
        const collapse = classic('setupWorkNotesCollapseToggle')
        if (collapse) collapse(ctx as never, current.id as never)
      })()
    }, 200)
    if (ctx.setTimer) ctx.setTimer('workDeferredSetup', setupTimer)

    const requestFn = classic('prksRequest')
    if (!requestFn) return
    void Promise.resolve(requestFn(
      ('/api/works/' + encodeURIComponent(String(current.id ?? '')) + '/related_folders') as never,
      { signal: routeSignal } as never,
      { priority: 'background' } as never,
    )).then((response) => {
      const http = response as { ok?: boolean; json?: () => Promise<unknown> }
      return http && http.ok && http.json ? http.json() : []
    }).then((related) => {
      if (!isCurrent()) return
      const target = ctx.query ? ctx.query('[data-prks-role="related-folders"]') : null
      if (!target || !Array.isArray(related) || related.length === 0) return
      target.replaceChildren()
      for (const folder of related) {
        if (!folder || typeof folder !== 'object') continue
        const row = folder as { title?: unknown; id?: unknown }
        const span = document.createElement('span')
        span.className = 'tag'
        span.style.background = 'var(--accent)'
        span.style.color = 'white'
        span.style.cursor = 'pointer'
        span.textContent = '\uD83D\uDCC1 ' + String(row.title || '')
        span.setAttribute('data-prks-route', '#/folders/' + encodeURIComponent(String(row.id || '')))
        target.appendChild(span)
      }
    }).catch((err: unknown) => {
      const aborted = classic('prksIsAbortError')
      if (aborted && aborted(err as never)) return
      console.error('related folders fetch failed', err)
    })
  }

  presentWork(ctx, contentDiv, {
    availability: 'ready',
    workId: described.workId,
    generation,
    surface: shell,
    attach,
  })
  return isCurrent()
}
