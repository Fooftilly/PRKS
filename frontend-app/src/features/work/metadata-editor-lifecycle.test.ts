import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import tabContextSource from '../../../../frontend/js/tab-context.js?raw'
import uiSource from '../../../../frontend/js/ui.js?raw'
import metadataEditorSource from '../../../../frontend/js/work-metadata-editor.js?raw'
import sourceEditorSource from '../../../../frontend/js/work-source-editor.js?raw'
import { registerWorkMetadataEditorBridge, resetWorkMetadataEditorForTests } from './metadata-session'

type WorkRecord = {
  id: string
  title: string
  status: string
  doc_type: string
  private_notes?: string
  source_url?: string
}

type MetaUi = {
  rightPanelTab: string
  workDetailsMode: string
  workMetaDraft: Record<string, string> | null
  workMetaBaseline: Record<string, string> | null
  workMetaDraftWorkId: string | null
  workMetaEditSession: number
}

type EditorState = {
  workId: string
  generation: number
  operations: Array<Record<string, unknown>>
  observed: unknown
  error: string | null
  errors?: Record<string, string | null>
  readVersion?: number
  preparing?: Promise<void>
}

type WorkCtx = {
  tabId: string
  generation: number
  ui: MetaUi
  route: { name: string; params?: { workId?: string } } | null
  setEntity: (type: string, value: WorkRecord | null) => void
  getEntity: (type: string) => WorkRecord | null
  getResource: (name: string) => EditorState | null
  setResource: (name: string, value: EditorState) => void
  beginRoute: (route: { name: string; params?: { workId?: string } }) => number
}

type RetainSaved = {
  draft: Record<string, string> | null
  baseline: Record<string, string> | null
  session: number
  workId: string | null
}

type LifecycleWindow = {
  eval: (code: string) => void
  prksWorkspaceSnapshot: () => { focusedTabId: string; mainTabId: string }
  prksMountTabContext: (tabId: string, host: HTMLElement) => void
  prksGetTabContext: (tabId: string) => WorkCtx
  prksDestroyAllTabContexts: () => void
  prksBeginWorkMetaSession: (ctx: WorkCtx, work: WorkRecord) => void
  prksEndWorkMetaSession: (ctx: WorkCtx) => void
  prksWorkMetaSessionStill: (ctx: WorkCtx, workId: string, session: number) => boolean
  prksWorkMetaRetainWorkId: (ctx: WorkCtx) => string
  prksRetainWorkMetaEditAcrossRefresh: (ctx: WorkCtx, keep: boolean, saved: RetainSaved) => void
  prksSaveWorkMetadataFields: (workId: string, groupName: string) => Promise<void>
  prksSaveWorkSource: (workId: string) => Promise<void>
  prksResolveWorkMetadataFieldConflict: (opId: string, apply: boolean, groupName: string) => Promise<void>
  prksResolveWorkSourceConflict: (opId: string, apply: boolean) => Promise<void>
  prksVuePresentWorkMetadataEditor: (ctx: WorkCtx, sourceKind: string) => boolean
  prksSync: {
    changed: () => void
    subscribe?: (event: { acknowledged?: unknown; operation?: string; op?: unknown }) => () => void
    store: {
      saveWorkMetadataFields: (workId: string, changes: unknown, observed: unknown) => Promise<void>
      saveWorkSource: (workId: string, source: unknown, observed: unknown) => Promise<void>
      resolveConflict: (opId: string, apply: boolean) => Promise<void>
    }
  }
  prksOfflineRuntimeSubscribe: (listener: () => void) => () => void
  prksReadWorkMetadataState: (workId: string) => Promise<{ value: unknown }>
  prksMountWorkMetadataEditor: (ctx: WorkCtx, workId: string, options?: { editing?: boolean }) => void
  prksMountWorkSourceEditor: (ctx: WorkCtx, workId: string, options?: { editing?: boolean }) => void
  prksWorkFieldToCanonical: (field: string, value: string) => string | null
  prksDirtyWorkMetadataFields: () => Record<string, string>
  prksObservedWorkFields: () => Record<string, string>
  prksWorkFieldLimitError: () => string | null
  prksRefreshPendingWorkMetadata: () => Promise<unknown[]>
  prksWorkMetadataFieldOperations: () => unknown[]
  prksPendingWorkMetadataState: () => string
  prksOfflineRuntimeState: () => string
  prksOfflineReconcileWorkField: (ack: unknown) => Promise<boolean>
  prksOfflineMarkEntityChanged: (type: string, id: string) => void
  prksCanonicalWorkSource: (value: string) => { source_url: string; provider: string; provider_id: string } | null
  prksEffectiveWorkSource: (work: WorkRecord) => WorkRecord
  prksWorkSourceOf: (work: unknown) => { source_url: string }
  prksWorkSourceIdentity: (source: { provider_id?: string; source_url?: string }) => string
  prksOfflineReadEntity: (type: string, id: string) => Promise<{ source: string; value: WorkRecord & { revision?: number; work_id?: string } }>
  prksRefreshPendingWorkSources: () => Promise<unknown[]>
  prksWorkSourceOperations: () => unknown[]
}

