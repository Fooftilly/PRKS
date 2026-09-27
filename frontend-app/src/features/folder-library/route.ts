import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

/** Folder Library index narrowing of the shared route instance (main-only). */
export interface FolderLibraryRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'folders'
  readonly params: Record<string, never>
}
