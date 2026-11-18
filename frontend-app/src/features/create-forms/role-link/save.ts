/**
 * Create Link in the Link Person to Work dialog (#1036).
 *
 * The classic `prksLinkPersonFromRoleModal` keeps the opener checks and the
 * durable write, the same path the Work panel's Link button takes. This
 * module owns what the dialog does with the outcome: which message to show,
 * and closing only while this opening may still settle UI.
 */

/** Work role types a link may take. Mentioned is a Work role, not a People role. */
export const LINK_ROLES = [
  'Author',
  'Editor',
  'Reviewer',
  'Mentioned',
  'Translator',
  'Introduction',
  'Foreword',
  'Afterword',
] as const

export const ROLE_LINK_NEEDS_BOTH = 'Choose a person and a file.'

export interface RoleLinkInput {
  person_id: string
  work_id: string
  role_type: string
  credit_name: string
}

export interface RoleLinkResult {
  code: string
  maySettle?: () => boolean
  settle?: () => void
}

export interface RoleLinkSaveDeps {
  link: (input: RoleLinkInput) => Promise<RoleLinkResult>
  duplicateAlert: (roleType: string) => Promise<void>
  failureAlert: (message: string) => Promise<void>
  close: () => void
}

export type RoleLinkOutcome = 'invalid' | 'stale' | 'duplicate' | 'saved' | 'failed'

export function roleLinkFailureMessage(code: string): string {
  switch (code) {
    case 'unavailable':
      return 'This file’s linked people cannot be changed right now. Open it once while connected to PRKS so its link state is prepared.'
    case 'too-long':
      return 'That name is too long for this file.'
    case 'busy':
      return 'This link is syncing or needs a decision. Try again shortly.'
    case 'dependency-failed':
      return 'This person could not be created on PRKS, so they cannot be linked to a file. Discard that creation in Sync Diagnostics and add them again.'
    default:
      return 'Could not create link.'
  }
}

/** Whether a Create Link has both ends picked. The form shows the message. */
export function roleLinkInputReady(input: RoleLinkInput): boolean {
  return !!input.person_id.trim() && !!input.work_id.trim()
}

export async function submitRoleLink(input: RoleLinkInput, deps: RoleLinkSaveDeps): Promise<RoleLinkOutcome> {
  if (!roleLinkInputReady(input)) return 'invalid'
  let result: RoleLinkResult
  try {
    result = await deps.link(input)
  } catch {
    result = { code: 'failed' }
  }
  const maySettle = () => (typeof result.maySettle === 'function' ? result.maySettle() === true : false)
  if (result.code === 'stale') return 'stale'
  if (result.code === 'duplicate') {
    await deps.duplicateAlert(input.role_type)
    return 'duplicate'
  }
  if (result.code === 'saved') {
    // A completion that outlives its opener must not close a newer dialog
    // or switch the pane that is focused now.
    if (maySettle()) {
      deps.close()
      result.settle?.()
    }
    return 'saved'
  }
  if (maySettle()) await deps.failureAlert(roleLinkFailureMessage(result.code))
  return 'failed'
}
