import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

/** Concepts index narrowing of the shared route instance. */
export interface ConceptsIndexRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'concepts'
  readonly params: Record<string, never>
}

/** Concept detail narrowing of the shared route instance. */
export interface ConceptDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'concept-detail'
  readonly params: { readonly conceptId: string }
}
