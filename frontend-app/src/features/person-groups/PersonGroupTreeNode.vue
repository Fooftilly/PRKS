<script setup lang="ts">
import { inject } from 'vue'
import { personGroupIntentsKey } from './intents'
import type { GroupTreeNode } from './projection'

defineProps<{
  node: GroupTreeNode
  filtering: boolean
}>()

const emit = defineEmits<{
  toggle: [groupId: string]
}>()

const intents = inject(personGroupIntentsKey)

function icon(name: string, size?: number | 'sm'): string {
  if (typeof window.prksIcon !== 'function') return ''
  if (size == null) return window.prksIcon(name)
  return window.prksIcon(name, { size: String(size) })
}

function href(id: string): string {
  return `#/people/groups/${encodeURIComponent(id)}`
}

function onToggle(event: Event, id: string): void {
  event.preventDefault()
  event.stopPropagation()
  emit('toggle', id)
}

function openGroup(event: MouseEvent, id: string): void {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
  event.preventDefault()
  intents?.openGroup(id)
}
</script>

<template>
  <div
    class="prks-group-tree__row"
    :class="{ 'prks-group-tree__row--match': node.match }"
    :data-group-id="node.id"
    role="treeitem"
    :aria-expanded="node.hasChildren ? (node.collapsed ? 'false' : 'true') : 'false'"
    :style="{ '--depth': node.depth }"
  >
    <button
      v-if="node.hasChildren && !filtering"
      type="button"
      class="prks-group-tree__toggle"
      :aria-expanded="node.collapsed ? 'false' : 'true'"
      :title="node.collapsed ? 'Expand subgroups' : 'Collapse subgroups'"
      @click="onToggle($event, node.id)"
    >
      <span v-html="icon('chevronRight', 14)"></span>
    </button>
    <span v-else class="prks-group-tree__toggle-spacer" aria-hidden="true"></span>
    <a class="prks-group-tree__link" :href="href(node.id)" @click="openGroup($event, node.id)">
      <span class="prks-group-tree__icon" v-html="icon('folders')"></span>
      <span class="prks-group-tree__title">{{ node.name || 'Group' }}</span>
    </a>
    <span v-if="node.meta" class="prks-group-tree__meta">{{ node.meta }}</span>
    <span v-else class="prks-group-tree__meta" aria-hidden="true"></span>
  </div>
  <div
    v-if="node.hasChildren"
    class="prks-group-tree__branch"
    :class="{ 'is-collapsed': node.collapsed }"
    :data-group-id="node.id"
  >
    <div class="prks-group-tree__branch-inner">
      <PersonGroupTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :filtering="filtering"
        @toggle="emit('toggle', $event)"
      />
    </div>
  </div>
</template>
