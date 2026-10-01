import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import WorkPdfAnnotationDrawer from './WorkPdfAnnotationDrawer.vue'

const state = {
  open: true,
  epoch: 1,
  viewerToken: 4,
  selectedId: 'ann-a',
  status: '1 annotation',
  published: true,
  generation: 3,
  items: [
    {
      id: 'ann-a',
      index: 0,
      text: 'Highlight',
      comment: 'note',
      pageLabel: 'Page 1',
      pageIndex: 0,
      wikiLink: '[[pdf:ann-a|Highlight]]',
      metadataLabels: ['Topic'],
    },
  ],
}

describe('annotation drawer', () => {
  const mounted: Array<{ unmount: () => void }> = []

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    delete (window as Window & { prksFlashButtonLabel?: unknown }).prksFlashButtonLabel
  })

  it('paints a published row and forwards the open generation', async () => {
    const jumps: Array<{ id: string; epoch: number; viewerToken: number; generation: number }> = []
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: (id, ticket) => {
          jumps.push({ id, ...ticket })
        },
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    const drawer = wrapper.get('[data-prks-role="pdf-annotation-drawer"]')
    expect(drawer.attributes('data-prks-list-published')).toBe('true')
    expect(wrapper.get('.annotation-row').attributes('data-selected')).toBe('true')
    expect(wrapper.get('.annotation-row__metadata').text()).toContain('Topic')
    await wrapper.get('.annotation-row__jump').trigger('click')
    expect(jumps).toEqual([{ id: 'ann-a', epoch: 1, viewerToken: 4, generation: 3 }])
  })

  it('flashes copy success through the shared helper', async () => {
    const flashes: string[] = []
    ;(window as Window & {
      prksFlashButtonLabel?: (
        button: HTMLButtonElement,
        ok: boolean,
        options: { successLabel: string; errorLabel: string },
      ) => void
    }).prksFlashButtonLabel = (_button, ok, options) => {
      flashes.push(ok ? options.successLabel : options.errorLabel)
    }
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    await wrapper.get('.annotation-row__copy-link').trigger('click')
    await flushPromises()
    expect(flashes).toEqual(['Copied'])
  })
})
