import type { InjectionKey } from 'vue'
import type { ConceptDefinitionEdit, ConceptDetail } from './types'

/** Owning TabContext fields Concept intents need. Not a second route model. */
export interface ConceptIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
}

export interface ConceptIntents {
  create(initialName?: string): Promise<void>
  rename(concept: ConceptDetail): Promise<void>
  remove(concept: ConceptDetail): Promise<void>
  editDefinition(concept: ConceptDetail): Promise<void>
  editAliases(concept: ConceptDetail): Promise<void>
  editParents(concept: ConceptDetail): Promise<void>
  viewGraph(concept: ConceptDetail): void
}

export const conceptIntentsKey: InjectionKey<ConceptIntents> = Symbol('prks-concept-intents')

function ownsDetail(
  owner: ConceptIntentOwner | null | undefined,
  generation: number,
  conceptId: string,
): boolean {
  const fn = window.prksTabContextOwnsEntityRoute
  if (typeof fn === 'function') {
    return fn(owner, generation, 'concept', conceptId, 'concept-detail')
  }
  return !!(owner && typeof owner.isCurrent === 'function' && owner.isCurrent(generation))
}

async function promptText(opts: {
  title: string
  message?: string
  defaultValue?: string
  multiline?: boolean
  okLabel?: string
}): Promise<string | null> {
  const fn = window.prksPromptTextDialog
  if (typeof fn !== 'function') return null
  return fn(opts)
}

async function reportFailure(
  owner: ConceptIntentOwner | null | undefined,
  generation: number,
  conceptId: string,
  err: unknown,
  fallback: string,
): Promise<void> {
  if (!ownsDetail(owner, generation, conceptId)) return
  const alertFn = window.prksAlertDialog
  if (typeof alertFn !== 'function') return
  await alertFn({
    title: fallback,
    message: (err && typeof err === 'object' && 'message' in err
      ? String((err as { message?: unknown }).message || fallback)
      : fallback) || fallback,
  })
}

function refreshDetail(owner: ConceptIntentOwner | null | undefined, generation: number, conceptId: string): void {
  if (!ownsDetail(owner, generation, conceptId)) return
  const nav = window.prksNavigate
  if (typeof nav !== 'function') return
  nav(`#/concepts/${encodeURIComponent(conceptId)}`, {
    replace: true,
    tabId: owner?.tabId,
  })
}

const BASE_UNAVAILABLE =
  'This concept cannot be edited offline yet. Open it once while connected to PRKS so its synchronization state is prepared.'

const NOTICE_PREVIEW_CHARS = 280

function errorCode(err: unknown): string {
  return err && typeof err === 'object' && 'code' in err
    ? String((err as { code?: unknown }).code || '')
    : ''
}

/** The hint a reopened Definition editor shows after a stale Save. */
export function changedElsewhereNotice(current: string): string {
  const text = current.trim()
  const preview =
    text.length > NOTICE_PREVIEW_CHARS ? `${text.slice(0, NOTICE_PREVIEW_CHARS)}…` : text
  return (
    'Not saved: this definition changed in another tab or on another device after you ' +
    `started editing. It now reads: ${preview ? `“${preview}”` : '(empty)'}. ` +
    'Your text is below. Save replaces the current definition with it; Cancel keeps the current definition.'
  )
}

/** Fail closed when a required durable Concept API is not on window. */
function requireDurableApi<T>(fn: T | undefined, label: string): T {
  if (typeof fn !== 'function') {
    const err = new Error(`${label} is unavailable`) as Error & { code?: string }
    err.code = 'durable_api_missing'
    throw err
  }
  return fn
}

/**
 * Typed intents → existing durable Concept APIs and dialog helpers.
 * No fetch(), no TanStack mutations, no second durable queue.
 */
