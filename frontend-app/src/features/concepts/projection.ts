import type {
  ConceptChildRef,
  ConceptDetail,
  ConceptDetailAvailability,
  ConceptIndexAvailability,
  ConceptIndexItem,
  ConceptMention,
  ConceptParentRef,
} from './types'

/**
 * One-way Concept index projection for one owner.
 * Built by the legacy coordinator after effective-row resolution.
 */
export interface ConceptIndexProjection {
  readonly availability: ConceptIndexAvailability
  readonly items: readonly ConceptIndexItem[]
  readonly generation: number
}

/**
 * One-way Concept detail projection for one owner.
 * Built by the legacy coordinator after effective detail + work references.
 */
export interface ConceptDetailProjection {
  readonly availability: ConceptDetailAvailability
  readonly concept: ConceptDetail | null
  readonly generation: number
}

function asString(value: unknown): string {
  return value == null ? '' : String(value)
}

function asParentRefs(value: unknown): ConceptParentRef[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as { id?: unknown; name?: unknown }
      const id = asString(row.id).trim()
      if (!id) return null
      return { id, name: asString(row.name).trim() || id }
    })
    .filter((entry): entry is ConceptParentRef => entry != null)
}

function asChildRefs(value: unknown): ConceptChildRef[] {
  return asParentRefs(value)
}

function asAliases(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((alias) => asString(alias).trim()).filter(Boolean)
}

function asMentions(value: unknown): ConceptMention[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as {
        work_id?: unknown
        title?: unknown
        occurrences?: unknown
      }
      const workId = asString(row.work_id).trim()
      if (!workId) return null
      const occurrences = Array.isArray(row.occurrences)
        ? row.occurrences.map((occ) => {
            if (!occ || typeof occ !== 'object') return { snippet: '' }
            return { snippet: asString((occ as { snippet?: unknown }).snippet) }
          })
        : []
      const mention: ConceptMention = {
        work_id: workId,
        title: asString(row.title),
        occurrences,
      }
      return mention
    })
    .filter((entry): entry is ConceptMention => entry != null)
}

/** Narrow an already-effective index row into the typed projection item. */
export function acceptConceptIndexItem(value: unknown): ConceptIndexItem | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  return {
    id,
    name: asString(row.name).trim() || 'Concept',
    aliases: asAliases(row.aliases),
    parents: asParentRefs(row.parents),
    subconcept_count: Number(row.subconcept_count) || 0,
    mention_count: Number(row.mention_count) || 0,
  }
}

export function acceptConceptIndexItems(value: unknown): ConceptIndexItem[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => acceptConceptIndexItem(item))
    .filter((item): item is ConceptIndexItem => item != null)
}

/** Narrow an already-effective detail record into the typed projection. */
export function acceptConceptDetail(value: unknown): ConceptDetail | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  const parents = asParentRefs(row.parents)
  const children = asChildRefs(row.children)
  return {
    id,
    name: asString(row.name).trim() || 'Concept',
    description: asString(row.description),
    aliases: asAliases(row.aliases),
    parents,
    children,
    mentions: asMentions(row.mentions),
    mention_count: Number(row.mention_count) || 0,
    subconcept_count: Number(row.subconcept_count) || children.length,
  }
}

export function buildConceptIndexProjection(input: {
  availability?: ConceptIndexAvailability
  items?: unknown
  generation: number
}): ConceptIndexProjection {
  const availability = input.availability === 'unavailable' ? 'unavailable' : 'ready'
  return {
    availability,
    items: availability === 'ready' ? acceptConceptIndexItems(input.items) : [],
    generation: input.generation,
  }
}

export function buildConceptDetailProjection(input: {
  availability?: ConceptDetailAvailability
  concept?: unknown
  generation: number
}): ConceptDetailProjection {
  const availability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  if (availability !== 'ready') {
    return { availability, concept: null, generation: input.generation }
  }
  return {
    availability: 'ready',
    concept: acceptConceptDetail(input.concept),
    generation: input.generation,
  }
}
