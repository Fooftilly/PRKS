import { describe, expect, it } from 'vitest'
import {
  buildFolderTree,
  folderDetailExpandIds,
  folderSubfolderNodes,
  folderTreeMetaLabel,
  normalizeGlanceParts,
} from './tree'
import type { FolderRow } from './types'

function row(
  id: string,
  title: string,
  parent: string | null,
  childCount = 0,
  workCount = 0,
): FolderRow {
  return { id, title, parent_id: parent, child_count: childCount, work_count: workCount }
}

const tree = [
  row('root', 'Root', null, 2, 1),
  row('child', 'Child', 'root', 1, 0),
  row('grand', 'Grand', 'child', 0, 3),
  row('other', 'Other', null, 0, 0),
]

describe('buildFolderTree', () => {
  it('indexes one parent walk and keeps siblings sorted', () => {
    const view = buildFolderTree(
      [row('b', 'Beta', null, 0), row('a', 'Alpha', null, 0), row('c', 'alpha', 'b', 0)],
      '',
      new Set(),
    )
    expect(view.nodes.map((node) => node.title)).toEqual(['Alpha', 'Beta'])
    expect(view.empty).toBe(false)
    expect(view.hasCollapsible).toBe(false)
  })

  it('collapses stored ids and opens a filter without scanning per node', () => {
    const closed = buildFolderTree(tree, '', new Set())
    const closedRoot = closed.nodes.find((node) => node.id === 'root')
    expect(closed.allCollapsed).toBe(true)
    expect(closedRoot?.collapsed).toBe(true)
    expect(closedRoot?.children[0]?.id).toBe('child')
    expect(closed.collapsibleIds).toEqual(['root', 'child'])

    const open = buildFolderTree(tree, '', new Set(['root']))
    const openRoot = open.nodes.find((node) => node.id === 'root')
    expect(open.allCollapsed).toBe(false)
    expect(openRoot?.collapsed).toBe(false)
    expect(openRoot?.children[0]?.collapsed).toBe(true)

    const filtered = buildFolderTree(tree, 'grand', new Set())
    expect(filtered.emptySearch).toBe(false)
    expect(filtered.nodes.map((node) => node.id)).toEqual(['root'])
    expect(filtered.nodes[0]?.match).toBe(false)
    expect(filtered.nodes[0]?.collapsed).toBe(false)
    expect(filtered.nodes[0]?.children[0]?.children[0]).toMatchObject({ id: 'grand', match: true })
    expect(filtered.allCollapsed).toBe(true)
    expect(buildFolderTree(tree, 'missing', new Set()).emptySearch).toBe(true)
  })

  it('marks the selected row and describes counts', () => {
    const view = buildFolderTree(tree, '', new Set(['root', 'child']), { selectedId: 'grand' })
    const root = view.nodes.find((node) => node.id === 'root')
    const grand = root?.children[0]?.children[0]
    expect(grand).toMatchObject({ selected: true, meta: '3 files' })
    expect(root?.meta).toBe('1 file · 2 subfolders')
    expect(folderTreeMetaLabel(1, 1)).toBe('1 file · 1 subfolder')
  })

  it('expands ancestors of the open folder once', () => {
    expect(folderDetailExpandIds('grand', tree)).toEqual(['child', 'root'])
    expect(folderDetailExpandIds('root', tree)).toEqual(['root'])
    expect(folderDetailExpandIds('other', tree)).toEqual([])
  })

  it('sorts flat subfolder rows and does not expand them', () => {
    const nodes = folderSubfolderNodes([
      { id: 'b', title: 'Beta', child_count: 2, work_count: 1 },
      { id: 'a', title: 'Alpha' },
    ])
    expect(nodes.map((node) => node.title)).toEqual(['Alpha', 'Beta'])
    expect(nodes[1]).toMatchObject({ hasChildren: false, meta: '1 file · 2 subfolders' })
  })

  it('keeps glance parts as plain text and same-app links', () => {
    expect(
      normalizeGlanceParts([
        '2 folders',
        null,
        { text: 'More recent', href: '#/recent' },
        { text: 'skip', href: 'https://example.test' },
        { html: '<b>no</b>' },
        { unknown: true, text: 'hidden' },
      ]),
    ).toEqual([
      { text: '2 folders' },
      { text: 'More recent', href: '#/recent' },
      { text: 'skip' },
    ])
  })
})
