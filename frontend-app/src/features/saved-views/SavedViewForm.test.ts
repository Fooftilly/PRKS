import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PrksApiError } from '../../api/http'
import SavedViewForm from './SavedViewForm.vue'
import { savedViewActionMessage, type SavedViewRecords } from './records'

interface Harness {
  records: { create: ReturnType<typeof vi.fn>; update: ReturnType<typeof vi.fn> }
  opened: string[]
  navigations: Array<{ hash: string; options?: unknown }>
  closes: number
  alerts: Array<{ title: string; message: string }>
  returnFocus?: HTMLElement | null
}

let h: Harness
let wrapper: { unmount: () => void } | null = null

function shell(): HTMLElement {
  return document.getElementById('saved-view-modal') as HTMLElement
}

function input(id: string): HTMLInputElement {
  return document.getElementById(id) as HTMLInputElement
}

function visible(id: string): boolean {
  return (document.getElementById(id) as HTMLElement).style.display !== 'none'
}

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function type(id: string, value: string): Promise<void> {
  const el = input(id)
  el.value = value
  el.dispatchEvent(new Event('input'))
  await nextTick()
}

async function clickSave(): Promise<void> {
  ;(document.getElementById('save-saved-view-btn') as HTMLButtonElement).click()
  await flush()
}

beforeEach(() => {
  document.body.innerHTML =
    '<div id="saved-view-modal" class="modal hidden">' +
    '<div id="saved-view-modal-vue" class="modal-body"></div></div><div id="app"></div>'
  h = {
    records: { create: vi.fn(async () => ({ id: 'SV-NEW' })), update: vi.fn(async () => ({})) },
    opened: [],
    navigations: [],
    closes: 0,
    alerts: [],
  }
  window.prksSavedViewRecords = {
    ...h.records,
    actionMessage: savedViewActionMessage,
  } as unknown as SavedViewRecords
  window.openModal = (id: string, options?: { returnFocus?: HTMLElement | null }) => {
    h.opened.push(id)
    h.returnFocus = options?.returnFocus ?? null
    document.getElementById(id)?.classList.remove('hidden')
  }
  window.requestModalClose = () => {
    h.closes += 1
    shell().classList.add('hidden')
    return true
  }
  window.prksNavigate = (hash: string, options?: unknown) => {
    h.navigations.push({ hash, options })
  }
  window.prksAlertDialog = async (opts) => {
    h.alerts.push(opts)
  }
  // Mounted inside a dialog body, as ModalDialog renders it.
  wrapper = mount(SavedViewForm, { attachTo: '#saved-view-modal-vue' })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  delete window.prksSavedViewRecords
  delete window.openModal
  delete window.requestModalClose
  delete window.prksNavigate
  delete window.prksAlertDialog
  delete window.prksOfflineGuardMutation
  delete (window as unknown as { prksGetMainTabContext?: unknown }).prksGetMainTabContext
  window.location.hash = ''
})

