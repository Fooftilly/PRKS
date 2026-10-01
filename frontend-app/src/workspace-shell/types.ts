/**
 * Read-only view of a committed workspace snapshot.
 *
 * This module does not import `src/workspace/`. The typed model stays in
 * `frontend/js/workspace-model.js`. The shell must not mutate these objects.
 */

export type WorkspaceMode = 'stacked' | 'tiled'
export type SplitAxis = 'left-right' | 'top-bottom'
export type TabStatusKind = '' | 'saving' | 'drafting' | 'error'

export interface ProjectionTab {
  readonly id: string
  readonly route: string
  readonly title: string
  readonly icon: string
  readonly history?: readonly string[]
  readonly historyIndex?: number
}

export interface ProjectionLeaf {
  readonly type: 'leaf'
  readonly tabId: string
}

export interface ProjectionSplit {
  readonly type: 'split'
  readonly id: string
  readonly axis: SplitAxis
  readonly ratio: number
  readonly first: ProjectionNode
  readonly second: ProjectionNode
}

export type ProjectionNode = ProjectionLeaf | ProjectionSplit

export interface ProjectionState {
  readonly version: number
  readonly mode: WorkspaceMode
  readonly mainTabId: string | null
  readonly focusedTabId: string | null
  readonly secondaryTree: ProjectionNode | null
  readonly tabs: readonly ProjectionTab[]
  readonly mainSplitRatio: number
}

export interface WorkspaceProjection {
  readonly state: ProjectionState
  readonly visualTiled: boolean
  readonly narrowFallback: boolean
  readonly tabStatus: Readonly<Record<string, TabStatusKind>>
  /** Publish id from the coordinator. Absent on hand-built test projections. */
  readonly commit?: number
}

export interface WorkspaceIntents {
  activate(tabId: string): void
  close(tabId: string): void
  focus(tabId: string): void
  tile(tabId: string): void
  openTabMenu(tabId: string, event: Event, anchor?: HTMLElement): void
  setMainRatio(ratio: number): void
  setNestedRatio(splitId: string, ratio: number): void
}

declare global {
  interface Window {
    prksWorkspaceSubscribe?: (listener: (projection: WorkspaceProjection) => void) => () => void
    prksWorkspaceRepublish?: () => void
    prksWorkspaceOnShellCommit?: (projection: WorkspaceProjection) => void
    prksWorkspaceReleaseRootSeparator?: (canvas: HTMLElement) => void
    prksWorkspaceReleaseNestedSeparator?: (container: HTMLElement) => void
    prksNotifyTabHostReparent?: (tabId: string) => void
    prksWorkspaceTabKeydown?: (event: KeyboardEvent) => void
    prksWorkspaceActivateTab?: (tabId: string) => void
    prksWorkspaceCloseTab?: (tabId: string) => void
    prksWorkspaceFocusTab?: (tabId: string) => void
    prksWorkspaceTileTab?: (tabId: string) => void
    prksWorkspaceOpenTabMenu?: (tabId: string, event: Event, anchor?: HTMLElement) => void
    prksWorkspaceSetMainSplitRatio?: (ratio: number, options?: { paint?: boolean }) => void
    prksWorkspaceSetNestedSplitRatio?: (splitId: string, ratio: number, options?: { paint?: boolean }) => void
    prksWorkspacePlaceContentHost?: (tabId: string, slot: HTMLElement) => HTMLElement | null
    prksWorkspaceReleaseContentHost?: (tabId: string) => void
    prksWorkspaceContentHostIds?: () => string[]
    prksWorkspaceCancelActiveDrag?: () => void
    prksWorkspaceInitDrag?: () => void
    prksWorkspaceCanAddSecondaryLeaf?: () => boolean
    prksWorkspaceWatchCanvas?: (canvas: HTMLElement) => void
    prksWorkspaceWatchNestedSplit?: (splitId: string, container: HTMLElement) => void
    prksWorkspaceUnwatchNestedSplit?: (splitId: string) => void
    prksWorkspaceSyncSplitSeparator?: (canvas: HTMLElement, visualTiled: boolean, snap: ProjectionState) => void
    prksWorkspaceSyncNestedSeparator?: (
      container: HTMLElement,
      node: ProjectionSplit,
      firstEl: HTMLElement,
      secondEl: HTMLElement,
    ) => void
    prksSyncDenseWorkspaceShell?: (visualTiled: boolean) => void
    prksRouteSupportsTile?: (route: string) => boolean
    prksIcon?: (name: string, options?: { size?: string | number; className?: string }) => string
    __prksWorkspaceShellOwned?: boolean
  }
}
