import type { PrksRouteInstanceBase } from '../../route-surface/route-instance'

export interface FolderDetailRouteInstance extends PrksRouteInstanceBase {
  readonly name: 'folder-detail'
  readonly params: { readonly folderId: string }
}
