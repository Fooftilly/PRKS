/** One painted command-palette row. Execution fields stay in the engine. */
export interface PaletteRowView {
  index: number
  id: string
  label: string
  subtitle: string
  icon: string
  section: string
  /** Section heading rendered before this row, or empty when the section continues. */
  heading: string
}

export type PaletteScope = 'all' | 'create'
export type PaletteNavigationTarget = 'current' | 'new-tab' | 'tile'

/**
 * Read model published by `command-palette.js`. Vue paints it and does not
 * search, rank, or dispatch.
 */
export interface PaletteView {
  open: boolean
  title: string
  placeholder: string
  query: string
  scope: PaletteScope
  navigationTarget: PaletteNavigationTarget
  createMode: boolean
  activeIndex: number
  activeDescendant: string
  rows: readonly PaletteRowView[]
  status: string
  hints: string
  showHints: boolean
  revision: number
}

export interface PalettePointerEvent {
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
}
