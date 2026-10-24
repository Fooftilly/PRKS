import { isWorkStatus, type WorkStatus } from '../../domain/work-status'

export { progressCanonicalHash } from '../../routing/route-model'

/** A Progress page filter is one Work status. */
export type ProgressStatus = WorkStatus

/**
 * Invalid or missing status parameters are not a status.
 * Navigation canonicalizes those hashes to Not Started before paint.
 */
export function normalizeProgressStatusParam(raw: string | null | undefined): ProgressStatus | null {
  if (raw == null || String(raw).trim() === '') return null
  let decoded: string
  try {
    decoded = decodeURIComponent(String(raw).trim())
  } catch {
    return null
  }
  return isWorkStatus(decoded) ? decoded : null
}

/** Route paint status. Missing and invalid values use the navigation default. */
export function canonicalProgressStatus(raw: string | null | undefined): ProgressStatus {
  return normalizeProgressStatusParam(raw) ?? 'Not Started'
}
