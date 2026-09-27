import type {
  PositionArgumentRef,
  PositionDetail,
  PositionDetailAvailability,
  PositionIndexAvailability,
  PositionIndexItem,
} from './types'

/**
 * One-way Position index projection for one owner.
 * Built by the legacy coordinator after `prksEffectivePositionRows`.
 */
export interface PositionIndexProjection {
  readonly availability: PositionIndexAvailability
  readonly items: readonly PositionIndexItem[]
  readonly generation: number
}

/**
 * One-way Position detail projection for one owner.
 * Built by the legacy coordinator after effective detail, pending
 * Argument-name overlay, and Work-metadata hydration.
 */
export interface PositionDetailProjection {
  readonly availability: PositionDetailAvailability
  readonly position: PositionDetail | null
  readonly generation: number
}

function asString(value: unknown): string {
  return value == null ? '' : String(value)
}

function asArguments(value: unknown): PositionArgumentRef[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as {
        id?: unknown
        name?: unknown
        kind?: unknown
        verdict_id?: unknown
        verdict_label?: unknown
      }
      const id = asString(row.id).trim()
      if (!id) return null
      return {
        id,
        name: asString(row.name).trim() || id,
        kind: asString(row.kind).trim(),
        verdict_id: asString(row.verdict_id).trim(),
        verdict_label: asString(row.verdict_label).trim(),
      }
    })
    .filter((entry): entry is PositionArgumentRef => entry != null)
}

/** Narrow an already-effective index row into the typed projection item. */
export function acceptPositionIndexItem(value: unknown): PositionIndexItem | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  return {
    id,
    name: asString(row.name).trim() || 'Position',
    description: asString(row.description),
  }
}

export function acceptPositionIndexItems(value: unknown): PositionIndexItem[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => acceptPositionIndexItem(item))
    .filter((item): item is PositionIndexItem => item != null)
}

/**
 * Narrow an already-effective detail record.
 * A pending create may arrive with no `arguments` array — that is an empty
 * targeting list, not a missing snapshot.
 */
export function acceptPositionDetail(value: unknown): PositionDetail | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  return {
    id,
    name: asString(row.name).trim() || 'Position',
    description: asString(row.description),
    arguments: asArguments(row.arguments),
  }
}

export function buildPositionIndexProjection(input: {
  availability?: PositionIndexAvailability
  items?: unknown
  generation: number
}): PositionIndexProjection {
  const availability = input.availability === 'unavailable' ? 'unavailable' : 'ready'
  return {
    availability,
    items: availability === 'ready' ? acceptPositionIndexItems(input.items) : [],
    generation: input.generation,
  }
}

export function buildPositionDetailProjection(input: {
  availability?: PositionDetailAvailability
  position?: unknown
  generation: number
}): PositionDetailProjection {
  const availability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  if (availability !== 'ready') {
    return { availability, position: null, generation: input.generation }
  }
  return {
    availability: 'ready',
    position: acceptPositionDetail(input.position),
    generation: input.generation,
  }
}
