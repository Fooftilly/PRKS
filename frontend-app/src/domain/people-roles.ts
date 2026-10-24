/**
 * The People/Processing bibliographic role subset. Intentionally excludes
 * Mentioned, which stays a Work-role-only type. Mirrors backend
 * work_role_sync.PEOPLE_ROLE_TYPES; pinned by tests/test_contract_parity.py.
 *
 * Domain owner: the route parser imports it to mark known `#/people/role/…`
 * roles; People and Processing code import it directly, not through routing.
 */
export const PEOPLE_ROLES = ['Author', 'Editor', 'Reviewer', 'Translator', 'Introduction', 'Foreword', 'Afterword'] as const

export type PeopleRole = (typeof PEOPLE_ROLES)[number]

export function isPeopleRole(value: string): value is PeopleRole {
  return (PEOPLE_ROLES as readonly string[]).indexOf(value) >= 0
}
