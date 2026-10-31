import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import FolderTree from './FolderTree.vue'
import type { FolderRow } from './types'

const folders: FolderRow[] = [
  { id: 'parent', title: 'Parent', parent_id: null, child_count: 1, work_count: 0 },
  { id: 'child', title: 'Child', parent_id: 'parent', child_count: 0, work_count: 2 },
]

describe('FolderTree presentation', () => {
  it('renders collapsed rows from state and toggles one pane of ids', async () => {
    const expanded = new Set<string>()
    const toggled: string[] = []
    const wrapper = mount(FolderTree, {
      props: {
        folders,
        expandedIds: expanded,
        onToggle: (id: string) => toggled.push(id),
      },
    })
    const row = wrapper.get('.prks-folder-tree__row[data-folder-id="parent"]')
    expect(row.attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.prks-folder-tree__branch').classes()).toContain('is-collapsed')
    expect(wrapper.get('.prks-folder-tree__link').attributes('href')).toBe('#/folders/parent')
    await wrapper.get('.prks-folder-tree__toggle').trigger('click')
    expect(toggled).toEqual(['parent'])
    await wrapper.setProps({ expandedIds: new Set(['parent']) })
    await nextTick()
    expect(wrapper.get('.prks-folder-tree__row[data-folder-id="parent"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.prks-folder-tree__branch').classes()).not.toContain('is-collapsed')
  })

  it('offers a new folder when the catalogue and the search are empty', async () => {
    const created: string[] = []
    const wrapper = mount(FolderTree, {
      props: {
        folders: [],
        onCreate: (query: string) => created.push(query),
      },
    })
    expect(wrapper.text()).toContain('No folders yet.')
    await wrapper.get('[data-prks-create-folder-query]').trigger('click')
    expect(created).toEqual([''])
    await wrapper.setProps({ folders, filterQuery: 'zzz' })
    expect(wrapper.text()).toContain('Create folder "zzz"')
  })
})
