export function fieldValue(id: string): string {
  const el = document.getElementById(id)
  if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return ''
  return String(el.value || '')
}

export function setFieldValue(id: string, value: string): void {
  const el = document.getElementById(id)
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) el.value = value
}

export function messageFrom(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message
  return fallback
}
