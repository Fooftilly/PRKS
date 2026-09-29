/**
 * Mounts the Work metadata editor into the shared right panel.
 * The draft object on the owning TabContext is the authority. The DOM is the view.
 */
import { h, isReactive, reactive, render } from 'vue'
import WorkMetadataEditor from './WorkMetadataEditor.vue'
import {
  acceptWorkMetaField,
  cloneWorkMetaDraft,
  workMetaDraftIsDirty,
  workMetaGroupFields,
  workMetaSessionStill,
  type WorkMetaDraft,
  type WorkMetaSessionView,
} from './metadata-draft'

export interface WorkMetadataConflictAction {
  readonly label: string
  readonly apply: boolean
}

export interface WorkMetadataConflict {
  readonly opId: string
  readonly field: string
  readonly group: string
  readonly text: string
  readonly actions: readonly WorkMetadataConflictAction[]
}

export interface WorkMetadataFieldChrome {
  readonly disabled: boolean
  readonly title: string
}

export interface WorkMetadataGroupChrome {
  status: string
  saveDisabled: boolean
  fields: Record<string, WorkMetadataFieldChrome>
  conflicts: WorkMetadataConflict[]
}

export interface WorkMetadataChrome {
  groups: Record<string, WorkMetadataGroupChrome>
  fieldError: { field: string; message: string } | null
}

export interface WorkMetadataOwner {
  tabId?: unknown
  destroyed?: boolean
  ui?: {
    workDetailsMode?: string
    workMetaDraft?: WorkMetaDraft | null
    workMetaBaseline?: WorkMetaDraft | null
    workMetaDraftWorkId?: string | null
    workMetaEditSession?: number
  } | null
  getEntity?: (type: string) => { id?: string } | null
}

interface MountedEditor {
  anchor: HTMLElement
  workId: string
  tabId: string
  draft: WorkMetaDraft
  baseline: WorkMetaDraft
  chrome: WorkMetadataChrome
  focusedField: string
}

let mounted: MountedEditor | null = null

function panelElement(): HTMLElement | null {
  return document.getElementById('panel-content')
}

function emptyChrome(): WorkMetadataChrome {
  return reactive({ groups: {}, fieldError: null })
}

function asDraft(value: Partial<Record<string, unknown>> | null | undefined): WorkMetaDraft {
  if (value && isReactive(value)) return value as WorkMetaDraft
  return reactive(cloneWorkMetaDraft(value))
}

export function dismissWorkMetadataEditor(): void {
  const anchor = mounted?.anchor
  mounted = null
  if (anchor && anchor.isConnected) render(null, anchor)
}

export function workMetadataEditorOwns(ctx: WorkMetadataOwner | null | undefined): boolean {
  if (!mounted || !ctx || ctx.destroyed || !ctx.ui) return false
  const panel = panelElement()
  if (!panel || !mounted.anchor.isConnected) return false
  if ((panel.dataset.prksOwnerTabId || '') !== String(ctx.tabId || '')) return false
  if (String(ctx.tabId || '') !== mounted.tabId) return false
  if (ctx.ui.workMetaDraft !== mounted.draft) return false
  const work = ctx.getEntity ? ctx.getEntity('work') : null
  return !!(work && String(work.id || '') === mounted.workId)
}

export function presentWorkMetadataEditor(ctx: WorkMetadataOwner, sourceKind: string): boolean {
  if (!ctx || !ctx.ui || ctx.ui.workDetailsMode !== 'metadata') return false
  const panel = panelElement()
  const anchor = panel?.querySelector('[data-prks-role="work-metadata-editor-anchor"]')
  if (!panel || !(anchor instanceof HTMLElement)) return false
  if ((panel.dataset.prksOwnerTabId || '') !== String(ctx.tabId || '')) return false
  const workId = String(ctx.ui.workMetaDraftWorkId || '')
  if (!workId) return false
  const draft = asDraft(ctx.ui.workMetaDraft)
  const baseline = asDraft(ctx.ui.workMetaBaseline)
  ctx.ui.workMetaDraft = draft
  ctx.ui.workMetaBaseline = baseline
  if (mounted && (mounted.anchor !== anchor || mounted.workId !== workId || mounted.tabId !== String(ctx.tabId))) {
    dismissWorkMetadataEditor()
  }
  const chrome = mounted && mounted.draft === draft ? mounted.chrome : emptyChrome()
  const focus = (field: string) => {
    if (mounted && mounted.draft === draft) mounted.focusedField = field
  }
  const blur = (field: string) => {
    if (mounted && mounted.draft === draft && mounted.focusedField === field) mounted.focusedField = ''
  }
  render(
    h(WorkMetadataEditor, {
      workId,
      sourceKind: sourceKind || '',
      draft,
      chrome,
      onFocusField: focus,
      onBlurField: blur,
    }),
    anchor,
  )
  mounted = {
    anchor,
    workId,
    tabId: String(ctx.tabId || ''),
    draft,
    baseline,
    chrome,
    focusedField: mounted && mounted.draft === draft ? mounted.focusedField : '',
  }
  return true
}

export function captureWorkMetadataDraft(ctx: WorkMetadataOwner | null | undefined): void {
  if (!ctx?.ui?.workMetaDraft || ctx.ui.workDetailsMode !== 'metadata') return
  const panel = panelElement()
  if (!panel || (panel.dataset.prksOwnerTabId || '') !== String(ctx.tabId || '')) return
  const hidden = panel.querySelector('#meta-doc-type')
  if (hidden instanceof HTMLInputElement) ctx.ui.workMetaDraft.doc_type = hidden.value
}

