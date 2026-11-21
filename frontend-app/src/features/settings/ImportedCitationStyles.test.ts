import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetImportedStylesForTests } from '../citations/styles'
import { settingsActiveCategory } from './navigation'
import ImportedCitationStyles from './ImportedCitationStyles.vue'

const HOUSE = `custom-${'e'.repeat(32)}`
const win = window as Window & { prksConfirmDestructive?: (options: unknown) => Promise<boolean> }

interface Call { method: string; path: string; body: unknown }
let calls: Call[]
let styles: Array<{ id: string; title: string; created_at: string }>
let importAnswer: { status: number; body: unknown }

beforeEach(() => {
  resetImportedStylesForTests()
  settingsActiveCategory.value = 'export'
  calls = []
  styles = [{ id: HOUSE, title: 'House Style', created_at: 'x' }]
  importAnswer = { status: 201, body: { id: `custom-${'f'.repeat(32)}`, title: 'New Style', created_at: 'x' } }
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null })
    if (method === 'POST') {
      if (importAnswer.status === 201) styles = [...styles, importAnswer.body as (typeof styles)[number]]
      return new Response(JSON.stringify(importAnswer.body), { status: importAnswer.status })
    }
    if (method === 'DELETE') {
      styles = styles.filter((style) => !path.endsWith(style.id))
      return new Response(JSON.stringify({ deleted: path.split('/').pop() }))
    }
    return new Response(JSON.stringify({ styles }))
  }))
  win.prksConfirmDestructive = vi.fn(async () => true)
})

afterEach(() => {
  settingsActiveCategory.value = 'general'
  vi.unstubAllGlobals()
  delete win.prksConfirmDestructive
  document.body.innerHTML = ''
})

async function pick(wrapper: ReturnType<typeof mount>, file: File): Promise<void> {
  const input = wrapper.get('[data-prks-role="import-citation-style-file"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

function names(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.prks-setting-row__label').map((row) => row.text())
}

describe('ImportedCitationStyles', () => {
  it('reads the list only once the Export category is shown', async () => {
    settingsActiveCategory.value = 'general'
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    expect(calls).toEqual([])
    settingsActiveCategory.value = 'export'
    await flushPromises()
    expect(names(wrapper)).toEqual(['House Style'])
  })

  it('lists the imported styles and links to where styles come from', async () => {
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    expect(names(wrapper)).toEqual(['House Style'])
    const links = wrapper.findAll('a').map((link) => link.attributes('href'))
    expect(links).toEqual(['https://www.zotero.org/styles', 'https://editor.citationstyles.org/visualEditor/'])
  })

  it('imports a picked file and lists it', async () => {
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    await pick(wrapper, new File(['<style/>'], 'new.csl'))
    expect(calls.find((call) => call.method === 'POST')).toEqual({ method: 'POST', path: '/api/citation-styles', body: { csl: '<style/>' } })
    expect(wrapper.get('[role="status"]').text()).toBe('Imported “New Style”. Choose it above to use it.')
    expect(names(wrapper)).toEqual(['House Style', 'New Style'])
    expect(wrapper.emitted('changed')).toEqual([[null]])
  })

  it('shows why PRKS refused a file, and refuses a file over 1 MB before sending it', async () => {
    importAnswer = { status: 400, body: { error: 'This is a dependent style: it only points to another style. Import that style instead.' } }
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    await pick(wrapper, new File(['<style/>'], 'dependent.csl'))
    expect(wrapper.get('[role="alert"]').text()).toBe('This is a dependent style: it only points to another style. Import that style instead.')
    calls = []
    await pick(wrapper, new File(['x'.repeat(1024 * 1024 + 1)], 'big.csl'))
    expect(wrapper.get('[role="alert"]').text()).toBe('The style file is larger than 1 MB.')
    expect(calls.filter((call) => call.method === 'POST')).toEqual([])
  })

  it('refuses a file that is not UTF-8 before sending it, and sends UTF-8 text as it is', async () => {
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    calls = []
    // "Café" in Latin-1: 0xE9 is not UTF-8.
    await pick(wrapper, new File([new Uint8Array([0x3c, 0x73, 0x3e, 0x43, 0x61, 0x66, 0xe9])], 'latin1.csl'))
    expect(wrapper.get('[role="alert"]').text()).toBe('The style file is not UTF-8 text. Save it as UTF-8 and import it again.')
    expect(calls.filter((call) => call.method === 'POST')).toEqual([])
    await pick(wrapper, new File(['<style>Café</style>'], 'utf8.csl'))
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ csl: '<style>Café</style>' })
  })

  it('deletes a style after the destructive confirm', async () => {
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    await wrapper.get(`[data-prks-style-id="${HOUSE}"] button`).trigger('click')
    await flushPromises()
    expect(win.prksConfirmDestructive).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: 'Delete style' }))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === `/api/citation-styles/${HOUSE}`)).toBe(true)
    expect(names(wrapper)).toEqual([])
    expect(wrapper.emitted('changed')).toEqual([[HOUSE]])
  })

  it('keeps a style when the confirm is cancelled', async () => {
    win.prksConfirmDestructive = vi.fn(async () => false)
    const wrapper = mount(ImportedCitationStyles)
    await flushPromises()
    await wrapper.get(`[data-prks-style-id="${HOUSE}"] button`).trigger('click')
    await flushPromises()
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(names(wrapper)).toEqual(['House Style'])
  })
})
