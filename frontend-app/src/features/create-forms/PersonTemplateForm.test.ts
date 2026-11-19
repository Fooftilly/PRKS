import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PersonTemplateForm from './PersonTemplateForm.vue'
import { PERSON_TEMPLATE_FIELDS, PERSON_TEMPLATE_HELP_TEXT } from './person-template/template'

interface StubWindow {
  openModal?: (id: string) => void
  closeModals?: () => void
  prksFocusedEntity?: (type: string) => unknown
  personDateToDisplayFormat?: (value: string) => string
  openPersonProfileEdit?: () => unknown
  prksAutosizeTextarea?: (el: HTMLTextAreaElement) => void
  openPersonProfileTemplateModal?: () => void
}

const KEYS: Array<keyof StubWindow> = [
  'openModal',
  'closeModals',
  'prksFocusedEntity',
  'personDateToDisplayFormat',
  'openPersonProfileEdit',
  'prksAutosizeTextarea',
  'openPersonProfileTemplateModal',
]

function stubs(): StubWindow {
  return window as unknown as StubWindow
}

const SIMONE = { id: 'P1', first_name: 'Simone', last_name: 'Weil', birth_date: '1909-02-03' }

let wrapper: ReturnType<typeof mount> | null = null
let person: Record<string, unknown> | null = SIMONE

function area(): HTMLTextAreaElement {
  return document.getElementById('person-template-json') as HTMLTextAreaElement
}

function feedback(): HTMLElement {
  return document.getElementById('person-template-feedback') as HTMLElement
}

function button(label: string): HTMLButtonElement {
  return [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label) as HTMLButtonElement
}

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

function editorHtml(): string {
  return PERSON_TEMPLATE_FIELDS.map((f) =>
    f.key === 'about' || f.key === 'links_other' ? `<textarea id="${f.id}"></textarea>` : `<input id="${f.id}">`,
  ).join('')
}

async function typeTemplate(value: string): Promise<void> {
  area().value = value
  area().dispatchEvent(new Event('input'))
  await nextTick()
}

beforeEach(() => {
  document.body.innerHTML =
    '<div id="person-template-modal" class="modal"><div id="person-template-modal-vue"></div></div><div id="editor"></div>'
  person = SIMONE
  const w = stubs()
  w.openModal = vi.fn()
  w.closeModals = vi.fn()
  w.prksFocusedEntity = (type) => (type === 'person' ? person : null)
  w.personDateToDisplayFormat = (v) => (v === '1909-02-03' ? '03/02/1909' : v)
  w.prksAutosizeTextarea = vi.fn()
  wrapper = mount(PersonTemplateForm, { attachTo: '#person-template-modal-vue' })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  for (const key of KEYS) delete stubs()[key]
  vi.useRealTimers()  // The copy tests define these on the instances; drop them so the
  // jsdom originals (none) come back for the next test.
  delete (navigator as unknown as { clipboard?: unknown }).clipboard
  delete (document as unknown as { execCommand?: unknown }).execCommand
})