export function applyWorkMetadataChrome(
  ownerTabId: string,
  workId: string,
  groups: readonly (WorkMetadataGroupChrome & { name: string })[],
): boolean {
  if (!mounted || mounted.tabId !== String(ownerTabId) || mounted.workId !== String(workId)) return false
  const panel = panelElement()
  if (!panel || (panel.dataset.prksOwnerTabId || '') !== String(ownerTabId)) return false
  for (const group of groups) {
    mounted.chrome.groups[group.name] = {
      status: group.status,
      saveDisabled: group.saveDisabled,
      fields: group.fields,
      conflicts: group.conflicts.slice(),
    }
  }
  return true
}

export function setWorkMetadataFieldError(field: string, message: string): void {
  if (!mounted) return
  mounted.chrome.fieldError = message ? { field, message } : null
  if (!message) return
  const input = mounted.anchor.querySelector(`[data-prks-work-field="${field}"]`)
  if (input instanceof HTMLElement) input.focus()
}

export function acceptMountedWorkMetadataField(ctx: WorkMetadataOwner, field: string, value: unknown): boolean {
  if (!mounted || !ctx?.ui || ctx.ui.workMetaDraft !== mounted.draft) return false
  acceptWorkMetaField(mounted.draft, mounted.baseline, field, value, mounted.focusedField)
  return true
}

export function sessionViewOf(ctx: WorkMetadataOwner | null | undefined): WorkMetaSessionView {
  const route = (ctx as { lastResolvedRoute?: { name?: string; params?: { workId?: string } }; route?: { name?: string; params?: { workId?: string } } } | null)?.lastResolvedRoute
    || (ctx as { route?: { name?: string; params?: { workId?: string } } } | null)?.route
  const work = ctx?.getEntity ? ctx.getEntity('work') : null
  const panel = panelElement()
  return {
    destroyed: !!ctx?.destroyed,
    mode: ctx?.ui?.workDetailsMode,
    session: ctx?.ui?.workMetaEditSession,
    draftWorkId: ctx?.ui?.workMetaDraftWorkId || '',
    entityWorkId: work && work.id ? String(work.id) : null,
    routeName: route?.name || null,
    routeWorkId: route?.params?.workId ? String(route.params.workId) : null,
    panelOwnerTabId: panel?.dataset.prksOwnerTabId || null,
    tabId: ctx?.tabId == null ? '' : String(ctx.tabId),
  }
}

export function registerWorkMetadataEditorBridge(root: Window & typeof globalThis): void {
  const target = root as Window & {
    prksVuePresentWorkMetadataEditor?: (ctx: WorkMetadataOwner, sourceKind: string) => boolean
    prksVueDismissWorkMetadataEditor?: () => void
    prksVueWorkMetadataEditorOwns?: (ctx: WorkMetadataOwner | null | undefined) => boolean
    prksVueCaptureWorkMetaDraft?: (ctx: WorkMetadataOwner | null | undefined) => void
    prksVueApplyWorkMetadataChrome?: (
      ownerTabId: string,
      workId: string,
      groups: readonly (WorkMetadataGroupChrome & { name: string })[],
    ) => boolean
    prksVueSetWorkMetadataFieldError?: (field: string, message: string) => void
    prksVueAcceptWorkMetadataField?: (ctx: WorkMetadataOwner, field: string, value: unknown) => boolean
    prksWorkMetadataGroupFields?: (group: string, sourceKind: string) => readonly string[]
    prksVueWorkMetaDraftIsDirty?: (
      draft: Partial<Record<string, unknown>> | null | undefined,
      baseline: Partial<Record<string, unknown>> | null | undefined,
    ) => boolean
    prksVueWorkMetaSessionStill?: (ctx: WorkMetadataOwner | null | undefined, workId: string, session: number) => boolean
  }
  target.prksVuePresentWorkMetadataEditor = (ctx, sourceKind) => presentWorkMetadataEditor(ctx, sourceKind)
  target.prksVueDismissWorkMetadataEditor = () => dismissWorkMetadataEditor()
  target.prksVueWorkMetadataEditorOwns = (ctx) => workMetadataEditorOwns(ctx)
  target.prksVueCaptureWorkMetaDraft = (ctx) => captureWorkMetadataDraft(ctx)
  target.prksVueApplyWorkMetadataChrome = (ownerTabId, workId, groups) =>
    applyWorkMetadataChrome(ownerTabId, workId, groups)
  target.prksVueSetWorkMetadataFieldError = (field, message) => setWorkMetadataFieldError(field, message)
  target.prksVueAcceptWorkMetadataField = (ctx, field, value) => acceptMountedWorkMetadataField(ctx, field, value)
  target.prksWorkMetadataGroupFields = (group, sourceKind) => workMetaGroupFields(group, sourceKind).slice()
  target.prksVueWorkMetaDraftIsDirty = (draft, baseline) => workMetaDraftIsDirty(draft, baseline)
  target.prksVueWorkMetaSessionStill = (ctx, workId, session) =>
    workMetaSessionStill(sessionViewOf(ctx), workId, session)
}

export function resetWorkMetadataEditorForTests(): void {
  dismissWorkMetadataEditor()
}