describe('Saved View form', () => {
  it('installs the openers and removes them on unmount', () => {
    expect(typeof window.prksOpenSavedViewModal).toBe('function')
    expect(typeof window.prksOpenSavedViewModalFromCurrentSearch).toBe('function')
    expect(typeof window.prksOpenSavedViewModalForCurrentView).toBe('function')
    wrapper?.unmount()
    wrapper = null
    expect(window.prksOpenSavedViewModal).toBeUndefined()
    expect(window.prksOpenSavedViewModalFromCurrentSearch).toBeUndefined()
    expect(window.prksOpenSavedViewModalForCurrentView).toBeUndefined()
  })

  it('opens from the Search owner hash and creates the view', async () => {
    window.location.hash = '#/folders'
    window.prksOpenSavedViewModalFromCurrentSearch?.('#/search?q=critical%20theory&author=Adorno')
    await flush()
    expect(h.opened).toEqual(['saved-view-modal'])
    expect((wrapper as unknown as { vm?: { title?: string } } | undefined)?.vm?.title).toBe('Save View')
    expect(input('saved-view-q').value).toBe('critical theory')
    expect(input('saved-view-author').value).toBe('Adorno')
    expect(document.getElementById('saved-view-q-label')?.textContent?.trim()).toBe('Keywords')
    await type('saved-view-name', 'Adorno — Culture Industry')
    await clickSave()
    expect(h.records.create).toHaveBeenCalledWith({
      name: 'Adorno — Culture Industry',
      search: { mode: 'advanced', q: 'critical theory', tag: '', author: 'Adorno', publisher: '' },
    })
    expect(h.closes).toBe(1)
    expect(h.navigations).toEqual([{ hash: '#/views/SV-NEW', options: undefined }])
  })

  it('alerts instead of opening when the search cannot be saved', async () => {
    window.prksOpenSavedViewModalFromCurrentSearch?.('#/search?q=x&tag=y')
    await flush()
    expect(h.opened).toEqual([])
    expect(h.alerts).toEqual([
      { title: 'Cannot save view', message: 'This search combination cannot be saved as a view.' },
    ])
  })

  it('keeps the dialog closed offline', async () => {
    window.prksOfflineGuardMutation = () => true
    window.prksOpenSavedViewModal?.({ name: 'Offline' })
    await flush()
    expect(h.opened).toEqual([])
  })

  it('requires a name and sends nothing', async () => {
    window.prksOpenSavedViewModal?.()
    await flush()
    await clickSave()
    expect(document.getElementById('saved-view-name-error')?.textContent).toBe('Name is required.')
    expect(input('saved-view-name').getAttribute('aria-invalid')).toBe('true')
    expect(document.activeElement?.id).toBe('saved-view-name')
    expect(h.records.create).not.toHaveBeenCalled()
  })

  it('submits on Enter in the name field', async () => {
    window.prksOpenSavedViewModal?.({ definition: { mode: 'all', q: 'x' } })
    await flush()
    await type('saved-view-name', 'Enter view')
    input('saved-view-name').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await flush()
    expect(h.records.create).toHaveBeenCalledTimes(1)
  })

  it('shows and hides fields by mode', async () => {
    window.prksOpenSavedViewModal?.({ definition: { mode: 'all', q: 'x' } })
    await flush()
    expect(visible('saved-view-field-q-wrap')).toBe(true)
    expect(visible('saved-view-field-tag-wrap')).toBe(false)
    expect(visible('saved-view-field-author-wrap')).toBe(false)
    expect(document.getElementById('saved-view-q-label')?.textContent?.trim()).toBe('All fields')
    const tag = document.querySelector<HTMLInputElement>('input[name="saved-view-mode"][value="tag"]')!
    tag.checked = true
    tag.dispatchEvent(new Event('change'))
    await nextTick()
    expect(visible('saved-view-field-q-wrap')).toBe(false)
    expect(visible('saved-view-field-tag-wrap')).toBe(true)
    expect(visible('saved-view-field-author-wrap')).toBe(true)
    expect(visible('saved-view-field-publisher-wrap')).toBe(true)
  })

  it('edits the current Main view and refreshes in place', async () => {
    ;(window as unknown as { prksGetMainTabContext: () => unknown }).prksGetMainTabContext = () => ({
      getEntity: (type: string) =>
        type === 'savedView'
          ? { id: 'SV-MAIN', name: 'Main View', search: { mode: 'tag', q: '', tag: 'T', author: '', publisher: '' } }
          : null,
    })
    window.location.hash = '#/views/SV-MAIN'
    window.prksOpenSavedViewModalForCurrentView?.()
    await flush()
    expect((wrapper as unknown as { vm?: { title?: string } } | undefined)?.vm?.title).toBe('Edit Saved View')
    expect(document.getElementById('save-saved-view-btn')?.textContent?.trim()).toBe('Save changes')
    expect(input('saved-view-name').value).toBe('Main View')
    expect(input('saved-view-tag').value).toBe('T')
    await clickSave()
    expect(h.records.update).toHaveBeenCalledWith('SV-MAIN', {
      name: 'Main View',
      search: { mode: 'tag', q: '', tag: 'T', author: '', publisher: '' },
    })
    expect(h.closes).toBe(1)
    expect(h.navigations).toEqual([{ hash: '#/views/SV-MAIN', options: { replace: true } }])
  })

  it('hands the shell the opener that lost focus while it loaded', async () => {
    const opener = document.createElement('button')
    window.prksOpenSavedViewModal?.({ viewId: 'SV-1', name: 'X', returnFocus: opener })
    await flush()
    expect(h.returnFocus).toBe(opener)
    window.prksOpenSavedViewModal?.({ name: 'Y' })
    await flush()
    expect(h.returnFocus).toBeNull()
  })

  it('ignores Edit this Saved View with no record', async () => {
    ;(window as unknown as { prksGetMainTabContext: () => unknown }).prksGetMainTabContext = () => ({
      getEntity: () => null,
    })
    window.prksOpenSavedViewModalForCurrentView?.()
    await flush()
    expect(h.opened).toEqual([])
  })

  it('keeps the dialog open with the server refusal', async () => {
    h.records.create.mockRejectedValueOnce(
      new PrksApiError('A Saved View with that name already exists.', 409, 'conflict'),
    )
    window.prksOpenSavedViewModal?.({ name: 'Taken', definition: { mode: 'all', q: 'x' } })
    await flush()
    await clickSave()
    expect(h.closes).toBe(0)
    const error = document.getElementById('saved-view-modal-error') as HTMLElement
    expect(error.textContent).toBe('A Saved View with that name already exists.')
    expect(error.classList.contains('hidden')).toBe(false)
    expect((document.getElementById('save-saved-view-btn') as HTMLButtonElement).disabled).toBe(false)
  })

  it('does not close or navigate after the dialog was dismissed', async () => {
    let resolve: (value: { id: string }) => void = () => {}
    h.records.create.mockImplementationOnce(() => new Promise((r) => (resolve = r)))
    window.prksOpenSavedViewModal?.({ name: 'Late', definition: { mode: 'all', q: 'x' } })
    await flush()
    await clickSave()
    shell().classList.add('hidden')
    resolve({ id: 'SV-LATE' })
    await flush()
    expect(h.closes).toBe(0)
    expect(h.navigations).toEqual([])
  })

  it('a newer opening starts clean and drops the older save', async () => {
    let resolve: (value: { id: string }) => void = () => {}
    h.records.create.mockImplementationOnce(() => new Promise((r) => (resolve = r)))
    window.prksOpenSavedViewModal?.({ name: 'First', definition: { mode: 'all', q: 'x' } })
    await flush()
    await clickSave()
    window.prksOpenSavedViewModal?.({ viewId: 'SV-2', name: 'Second' })
    await flush()
    expect(input('saved-view-name').value).toBe('Second')
    expect((document.getElementById('save-saved-view-btn') as HTMLButtonElement).disabled).toBe(false)
    resolve({ id: 'SV-FIRST' })
    await flush()
    expect(h.closes).toBe(0)
    expect(h.navigations).toEqual([])
  })
})
