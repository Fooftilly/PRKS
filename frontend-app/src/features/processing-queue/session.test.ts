import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { processingItemRow } from '../../api/processing-items.fixtures'
import { resetRouteSurfaceForTests } from '../../route-surface/lifecycle'
import type { ProcessingQueueApi } from './queue'
import { docTypeOptions, folderOptions, presentProcessingQueue } from './session'

afterEach(() => {
  resetRouteSurfaceForTests()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

async function flush(): Promise<void> {
  for (let i = 0; i < 3; i += 1) {
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}

function host(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

function api(overrides: Partial<ProcessingQueueApi> = {}): ProcessingQueueApi {
  return {
    list: vi.fn(async () => ({ items: [processingItemRow()], nextCursor: null })),
    upload: vi.fn(),
    update: vi.fn(async (_id: string, revision: number, edit: Record<string, string>) =>
      processingItemRow({ revision: revision + 1, draft: { ...processingItemRow().draft!, ...edit } })),
    remove: vi.fn(),
    importOne: vi.fn(async () => ({ itemId: 'item-1', workId: 'W-7', created: true })),
    importMany: vi.fn(),
    ...overrides,
  } as ProcessingQueueApi
}

describe('Processing queue page', () => {
  it('reads the queue, saves a draft, then imports and links the Work', async () => {
    const badge = vi.fn()
    vi.stubGlobal('prksSetProcessingAttentionCount', badge)
    const fake = api()
    const el = host()
    presentProcessingQueue({
      owner: { isCurrent: () => true },
      host: el,
      folders: [{ id: 'F-1', title: 'Reading' }],
      docTypes: [{ value: 'article', label: 'Article' }],
      generation: 1,
      api: fake,
    })
    await flush()
    const card = el.querySelector<HTMLElement>('[data-processing-item-id="item-1"]')
    expect(card?.textContent).toContain('paper.pdf')
    expect(badge).toHaveBeenLastCalledWith(1)

    const title = card!.querySelector<HTMLInputElement>('[data-field="title"]')!
    title.value = 'A Paper'
    title.dispatchEvent(new Event('input'))
    const folder = card!.querySelector<HTMLSelectElement>('[data-field="target_folder_id"]')!
    folder.value = 'F-1'
    folder.dispatchEvent(new Event('change'))
    await nextTick()
    expect(card!.querySelector<HTMLButtonElement>('[data-processing-import]')!.disabled).toBe(true)
    expect(card!.querySelector('[data-processing-save-status]')?.textContent).toContain('Unsaved changes')
    card!.querySelector<HTMLFormElement>('form')!.dispatchEvent(new Event('submit'))
    await flush()
    expect(fake.update).toHaveBeenCalledWith('item-1', 1, expect.objectContaining({ title: 'A Paper', target_folder_id: 'F-1' }))
    expect(card!.querySelector('[data-processing-save-status]')?.textContent).toContain('Draft saved.')

    card!.querySelector<HTMLButtonElement>('[data-processing-import]')!.click()
    await flush()
    expect(fake.importOne).toHaveBeenCalledWith('item-1')
    expect(el.querySelector('[data-processing-item-id]')).toBeNull()
    expect(el.querySelector('[data-processing-imported] a')?.getAttribute('href')).toBe('#/works/W-7')
    expect(badge).toHaveBeenLastCalledWith(0)
  })

  it('previews the uploaded PDF in the PRKS viewer and releases it on hide', async () => {
    const destroy = vi.fn()
    const mount = vi.fn(async () => ({ destroy }))
    vi.stubGlobal('prksMountPdfPreview', mount)
    const el = host()
    presentProcessingQueue({ owner: {}, host: el, folders: [], docTypes: [], api: api() })
    await flush()
    el.querySelector<HTMLButtonElement>('[data-processing-preview]')!.click()
    await flush()
    expect(mount).toHaveBeenCalledWith(el.querySelector('[data-processing-preview-host]'), '/api/processing-items/item-1/pdf', 'paper.pdf')
    el.querySelector<HTMLButtonElement>('[data-processing-preview]')!.click()
    await flush()
    expect(destroy).toHaveBeenCalledTimes(1)
    expect(el.querySelector('[data-processing-preview-host]')).toBeNull()
  })

  it('says so when the viewer is missing', async () => {
    const el = host()
    presentProcessingQueue({ owner: {}, host: el, folders: [], docTypes: [], api: api() })
    await flush()
    el.querySelector<HTMLButtonElement>('[data-processing-preview]')!.click()
    await flush()
    expect(el.querySelector('[data-processing-preview-error]')?.textContent).toContain('not available')
  })

  it('uploads dropped files', async () => {
    const upload = vi.fn(async () => ({ item: processingItemRow({ id: 'item-2', original_filename: 'drop.pdf' }), created: true,
      duplicates: { items: [], works: [], more_items: false, more_works: false } }))
    const el = host()
    presentProcessingQueue({ owner: {}, host: el, folders: [], docTypes: [], api: api({ upload }) })
    await flush()
    const file = new File(['%PDF'], 'drop.pdf', { type: 'application/pdf' })
    const drop = new Event('drop', { bubbles: true, cancelable: true }) as DragEvent
    Object.defineProperty(drop, 'dataTransfer', { value: { files: [file], types: ['Files'] } })
    el.querySelector('[data-processing-drop]')!.dispatchEvent(drop)
    await flush()
    expect(upload).toHaveBeenCalledWith(file, expect.any(String), expect.any(Function))
    expect(el.querySelector('[data-processing-item-id="item-2"]')).not.toBeNull()
  })

  it('does not move the badge for a page its owner no longer shows', async () => {
    const badge = vi.fn()
    vi.stubGlobal('prksSetProcessingAttentionCount', badge)
    presentProcessingQueue({ owner: { isCurrent: () => false }, host: host(), folders: [], docTypes: [], generation: 1, api: api() })
    await flush()
    expect(badge).not.toHaveBeenCalled()
  })

  it('normalizes folders and document types', () => {
    expect(folderOptions([{ id: 'b', title: 'Beta' }, { id: 'a', title: 'Alpha' }, { title: 'no id' }, null]))
      .toEqual([{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }])
    expect(docTypeOptions([{ value: 'book', label: 'Book' }, { label: 'x' }])).toEqual([{ value: 'book', label: 'Book' }])
  })
})
