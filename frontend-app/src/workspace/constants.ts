/** In-memory workspace snapshot version. Not the persistence schema version. */
export const WORKSPACE_STATE_VERSION = 1

export const WORKSPACE_MODE_STACKED = 'stacked' as const
export const WORKSPACE_MODE_TILED = 'tiled' as const

/** Main region width / usable root split width. */
export const DEFAULT_MAIN_SPLIT_RATIO = 0.58

/** Nested Secondary split ratio (`first / this split`). */
export const DEFAULT_NESTED_SPLIT_RATIO = 0.5

/** 1 Main + at most 3 Secondary leaves mounted at once. */
export const MAX_VISIBLE_PANES = 4

export const MAX_SECONDARY_LEAVES = MAX_VISIBLE_PANES - 1
