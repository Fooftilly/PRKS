/**
 * Typed Argument shapes for Vue route projections.
 * Already-effective rows/detail from the legacy coordinator — not a second
 * catalogue or durable-operation store. Arguments and Stances are one family.
 */

export type ArgumentKind = 'argument' | 'stance'
export type ArgumentKindFilter = 'all' | ArgumentKind

export interface ArgumentIndexTarget {
  readonly id: string
  readonly name: string
  readonly type: string
}

export interface ArgumentIndexSource {
  readonly work_id: string
  readonly work_title: string
}

/** One effective Argument/Stance index row from the complete collection. */
export interface ArgumentIndexItem {
  readonly id: string
  readonly name: string
  readonly kind: ArgumentKind
  readonly main_text: string
  readonly response_count: number
  readonly targets: readonly ArgumentIndexTarget[]
  readonly sources: readonly ArgumentIndexSource[]
}

export interface ArgumentVerdict {
  readonly id: string
  readonly label: string
}

export interface ArgumentTargetRef {
  readonly type: 'position' | 'argument'
  readonly id: string
  readonly name: string
  readonly kind: string
  readonly verdict_id: string
  readonly verdict_label: string
}

export interface ArgumentSourceAuthor {
  readonly first_name: string
  readonly last_name: string
  readonly credit_name: string
}

export interface ArgumentSourceRef {
  readonly work_id: string
  readonly work_title: string
  readonly pages: string
  readonly authors: readonly ArgumentSourceAuthor[]
}

export interface ArgumentResponseRef {
  readonly id: string
  readonly name: string
  readonly kind: string
  readonly verdict_id: string
  readonly verdict_label: string
}

export interface ArgumentMentionRef {
  readonly work_id: string
  readonly title: string
}

/**
 * One effective Argument detail record.
 * Pending names and Work titles are already resolved by the coordinator.
 */
export interface ArgumentDetail {
  readonly id: string
  readonly name: string
  readonly kind: ArgumentKind
  readonly main_text: string
  readonly targets: readonly ArgumentTargetRef[]
  readonly sources: readonly ArgumentSourceRef[]
  readonly responses: readonly ArgumentResponseRef[]
  readonly mentions: readonly ArgumentMentionRef[]
  readonly verdicts: readonly ArgumentVerdict[]
}

export interface ArgumentEditorTarget {
  type: 'position' | 'argument'
  id: string
  name: string
  kind: string
  verdict_id: string
}

export interface ArgumentEditorSource {
  work_id: string
  work_title: string
  pages: string
}

/** Local row identity. Stripped before the durable draft is saved. */
export interface ArgumentEditorTargetRow extends ArgumentEditorTarget {
  rowKey: string
}

/** Local row identity. Stripped before the durable draft is saved. */
export interface ArgumentEditorSourceRow extends ArgumentEditorSource {
  rowKey: string
}

/** Vue-local edit form. `rowKey` never leaves the editor. */
export interface ArgumentEditorForm {
  name: string
  kind: ArgumentKind
  main_text: string
  targets: ArgumentEditorTargetRow[]
  sources: ArgumentEditorSourceRow[]
}

/** Durable edit draft. Not the Vue form, and not a second store. */
export interface ArgumentEditorDraft {
  name: string
  kind: ArgumentKind
  main_text: string
  targets: ArgumentEditorTarget[]
  sources: ArgumentEditorSource[]
}

export type ArgumentIndexAvailability = 'ready' | 'unavailable'
export type ArgumentDetailAvailability = 'ready' | 'unavailable' | 'not-found'
