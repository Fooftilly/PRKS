/** Person Group index/detail types. Effective values arrive already overlaid. */

export type PersonGroupsAvailability = 'ready' | 'unavailable' | 'not-found'

export interface PersonGroupIndexItem {
  readonly id: string
  readonly name: string
  readonly parentId: string
  readonly memberCount: number
  readonly childCount: number
}

export interface PersonGroupMemberItem {
  readonly id: string
  readonly firstName: string
  readonly lastName: string
  readonly lifespan: string
}

export interface PersonGroupChildItem {
  readonly id: string
  readonly name: string
}

export interface PersonGroupParentItem {
  readonly id: string
  readonly name: string
}

export interface PersonGroupFieldDraft {
  name: string
  description: string
  parent_id: string
  parent_name: string
}

export interface PersonGroupDetail {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly parent: PersonGroupParentItem | null
  readonly children: readonly PersonGroupChildItem[]
  readonly members: readonly PersonGroupMemberItem[]
}
