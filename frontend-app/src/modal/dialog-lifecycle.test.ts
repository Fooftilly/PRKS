import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import ModalDialog from './ModalDialog.vue'
import {
  beginOpening,
  dialogsClosed,
  dismissDialogInnerLayer,
  hideDialogs,
  isDialogDirty,
  resetDialogBaselines,
  resetDialogsForTest,
  showDialog,
  useDialogLifecycle,
  type DialogLifecycle,
  type DialogOpening,
} from './dialogs'

/** A form body that registers `lifecycle` with the dialog it sits in. */
function formWith(lifecycle: DialogLifecycle) {
  return defineComponent({
    setup() {
      useDialogLifecycle(lifecycle)
      return () => [h('input', { id: 'f-field' }), h('input', { id: 'f-other' })]
    },
  })
}

function mountDialog(lifecycle: DialogLifecycle) {
  const Form = formWith(lifecycle)
  return mount(
    { render: () => h(ModalDialog, { id: 'f-modal', title: 'F' }, () => h(Form)) },
    { attachTo: document.body },
  )
}

function root(): HTMLElement {
  return document.getElementById('f-modal') as HTMLElement
}

function field(id = 'f-field'): HTMLInputElement {
  return document.getElementById(id) as HTMLInputElement
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()))
}

function open(): number {
  const generation = showDialog('f-modal')
  beginOpening('f-modal')
  return generation
}

describe('dialog lifecycle', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    resetDialogsForTest()
  })

  it('runs a synchronous opening, then takes the baseline and focuses', async () => {
    const seen: number[] = []
    mountDialog({
      opened: (opening) => {
        seen.push(opening.generation)
        field().value = ''
      },
      focusInitial: () => field().focus(),
    })
    field().value = 'left over'
    const generation = open()
    expect(seen).toEqual([generation])
    expect(root().hasAttribute('inert')).toBe(false)
    expect(document.activeElement?.id).toBe('f-field')
    expect(isDialogDirty('f-modal')).toBe(false)
    field().value = 'typed'
    expect(isDialogDirty('f-modal')).toBe(true)
    await nextFrame()
    await nextTick()
    expect(root().dataset.prksDialogReady).toBe('true')
  })

  it('keeps the dialog inert while a returned prepare is pending', async () => {
    let finish: () => void = () => {}
    mountDialog({
      opened: () =>
        new Promise<void>((resolve) => {
          finish = () => {
            field().value = 'prepared'
            resolve()
          }
        }),
      focusInitial: () => field().focus(),
    })
    open()
    expect(root().hasAttribute('inert')).toBe(true)
    expect(isDialogDirty('f-modal')).toBe(false)
    expect(document.activeElement?.id).not.toBe('f-field')
    finish()
    await Promise.resolve()
    await nextTick()
    expect(root().hasAttribute('inert')).toBe(false)
    expect(document.activeElement?.id).toBe('f-field')
    // The prepared values are the baseline, not a change.
    expect(isDialogDirty('f-modal')).toBe(false)
  })

  it('drops an older opening that settles after a newer one began', async () => {
    const openings: DialogOpening[] = []
    const finishers: Array<() => void> = []
    let focused = 0
    mountDialog({
      opened: (opening) => {
        openings.push(opening)
        return new Promise<void>((resolve) => finishers.push(resolve))
      },
      focusInitial: () => {
        focused += 1
      },
    })
    open()
    hideDialogs()
    open()
    expect(openings[0].isCurrent()).toBe(false)
    expect(openings[1].isCurrent()).toBe(true)
    finishers[0]()
    await Promise.resolve()
    await nextTick()
    expect(focused).toBe(0)
    expect(root().hasAttribute('inert')).toBe(true)
    finishers[1]()
    await Promise.resolve()
    await nextTick()
    expect(focused).toBe(1)
    expect(root().hasAttribute('inert')).toBe(false)
  })

  it('still settles when the prepare rejects or the opening throws', async () => {
    let focused = 0
    let mode: 'reject' | 'throw' = 'reject'
    mountDialog({
      opened: () => {
        if (mode === 'throw') throw new Error('broken')
        return Promise.reject(new Error('offline'))
      },
      focusInitial: () => {
        focused += 1
      },
    })
    open()
    await Promise.resolve()
    await Promise.resolve()
    await nextTick()
    expect(focused).toBe(1)
    expect(root().hasAttribute('inert')).toBe(false)
    mode = 'throw'
    const errors = console.error
    console.error = () => {}
    try {
      open()
    } finally {
      console.error = errors
    }
    expect(focused).toBe(2)
  })

  it('tells a closed dialog to drop its draft, never the open one', () => {
    let closed = 0
    mountDialog({ closed: () => (closed += 1) })
    open()
    dialogsClosed()
    expect(closed).toBe(0)
    hideDialogs()
    dialogsClosed()
    expect(closed).toBe(1)
  })

  it('asks the open dialog for its inner layer only', () => {
    let inner = true
    mountDialog({ dismissInnerLayer: () => inner })
    expect(dismissDialogInnerLayer('f-modal')).toBe(false)
    open()
    expect(dismissDialogInnerLayer('f-modal')).toBe(true)
    inner = false
    expect(dismissDialogInnerLayer('f-modal')).toBe(false)
  })

  it('reports no unsaved changes without a baseline', () => {
    mountDialog({})
    open()
    field().value = 'typed'
    expect(isDialogDirty('f-modal')).toBe(true)
    resetDialogBaselines()
    expect(isDialogDirty('f-modal')).toBe(false)
  })

  it('forgets the form lifecycle when the form unmounts', () => {
    let opened = 0
    const wrapper = mountDialog({ opened: () => void (opened += 1) })
    wrapper.unmount()
    open()
    expect(opened).toBe(0)
  })
})
