import type { InjectionKey } from 'vue'
import type { ConceptDetail } from './types'

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
      await flow(initialName)
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
        await window.updateConcept?.(concept.id, { name: String(next).trim() })
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
      const ok =
        typeof confirmFn === 'function'
          ? await confirmFn({
              title: 'Delete Concept?',
              message:
                'Delete this Concept? Notes that still mention it will recreate a similarly named Concept on save.',
              confirmLabel: 'Delete',
            })
          : true
      if (!ok) return
      if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return
      try {
        await window.deleteConcept?.(concept.id)
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
      const next = await promptText({
        title: 'Definition',
        message: 'Markdown',
        defaultValue: concept.description || '',
        multiline: true,
        okLabel: 'Save',
      })
      if (next == null) return
      if (!ownsDetail(owner, generation, concept.id)) return
      try {
        await window.updateConcept?.(concept.id, { description: next })
      } catch (err) {
        await reportFailure(owner, generation, concept.id, err, 'Could not save the definition')
        return
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
        await window.putConceptAliases?.(concept.id, aliases)
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
        await window.putConceptParents?.(concept.id, ids)
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
