import type {
  ArgumentDetail,
  ArgumentDetailAvailability,
  ArgumentEditorDraft,
  ArgumentEditorForm,
  ArgumentIndexAvailability,
  ArgumentIndexItem,
  ArgumentIndexSource,
  ArgumentIndexTarget,
  ArgumentKind,
  ArgumentKindFilter,
  ArgumentMentionRef,
  ArgumentResponseRef,
  ArgumentSourceAuthor,
  ArgumentSourceRef,
  ArgumentTargetRef,
  ArgumentVerdict,
} from './types'
import { normalizeArgumentKindFilter } from './match'

/**
 * One-way Argument index projection for one owner.
 * Built by the legacy coordinator after `prksEffectiveArgumentRows` and
 * `prksFilterArgumentsByKind` on the complete cached collection.
 */
export interface ArgumentIndexProjection {
  readonly availability: ArgumentIndexAvailability
  readonly kind: ArgumentKindFilter
  readonly items: readonly ArgumentIndexItem[]
  readonly generation: number
}

/**
 * One-way Argument detail projection for one owner.
 * Built by the legacy coordinator after effective detail, pending Position
 * and Argument name overlays, and Work-metadata hydration.
 */
export interface ArgumentDetailProjection {
  readonly availability: ArgumentDetailAvailability
  readonly argument: ArgumentDetail | null
  readonly generation: number
}

function asString(value: unknown): string {
  return value == null ? '' : String(value)
}

function asKind(value: unknown): ArgumentKind {
  return value === 'stance' ? 'stance' : 'argument'
}

function asTargetType(value: unknown): 'position' | 'argument' {
  return value === 'argument' ? 'argument' : 'position'
}

function asIndexTargets(value: unknown): ArgumentIndexTarget[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as { id?: unknown; name?: unknown; type?: unknown }
      const id = asString(row.id).trim()
      if (!id) return null
      return { id, name: asString(row.name), type: asString(row.type) }
    })
    .filter((entry): entry is ArgumentIndexTarget => entry != null)
}

function asIndexSources(value: unknown): ArgumentIndexSource[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as { work_id?: unknown; work_title?: unknown }
      const workId = asString(row.work_id).trim()
      if (!workId) return null
      return { work_id: workId, work_title: asString(row.work_title) }
    })
    .filter((entry): entry is ArgumentIndexSource => entry != null)
}

export function acceptArgumentIndexItem(value: unknown): ArgumentIndexItem | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  const responseCount = Number(row.response_count)
  return {
    id,
    name: asString(row.name).trim() || id,
    kind: asKind(row.kind),
    main_text: asString(row.main_text),
    response_count: Number.isFinite(responseCount) ? responseCount : 0,
    targets: asIndexTargets(row.targets),
    sources: asIndexSources(row.sources),
  }
}

export function acceptArgumentIndexItems(value: unknown): ArgumentIndexItem[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => acceptArgumentIndexItem(item))
    .filter((item): item is ArgumentIndexItem => item != null)
}

function asAuthors(value: unknown): ArgumentSourceAuthor[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as { first_name?: unknown; last_name?: unknown; credit_name?: unknown }
      return {
        first_name: asString(row.first_name),
        last_name: asString(row.last_name),
        credit_name: asString(row.credit_name),
      }
    })
    .filter((entry): entry is ArgumentSourceAuthor => entry != null)
}

function asTargets(value: unknown): ArgumentTargetRef[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as Record<string, unknown>
      const id = asString(row.id).trim()
      if (!id) return null
      return {
        type: asTargetType(row.type),
        id,
        name: asString(row.name).trim() || id,
        kind: asString(row.kind),
        verdict_id: asString(row.verdict_id),
        verdict_label: asString(row.verdict_label),
      }
    })
    .filter((entry): entry is ArgumentTargetRef => entry != null)
}

function asSources(value: unknown): ArgumentSourceRef[] {
  if (!Array.isArray(value)) return []
  const out: ArgumentSourceRef[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') continue
    const row = entry as Record<string, unknown>
    const workId = asString(row.work_id).trim()
    if (!workId) continue
    out.push({
      work_id: workId,
      work_title: asString(row.work_title).trim() || workId,
      pages: asString(row.pages),
      authors: asAuthors(row.authors),
    })
  }
  return out
}