export function browserConceptIntents(
  owner: ConceptIntentOwner | null | undefined,
  generation: number,
): ConceptIntents {
  return {
    async create(initialName) {
      const flow = window.prksCreateConceptFlow
      if (typeof flow !== 'function') return
      await flow(initialName, {
        tabId: owner?.tabId,
        generation,
        isCurrent:
          owner && typeof owner.isCurrent === 'function'
            ? (gen: number) => !!owner.isCurrent?.(gen)
            : undefined,
      })
    },

    async rename(concept) {
      const next = await promptText({
        title: 'Rename Concept',
        defaultValue: concept.name || '',
        okLabel: 'Save',
      })
      if (next == null || !String(next).trim()) return
      if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return
      try {
        const update = requireDurableApi(window.updateConcept, 'updateConcept')
        await update(concept.id, { name: String(next).trim() })
        refreshDetail(owner, generation, concept.id)
      } catch (err) {
        if (!owner.isCurrent(generation)) return
        const alertFn = window.prksAlertDialog
        if (typeof alertFn === 'function') {
          await alertFn({
            title: 'Could not rename',
            message: (err && typeof err === 'object' && 'message' in err
              ? String((err as { message?: unknown }).message || '')
              : '') || '',
          })
        }
      }
    },

    async remove(concept) {
      const confirmFn = window.prksConfirmDestructive
      // Fail closed when the destructive-confirm helper is missing — never delete silently.
      const ok =
        typeof confirmFn === 'function'
          ? await confirmFn({
              title: 'Delete Concept?',
              message:
                'Delete this Concept? Notes that still mention it will recreate a similarly named Concept on save.',
              confirmLabel: 'Delete',
            })
          : false
      if (!ok) return
      if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return
      try {
        const del = requireDurableApi(window.deleteConcept, 'deleteConcept')
        await del(concept.id)
        if (ownsDetail(owner, generation, concept.id) && typeof window.prksNavigate === 'function') {
          window.prksNavigate('#/concepts', { replace: true, tabId: owner.tabId })
        }
      } catch (err) {
        if (!owner.isCurrent(generation)) return
        const code =
          err && typeof err === 'object' && 'code' in err
            ? String((err as { code?: unknown }).code || '')
            : ''
        const msg =
          code === 'concept_in_use'
            ? 'This Concept is still referenced in research notes. Remove or replace those references before deleting it.'
            : (err && typeof err === 'object' && 'message' in err
                ? String((err as { message?: unknown }).message || 'Could not delete Concept.')
                : 'Could not delete Concept.')
        const alertFn = window.prksAlertDialog
        if (typeof alertFn === 'function') {
          await alertFn({ title: 'Cannot delete Concept', message: msg })
        }
      }
    },

    async editDefinition(concept) {
      /* The base is read when the editor opens, with the text it shows, so a
       * Save measures this draft against what it was typed on rather than
       * against whatever another tab stored meanwhile (#568). */
      let session: ConceptDefinitionEdit | null
      try {
        const begin = requireDurableApi(
          window.prksBeginConceptDefinitionEdit,
          'prksBeginConceptDefinitionEdit',
        )
        session = await begin(concept.id)
      } catch (err) {
        await reportFailure(owner, generation, concept.id, err, 'Could not edit the definition')
        return
      }
      if (!session) {
        await reportFailure(
          owner,
          generation,
          concept.id,
          new Error(BASE_UNAVAILABLE),
          'Could not edit the definition',
        )
        return
      }
      if (!ownsDetail(owner, generation, concept.id)) return
      let draft = session.value
      let notice = ''
      for (;;) {
        const next = await promptText({
          title: 'Definition',
          message: notice || 'Markdown',
          defaultValue: draft,
          multiline: true,
          okLabel: 'Save',
        })
        if (!ownsDetail(owner, generation, concept.id)) return
        if (next == null) {
          // The page may still show text from before the editor opened.
          if (notice || session.value !== (concept.description || '')) {
            refreshDetail(owner, generation, concept.id)
          }
          return
        }
        try {
          const update = requireDurableApi(window.updateConcept, 'updateConcept')
          await update(concept.id, { description: next }, { definitionEdit: session })
          break
        } catch (err) {
          if (errorCode(err) !== 'changed_elsewhere') {
            await reportFailure(owner, generation, concept.id, err, 'Could not save the definition')
            return
          }
        }
        /* Changed in another tab or device since this editor opened. Keep the
         * draft, show what PRKS has now, and let the user decide: Save again
         * replaces that text deliberately, Cancel keeps it. */
        const fresh = await window.prksBeginConceptDefinitionEdit?.(concept.id)
        if (!ownsDetail(owner, generation, concept.id)) return
        if (!fresh) {
          await reportFailure(
            owner,
            generation,
            concept.id,
            new Error(BASE_UNAVAILABLE),
            'Could not save the definition',
          )
          return
        }
        session = fresh
        draft = next
        notice = changedElsewhereNotice(fresh.value)
      }
      refreshDetail(owner, generation, concept.id)
    },

    async editAliases(concept) {
      const next = await promptText({
        title: 'Search keys / aliases',
        message: 'One alias per line',
        defaultValue: (concept.aliases || []).join('\n'),
        multiline: true,
        okLabel: 'Save',
      })
      if (next == null) return
      if (!ownsDetail(owner, generation, concept.id)) return
      const aliases = next
        .split(/\n/)
        .map((s) => s.trim())
        .filter(Boolean)
      try {
        const put = requireDurableApi(window.putConceptAliases, 'putConceptAliases')
        await put(concept.id, aliases)
      } catch (err) {
        await reportFailure(owner, generation, concept.id, err, 'Could not save these aliases')
        return
      }
      refreshDetail(owner, generation, concept.id)
    },

    async editParents(concept) {
      const next = await promptText({
        title: 'Parent concepts',
        message: 'Parent Concept IDs, comma-separated',
        defaultValue: (concept.parents || []).map((p) => p.id).join(', '),
        okLabel: 'Save',
      })
      if (next == null) return
      if (!ownsDetail(owner, generation, concept.id)) return
      const ids = next
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      try {
        const put = requireDurableApi(window.putConceptParents, 'putConceptParents')
        await put(concept.id, ids)
      } catch (err) {
        await reportFailure(owner, generation, concept.id, err, 'Could not save these parents')
        return
      }
      refreshDetail(owner, generation, concept.id)
    },

    viewGraph(concept) {
      const focusHash = window.prksGraphFocusHash
      const hash =
        typeof focusHash === 'function'
          ? focusHash('concept', concept.id)
          : `#/graph?focus=${encodeURIComponent(`concept:${concept.id}`)}`
      if (typeof window.prksNavigate === 'function') {
        window.prksNavigate(hash, { tabId: owner?.tabId })
      }
    },
  }
}
