import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import WorkPdfAnnotationPopup from './WorkPdfAnnotationPopup.vue'

const state = {
  open: true,
  annId: 'A',
  epoch: 1,
  comment: '',
  meta: 'Page 1',
  pageIndex: 0,
  generation: 1,
  deletable: true,
}

describe('annotation popup anchor', () => {
  const panes: HTMLElement[] = []

  afterEach(() => {
    for (const pane of panes.splice(0)) pane.remove()
  })

  it('positions the popup when an offscreen list open mounts the anchor later', async () => {
    const pane = document.createElement('div')
    document.body.appendChild(pane)
    panes.push(pane)
    const wrapper = mount(WorkPdfAnnotationPopup, {
      props: {
        state,
        tabId: 'tab-a',
        anchor: () => pane.querySelector<HTMLElement>('[data-prks-role="pdf-annotation-anchor"]'),
        boundary: () => pane,
        onSave: () => {},
        onClose: () => {},
        onDelete: () => {},
      },
    })
    await flushPromises()
    const popup = wrapper.get('[data-prks-role="pdf-annotation-popup"]').element as HTMLElement
    expect(popup.style.left).toBe('')

    const anchor = document.createElement('div')
    anchor.setAttribute('data-prks-role', 'pdf-annotation-anchor')
    anchor.setAttribute('data-prks-annotation-id', 'A')
    pane.appendChild(anchor)
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(popup.style.left).not.toBe('')
    expect(popup.style.top).not.toBe('')
    wrapper.unmount()
  })
})

describe('annotation popup escape', () => {
  const mounted: Array<{ unmount: () => void }> = []

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
  })

  it('closes only the popup whose textarea received Escape', async () => {
    const closed: string[] = []
    const openState = {
      open: true,
      epoch: 1,
      comment: '',
      meta: 'Page 1',
      pageIndex: 0,
      generation: 1,
      deletable: true,
    }
    const wrapperA = mount(WorkPdfAnnotationPopup, {
      props: {
        state: { ...openState, annId: 'A' },
        tabId: 'tab-a',
        anchor: () => null,
        boundary: () => null,
        onSave: () => {},
        onClose: () => {
          closed.push('A')
        },
        onDelete: () => {},
      },
      attachTo: document.body,
    })
    const wrapperB = mount(WorkPdfAnnotationPopup, {
      props: {
        state: { ...openState, annId: 'B', comment: 'kept' },
        tabId: 'tab-b',
        anchor: () => null,
        boundary: () => null,
        onSave: () => {},
        onClose: () => {
          closed.push('B')
        },
        onDelete: () => {},
      },
      attachTo: document.body,
    })
    mounted.push(wrapperA, wrapperB)
    await flushPromises()

    const textA = wrapperA.get('[data-prks-role="pdf-annotation-popup-text"]')
    const textB = wrapperB.get('[data-prks-role="pdf-annotation-popup-text"]')
    await textA.setValue('only-a')
    await textB.setValue('kept')
    textA.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))

    expect(closed).toEqual(['A'])
    expect((textB.element as HTMLTextAreaElement).value).toBe('kept')
    expect(wrapperB.find('[data-prks-role="pdf-annotation-popup"]').exists()).toBe(true)
  })
})