function asResponses(value: unknown): ArgumentResponseRef[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as Record<string, unknown>
      const id = asString(row.id).trim()
      if (!id) return null
      return {
        id,
        name: asString(row.name).trim() || id,
        kind: asString(row.kind),
        verdict_id: asString(row.verdict_id),
        verdict_label: asString(row.verdict_label),
      }
    })
    .filter((entry): entry is ArgumentResponseRef => entry != null)
}

function asMentions(value: unknown): ArgumentMentionRef[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as { work_id?: unknown; title?: unknown }
      const workId = asString(row.work_id).trim()
      if (!workId) return null
      return { work_id: workId, title: asString(row.title).trim() || workId }
    })
    .filter((entry): entry is ArgumentMentionRef => entry != null)
}

function asVerdicts(value: unknown): ArgumentVerdict[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const row = entry as { id?: unknown; label?: unknown }
      const id = asString(row.id).trim()
      if (!id) return null
      return { id, label: asString(row.label).trim() || id }
    })
    .filter((entry): entry is ArgumentVerdict => entry != null)
}

/**
 * Narrow an already-effective detail record.
 * A pending create may arrive without responses, mentions, or verdicts —
 * those empty lists are the truth, not a missing snapshot.
 */
export function acceptArgumentDetail(value: unknown): ArgumentDetail | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const id = asString(row.id).trim()
  if (!id) return null
  return {
    id,
    name: asString(row.name).trim() || id,
    kind: asKind(row.kind),
    main_text: asString(row.main_text),
    targets: asTargets(row.targets),
    sources: asSources(row.sources),
    responses: asResponses(row.responses),
    mentions: asMentions(row.mentions),
    verdicts: asVerdicts(row.verdicts),
  }
}

let argumentEditorMounts = 0

/**
 * Ids for one mounted editor draft.
 * A counter, not a UUID: plain HTTP is a supported LAN origin and has no
 * `crypto.randomUUID`. The ids stay inside this draft.
 */
export function createEditorRowKeys(): () => string {
  const editor = ++argumentEditorMounts
  let next = 0
  return () => {
    next += 1
    return `arg-${editor}-${next}`
  }
}

export function draftFromArgument(
  argument: ArgumentDetail,
  nextRowKey: () => string,
): ArgumentEditorForm {
  return {
    name: argument.name,
    kind: argument.kind,
    main_text: argument.main_text,
    targets: argument.targets.map((target) => ({
      rowKey: nextRowKey(),
      type: target.type,
      id: target.id,
      name: target.name,
      kind: target.kind,
      verdict_id: target.verdict_id,
    })),
    sources: argument.sources.map((source) => ({
      rowKey: nextRowKey(),
      work_id: source.work_id,
      work_title: source.work_title,
      pages: source.pages,
    })),
  }
}

/** Copy the fields the durable writer keeps. `rowKey` stays in the form. */
export function argumentEditorDraftFromForm(form: ArgumentEditorForm): ArgumentEditorDraft {
  return {
    name: form.name,
    kind: form.kind,
    main_text: form.main_text,
    targets: form.targets.map((row) => ({
      type: row.type,
      id: row.id,
      name: row.name,
      kind: row.kind,
      verdict_id: row.verdict_id,
    })),
    sources: form.sources.map((row) => ({
      work_id: row.work_id,
      work_title: row.work_title,
      pages: row.pages,
    })),
  }
}

export function buildArgumentIndexProjection(input: {
  availability?: ArgumentIndexAvailability
  kind?: string
  items?: unknown
  generation: number
}): ArgumentIndexProjection {
  const availability = input.availability === 'unavailable' ? 'unavailable' : 'ready'
  return {
    availability,
    kind: normalizeArgumentKindFilter(input.kind),
    items: availability === 'ready' ? acceptArgumentIndexItems(input.items) : [],
    generation: input.generation,
  }
}

export function buildArgumentDetailProjection(input: {
  availability?: ArgumentDetailAvailability
  argument?: unknown
  generation: number
}): ArgumentDetailProjection {
  const availability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  if (availability !== 'ready') {
    return { availability, argument: null, generation: input.generation }
  }
  return {
    availability: 'ready',
    argument: acceptArgumentDetail(input.argument),
    generation: input.generation,
  }
}