describe('Edit Profile Using Template form', () => {
  it('installs the opener, which fills the template from the focused person and opens the shell', async () => {
    stubs().openPersonProfileTemplateModal?.()
    await nextTick()
    const tpl = JSON.parse(area().value)
    expect(tpl.last_name).toBe('Weil')
    expect(tpl.birth_date).toBe('03/02/1909')
    expect(Object.keys(tpl)).toEqual(PERSON_TEMPLATE_FIELDS.map((f) => f.key))
    expect(stubs().openModal).toHaveBeenCalledWith('person-template-modal')
    expect(feedback().textContent).toBe('')
  })

  it('removes the opener when it unmounts', () => {
    expect(typeof stubs().openPersonProfileTemplateModal).toBe('function')
    wrapper!.unmount()
    wrapper = null
    expect(stubs().openPersonProfileTemplateModal).toBeUndefined()
  })

  it('reloads from the profile and says so', async () => {
    stubs().openPersonProfileTemplateModal?.()
    await typeTemplate('scratch')
    person = { ...SIMONE, aliases: 'S. Weil' }
    button('Reload template from profile').click()
    await nextTick()
    expect(JSON.parse(area().value).aliases).toBe('S. Weil')
    expect(feedback().textContent).toBe('Template loaded from current profile values.')
    expect(feedback().classList.contains('prks-inline-message--error')).toBe(false)
  })

  it('shows a parse error and leaves the editor alone', async () => {
    const open = vi.fn()
    stubs().openPersonProfileEdit = open
    stubs().openPersonProfileTemplateModal?.()
    await typeTemplate('{"first_name": "x"}')
    button('Apply template').click()
    await flush()
    expect(feedback().textContent).toContain('Missing field(s)')
    expect(feedback().classList.contains('prks-inline-message--error')).toBe(true)
    expect(feedback().getAttribute('role')).toBe('alert')
    expect(open).not.toHaveBeenCalled()
    expect(stubs().closeModals).not.toHaveBeenCalled()
  })

  it('opens the profile editor, fills it once rendered, then closes', async () => {
    const seen: Record<string, string> = {}
    stubs().openPersonProfileEdit = vi.fn(() => {
      // The Vue Person route renders the editor on its next tick.
      void nextTick(() => {
        document.getElementById('editor')!.innerHTML = editorHtml()
        document.getElementById('editor')!.addEventListener('input', (e) => {
          const t = e.target as HTMLInputElement
          seen[t.id] = t.value
        })
      })
    })
    stubs().openPersonProfileTemplateModal?.()
    await nextTick()
    const tpl = JSON.parse(area().value)
    await typeTemplate(JSON.stringify({ ...tpl, about: 'French philosopher.' }))
    button('Apply template').click()
    await flush()
    expect(seen['pd-about']).toBe('French philosopher.')
    expect(seen['pd-last-name']).toBe('Weil')
    expect(stubs().closeModals).toHaveBeenCalledTimes(1)
  })

  it('says which field the editor lacks and stays open', async () => {
    stubs().openPersonProfileEdit = vi.fn()
    stubs().openPersonProfileTemplateModal?.()
    button('Apply template').click()
    await flush()
    expect(feedback().textContent).toBe('Could not find field "first_name" in edit form.')
    expect(stubs().closeModals).not.toHaveBeenCalled()
  })

  it('copies the guide, then the template with it, and flashes the pressed button', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    stubs().openPersonProfileTemplateModal?.()
    await nextTick()
    const guide = document.querySelector<HTMLButtonElement>('[data-prks-copy="guide"]')!
    const both = document.querySelector<HTMLButtonElement>('[data-prks-copy="both"]')!
    expect(guide.getAttribute('aria-label')).toBe('Copy template field guide')
    guide.click()
    await flush()
    expect(writeText).toHaveBeenLastCalledWith(PERSON_TEMPLATE_HELP_TEXT)
    expect(feedback().textContent).toBe('Template field guide copied.')
    expect(guide.getAttribute('aria-label')).toBe('Copied')
    expect(guide.classList.contains('inline-action-btn--copied')).toBe(true)
    both.click()
    await flush()
    expect(writeText).toHaveBeenLastCalledWith(`${area().value.trim()}\n\n${PERSON_TEMPLATE_HELP_TEXT}`)
    expect(feedback().textContent).toBe('Template + field guide copied.')
    expect(guide.getAttribute('aria-label')).toBe('Copy template field guide')
    vi.advanceTimersByTime(1500)
    await nextTick()
    expect(both.getAttribute('aria-label')).toBe('Copy template and field guide')
    expect(both.classList.contains('inline-action-btn--copied')).toBe(false)
  })

  it('reports a failed copy', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    Object.defineProperty(document, 'execCommand', { value: () => false, configurable: true })
    stubs().openPersonProfileTemplateModal?.()
    document.querySelector<HTMLButtonElement>('[data-prks-copy="guide"]')!.click()
    await flush()
    expect(feedback().textContent).toBe('Could not copy guide. Copy manually.')
    expect(feedback().classList.contains('prks-inline-message--error')).toBe(true)
    expect(document.querySelector('[data-prks-copy="guide"]')!.getAttribute('aria-label')).toBe('Copy failed')
  })

  it('focuses the template field', () => {
    ;(wrapper!.vm as unknown as { focusInitial: () => void }).focusInitial()
    expect(document.activeElement).toBe(area())
  })
})
