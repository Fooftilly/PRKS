import type { PublisherInUse } from '../../api/publishers'

/**
 * Publisher rows for `#/publishers`. Order is the server's (name,
 * case-insensitive). The typed client has already checked the wire shape.
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

export function publisherStatsLabel(workCount: number, aliasCount: number): string {
  const files = `${workCount} file${workCount === 1 ? '' : 's'}`
  if (!aliasCount) return files
  return `${files} · ${aliasCount} alias${aliasCount === 1 ? '' : 'es'}`
}

export function publisherSearchHash(name: string): string {
  return `#/search?publisher=${encodeURIComponent(name)}`
}

export function publisherRows(publishers: readonly PublisherInUse[]): PublisherRow[] {
  return publishers.map((publisher) => ({
    id: publisher.id,
    name: publisher.name,
    workCount: publisher.work_count,
    aliases: publisher.aliases,
    stats: publisherStatsLabel(publisher.work_count, publisher.aliases.length),
    searchHash: publisherSearchHash(publisher.name),
    encodedName: encodeURIComponent(publisher.name),
  }))
}