const panelWindow = window as unknown as LifecycleWindow

const THUMB_ERROR = 'Enter a page number of 1 or more, or leave it empty for page 1.'

function work(id = 'work-a', title = 'Alpha'): WorkRecord {
  return { id, title, status: 'Not Started', doc_type: 'article', private_notes: '', source_url: '' }
}

function mount(tabId = 'tab-a'): WorkCtx {
  document.body.innerHTML = `<div id="host"></div><div id="panel-content" data-prks-owner-tab-id="${tabId}"></div>`
  panelWindow.prksWorkspaceSnapshot = () => ({ focusedTabId: tabId, mainTabId: tabId })
  const host = document.getElementById('host')
  if (!host) throw new Error('host missing')
  panelWindow.prksMountTabContext(tabId, host)
  const ctx = panelWindow.prksGetTabContext(tabId)
  const record = work()
  ctx.setEntity('work', record)
  ctx.route = { name: 'work', params: { workId: record.id } }
  ctx.ui.rightPanelTab = 'details'
  ctx.ui.workDetailsMode = 'metadata'
  panelWindow.prksBeginWorkMetaSession(ctx, record)
  return ctx
}

function editorState(ctx: WorkCtx, name: string): EditorState {
  const state: EditorState = {
    workId: 'work-a',
    generation: ctx.generation,
    operations: [],
    observed: { fields: { title: 'Alpha' } },
    error: null,
  }
  ctx.setResource(name, state)
  return state
}

beforeAll(() => {
  panelWindow.eval(tabContextSource)
  panelWindow.eval(uiSource)
  panelWindow.eval(metadataEditorSource)
  panelWindow.eval(sourceEditorSource)
  registerWorkMetadataEditorBridge(window)
})

afterEach(() => {
  resetWorkMetadataEditorForTests()
  panelWindow.prksDestroyAllTabContexts()
  document.body.innerHTML = ''
})

