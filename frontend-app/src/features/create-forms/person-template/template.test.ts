import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  copyText,
  fillProfileEditor,
  formatPersonTemplate,
  parsePersonTemplate,
  PERSON_TEMPLATE_FIELDS,
  PERSON_TEMPLATE_HELP_TEXT,
  templateFromPerson,
} from './template'

const KEYS = PERSON_TEMPLATE_FIELDS.map((f) => f.key)

function full(over: Record<string, unknown> = {}): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const key of KEYS) out[key] = ''
  return { ...out, ...over }
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()  // The copy tests define these on the instances; drop them so the
  // jsdom originals (none) come back for the next test.
  delete (navigator as unknown as { clipboard?: unknown }).clipboard
  delete (document as unknown as { execCommand?: unknown }).execCommand
})

describe('person template', () => {
  it('builds every key in order, as strings, with display dates', () => {
    const tpl = templateFromPerson(
      { first_name: 'Hannah', last_name: 'Arendt', birth_date: '1906-10-14', about: null },
      (v) => (v ? `shown:${v}` : ''),
    )
    expect(Object.keys(tpl)).toEqual(KEYS)
    expect(tpl.first_name).toBe('Hannah')
    expect(tpl.about).toBe('')
    expect(tpl.birth_date).toBe('shown:1906-10-14')
    expect(tpl.death_date).toBe('')
    expect(JSON.parse(formatPersonTemplate(tpl))).toEqual(tpl)
  })

  it('a missing person gives an empty template', () => {
    expect(Object.values(templateFromPerson(null)).every((v) => v === '')).toBe(true)
  })

  it('parses only the exact keys, all strings', () => {
    expect(parsePersonTemplate(JSON.stringify(full({ last_name: 'Weil' })))).toMatchObject({
      ok: true,
      values: { last_name: 'Weil' },
    })
    expect(parsePersonTemplate('{nope')).toEqual({ ok: false, error: 'Invalid JSON. Paste valid JSON object template.' })
    expect(parsePersonTemplate('[]')).toEqual({ ok: false, error: 'Template must be JSON object.' })
    expect(parsePersonTemplate('null')).toEqual({ ok: false, error: 'Template must be JSON object.' })
    expect(parsePersonTemplate(JSON.stringify(full({ extra: '' })))).toEqual({
      ok: false,
      error: 'Unknown field(s): extra',
    })
    const missing = full()
    delete missing.about
    delete missing.links_other
    expect(parsePersonTemplate(JSON.stringify(missing))).toEqual({
      ok: false,
      error: 'Missing field(s): about, links_other',
    })
    expect(parsePersonTemplate(JSON.stringify(full({ birth_date: 1903 })))).toEqual({
      ok: false,
      error: 'Field "birth_date" must be string.',
    })
  })

  it('fills each editor field with an input event, or nothing when one is missing', () => {
    document.body.innerHTML = PERSON_TEMPLATE_FIELDS.map((f) =>
      f.key === 'about' || f.key === 'links_other' ? `<textarea id="${f.id}"></textarea>` : `<input id="${f.id}">`,
    ).join('')
    const seen: string[] = []
    document.body.addEventListener('input', (e) => seen.push((e.target as HTMLElement).id))
    const values = templateFromPerson({ last_name: 'Weil', about: 'Philosopher.' })
    expect(fillProfileEditor(values)).toEqual({ ok: true })
    expect((document.getElementById('pd-last-name') as HTMLInputElement).value).toBe('Weil')
    expect((document.getElementById('pd-about') as HTMLTextAreaElement).value).toBe('Philosopher.')
    expect(seen).toEqual(PERSON_TEMPLATE_FIELDS.map((f) => f.id))

    document.getElementById('pd-link-iep')!.remove()
    ;(document.getElementById('pd-last-name') as HTMLInputElement).value = 'unchanged'
    expect(fillProfileEditor(values)).toEqual({ ok: false, error: 'Could not find field "link_iep" in edit form.' })
    expect((document.getElementById('pd-last-name') as HTMLInputElement).value).toBe('unchanged')
  })

  it('keeps the field guide rules', () => {
    expect(PERSON_TEMPLATE_HELP_TEXT.startsWith('Fill this template according to the provided rules:\n\n')).toBe(true)
    for (const key of KEYS) expect(PERSON_TEMPLATE_HELP_TEXT).toContain(key)
    expect(PERSON_TEMPLATE_HELP_TEXT).toContain('- Keep all values as JSON strings.')
  })

  it('copies through the clipboard, else the selection fallback', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    expect(await copyText('a')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('a')

    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    const exec = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    expect(await copyText('b')).toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
    exec.mockReturnValue(false)
    expect(await copyText('c')).toBe(false)
  })
})
