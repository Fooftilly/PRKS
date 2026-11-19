import { describe, expect, it, vi } from 'vitest'
import { roleLinkFailureMessage, submitRoleLink, type RoleLinkResult } from './save'

const INPUT = { person_id: 'P1', work_id: 'W1', role_type: 'Editor', credit_name: '' }

function deps(result: RoleLinkResult | (() => Promise<RoleLinkResult>), current = true) {
  return {
    link: vi.fn(typeof result === 'function' ? result : async () => result),
    duplicateAlert: vi.fn(async () => {}),
    failureAlert: vi.fn(async () => {}),
    close: vi.fn(),
    current: vi.fn(() => current),
  }
}

describe('Create Link outcome', () => {
  it('needs both a person and a file before anything is sent', async () => {
    const d = deps({ code: 'saved' })
    expect(await submitRoleLink({ ...INPUT, person_id: ' ' }, d)).toBe('invalid')
    expect(await submitRoleLink({ ...INPUT, work_id: '' }, d)).toBe('invalid')
    expect(d.link).not.toHaveBeenCalled()
  })

  it('closes and refreshes the opener after a saved link it may still settle', async () => {
    const settle = vi.fn()
    const d = deps({ code: 'saved', maySettle: () => true, settle })
    expect(await submitRoleLink(INPUT, d)).toBe('saved')
    expect(d.link).toHaveBeenCalledWith(INPUT)
    expect(d.close).toHaveBeenCalledTimes(1)
    expect(settle).toHaveBeenCalledTimes(1)
  })

  it('a saved link whose opener moved on closes nothing', async () => {
    const settle = vi.fn()
    const d = deps({ code: 'saved', maySettle: () => false, settle })
    expect(await submitRoleLink(INPUT, d)).toBe('saved')
    expect(d.close).not.toHaveBeenCalled()
    expect(settle).not.toHaveBeenCalled()
  })

  it('a stale opener says nothing', async () => {
    const d = deps({ code: 'stale' })
    expect(await submitRoleLink(INPUT, d)).toBe('stale')
    expect(d.failureAlert).not.toHaveBeenCalled()
    expect(d.close).not.toHaveBeenCalled()
  })

  it('names a duplicate link by its role, only while this opening may settle', async () => {
    const d = deps({ code: 'duplicate', maySettle: () => true })
    expect(await submitRoleLink(INPUT, d)).toBe('duplicate')
    expect(d.duplicateAlert).toHaveBeenCalledWith('Editor')
    const older = deps({ code: 'duplicate', maySettle: () => false })
    expect(await submitRoleLink(INPUT, older)).toBe('duplicate')
    expect(older.duplicateAlert).not.toHaveBeenCalled()
  })

  it('alerts a refusal only while this opening may settle', async () => {
    const live = deps({ code: 'unavailable', maySettle: () => true })
    expect(await submitRoleLink(INPUT, live)).toBe('failed')
    expect(live.failureAlert).toHaveBeenCalledWith(roleLinkFailureMessage('unavailable'))
    const older = deps({ code: 'failed', maySettle: () => false })
    expect(await submitRoleLink(INPUT, older)).toBe('failed')
    expect(older.failureAlert).not.toHaveBeenCalled()
  })

  it('a link that throws says so while this opening is current, else nothing', async () => {
    const boom = async (): Promise<RoleLinkResult> => {
      throw new Error('boom')
    }
    const d = deps(boom)
    expect(await submitRoleLink(INPUT, d)).toBe('failed')
    expect(d.failureAlert).toHaveBeenCalledWith('Could not create link.')
    expect(d.close).not.toHaveBeenCalled()
    const older = deps(boom, false)
    expect(await submitRoleLink(INPUT, older)).toBe('failed')
    expect(older.failureAlert).not.toHaveBeenCalled()
  })

  it('keeps the classic refusal wording', () => {
    expect(roleLinkFailureMessage('too-long')).toBe('That name is too long for this file.')
    expect(roleLinkFailureMessage('busy')).toBe('This link is syncing or needs a decision. Try again shortly.')
    expect(roleLinkFailureMessage('dependency-failed')).toContain('Sync Diagnostics')
    expect(roleLinkFailureMessage('unavailable')).toContain('Open it once while connected to PRKS')
    expect(roleLinkFailureMessage('anything')).toBe('Could not create link.')
  })
})
