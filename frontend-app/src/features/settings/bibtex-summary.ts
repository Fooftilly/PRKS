/** One count, taken from the same flags the export toggles render as aria-checked. */

export function bibtexExportSummary(included: readonly boolean[]): string {
  const total = included.length
  let on = 0
  for (const value of included) {
    if (value) on += 1
  }
  return on + ' of ' + total + ' fields included'
}
