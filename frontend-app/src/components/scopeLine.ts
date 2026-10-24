export type ScopeLineOptions = {
  shown?: number | null
  total?: number | null
  filter?: string | null
  label?: string | null
  unavailable?: boolean
  unavailableText?: string
}

/** Collection scope wording ("12 of 48 matching", "128 People"). */
export function scopeLineParts(options: ScopeLineOptions): string[] {
  const parts: string[] = []
  const label = options.label != null ? String(options.label).trim() : ''
  const totalKnown = options.total != null && Number.isFinite(Number(options.total))
  const shownKnown = options.shown != null && Number.isFinite(Number(options.shown))
  const filterOn = !!(options.filter && String(options.filter).trim())

  if (shownKnown && totalKnown && filterOn) {
    parts.push(`${Number(options.shown)} of ${Number(options.total)} matching`)
  } else if (totalKnown) {
    const total = Number(options.total)
    parts.push(label ? `${total} ${label}` : String(total))
  } else if (shownKnown && filterOn) {
    parts.push(`${Number(options.shown)} matching`)
  } else if (options.unavailable) {
    parts.push(options.unavailableText || 'Not available offline')
  }

  return parts
}
