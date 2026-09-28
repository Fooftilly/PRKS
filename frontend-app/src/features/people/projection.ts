import type {
  PeopleIndexAvailability,
  PersonDetail,
  PersonDetailAvailability,
  PersonFieldDraft,
  PersonGroupChip,
  PersonIndexItem,
  PersonWorkItem,
} from './types'

export interface PeopleIndexProjection {
  readonly availability: PeopleIndexAvailability
  readonly people: readonly PersonIndexItem[]
  readonly roleFilter: string
  readonly roleLabel: string
  readonly generation: number
}

export interface PersonDetailProjection {
  readonly availability: PersonDetailAvailability
  readonly person: PersonDetail | null
  readonly personId: string
  readonly editing: boolean
  /** The focused pane is the only one that mounts the id'd editor. */
  readonly editorActive: boolean
  readonly worksEditing: boolean
  readonly offlineCached: boolean
  readonly generation: number
}

const ROLE_LABELS: Record<string, string> = {
  Author: 'Authors',
  Editor: 'Editors',
  Reviewer: 'Reviewers',
  Translator: 'Translators',
  Introduction: 'Introduction writers',
  Foreword: 'Foreword writers',
  Afterword: 'Afterword writers',
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (value == null) return ''
  return String(value)
}

function displayDate(stored: string): string {
  const format = window.personDateToDisplayFormat
  if (typeof format === 'function') return format(stored) || ''
  const trimmed = stored.trim()
  if (/^-?\d+$/.test(trimmed)) return trimmed
  const match = trimmed.match(/^(-?\d+)-(\d{2})-(\d{2})$/)
  if (!match) return ''
  return `${match[3]}/${match[2]}/${match[1]}`
}

function lifespanOf(record: Record<string, unknown>): string {
  const format = window.personLifespanDisplay
  if (typeof format === 'function') return format(record) || ''
  const birth = text(record.birth_date).trim()
  const death = text(record.death_date).trim()
  if (birth && death) return `${birth} – ${death}`
  if (birth) return `Born ${birth}`
  if (death) return `Died ${death}`
  return ''
}

function chips(value: unknown): PersonGroupChip[] {
  if (!Array.isArray(value)) return []
  const out: PersonGroupChip[] = []
  for (const row of value) {
    const record = asRecord(row)
    if (!record) continue
    const id = text(record.id).trim()
    if (!id) continue
    out.push({ id, name: text(record.name) })
  }
  return out
}

function httpUrl(value: string): string {
  const safe = window.safeHttpUrl
  if (typeof safe === 'function') return safe(value) || ''
  const trimmed = value.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return ''
}

export function roleLabel(role: string): string {
  if (!role) return ''
  return ROLE_LABELS[role] || role
}

export function personIndexItemFromRow(row: unknown): PersonIndexItem | null {
  const record = asRecord(row)
  if (!record) return null
  const id = text(record.id).trim()
  if (!id) return null
  const roles = Array.isArray(record.assigned_roles)
    ? record.assigned_roles.map((role) => text(role)).filter(Boolean)
    : []
  return {
    id,
    firstName: text(record.first_name),
    lastName: text(record.last_name),
    aliases: text(record.aliases),
    about: text(record.about),
    lifespan: lifespanOf(record),
    roles,
    groups: chips(record.groups),
  }
}

function fieldsFrom(record: Record<string, unknown>): PersonFieldDraft {
  return {
    first_name: text(record.first_name),
    last_name: text(record.last_name),
    aliases: text(record.aliases),
    about: text(record.about),
    birth_date: displayDate(text(record.birth_date)),
    death_date: displayDate(text(record.death_date)),
    image_url: text(record.image_url),
    link_wikipedia: text(record.link_wikipedia),
    link_stanford_encyclopedia: text(record.link_stanford_encyclopedia),
    link_iep: text(record.link_iep),
    links_other: text(record.links_other),
  }
}

function optionalNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}

function worksFrom(record: Record<string, unknown>): PersonWorkItem[] {
  const rows = Array.isArray(record.works) ? record.works : []
  const out: PersonWorkItem[] = []
  for (const row of rows) {
    const work = asRecord(row)
    if (!work) continue
    const id = text(work.id).trim()
    if (!id) continue
    out.push({
      id,
      title: text(work.title).trim() || 'Untitled',
      roleType: text(work.role_type).trim() || 'Linked',
      orderIndex: text(work.order_index) || '0',
      subtitle: text(work.subtitle || work.credit_name),
      filePath: text(work.file_path),
      thumbUrl: text(work.thumb_url),
      thumbPage: optionalNumber(work.thumb_page),
      status: text(work.status),
      docType: text(work.doc_type),
      year: text(work.year),
      publishedDate: text(work.published_date),
      sizeBytes: optionalNumber(work.file_size_bytes),
      linkedAuthors: text(work.linked_authors),
      authorText: text(work.author_text),
      primaryAuthor: text(work.primary_author),
      primaryEditor: text(work.primary_editor),
      sourceKind: text(work.source_kind),
      sourceUrl: text(work.source_url),
      provider: text(work.provider),
      providerId: text(work.provider_id),
    })
  }
  return out
}

