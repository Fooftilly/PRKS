<script setup lang="ts">
import type { FolderTreeNode } from './tree'

const props = withDefaults(
  defineProps<{
    node: FolderTreeNode
    filtering?: boolean
    role?: 'treeitem' | 'listitem'
  }>(),
  {
    filtering: false,
    role: 'treeitem',
  },
)

const emit = defineEmits<{
  toggle: [folderId: string]
}>()

function icon(name: string, size?: number): string {
  if (typeof window.prksIcon !== 'function') return ''
  if (size == null) return window.prksIcon(name)
  return window.prksIcon(name, { size })
}

function href(id: string): string {
  return `#/folders/${encodeURIComponent(id)}`
}

function onToggle(event: Event): void {
  event.preventDefault()
  event.stopPropagation()
  emit('toggle', props.node.id)
}

const treeItem = props.role === 'treeitem'
</script>

<template>
  <div
    class="prks-folder-tree__row"
    :class="{
      'prks-folder-tree__row--match': node.match,
      'is-selected': node.selected,
    }"
    :data-folder-id="node.id"
    :role="role"
    :aria-expanded="treeItem ? (node.hasChildren && !node.collapsed ? 'true' : 'false') : undefined"
    :style="{ '--depth': node.depth }"
  >
    <button
      v-if="node.hasChildren && role === 'treeitem'"
      type="button"
      class="prks-folder-tree__toggle"
      :aria-expanded="node.collapsed ? 'false' : 'true'"
      :title="node.collapsed ? 'Expand subfolders' : 'Collapse subfolders'"
      @click="onToggle"
    >
      <span v-html="icon('chevronRight', 14)"></span>
    </button>
    <span v-else class="prks-folder-tree__toggle-spacer" aria-hidden="true"></span>
    <a
      class="prks-folder-tree__link"
      :href="href(node.id)"
      :aria-current="node.selected ? 'page' : undefined"
    >
      <span class="prks-folder-tree__icon" v-html="icon('folder')"></span>
      <span class="prks-folder-tree__title">{{ node.title || 'Folder' }}</span>
    </a>
    <span v-if="node.meta" class="prks-folder-tree__meta">{{ node.meta }}</span>
    <span v-else class="prks-folder-tree__meta" aria-hidden="true"></span>
  </div>
  <div
    v-if="node.hasChildren && role === 'treeitem'"
    class="prks-folder-tree__branch"
    :class="{ 'is-collapsed': node.collapsed }"
    :data-folder-id="node.id"
  >
    <div class="prks-folder-tree__branch-inner">
      <FolderTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :filtering="filtering"
        :role="role"
        @toggle="emit('toggle', $event)"
      />
    </div>
  </div>
</template>
