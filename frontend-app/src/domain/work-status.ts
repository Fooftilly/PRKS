/**
 * Work progress status vocabulary. Mirrors the DB check and backend
 * work_metadata_sync.WORK_STATUSES; pinned by tests/test_contract_parity.py.
 *
 * Domain owner: the route parser imports it to validate `#/progress`, and
 * Work and Progress code import it directly, not through routing.
 */
export const WORK_STATUSES = ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'] as const

export type WorkStatus = (typeof WORK_STATUSES)[number]

export function isWorkStatus(value: string): value is WorkStatus {
  return (WORK_STATUSES as readonly string[]).indexOf(value) >= 0
}
