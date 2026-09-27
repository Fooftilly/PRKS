/**
 * Pure Secondary-tree transforms. No DOM, history, TabContext, or module-level
 * id counter. Callers pass `nextSplitId` when a new split node is created.
 * Unchanged subtrees are returned by reference so host reconciliation can key
 * off tab id / split id rather than object identity.
 */
import { DEFAULT_NESTED_SPLIT_RATIO } from './constants'
import { asSplitId, asTabId, type SplitAxis, type SplitId, type WorkspaceLeaf, type WorkspacePaneNode, type WorkspaceSplit } from './types'

export type SplitIdFactory = () => SplitId

const AXES: Readonly<Record<string, true>> = { 'left-right': true, 'top-bottom': true }

export function clampNestedRatio(value: unknown): number {
  const n = Number(value)
  if (!Number.isFinite(n)) return DEFAULT_NESTED_SPLIT_RATIO
  return Math.max(0, Math.min(1, n))
}

export function makeLeaf(tabId: string): WorkspaceLeaf {
  return { type: 'leaf', tabId: asTabId(String(tabId)) }
}

export function makeSplit(
  axis: string,
  first: WorkspacePaneNode,
  second: WorkspacePaneNode,
  ratio: unknown,
  id: string,
): WorkspaceSplit {
  return {
    type: 'split',
    id: asSplitId(id),
    axis: axis === 'top-bottom' ? 'top-bottom' : 'left-right',
    ratio: clampNestedRatio(ratio == null ? DEFAULT_NESTED_SPLIT_RATIO : ratio),
    first,
    second,
  }
}

export function isLeaf(node: unknown): node is WorkspaceLeaf {
  if (!node || typeof node !== 'object') return false
  const record = node as WorkspaceLeaf
  return record.type === 'leaf' && !!record.tabId
}

export function isSplit(node: unknown): node is WorkspaceSplit {
  if (!node || typeof node !== 'object') return false
  const record = node as WorkspaceSplit
  return record.type === 'split' && !!record.id && !!record.first && !!record.second
}

export function collectLeafTabIds(tree: unknown): string[] {
  const out: string[] = []
  const walk = (node: unknown) => {
    if (!node) return
    if (isLeaf(node)) {
      out.push(node.tabId)
      return
    }
    if (isSplit(node)) {
      walk(node.first)
      walk(node.second)
    }
  }
  walk(tree)
  return out
}

export function collectSplitIds(tree: unknown): string[] {
  const out: string[] = []
  const walk = (node: unknown) => {
    if (!node) return
    if (isSplit(node)) {
      out.push(node.id)
      walk(node.first)
      walk(node.second)
    }
  }
  walk(tree)
  return out
}

export function containsTab(tree: unknown, tabId: string | null | undefined): boolean {
  if (!tabId) return false
  return collectLeafTabIds(tree).indexOf(tabId) !== -1
}

export function findLeafByTabId(tree: unknown, tabId: string | null | undefined): WorkspaceLeaf | null {
  if (!tabId) return null
  let found: WorkspaceLeaf | null = null
  const walk = (node: unknown) => {
    if (found || !node) return
    if (isLeaf(node)) {
      if (node.tabId === tabId) found = node
      return
    }
    if (isSplit(node)) {
      walk(node.first)
      walk(node.second)
    }
  }
  walk(tree)
  return found
}

export function findNodeById(tree: unknown, nodeId: string | null | undefined): WorkspaceSplit | null {
  if (!nodeId) return null
  let found: WorkspaceSplit | null = null
  const walk = (node: unknown) => {
    if (found || !node) return
    if (isSplit(node)) {
      if (node.id === nodeId) {
        found = node
        return
      }
      walk(node.first)
      walk(node.second)
    }
  }
  walk(tree)
  return found
}

function rewritePath(
  tree: WorkspacePaneNode | null,
  predicate: (node: WorkspacePaneNode) => boolean,
  transform: (node: WorkspacePaneNode) => WorkspacePaneNode | null,
): { tree: WorkspacePaneNode | null; replaced: boolean } {
  let replaced = false

  const walk = (node: WorkspacePaneNode | null): WorkspacePaneNode | null => {
    if (!node) return node
    if (predicate(node)) {
      replaced = true
      return transform(node)
    }
    if (!isSplit(node)) return node
    const nextFirst = walk(node.first)
    const nextSecond = walk(node.second)
    if (nextFirst === node.first && nextSecond === node.second) return node
    if (!nextFirst && !nextSecond) return null
    if (!nextFirst) return nextSecond
    if (!nextSecond) return nextFirst
    return makeSplit(node.axis, nextFirst, nextSecond, node.ratio, node.id)
  }

  return { tree: walk(tree), replaced }
}

export function replaceLeaf(
  tree: WorkspacePaneNode | null,
  tabId: string | null | undefined,
  replacement: WorkspacePaneNode | null,
): WorkspacePaneNode | null {
  if (!tabId) return tree
  const result = rewritePath(
    tree,
    (node) => isLeaf(node) && node.tabId === tabId,
    () => replacement,
  )
  return result.tree
}

export function replaceTabId(
  tree: WorkspacePaneNode | null,
  oldTabId: string,
  newTabId: string,
): WorkspacePaneNode | null {
  return replaceLeaf(tree, oldTabId, makeLeaf(newTabId))
}

export interface SplitLeafOptions {
  axis?: string
  newTabId?: string
  placement?: string
  ratio?: number
}

