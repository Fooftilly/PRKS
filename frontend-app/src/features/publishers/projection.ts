/**
 * Publisher groups for `#/publishers`. Order is the coordinator's
 * `fetchPublishersInUse` order. This projection does not fetch or sort.
 */

export interface PublisherRow {
  readonly id: string
  readonly name: string
  readonly workCount: number
  readonly aliases: readonly string[]
  readonly stats: string
  readonly searchHash: string
  readonly encodedName: string
}

export interface PublishersResume {
  readonly aliasPublisherId?: string | null
}

export interface PublishersProjection {
  readonly rows: readonly PublisherRow[]
  readonly generation: number
  /** Set when a reload must reopen this publisher's alias dialog. */
  readonly openAliasPublisherId: string | null
}

export function publisherStatsLabel(workCount: number, aliasCount: number): string {
  const files = `${workCount} file${workCount === 1 ? '' : 's'}`
  if (!aliasCount) return files
  return `${files} · ${aliasCount} alias${aliasCount === 1 ? '' : 'es'}`
}

export function publisherSearchHash(name: string): string {
  return `#/search?publisher=${encodeURIComponent(name)}`
}

function countOf(value: unknown): number {
  const count = Number(value)
  if (!Number.isFinite(count) || count < 0) return 0
  return count
}

export function acceptPublisherRows(value: unknown): PublisherRow[] {
  if (!Array.isArray(value)) return []
  const rows: PublisherRow[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    const record = row as {
      id?: unknown
      name?: unknown
      work_count?: unknown
      aliases?: unknown
    }
    const id = record.id == null ? '' : String(record.id)
    if (!id) continue
    const name = typeof record.name === 'string' ? record.name : ''
    const aliases = Array.isArray(record.aliases)
      ? record.aliases.filter((alias): alias is string => typeof alias === 'string')
      : []
    const workCount = countOf(record.work_count)
    rows.push({
      id,
      name,
      workCount,
      aliases,
      stats: publisherStatsLabel(workCount, aliases.length),
      searchHash: publisherSearchHash(name),
      encodedName: encodeURIComponent(name),
    })
  }
  return rows
}

export function buildPublishersProjection(input: {
  publishers?: unknown
  generation: number
  resume?: PublishersResume | null
}): PublishersProjection {
  const rows = acceptPublisherRows(input.publishers)
  const requested = input.resume?.aliasPublisherId
  const openAliasPublisherId =
    typeof requested === 'string' && requested && rows.some((row) => row.id === requested)
      ? requested
      : null
  return {
    rows,
    generation: input.generation,
    openAliasPublisherId,
  }
}
