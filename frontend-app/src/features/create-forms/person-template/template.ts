/**
 * Edit Profile Using Template (#1036).
 *
 * The template is the open person's profile fields as strict JSON: exact
 * keys, string values only. Applying it opens that person's profile editor
 * and fills its fields; nothing is saved until the editor's own Save, so
 * this dialog performs no durable write of its own.
 */

/** Template keys in order, with the profile editor field each one fills. */
export const PERSON_TEMPLATE_FIELDS = [
  { key: 'first_name', id: 'pd-first-name' },
  { key: 'last_name', id: 'pd-last-name' },
  { key: 'aliases', id: 'pd-aliases' },
  { key: 'about', id: 'pd-about' },
  { key: 'birth_date', id: 'pd-birth-date' },
  { key: 'death_date', id: 'pd-death-date' },
  { key: 'image_url', id: 'pd-image-url' },
  { key: 'link_wikipedia', id: 'pd-link-wikipedia' },
  { key: 'link_stanford_encyclopedia', id: 'pd-link-stanford' },
  { key: 'link_iep', id: 'pd-link-iep' },
  { key: 'links_other', id: 'pd-links-other' },
] as const

export type PersonTemplateKey = (typeof PERSON_TEMPLATE_FIELDS)[number]['key']
export type PersonTemplateValues = Record<PersonTemplateKey, string>

/** The field guide as plain text, for the copy buttons. The dialog shows the same rules as a list. */
export const PERSON_TEMPLATE_HELP_TEXT = [

  'Fill this template according to the provided rules:',
  '',
  '- first_name, last_name, aliases: plain text strings. Middle names should be placed in first_name field.',
  '- about: MUST review and update when filling this template; DO NOT leave unchanged or empty if you are enriching other fields and reliable sources exist. Short encyclopedic biography for person cards and search (About / expertise). Include who they are, time period, field/domain, 2-4 main ideas/works/contributions, and why they are notable. DO NOT put birth year, death year, or lifespan dates in about; use birth_date and death_date only. Neutral third-person prose, complete sentences, plain text only (JSON string; optional blank line between paragraphs as \\n\\n). No Markdown, bullet lists, or URLs (use link_* and links_other). Target ~80-500 words (~400-2500 characters); avoid one-liners and essay-length dumps. Synthesize from Wikipedia, SEP, IEP, or provided links; do not invent facts. If existing text is present, improve or expand it rather than skipping this field.',
  '- birth_date, death_date: plain date strings only — never prose, never "BC", "BCE", or "AD" suffixes. Allowed: (1) AD year-only positive yyyy, e.g. "1903". (2) BC year-only: leading minus + BC year digits, e.g. source says 428 BC → "-428" (same for 384 BC → "-384"). (3) Full date dd/mm/yyyy; BC uses negative year in last segment, e.g. 15 March 384 BC → "15/03/-384"; AD e.g. "20/02/2001". Wrong: "384 BC", "428 BCE", "-384 BC", "born 428 BC". Use day and month only if source states them; else year-only. Unknown date → "".',
  '- image_url: direct public HTTP/HTTPS portrait URL only (https preferred). Static JPEG/PNG/WebP/GIF raster only. The URL itself must return HTTP 200 with the image; PRKS does not follow redirects. Private/local network URLs are not accepted. MUST verify the link works before filling (use GET or HEAD, or open in browser). If 404, 403, login wall, HTML error page, redirect, or broken hotlink, set image_url to "". Prefer current stable upload.wikimedia.org (or equivalent direct file) URLs taken from the person\'s verified Wikimedia/Wikipedia file page — not guessed filenames, not old revision URLs, not search-result or temporary CDN/signed links. Do not invent URLs from memory. If no working portrait URL is confirmed, leave empty.',
  '- link_wikipedia, link_stanford_encyclopedia, link_iep: bare URL string only (starts with https://). Wrong: [Title](https://...), Markdown, or labels — [Title](url) format is ONLY for links_other. One direct entry URL per field; prefer https; not homepage/search URL. Do not repeat in links_other. Example link_wikipedia: "https://en.wikipedia.org/wiki/Example_Name".',
  "- links_other: one entry per line inside single JSON string. MUST USE [Title](https://...) Markdown link format per line (this is the only field that uses brackets). Only additional references not already in link_wikipedia, link_stanford_encyclopedia, or link_iep. DO NOT duplicate Wikipedia, Stanford Encyclopedia of Philosophy, or IEP links here. DO NOT add free-text notes, DOI links, paper links, or links by this person unless biographical.",
  '- empty fields should stay empty strings; do not fill placeholders such as N/A, unknown, none, -, or TBD.',
  '- aliases: free text; comma-separated aliases recommended. Include Serbian Latin (not Cyrillic) variation if it exists. Include common spelling/transliteration variants and first+last version when middle name exists. Do not include titles/honorifics (Dr., Prof., Sir) as aliases.',
  '- Keep exact keys; do not add/remove keys.',
  '- Keep all values as JSON strings.',
].join('\n')

