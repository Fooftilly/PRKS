import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { processingItemRow } from '../../api/processing-items.fixtures'
import type { ProcessingItemImported } from '../../api/processing-items'
import ProcessingQueueRoute from './ProcessingQueueRoute.vue'
import { ProcessingQueue, type ProcessingQueueApi } from './queue'

afterEach(() => {
  document.body.innerHTML = ''
})

function deferredImports(): { api: ProcessingQueueApi, finish: () => void } {
  let finish: () => void = () => {}
  const answer = (id: string): ProcessingItemImported => ({ itemId: id, workId: `W-${id}`, created: true })
  const api = {
    list: vi.fn(async () => ({ items: [processingItemRow({ id: 'a', original_filename: 'a.pdf' })], nextCursor: null })),
    upload: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    importOne: vi.fn((id: string) => new Promise<ProcessingItemImported>((resolve) => {
      finish = () => resolve(answer(id))
    })),
    importMany: vi.fn((ids: readonly string[]) => new Promise((resolve) => {
      finish = () => resolve(ids.map((id) => ({
        item_id: id, ok: true, work_id: `W-${id}`, created: true, code: null, message: null, item: null,
      })))
    })),
  } as unknown as ProcessingQueueApi
  return { api, finish: () => finish() }
}

function fields(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('[data-processing-item-id="a"] [data-field]')
}

describe('Processing queue page', () => {
  for (const mode of ['single', 'batch'] as const) {
    it(`locks a draft's fields while its ${mode} import is in flight`, async () => {
      const { api, finish } = deferredImports()
      const wrapper = mount(ProcessingQueueRoute, {
        props: { queue: new ProcessingQueue(api), folders: [], docTypes: [] },
        attachTo: document.body,
      })
      await flushPromises()
      expect(fields(wrapper)).toHaveLength(5)
      expect(fields(wrapper).every((field) => !field.attributes('disabled'))).toBe(true)

      if (mode === 'single') {
        await wrapper.find('[data-processing-item-id="a"] [data-processing-import]').trigger('click')
      } else {
        await wrapper.find('[data-processing-item-id="a"] [data-processing-select]').setValue(true)
        await wrapper.find('[data-processing-import-selected]').trigger('click')
      }
      await flushPromises()
      // Sent, not answered: no edit can be typed that the import would discard.
      expect(fields(wrapper).every((field) => field.attributes('disabled') !== undefined)).toBe(true)

      finish()
      await flushPromises()
      expect(wrapper.find('[data-processing-item-id="a"]').exists()).toBe(false)
      wrapper.unmount()
    })
  }

  it('keeps an unsaved edit when a refresh brings a newer revision of the same item', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [processingItemRow({ id: 'a', revision: 1 })], nextCursor: null })
      .mockResolvedValueOnce({
        items: [processingItemRow({ id: 'a', revision: 2, draft: { ...processingItemRow().draft!, title: 'Saved elsewhere' } })],
        nextCursor: null,
      })
    const update = vi.fn(async () => processingItemRow({ id: 'a', revision: 2 }))
    const api = { list, upload: vi.fn(), update, remove: vi.fn(), importOne: vi.fn(), importMany: vi.fn() } as unknown as ProcessingQueueApi
    const wrapper = mount(ProcessingQueueRoute, {
      props: { queue: new ProcessingQueue(api), folders: [], docTypes: [] },
      attachTo: document.body,
    })
    await flushPromises()
    const title = () => wrapper.find<HTMLInputElement>('[data-processing-item-id="a"] [data-field="title"]')
    await title().setValue('Typed here')

    await wrapper.find('[data-processing-refresh]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(2)
    expect(title().element.value).toBe('Typed here')
    expect(wrapper.find('[data-processing-item-id="a"] [data-processing-save-status]').text()).toContain('Unsaved changes')

    // The save goes at the revision the edit was made on, so the server decides.
    await wrapper.find('[data-processing-item-id="a"] form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('a', 1, expect.objectContaining({ title: 'Typed here' }))
    wrapper.unmount()
  })
})
