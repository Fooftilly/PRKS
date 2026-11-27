/**
 * The Saved View dialog's rules, apart from its markup (#1036).
 *
 * One dialog creates a view from a Search and edits an existing one. It is
 * online-only: openers stop at the offline guard, and every write goes
 * through the Saved View records service. These helpers decide what an
 * opening fills in and what a Save sends; `SavedViewForm.vue` paints them.
 */
import { PrksApiError } from '../../api/http'
import { SAVED_VIEW_REVISION_CONFLICT, type SavedView, type SavedViewInput, type SavedViewMode } from '../../api/saved-views'
import { parseRoute } from '../../routing/route-model'
import { definitionFromRoute, SEARCH_UNSAVABLE_MESSAGE } from '../search/codec'

export const SAVED_VIEW_OFFLINE_MESSAGE = 'Saved Views require a connection to PRKS.'
export const SAVED_VIEW_NAME_REQUIRED = 'Name is required.'
export const SAVED_VIEW_CREATE_FAILURE = 'Could not save view.'
export const SAVED_VIEW_UPDATE_FAILURE = 'Could not update Saved View.'
export const SAVED_VIEW_CHANGED_ELSEWHERE =
  'This Saved View changed in another tab or on another device after you started editing. ' +
  'Save again to replace it with your version, or Cancel to keep the other one.'

export interface SavedViewDefinitionInput {
  mode?: string
  q?: string
  tag?: string
  author?: string
  publisher?: string
}

/** What an opener hands the dialog. A `viewId` makes it an edit. */
export interface SavedViewOpening {
  viewId?: string
  /** The view's `revision` when this edit opened; an edit needs it. */
  baseRevision?: number
  name?: string
  definition?: SavedViewDefinitionInput
  /**
   * The control focus returns to on close, when focus has already left it:
   * the index Edit button is disabled while it reads the record (#487).
   */
  returnFocus?: HTMLElement | null
}

export interface SavedViewDraft {
  name: string
  mode: SavedViewMode
  q: string
  tag: string
  author: string
  publisher: string
}

export interface SavedViewDialogState {
  viewId: string
  /** The revision an edit is measured against; null for a create. */
  baseRevision: number | null
  draft: SavedViewDraft
}

function modeOf(raw: unknown): SavedViewMode {
  return raw === 'all' || raw === 'tag' || raw === 'advanced' ? raw : 'advanced'
}

function text(raw: unknown): string {
  return typeof raw === 'string' ? raw : ''
}

/** The fields an opening fills in. A missing definition is a blank Advanced search. */
export function stateFromOpening(opening: SavedViewOpening | null | undefined): SavedViewDialogState {
  const opts = opening ?? {}
  const def = opts.definition ?? {}
  return {
    viewId: text(opts.viewId),
    baseRevision:
      typeof opts.baseRevision === 'number' && Number.isSafeInteger(opts.baseRevision) && opts.baseRevision >= 0
        ? opts.baseRevision
        : null,
    draft: {
      name: text(opts.name),
      mode: modeOf(def.mode),
      q: text(def.q),
      tag: text(def.tag),
      author: text(def.author),
      publisher: text(def.publisher),
    },
  }
}

/**
 * The definition a Save sends, trimmed. Only the fields the mode shows are
 * sent: the server refuses a tag on an Advanced view and any filter on an
 * All-fields one, so a value left hidden by a mode switch would fail the
 * Save. The draft keeps it, so switching back restores it.
 */
export function inputFromDraft(draft: SavedViewDraft): SavedViewInput {
  const { mode } = draft
  return {
    name: draft.name.trim(),
    search: {
      mode,
      q: mode === 'tag' ? '' : draft.q.trim(),
      tag: mode === 'tag' ? draft.tag.trim() : '',
      author: mode === 'all' ? '' : draft.author.trim(),
      publisher: mode === 'all' ? '' : draft.publisher.trim(),
    },
  }
}

export type SearchOpening = { ok: true; opening: SavedViewOpening } | { ok: false; message: string }

/** Save View from a Search: that owner's hash, parsed by the one route model. */
export function openingFromSearchHash(hash: string): SearchOpening {
  const parsed = definitionFromRoute(parseRoute(hash))
  if (!parsed.ok) return { ok: false, message: parsed.message || SEARCH_UNSAVABLE_MESSAGE }
  return { ok: true, opening: { definition: { ...parsed.definition } } }
}

/** Edit this Saved View: the record the Main TabContext holds, or nothing. */
export function openingFromView(
  view: Partial<Pick<SavedView, 'id' | 'name' | 'search' | 'revision'>> | null | undefined,
): SavedViewOpening | null {
  if (!view || typeof view.id !== 'string' || !view.id) return null
  return { viewId: view.id, name: text(view.name), definition: view.search ?? {}, baseRevision: view.revision }
}

export interface SavedViewSaveDeps {
  create: (input: SavedViewInput) => Promise<Pick<SavedView, 'id'>>
  update: (viewId: string, input: SavedViewInput, baseRevision: number) => Promise<unknown>
  /** The view as the server holds it now, after a revision conflict. */
  read: (viewId: string) => Promise<Pick<SavedView, 'revision'> | null>
  actionMessage: (err: unknown, fallback: string) => string
}

export type SavedViewSaveOutcome =
  | { status: 'name-required' }
  | { status: 'created'; id: string }
  | { status: 'updated' }
  | { status: 'failed'; message: string }
  /** Changed elsewhere since the edit opened. Saving again with `baseRevision` replaces that change. */
  | { status: 'conflict'; message: string; baseRevision: number }

/**
 * A stale edit was refused. The user decides: the dialog keeps their draft and
 * now measures it against the view's current revision, so Save again replaces
 * the other change knowingly and Cancel keeps it.
 */
async function conflictOutcome(
  viewId: string,
  deps: SavedViewSaveDeps,
  err: unknown,
): Promise<SavedViewSaveOutcome> {
  let current: Pick<SavedView, 'revision'> | null = null
  try {
    current = await deps.read(viewId)
  } catch {
    current = null
  }
  if (!current) return { status: 'failed', message: deps.actionMessage(err, SAVED_VIEW_UPDATE_FAILURE) }
  return { status: 'conflict', message: SAVED_VIEW_CHANGED_ELSEWHERE, baseRevision: current.revision }
}

/** One Save. A refusal carries the server's text when it sent one. */
export async function saveSavedView(
  state: SavedViewDialogState,
  deps: SavedViewSaveDeps,
): Promise<SavedViewSaveOutcome> {
  const input = inputFromDraft(state.draft)
  if (!input.name) return { status: 'name-required' }
  if (state.viewId) {
    if (state.baseRevision == null) return { status: 'failed', message: SAVED_VIEW_UPDATE_FAILURE }
    try {
      await deps.update(state.viewId, input, state.baseRevision)
    } catch (err) {
      if (err instanceof PrksApiError && err.status === 409 && err.code === SAVED_VIEW_REVISION_CONFLICT) {
        return conflictOutcome(state.viewId, deps, err)
      }
      return { status: 'failed', message: deps.actionMessage(err, SAVED_VIEW_UPDATE_FAILURE) }
    }
    return { status: 'updated' }
  }
  let created: Pick<SavedView, 'id'>
  try {
    created = await deps.create(input)
  } catch (err) {
    return { status: 'failed', message: deps.actionMessage(err, SAVED_VIEW_CREATE_FAILURE) }
  }
  return { status: 'created', id: typeof created?.id === 'string' ? created.id : '' }
}
