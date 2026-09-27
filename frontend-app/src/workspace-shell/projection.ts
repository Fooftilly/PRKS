import type { InjectionKey, Ref } from 'vue'
import type { WorkspaceProjection } from './types'

export const workspaceProjectionKey: InjectionKey<Ref<WorkspaceProjection | null>> = Symbol(
  'prks-workspace-projection',
)
