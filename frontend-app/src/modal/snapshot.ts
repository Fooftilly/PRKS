/**
 * Same field snapshot as `prksSerializeModalFormState` in `ui.js`, which
 * keeps its copy for the classic Settings shell. This one is the dirty
 * baseline of the dialogs ModalHost renders (`dialogs.ts`).
 */
export function snapshotModalFields(modal: ParentNode): string {
  const fields = modal.querySelectorAll('input, textarea, select')
  const parts: Array<[string, string]> = []
  fields.forEach((node, idx) => {
    const el = node as HTMLInputElement
    const tag = (el.tagName || '').toLowerCase()
    const type = (el.type || '').toLowerCase()
    const key = `${tag}:${type}:${el.id || (el as HTMLInputElement).name || idx}`
    if (tag === 'select') {
      const select = node as HTMLSelectElement
      const opts = Array.from(select.options || []).map((opt) => `${opt.value}:${opt.selected ? '1' : '0'}`)
      parts.push([key, opts.join('|')])
      return
    }
    if (type === 'checkbox' || type === 'radio') {
      parts.push([key, el.checked ? '1' : '0'])
      return
    }
    if (type === 'file') {
      const files = Array.from(el.files || []).map((file) => `${file.name}:${file.size}:${file.lastModified}`)
      parts.push([key, files.join('|')])
      return
    }
    parts.push([key, String(el.value || '')])
  })
  return JSON.stringify(parts)
}
