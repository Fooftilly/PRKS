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
})
