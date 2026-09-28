import { describe, expect, it } from 'vitest'
import { buildGroupTree, buildPersonGroupDetailProjection, buildPersonGroupsIndexProjection, collapsibleGroupIds } from './projection'

const parent = { id: 'G1', name: 'Parent Branch', parent_id: '', member_count: 1, child_count: 1 }
const child = { id: 'G2', name: 'Child Branch', parent_id: 'G1', member_count: 0, child_count: 0 }
const renamed = { id: 'G3', name: 'Pending name', parent_id: '', member_count: 2, child_count: 0 }

describe('Person Group projections', () => {
  it('keeps an empty catalogue distinct from an unavailable one', () => {
    expect(buildPersonGroupsIndexProjection({ items: [] }).groups).toEqual([])
    expect(buildPersonGroupsIndexProjection({ items: [] }).availability).toBe('ready')
    expect(buildPersonGroupsIndexProjection({ availability: 'unavailable', items: null }).availability).toBe('unavailable')
  })

  it('projects hierarchy, pending rename, and member counts already overlaid', () => {
    const projection = buildPersonGroupsIndexProjection({ items: [child, renamed, parent] })
    expect(projection.groups.map((group) => group.name)).toEqual(['Child Branch', 'Pending name', 'Parent Branch'])
    expect(projection.groups[0]?.parentId).toBe('G1')
    expect(projection.groups[2]?.memberCount).toBe(1)
    expect(collapsibleGroupIds(projection.groups)).toEqual(['G1'])
  })

  it('search shows the match, its ancestors, and its descendants', () => {
    const groups = buildPersonGroupsIndexProjection({ items: [parent, child] }).groups
    const tree = buildGroupTree(groups, 'child', new Set())
    expect(tree.emptySearch).toBe(false)
    expect(tree.nodes.map((node) => node.name)).toEqual(['Parent Branch'])
    expect(tree.nodes[0]?.children.map((node) => node.name)).toEqual(['Child Branch'])
    expect(tree.nodes[0]?.children[0]?.match).toBe(true)
    expect(tree.nodes[0]?.collapsed).toBe(false)
  })

  it('search with no matches is not the empty catalogue', () => {
    const groups = buildPersonGroupsIndexProjection({ items: [parent] }).groups
    const tree = buildGroupTree(groups, 'missing', new Set())
    expect(tree.emptySearch).toBe(true)
    expect(tree.nodes).toEqual([])
  })

  it('collapse is explicit and expand-all covers every parent', () => {
    const groups = buildPersonGroupsIndexProjection({ items: [parent, child] }).groups
    const collapsed = buildGroupTree(groups, '', new Set())
    expect(collapsed.allCollapsed).toBe(true)
    expect(collapsed.nodes[0]?.collapsed).toBe(true)
    const open = buildGroupTree(groups, '', new Set(['G1']))
    expect(open.allCollapsed).toBe(false)
    expect(open.nodes[0]?.collapsed).toBe(false)
  })

  it('detail keeps parent, subgroups, members, and edit modes exclusive', () => {
    const projection = buildPersonGroupDetailProjection({
      group: {
        id: 'G1',
        name: 'Parent Branch',
        description: 'Group description',
        parent: null,
        children: [{ id: 'G2', name: 'Child Branch' }],
        members: [{ id: 'P1', first_name: 'Ada', last_name: 'Lovelace' }],
      },
      editing: true,
      membersEditing: true,
    })
    expect(projection.group?.description).toBe('Group description')
    expect(projection.group?.children[0]?.name).toBe('Child Branch')
    expect(projection.group?.members[0]?.firstName).toBe('Ada')
    expect(projection.editing).toBe(true)
    expect(projection.membersEditing).toBe(false)
  })

  it('unavailable detail does not invent a group', () => {
    const projection = buildPersonGroupDetailProjection({
      availability: 'unavailable',
      groupId: 'G9',
      group: { id: 'G9', name: 'Cached' },
    })
    expect(projection.availability).toBe('unavailable')
    expect(projection.group).toBeNull()
  })
})
