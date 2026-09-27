import { describe, expect, it } from 'vitest'
import { KEYBOARD_EQUIVALENT_PATHS, keyboardEquivalentSummary } from './a11y'

describe('workspace-dnd a11y keyboard equivalents', () => {
  it('documents at least one keyboard path per major drag gesture', () => {
    expect(KEYBOARD_EQUIVALENT_PATHS.length).toBeGreaterThanOrEqual(4)
    expect(KEYBOARD_EQUIVALENT_PATHS.some((row) => row.dragGesture.includes('reorder'))).toBe(true)
    expect(KEYBOARD_EQUIVALENT_PATHS.some((row) => row.keyboardPath.includes('Hide from split'))).toBe(
      true,
    )
    expect(keyboardEquivalentSummary()).toContain('Escape')
  })
})
