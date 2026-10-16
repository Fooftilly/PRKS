/**
 * #651 C3: small automated semantic-accessibility pilot for stable UI primitives.
 *
 * axe-core is deliberately limited to DOM-semantic rules that jsdom can
 * evaluate. This does not claim browser keyboard, contrast or screen-reader
 * coverage; those require real-browser/manual checks.
 */
import axe from 'axe-core'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PrksButton from './PrksButton.vue'
import PrksField from './PrksField.vue'
import PrksIconButton from './PrksIconButton.vue'

const SEMANTIC_RULES = [
  'button-name',
  'label',
  'aria-valid-attr',
  'aria-valid-attr-value',
]

async function checkSemantics(element: Element) {
  return axe.run(element, {
    runOnly: { type: 'rule', values: SEMANTIC_RULES },
  })
}

describe('automated axe semantic-accessibility pilot', () => {
  it('detects a deliberately unnamed button (rule-engine canary)', async () => {
    const button = document.createElement('button')
    button.type = 'button'
    document.body.append(button)
    try {
      const result = await checkSemantics(button)
      expect(result.violations.map((violation) => violation.id)).toContain('button-name')
    } finally {
      button.remove()
    }
  })

  it('accepts the native named action, including its busy state', async () => {
    const wrapper = mount(PrksButton, {
      attachTo: document.body,
      props: { busy: true, busyLabel: 'Saving…' },
      slots: { default: 'Save' },
    })
    try {
      const result = await checkSemantics(wrapper.element)
      expect(result.violations).toEqual([])
    } finally {
      wrapper.unmount()
    }
  })

  it('accepts an icon-only action with an accessible name', async () => {
    const wrapper = mount(PrksIconButton, {
      attachTo: document.body,
      props: { label: 'Close pane', busy: true },
      slots: { default: '<span aria-hidden="true">×</span>' },
    })
    try {
      const result = await checkSemantics(wrapper.element)
      expect(result.violations).toEqual([])
    } finally {
      wrapper.unmount()
    }
  })

  it('accepts a labeled input with valid help and error references', async () => {
    const wrapper = mount(PrksField, {
      attachTo: document.body,
      props: {
        label: 'Work title',
        forId: 'a11y-work-title',
        help: 'Enter the title as printed.',
        error: 'A title is required.',
      },
      slots: { default: '<input id="a11y-work-title" type="text" aria-invalid="true">' },
    })
    try {
      const result = await checkSemantics(wrapper.element)
      expect(result.violations).toEqual([])
    } finally {
      wrapper.unmount()
    }
  })
})
