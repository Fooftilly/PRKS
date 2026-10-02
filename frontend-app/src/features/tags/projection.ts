/**
 * Used-tag cloud for `#/tags`. Order is the coordinator's `fetchTags` order.
 * Scale and the CSS color guard match the legacy page. This projection does
 * not fetch, sort, or read the durable queue.
 */

const TAG_COLOR_FALLBACK = '#6d6cf7'
const MIN_SCALE = 0.85
const MAX_SCALE = 1.85
const SAFE_COLOR_KEYWORDS = new Set([
  'red',
  'green',
  'blue',
  'yellow',
  'orange',
  'purple',
  'pink',
  'brown',
  'black',
  'white',
  'gray',
  'grey',
  'cyan',
  'magenta',
  'lime',
  'maroon',
  'navy',
  'olive',
  'teal',
  'aqua',
  'fuchsia',
  'silver',
])

export interface TagCloudRow {
  readonly id: string
  readonly name: string
  readonly color: string
  readonly aliases: readonly string[]
  readonly workCount: number
  readonly folderCount: number
  readonly total: number
  readonly scale: string
  readonly borderWidth: string
  readonly searchHash: string
  readonly encodedName: string
}

export interface TagsResume {
  readonly aliasTagId?: string | null
}

export interface TagsProjection {
  readonly rows: readonly TagCloudRow[]
  readonly generation: number
  /** Set when a reload must reopen this tag's alias dialog. Absent otherwise. */
  readonly openAliasTagId: string | null
}

export function safeTagCssColor(raw: unknown, fallback: string = TAG_COLOR_FALLBACK): string {
  const value = String(raw || '').trim()
  const fb = fallback || TAG_COLOR_FALLBACK
  if (/^#[0-9a-fA-F]{3}$/.test(value)) return value
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return value
  if (/^#[0-9a-fA-F]{8}$/.test(value)) return value
  if (SAFE_COLOR_KEYWORDS.has(value.toLowerCase())) return value
  return fb
}

/** Legacy cloud scale: log10 of usage, 0.85–1.85, or 1 when every tag ties. */
export function tagCloudScale(total: number, minTotal: number, maxTotal: number): number {
  const logMin = Math.log10(minTotal + 1)
  const logMax = Math.log10(maxTotal + 1)
  const log = Math.log10((total || 0) + 1)
  if (logMax === logMin) return 1
  const t = (log - logMin) / (logMax - logMin)
  return MIN_SCALE + t * (MAX_SCALE - MIN_SCALE)
}

export function tagSearchHash(name: string): string {
  return `#/search?tag=${encodeURIComponent(name)}`
}

function countOf(value: unknown): number {
  const count = Number(value)
  if (!Number.isFinite(count) || count < 0) return 0
  return count
}

export function acceptTagRows(value: unknown): TagCloudRow[] {
  if (!Array.isArray(value)) return []
  const draft: Array<{
    id: string
    name: string
    color: string
    aliases: string[]
    workCount: number
    folderCount: number
    total: number
  }> = []
  for (const row of value) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    const record = row as {
      id?: unknown
      name?: unknown
      color?: unknown
      aliases?: unknown
      work_count?: unknown
      folder_count?: unknown
    }
    const id = record.id == null ? '' : String(record.id)
    if (!id) continue
    const workCount = countOf(record.work_count)
    const folderCount = countOf(record.folder_count)
    const aliases = Array.isArray(record.aliases)
      ? record.aliases.filter((alias): alias is string => typeof alias === 'string')
      : []
    draft.push({
      id,
      name: typeof record.name === 'string' ? record.name : '',
      color: safeTagCssColor(record.color),
      aliases,
      workCount,
      folderCount,
      total: workCount + folderCount,
    })
  }
  const totals = draft.map((row) => row.total)
  const minTotal = totals.length ? Math.min(...totals) : 0
  const maxTotal = totals.length ? Math.max(...totals) : 0
  return draft.map((row) => {
    const scale = tagCloudScale(row.total, minTotal, maxTotal)
    return {
      ...row,
      scale: scale.toFixed(3),
      borderWidth: `${(4 * scale).toFixed(2)}px`,
      searchHash: tagSearchHash(row.name),
      encodedName: encodeURIComponent(row.name),
    }
  })
}

export function filterMergeCandidates(
  rows: readonly TagCloudRow[],
  sourceId: string,
  filter: string,
): TagCloudRow[] {
  const query = filter.trim().toLowerCase()
  return rows.filter((row) => {
    if (row.id === sourceId) return false
    if (!query) return true
    return row.name.toLowerCase().includes(query)
  })
}

export function buildTagsProjection(input: {
  tags?: unknown
  generation: number
  resume?: TagsResume | null
}): TagsProjection {
  const rows = acceptTagRows(input.tags)
  const requested = input.resume?.aliasTagId
  const openAliasTagId =
    typeof requested === 'string' && requested && rows.some((row) => row.id === requested)
      ? requested
      : null
  return {
    rows,
    generation: input.generation,
    openAliasTagId,
  }
}