function otherLinkRows(value: string): { label: string; href: string }[] {
  const out: { label: string; href: string }[] = []
  for (const line of value.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const markdown = trimmed.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (markdown) {
      const label = (markdown[1] || '').trim()
      const href = httpUrl(markdown[2] || '')
      if (label && href) out.push({ label, href })
      continue
    }
    const href = httpUrl(trimmed)
    if (!href) continue
    const label = href.replace(/^https?:\/\//i, '').split('/')[0] || href
    out.push({ label, href })
  }
  return out
}

function linksFrom(fields: PersonFieldDraft): { label: string; href: string }[] {
  const pairs: { label: string; value: string }[] = [
    { label: 'Wikipedia', value: fields.link_wikipedia },
    { label: 'Stanford Encyclopedia of Philosophy', value: fields.link_stanford_encyclopedia },
    { label: 'Internet Encyclopedia of Philosophy', value: fields.link_iep },
  ]
  return pairs
    .flatMap((pair) => {
      const href = httpUrl(pair.value)
      return href ? [{ label: pair.label, href }] : []
    })
    .concat(otherLinkRows(fields.links_other))
}

function referenceCountOf(fields: PersonFieldDraft): number {
  let count = 0
  if (httpUrl(fields.link_wikipedia)) count += 1
  if (httpUrl(fields.link_stanford_encyclopedia)) count += 1
  if (httpUrl(fields.link_iep)) count += 1
  count += otherLinkRows(fields.links_other).length
  return count
}

export function personDetailFromRow(row: unknown): PersonDetail | null {
  const record = asRecord(row)
  if (!record) return null
  const id = text(record.id).trim()
  if (!id) return null
  const fields = fieldsFrom(record)
  const aliases = fields.aliases
    .split(',')
    .map((alias) => alias.trim())
    .filter(Boolean)
  return {
    id,
    firstName: fields.first_name,
    lastName: fields.last_name,
    fields,
    lifespan: lifespanOf(record),
    aliases,
    about: fields.about.trim(),
    imageUrl: fields.image_url.trim(),
    links: linksFrom(fields),
    otherLinks: fields.links_other,
    groups: chips(record.groups),
    works: worksFrom(record),
    referenceCount: referenceCountOf(fields),
  }
}

export function buildPeopleIndexProjection(input: {
  availability?: string
  items?: unknown
  roleFilter?: string
  unknownRole?: boolean
  generation?: number
}): PeopleIndexProjection {
  const availability: PeopleIndexAvailability = input.unknownRole
    ? 'unknown-role'
    : input.availability === 'unavailable'
      ? 'unavailable'
      : 'ready'
  const people =
    availability === 'ready' && Array.isArray(input.items)
      ? input.items.map(personIndexItemFromRow).filter((row): row is PersonIndexItem => !!row)
      : []
  const roleFilter = text(input.roleFilter).trim()
  return {
    availability,
    people,
    roleFilter,
    roleLabel: roleLabel(roleFilter),
    generation: typeof input.generation === 'number' ? input.generation : 0,
  }
}

export function buildPersonDetailProjection(input: {
  availability?: string
  person?: unknown
  personId?: string
  editing?: boolean
  editorActive?: boolean
  worksEditing?: boolean
  offlineCached?: boolean
  generation?: number
}): PersonDetailProjection {
  const requested: PersonDetailAvailability =
    input.availability === 'unavailable' || input.availability === 'not-found'
      ? input.availability
      : 'ready'
  const person = requested === 'ready' ? personDetailFromRow(input.person) : null
  const personId = text(input.personId).trim() || person?.id || ''
  return {
    availability: person || requested !== 'ready' ? requested : 'not-found',
    person,
    personId,
    editing: input.editing === true && !!person,
    editorActive: input.editorActive !== false && input.editing === true && !!person,
    worksEditing: input.worksEditing === true && !!person,
    offlineCached: input.offlineCached === true,
    generation: typeof input.generation === 'number' ? input.generation : 0,
  }
}
