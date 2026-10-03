/**
 * Discriminated identity of one Vue-mounted PRKS route instance.
 *
 * Feature modules own their params. This union imports those types. It does
 * not parse hashes and it is not a second router: `parseRoute` in
 * `../routing/route-model` is the only hash parser, and every member's `name` is one
 * of its registry names. Work/PDF and unrecognized hashes stay on the classic
 * runtime and are not members here.
 *
 * `app.js` route dispatch is still coordinator-owned. The search query codec
 * is `features/search/codec.ts`, not this type. Per-feature dismiss globals
 * stay with their features.
 */
import type { ArgumentDetailRouteInstance, ArgumentsIndexRouteInstance } from '../features/arguments/route'
import type { ConceptDetailRouteInstance, ConceptsIndexRouteInstance } from '../features/concepts/route'
import type { FolderDetailRouteInstance } from '../features/folder-detail/route'
import type { FolderLibraryRouteInstance } from '../features/folder-library/route'
import type { PeopleIndexRouteInstance, PersonDetailRouteInstance } from '../features/people/route'
import type { PersonGroupDetailRouteInstance, PersonGroupsIndexRouteInstance } from '../features/person-groups/route'
import type { PlaylistDetailRouteInstance, PlaylistsIndexRouteInstance } from '../features/playlists/route'
import type { PositionDetailRouteInstance, PositionsIndexRouteInstance } from '../features/positions/route'
import type { ProcessingRouteInstance } from '../features/processing/route'
import type { ProgressRouteInstance } from '../features/progress/route'
import type { PublishersRouteInstance } from '../features/publishers/route'
import type { RecentRouteInstance } from '../features/recent/route'
import type { ResearchGraphRouteInstance } from '../features/research-graph/route'
import type { SavedViewDetailRouteInstance, SavedViewsIndexRouteInstance } from '../features/saved-views/route'
import type { SearchRouteInstance } from '../features/search/route'
import type { TagsRouteInstance } from '../features/tags/route'
import type { TypeDetailRouteInstance, TypesIndexRouteInstance } from '../features/types/route'

export type PrksRouteInstance =
  | FolderLibraryRouteInstance
  | FolderDetailRouteInstance
  | RecentRouteInstance
  | SavedViewsIndexRouteInstance
  | SavedViewDetailRouteInstance
  | TypesIndexRouteInstance
  | TypeDetailRouteInstance
  | PlaylistsIndexRouteInstance
  | PlaylistDetailRouteInstance
  | TagsRouteInstance
  | PublishersRouteInstance
  | ConceptsIndexRouteInstance
  | ConceptDetailRouteInstance
  | PositionsIndexRouteInstance
  | PositionDetailRouteInstance
  | ArgumentsIndexRouteInstance
  | ArgumentDetailRouteInstance
  | ProcessingRouteInstance
  | SearchRouteInstance
  | ProgressRouteInstance
  | PeopleIndexRouteInstance
  | PersonDetailRouteInstance
  | PersonGroupsIndexRouteInstance
  | PersonGroupDetailRouteInstance
  | ResearchGraphRouteInstance

/**
 * Present input for one union member. `Omit` is not distributive, so this
 * applies it to each member, then adds an optional generation. A name and
 * params pair from different members is not assignable.
 */
type RouteInstanceInputMember<T> = T extends unknown
  ? Omit<T, 'generation'> & { readonly generation?: number }
  : never

/** Present input. Generation is filled from this owner when the caller omits it. */
export type PrksRouteInstanceInput = RouteInstanceInputMember<PrksRouteInstance>
