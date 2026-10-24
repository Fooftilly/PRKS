/**
 * Typed Concept shapes for Vue route projections.
 * Already-effective rows/detail from the legacy coordinator — not a second
 * catalogue or durable-operation store.
 */

export interface ConceptParentRef {
  readonly id: string
  readonly name: string
}

export interface ConceptChildRef {
  readonly id: string
  readonly name: string
}

export interface ConceptMentionOccurrence {
  readonly snippet?: string
}

export interface ConceptMention {
  readonly work_id: string
  readonly title?: string
  readonly occurrences?: readonly ConceptMentionOccurrence[]
}

/** One effective Concept index row. */
export interface ConceptIndexItem {
  readonly id: string
  readonly name: string
  readonly aliases: readonly string[]
  readonly parents: readonly ConceptParentRef[]
  readonly subconcept_count: number
  readonly mention_count: number
}

/** One effective Concept detail record. */
export interface ConceptDetail {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly aliases: readonly string[]
  readonly parents: readonly ConceptParentRef[]
  readonly children: readonly ConceptChildRef[]
  readonly mentions: readonly ConceptMention[]
  readonly mention_count: number
  readonly subconcept_count: number
}

export type ConceptIndexAvailability = 'ready' | 'unavailable'
export type ConceptDetailAvailability = 'ready' | 'unavailable' | 'not-found'

/**
 * A Definition editor's session, from `prksBeginConceptDefinitionEdit` when it
 * opens (#568): the text it shows and the base that text was typed on. Opaque
 * to Vue apart from `value`; `updateConcept` measures the Save against it.
 */
export interface ConceptDefinitionEdit {
  readonly concept_id: string
  readonly value: string
  readonly base: { readonly description: { readonly value: string; readonly revision: number } }
  readonly expected: { readonly description: string | null }
}