describe('work metadata editor lifecycle', () => {
  it('does not reuse an edit-session token after route teardown', () => {
    const ctx = mount()
    const first = ctx.ui.workMetaEditSession
    expect(first).toBeGreaterThan(0)
    ctx.beginRoute({ name: 'folders' })
    expect(ctx.ui.workMetaEditSession).toBe(first)
    expect(ctx.ui.workMetaDraft).toBeNull()
    const record = work()
    ctx.setEntity('work', record)
    ctx.ui.workDetailsMode = 'metadata'
    panelWindow.prksBeginWorkMetaSession(ctx, record)
    expect(ctx.ui.workMetaEditSession).toBe(first + 1)
    expect(panelWindow.prksWorkMetaSessionStill(ctx, record.id, first)).toBe(false)
    expect(panelWindow.prksWorkMetaSessionStill(ctx, record.id, first + 1)).toBe(true)
  })

  it('keeps a retained draft when a second render starts with no entity', () => {
    const ctx = mount()
    const record = ctx.getEntity('work')
    if (!record || !ctx.ui.workMetaDraft) throw new Error('session missing')
    ctx.ui.workMetaDraft.title = 'Kept title'
    const saved: RetainSaved = {
      draft: ctx.ui.workMetaDraft,
      baseline: ctx.ui.workMetaBaseline,
      session: ctx.ui.workMetaEditSession,
      workId: ctx.ui.workMetaDraftWorkId,
    }
    ctx.beginRoute({ name: 'work', params: { workId: record.id } })
    panelWindow.prksRetainWorkMetaEditAcrossRefresh(ctx, true, saved)
    ctx.setEntity('work', null)
    expect(panelWindow.prksWorkMetaRetainWorkId(ctx)).toBe(record.id)
    const second: RetainSaved = {
      draft: ctx.ui.workMetaDraft,
      baseline: ctx.ui.workMetaBaseline,
      session: ctx.ui.workMetaEditSession,
      workId: ctx.ui.workMetaDraftWorkId,
    }
    const previousWorkId = panelWindow.prksWorkMetaRetainWorkId(ctx)
    const route = { name: 'work', params: { workId: record.id } }
    ctx.beginRoute(route)
    const sameWork = previousWorkId !== '' && String(route.params.workId) === previousWorkId
    expect(sameWork).toBe(true)
    panelWindow.prksRetainWorkMetaEditAcrossRefresh(ctx, true, second)
    expect(ctx.ui.workMetaDraft?.title).toBe('Kept title')
    expect(ctx.ui.workMetaEditSession).toBe(saved.session)
  })

  it('wakes sync after a durable metadata write even if the session ends', async () => {
    const ctx = mount()
    const baseline = ctx.ui.workMetaBaseline
    if (!baseline || !ctx.ui.workMetaDraft) throw new Error('session missing')
    ctx.ui.workMetaDraft.title = 'New title'
    editorState(ctx, 'workMetadataEditor')
    let changed = 0
    let release: () => void = () => {}
    const write = new Promise<void>((resolve) => {
      release = resolve
    })
    ;(panelWindow as unknown as { PRKS_SYNCED_WORK_FIELD_LABELS: Record<string, string> }).PRKS_SYNCED_WORK_FIELD_LABELS = {}
    panelWindow.prksWorkFieldToCanonical = (_field, value) => value
    panelWindow.prksDirtyWorkMetadataFields = () => ({ title: 'New title' })
    panelWindow.prksObservedWorkFields = () => ({ title: 'Alpha' })
    panelWindow.prksWorkFieldLimitError = () => null
    panelWindow.prksRefreshPendingWorkMetadata = () => Promise.resolve([])
    panelWindow.prksWorkMetadataFieldOperations = () => []
    panelWindow.prksPendingWorkMetadataState = () => 'ready'
    panelWindow.prksOfflineRuntimeState = () => 'online'
    panelWindow.prksSync = {
      changed: () => {
        changed += 1
      },
      store: {
        saveWorkMetadataFields: () => write,
        saveWorkSource: () => Promise.resolve(),
        resolveConflict: () => Promise.resolve(),
      },
    }
    const pending = panelWindow.prksSaveWorkMetadataFields('work-a', 'identity')
    await Promise.resolve()
    panelWindow.prksEndWorkMetaSession(ctx)
    release()
    await pending
    expect(changed).toBe(1)
    expect(baseline.title).toBe('Alpha')
  })

  it('wakes sync after a durable source write even if the session ends', async () => {
    const ctx = mount()
    const baseline = ctx.ui.workMetaBaseline
    if (!baseline || !ctx.ui.workMetaDraft) throw new Error('session missing')
    ctx.ui.workMetaDraft.source_url = 'https://www.youtube.com/watch?v=newvideo1'
    const state = editorState(ctx, 'workSourceEditor')
    state.observed = { revision: 1, identity: 'old' }
    let changed = 0
    let release: () => void = () => {}
    const write = new Promise<void>((resolve) => {
      release = resolve
    })
    panelWindow.prksCanonicalWorkSource = (value) => (
      value ? { source_url: value, provider: 'youtube', provider_id: 'newvideo1' } : null
    )
    panelWindow.prksEffectiveWorkSource = (item) => item
    panelWindow.prksWorkSourceOf = () => ({ source_url: 'https://youtu.be/oldvideo1' })
    panelWindow.prksWorkSourceIdentity = (source) => source.provider_id || source.source_url || ''
    panelWindow.prksRefreshPendingWorkSources = () => Promise.resolve([])
    panelWindow.prksWorkSourceOperations = () => []
    panelWindow.prksSync = {
      changed: () => {
        changed += 1
      },
      store: {
        saveWorkMetadataFields: () => Promise.resolve(),
        saveWorkSource: () => write,
        resolveConflict: () => Promise.resolve(),
      },
    }
    const pending = panelWindow.prksSaveWorkSource('work-a')
    await Promise.resolve()
    panelWindow.prksEndWorkMetaSession(ctx)
    release()
    await pending
    expect(changed).toBe(1)
    expect(baseline.source_url).toBe('')
  })

  it('does not let a stale field-conflict continuation edit the next session', async () => {
    const ctx = mount()
    const state = editorState(ctx, 'workMetadataEditor')
    state.operations = [{
      op_id: 'op-1',
      payload: { field: 'title', value: 'Mine' },
      server_result: {
        code: 'REVISION_CONFLICT',
        current_value: 'Server title',
        current_revision: 2,
      },
      status: 'conflict',
    }]
    let release: (ok: boolean) => void = () => {}
    const reconcile = new Promise<boolean>((resolve) => {
      release = resolve
    })
    let resolved = 0
    panelWindow.prksOfflineReconcileWorkField = () => reconcile
    panelWindow.prksOfflineMarkEntityChanged = () => {}
    panelWindow.prksSync = {
      changed: () => {},
      store: {
        saveWorkMetadataFields: () => Promise.resolve(),
        saveWorkSource: () => Promise.resolve(),
        resolveConflict: () => {
          resolved += 1
          return Promise.resolve()
        },
      },
    }
    const pending = panelWindow.prksResolveWorkMetadataFieldConflict('op-1', false, 'identity')
    await Promise.resolve()
    const record = work()
    panelWindow.prksEndWorkMetaSession(ctx)
    ctx.setEntity('work', record)
    ctx.ui.workDetailsMode = 'metadata'
    panelWindow.prksBeginWorkMetaSession(ctx, record)
    if (!ctx.ui.workMetaDraft) throw new Error('new session missing')
    ctx.ui.workMetaDraft.title = 'Fresh'
    state.errors = { identity: 'new-session-error' }
    release(true)
    await pending
    expect(resolved).toBe(1)
    expect(ctx.getEntity('work')?.title).toBe('Alpha')
    expect(ctx.ui.workMetaDraft.title).toBe('Fresh')
    expect(state.errors.identity).toBe('new-session-error')
  })

  it('does not let a stale source discard adopt a re-read into the next session', async () => {
    const ctx = mount()
    const state = editorState(ctx, 'workSourceEditor')
    state.operations = [{
      op_id: 'src-1',
      payload: { source: { url: 'https://youtu.be/old' } },
      server_result: { code: 'SOURCE_REVISION_CONFLICT', current_revision: 4, current_preview: 'https://youtu.be/new' },
      status: 'conflict',
    }]
    let release: () => void = () => {}
    const resolving = new Promise<void>((resolve) => {
      release = resolve
    })
    let resolved = 0
    panelWindow.prksOfflineMarkEntityChanged = () => {}
    panelWindow.prksWorkSourceOf = () => ({ source_url: '' })
    panelWindow.prksWorkSourceIdentity = () => 'adopted'
    panelWindow.prksOfflineReadEntity = async () => ({
      source: 'server',
      value: { id: 'work-a', title: 'Adopted', status: 'Not Started', doc_type: 'article', revision: 4, work_id: 'work-a' },
    })
    panelWindow.prksRefreshPendingWorkSources = () => Promise.resolve([])
    panelWindow.prksWorkSourceOperations = () => []
    panelWindow.prksSync = {
      changed: () => {},
      store: {
        saveWorkMetadataFields: () => Promise.resolve(),
        saveWorkSource: () => Promise.resolve(),
        resolveConflict: () => {
          resolved += 1
          return resolving
        },
      },
    }
    const pending = panelWindow.prksResolveWorkSourceConflict('src-1', false)
    await Promise.resolve()
    const record = work()
    panelWindow.prksEndWorkMetaSession(ctx)
    ctx.setEntity('work', record)
    ctx.ui.workDetailsMode = 'metadata'
    panelWindow.prksBeginWorkMetaSession(ctx, record)
    state.error = 'fresh-error'
    release()
    await pending
    expect(resolved).toBe(1)
    expect(ctx.getEntity('work')?.title).toBe('Alpha')
    expect(state.error).toBe('fresh-error')
  })

  it('keeps a repeated thumb-page error when Vue owns the error text', async () => {
    const ctx = mount()
    const panel = document.getElementById('panel-content')
    if (!panel) throw new Error('panel missing')
    panel.innerHTML = '<div data-prks-role="work-metadata-editor-anchor"></div>'
    expect(panelWindow.prksVuePresentWorkMetadataEditor(ctx, 'pdf')).toBe(true)
    await nextTick()
    editorState(ctx, 'workMetadataEditor')
    panelWindow.prksWorkFieldToCanonical = (field, value) => (field === 'thumb_page' ? null : value)
    panelWindow.prksObservedWorkFields = () => ({})
    panelWindow.prksRefreshPendingWorkMetadata = () => Promise.resolve([])
    panelWindow.prksWorkMetadataFieldOperations = () => []
    panelWindow.prksPendingWorkMetadataState = () => 'ready'
    panelWindow.prksOfflineRuntimeState = () => 'online'
    panelWindow.prksSync = {
      changed: () => {},
      store: {
        saveWorkMetadataFields: () => Promise.resolve(),
        saveWorkSource: () => Promise.resolve(),
        resolveConflict: () => Promise.resolve(),
      },
    }
    if (!ctx.ui.workMetaDraft) throw new Error('draft missing')
    ctx.ui.workMetaDraft.thumb_page = '0'
    await panelWindow.prksSaveWorkMetadataFields('work-a', 'bib')
    await nextTick()
    const error = document.getElementById('meta-thumb-page-error')
    expect(error?.textContent).toBe(THUMB_ERROR)
    ctx.ui.workMetaDraft.thumb_page = '-1'
    await panelWindow.prksSaveWorkMetadataFields('work-a', 'bib')
    await nextTick()
    expect(document.getElementById('meta-thumb-page-error')?.textContent).toBe(THUMB_ERROR)
  })

  it('keeps the newer observed base when an older metadata read rejects', async () => {
    const ctx = mount()
    const older = deferred<{ value: { fields: { title: string } } }>()
    const newer = deferred<{ value: { fields: { title: string } } }>()
    let calls = 0
    let paints = 0
    installEditorRuntime()
    panelWindow.prksReadWorkMetadataState = () => {
      calls += 1
      return calls === 1 ? older.promise : newer.promise
    }
    panelWindow.prksRefreshPendingWorkMetadata = () => {
      paints += 1
      return Promise.resolve([])
    }
    panelWindow.prksMountWorkMetadataEditor(ctx, 'work-a', { editing: true })
    const state = ctx.getResource('workMetadataEditor')
    const firstPrepare = state?.preparing
    panelWindow.prksMountWorkMetadataEditor(ctx, 'work-a', { editing: true })
    const secondPrepare = state?.preparing
    if (!state || !firstPrepare || !secondPrepare) throw new Error('metadata read missing')
    newer.resolve({ value: { fields: { title: 'Newer title' } } })
    await secondPrepare
    expect(state.observed).toEqual({ fields: { title: 'Newer title' } })
    const paintsAfterNewer = paints
    older.reject(new Error('stale metadata read'))
    await firstPrepare
    expect(state.observed).toEqual({ fields: { title: 'Newer title' } })
    expect(paints).toBe(paintsAfterNewer)
  })

  it('keeps the newer observed source when an older source read rejects', async () => {
    const ctx = mount()
    installEditorRuntime()
    panelWindow.prksWorkSourceOf = record => {
      const source = record as { source_url?: string }
      return { source_url: String(source.source_url || '') }
    }
    panelWindow.prksWorkSourceIdentity = source => source.source_url || ''
    const reads: Array<ReturnType<typeof deferred<{ source: string; value: WorkRecord & { revision?: number } }>>> = []
    let paints = 0
    panelWindow.prksOfflineReadEntity = () => {
      const gate = deferred<{ source: string; value: WorkRecord & { revision?: number } }>()
      reads.push(gate)
      return gate.promise
    }
    panelWindow.prksRefreshPendingWorkSources = () => {
      paints += 1
      return Promise.resolve([])
    }
    panelWindow.prksMountWorkSourceEditor(ctx, 'work-a', { editing: true })
    const state = ctx.getResource('workSourceEditor')
    const firstPrepare = state?.preparing
    panelWindow.prksMountWorkSourceEditor(ctx, 'work-a', { editing: true })
    const secondPrepare = state?.preparing
    if (!state || !firstPrepare || !secondPrepare) throw new Error('source read missing')
    expect(reads.length).toBe(4)
    const newerWork = work()
    newerWork.source_url = 'https://example.test/newer'
    reads[2].resolve({ source: 'server', value: { ...newerWork, revision: 7 } })
    reads[3].resolve({ source: 'server', value: newerWork })
    await secondPrepare
    expect(state.observed).toEqual({ revision: 7, identity: 'https://example.test/newer' })
    const paintsAfterNewer = paints
    reads[0].reject(new Error('stale source read'))
    await firstPrepare
    expect(state.observed).toEqual({ revision: 7, identity: 'https://example.test/newer' })
    expect(paints).toBe(paintsAfterNewer)
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function installEditorRuntime() {
  panelWindow.prksSync = {
    changed: () => {},
    subscribe: () => () => {},
    store: {
      saveWorkMetadataFields: () => Promise.resolve(),
      saveWorkSource: () => Promise.resolve(),
      resolveConflict: () => Promise.resolve(),
    },
  }
  panelWindow.prksOfflineRuntimeSubscribe = () => () => {}
  panelWindow.prksRefreshPendingWorkMetadata = () => Promise.resolve([])
  panelWindow.prksWorkMetadataFieldOperations = () => []
  panelWindow.prksPendingWorkMetadataState = () => 'ready'
  panelWindow.prksOfflineRuntimeState = () => 'online'
  panelWindow.prksRefreshPendingWorkSources = () => Promise.resolve([])
  panelWindow.prksWorkSourceOperations = () => []
}
