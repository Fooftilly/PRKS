import { createVNode } from 'vue'
import {
  dismissRouteSurface,
  presentRouteSurface,
  registerEarlyRoutePresenter,
  registerRouteWindowBridge,
  resetRouteSurfaceForTests,
  type RouteSurfaceOwner,
} from '../../route-surface/lifecycle'
import { closeTagsAliasModals, closeTagsMergeModals } from './closers'
import { browserTagsIntents, type TagsIntentOwner } from './intents'
import { buildTagsProjection, type TagsProjection, type TagsResume } from './projection'
import type { TagsRouteInstance } from './route'
import TagsRoute from './TagsRoute.vue'

export interface TagsPresentInput {
  owner: RouteSurfaceOwner & TagsIntentOwner
  host: HTMLElement
  tags: unknown
  generation?: number
  /**
   * True when this owner is the Main shell. Recorded on the route instance.
   * The Tags sidebar is static copy; Vue does not publish it.
   */
  shell?: boolean
  /** Reopen the alias or merge dialog that is still current after this owner reloads. */
  resume?: TagsResume | null
}

const TAGS_FEATURE = 'tags'

export interface TagsRefreshSink {
  set: ((message: string) => void) | null
}

export type TagsDialogKind = 'alias' | 'merge' | null

/** The dialog this pane currently has open. A reload reads it before remounting. */
export interface TagsDialogState {
  kind: TagsDialogKind
  aliasTagId: string | null
  mergeSourceId: string | null
  mergeTargetId: string | null
}

function resumeFromDialog(dialog: TagsDialogState): TagsResume | null {
  if (dialog.kind === 'merge' && dialog.mergeSourceId) {
    return {
      mergeSourceId: dialog.mergeSourceId,
      mergeTargetId: dialog.mergeTargetId,
    }
  }
  if (dialog.kind === 'alias' && dialog.aliasTagId) {
    return { aliasTagId: dialog.aliasTagId }
  }
  return null
}

const refreshSinks = new WeakMap<object, TagsRefreshSink>()

function refreshSinkFor(owner: object): TagsRefreshSink {
  let sink = refreshSinks.get(owner)
  if (!sink) {
    sink = { set: null }
    refreshSinks.set(owner, sink)
  }
  return sink
}

/** Keep the painted list and show the refresh failure on that owner. */
export function reportTagsRefreshFailure(owner: object | null | undefined, message: string): void {
  if (!owner) return
  const text = String(message || '').trim() || 'Could not refresh tags.'
  refreshSinks.get(owner)?.set?.(text)
}

function isTagsEarlyRequest(
  value: unknown,
): value is Omit<TagsPresentInput, 'host'> & { feature: typeof TAGS_FEATURE } {
  if (!value || typeof value !== 'object') return false
  const record = value as Partial<TagsPresentInput> & { feature?: unknown }
  return record.feature === TAGS_FEATURE && !!record.owner && 'tags' in record
}

/**
 * Paint one owner's Tags vocabulary page. The coordinator has already loaded
 * `fetchTags({ used: true })`. Vue does not fetch it.
 */
export function presentTags(input: TagsPresentInput): void {
  if (!input || !input.owner || typeof input.owner !== 'object') return
  const owner = input.owner
  const tags = input.tags
  const resume = input.resume
  const route: Omit<TagsRouteInstance, 'generation'> & { generation?: number } = {
    name: 'tags',
    canonicalHash: '#/tags',
    params: {},
    ownsMainShell: input.shell !== false,
    generation: input.generation,
  }
  presentRouteSurface({
    owner,
    host: input.host,
    route,
    render: (generation) => {
      const projection: TagsProjection = buildTagsProjection({ tags, generation, resume })
      const dialogState: TagsDialogState = {
        kind: projection.openMergeSourceId ? 'merge' : projection.openAliasTagId ? 'alias' : null,
        aliasTagId: projection.openAliasTagId,
        mergeSourceId: projection.openMergeSourceId,
        mergeTargetId: projection.openMergeTargetId,
      }
      return createVNode(TagsRoute, {
        projection,
        intents: browserTagsIntents(owner, generation, {
          currentDialog: () => resumeFromDialog(dialogState),
        }),
        dialogState,
        refreshSink: refreshSinkFor(owner),
      })
    },
  })
}

export function dismissTags(owner: object | null | undefined): void {
  dismissRouteSurface(owner)
}

export function resetTagsSessionForTests(): void {
  resetRouteSurfaceForTests()
}

export function registerTagsBridge(target: Window = window): void {
  registerRouteWindowBridge(target)
  target.prksVueDismissTags = dismissTags
  target.prksVueCloseTagsAliasModal = closeTagsAliasModals
  target.prksVueCloseTagsMergeModal = closeTagsMergeModals
  target.prksVueReportTagsRefreshFailure = reportTagsRefreshFailure
  registerEarlyRoutePresenter(
    TAGS_FEATURE,
    (request, host) => {
      if (!isTagsEarlyRequest(request)) return false
      const { owner, tags, generation, shell, resume } = request
      presentTags({ owner, host, tags, generation, shell, resume })
      return true
    },
    target,
  )
}