export function splitLeaf(
  tree: WorkspacePaneNode | null,
  tabId: string,
  options: SplitLeafOptions | null | undefined,
  nextSplitId: SplitIdFactory,
): WorkspacePaneNode | null {
  const opts = options || {}
  if (!containsTab(tree, tabId) || containsTab(tree, opts.newTabId)) return tree
  const axis = opts.axis === 'top-bottom' ? 'top-bottom' : 'left-right'
  const existing = makeLeaf(tabId)
  const incoming = makeLeaf(String(opts.newTabId))
  const first = opts.placement === 'first' ? incoming : existing
  const second = opts.placement === 'first' ? existing : incoming
  const splitNode = makeSplit(axis, first, second, opts.ratio, nextSplitId())
  return replaceLeaf(tree, tabId, splitNode)
}

export interface MoveLeafOptions {
  axis?: string
  placement?: string
  ratio?: number
}

export function moveLeafRelativeToTarget(
  tree: WorkspacePaneNode | null,
  sourceTabId: string | null | undefined,
  targetTabId: string | null | undefined,
  options: MoveLeafOptions | null | undefined,
  nextSplitId: SplitIdFactory,
): WorkspacePaneNode | null {
  if (!sourceTabId || !targetTabId || sourceTabId === targetTabId) return tree
  if (!containsTab(tree, sourceTabId) || !containsTab(tree, targetTabId)) return tree
  const opts = options || {}
  const axis = opts.axis === 'top-bottom' ? 'top-bottom' : 'left-right'
  const placement = opts.placement === 'first' ? 'first' : 'second'
  const withoutSource = normalizeTree(removeLeaf(tree, sourceTabId))
  if (!containsTab(withoutSource, targetTabId)) return tree
  return splitLeaf(
    withoutSource,
    targetTabId,
    { axis, newTabId: sourceTabId, placement, ratio: opts.ratio },
    nextSplitId,
  )
}

export function removeLeaf(tree: WorkspacePaneNode | null, tabId: string | null | undefined): WorkspacePaneNode | null {
  if (!tree || !tabId) return tree
  const result = rewritePath(
    tree,
    (node) => isLeaf(node) && node.tabId === tabId,
    () => null,
  )
  return result.replaced ? result.tree : tree
}

export function findSiblingLeafTabId(tree: unknown, tabId: string): string | null {
  let sibling: string | null = null
  const walk = (node: unknown) => {
    if (sibling || !isSplit(node)) return
    if (isLeaf(node.first) && node.first.tabId === tabId) {
      sibling = collectLeafTabIds(node.second)[0] || null
      return
    }
    if (isLeaf(node.second) && node.second.tabId === tabId) {
      sibling = collectLeafTabIds(node.first)[0] || null
      return
    }
    walk(node.first)
    walk(node.second)
  }
  walk(tree)
  return sibling
}

export function setSplitRatio(
  tree: WorkspacePaneNode | null,
  splitId: string | null | undefined,
  ratio: unknown,
): WorkspacePaneNode | null {
  if (!splitId) return tree
  const result = rewritePath(
    tree,
    (node) => isSplit(node) && node.id === splitId,
    (node) => {
      const split = node as WorkspaceSplit
      return makeSplit(split.axis, split.first, split.second, clampNestedRatio(ratio), split.id)
    },
  )
  return result.tree
}

export function normalizeTree(tree: WorkspacePaneNode | null): WorkspacePaneNode | null {
  if (!tree) return null
  if (isLeaf(tree)) return tree
  if (!isSplit(tree)) return null
  const first = normalizeTree(tree.first)
  const second = normalizeTree(tree.second)
  if (!first && !second) return null
  if (!first) return second
  if (!second) return first
  if (first === tree.first && second === tree.second) return tree
  return makeSplit(tree.axis, first, second, tree.ratio, tree.id)
}

export interface TreeValidation {
  ok: boolean
  errors: string[]
}

export function validateTree(tree: unknown): TreeValidation {
  const errors: string[] = []
  if (tree == null) return { ok: true, errors }
  const seenTabIds: Record<string, true> = Object.create(null) as Record<string, true>
  const seenSplitIds: Record<string, true> = Object.create(null) as Record<string, true>

  const walk = (node: unknown, path: string) => {
    if (node == null) {
      errors.push('null node at ' + path)
      return
    }
    if (typeof node !== 'object') {
      errors.push('unknown node type ' + String(node) + ' at ' + path)
      return
    }
    const record = node as { type?: unknown; tabId?: unknown; id?: unknown; axis?: unknown; ratio?: unknown; first?: unknown; second?: unknown }
    if (record.type === 'leaf') {
      if (!record.tabId) {
        errors.push('leaf missing tabId at ' + path)
        return
      }
      const id = String(record.tabId)
      if (seenTabIds[id]) errors.push('duplicate tabId ' + id + ' at ' + path)
      seenTabIds[id] = true
      return
    }
    if (record.type === 'split') {
      if (!record.id) errors.push('split missing id at ' + path)
      else if (seenSplitIds[String(record.id)]) errors.push('duplicate split id ' + String(record.id) + ' at ' + path)
      else seenSplitIds[String(record.id)] = true
      if (!AXES[String(record.axis)]) errors.push('invalid axis ' + String(record.axis) + ' at ' + path)
      const ratio = record.ratio
      if (typeof ratio !== 'number' || !Number.isFinite(ratio) || ratio < 0 || ratio > 1) {
        errors.push('invalid ratio ' + String(ratio) + ' at ' + path)
      }
      if (!record.first || !record.second) {
        errors.push('split missing a child at ' + path)
        return
      }
      walk(record.first, path + '.first')
      walk(record.second, path + '.second')
      return
    }
    errors.push('unknown node type ' + String(record.type) + ' at ' + path)
  }

  walk(tree, 'root')
  return { ok: errors.length === 0, errors }
}

export function leafCount(tree: unknown): number {
  return collectLeafTabIds(tree).length
}

export type { SplitAxis }
