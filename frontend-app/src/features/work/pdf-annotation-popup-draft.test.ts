import { describe, expect, it } from 'vitest'
import { popupDraftForSession } from './pdf-annotation-popup-draft'

describe('annotation popup draft', () => {
  it('keeps the draft for the same annotation session', () => {
    expect(popupDraftForSession(
      { annId: 'A', epoch: 1 },
      { open: true, annId: 'A', epoch: 1, comment: 'saved' },
      'typed',
    )).toBe('typed')
  })

  it('does not carry annotation A into popup B', () => {
    expect(popupDraftForSession(
      { annId: 'A', epoch: 1 },
      { open: true, annId: 'B', epoch: 2, comment: 'from B' },
      'typed on A',
    )).toBe('from B')
  })

  it('drops the draft when the popup closes', () => {
    expect(popupDraftForSession(
      { annId: 'A', epoch: 1 },
      { open: false, annId: '', epoch: 2, comment: '' },
      'typed',
    )).toBe('')
  })
})