export type PersonTemplateParse =
  | { ok: true; values: PersonTemplateValues }
  | { ok: false; error: string }

/** The template for a person record. Dates use the profile editor's display format. */
export function templateFromPerson(
  person: Partial<Record<PersonTemplateKey, unknown>> | null | undefined,
  displayDate: (value: string) => string = (value) => value,
): PersonTemplateValues {
  const p = person || {}
  const text = (key: PersonTemplateKey) => String(p[key] ?? '')
  const out = {} as PersonTemplateValues
  for (const { key } of PERSON_TEMPLATE_FIELDS) out[key] = text(key)
  out.birth_date = String(displayDate(out.birth_date) ?? '')
  out.death_date = String(displayDate(out.death_date) ?? '')
  return out
}

export function formatPersonTemplate(values: PersonTemplateValues): string {
  return JSON.stringify(values, null, 2)
}

/** Strict parse: a JSON object with exactly the template keys, all strings. */
export function parsePersonTemplate(raw: string): PersonTemplateParse {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, error: 'Invalid JSON. Paste valid JSON object template.' }
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    return { ok: false, error: 'Template must be JSON object.' }
  }
  const record = parsed as Record<string, unknown>
  const allowed = new Set<string>(PERSON_TEMPLATE_FIELDS.map((f) => f.key))
  const unknown = Object.keys(record).filter((k) => !allowed.has(k))
  if (unknown.length) return { ok: false, error: `Unknown field(s): ${unknown.join(', ')}` }
  const missing = PERSON_TEMPLATE_FIELDS.map((f) => f.key).filter(
    (k) => !Object.prototype.hasOwnProperty.call(record, k),
  )
  if (missing.length) return { ok: false, error: `Missing field(s): ${missing.join(', ')}` }
  const values = {} as PersonTemplateValues
  for (const { key } of PERSON_TEMPLATE_FIELDS) {
    const value = record[key]
    if (typeof value !== 'string') return { ok: false, error: `Field "${key}" must be string.` }
    values[key] = value
  }
  return { ok: true, values }
}

/**
 * Fills the profile editor's fields. Each field takes its value and an
 * input event, so the editor's own draft (v-model) records it. Nothing is
 * written when a field is missing.
 */
export function fillProfileEditor(
  values: PersonTemplateValues,
  root: Pick<Document, 'getElementById'> = document,
): { ok: true } | { ok: false; error: string } {
  const targets: Array<[HTMLInputElement | HTMLTextAreaElement, string]> = []
  for (const { key, id } of PERSON_TEMPLATE_FIELDS) {
    const el = root.getElementById(id)
    if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) {
      return { ok: false, error: `Could not find field "${key}" in edit form.` }
    }
    targets.push([el, values[key]])
  }
  for (const [el, value] of targets) {
    el.value = value
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }
  return { ok: true }
}

/** Clipboard write with the hidden-textarea fallback the classic dialog used. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall through to the selection copy.
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.left = '-9999px'
  document.body.appendChild(ta)
  const restore = document.activeElement instanceof HTMLElement ? document.activeElement : null
  ta.focus()
  ta.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  } finally {
    ta.remove()
    restore?.focus()
  }
  return ok
}
