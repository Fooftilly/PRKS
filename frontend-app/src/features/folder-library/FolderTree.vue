<script setup lang="ts">
import { computed, onMounted, onUpdated, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import FolderTreeNode from './FolderTreeNode.vue'
import { buildFolderTree } from './tree'
import { folderTreeIconHost, refreshFolderIcons } from './tree-icons'
import type { FolderRow } from './types'

const props = withDefaults(
  defineProps<{
    folders: readonly FolderRow[]
    filterQuery?: string
    expandedIds?: ReadonlySet<string>
    selectedId?: string | null
    /** Detail hierarchy uses the nav label and has no create action. */
    variant?: 'library' | 'detail'
  }>(),
  {
    filterQuery: '',
    expandedIds: () => new Set<string>(),
    selectedId: null,
    variant: 'library',
  },
)

const emit = defineEmits<{
  toggle: [folderId: string]
  create: [query: string]
}>()

const iconRoot = ref<HTMLElement | null>(null)

function paintIcons(): void {
  refreshFolderIcons(folderTreeIconHost(iconRoot.value))
}

onMounted(paintIcons)
onUpdated(paintIcons)

const tree = computed(() =>
  buildFolderTree(props.folders, props.filterQuery, props.expandedIds, {
    selectedId: props.selectedId,
  }),
)

const filtering = computed(() => props.filterQuery.trim() !== '')
const createQuery = computed(() => props.filterQuery.trim())

function onCreate(): void {
  emit('create', createQuery.value)
}
</script>

<template>
  <div ref="iconRoot" style="display: contents">
  <div
    v-if="tree.empty && variant === 'library'"
    class="prks-folder-tree__empty-state"
  >
    <PrksInlineMessage class="prks-folder-tree__empty">No folders yet.</PrksInlineMessage>
    <PrksButton
      variant="primary"
      class="prks-folder-tree__create-btn"
      data-prks-create-folder-query=""
      @click="onCreate"
    >
      New folder
    </PrksButton>
  </div>
  <div v-else-if="tree.empty" class="prks-folder-tree__empty-state">
    <PrksInlineMessage class="prks-folder-tree__empty">No folders yet.</PrksInlineMessage>
  </div>
  <div
    v-else-if="tree.emptySearch && variant === 'library'"
    class="prks-folder-tree__empty-state"
  >
    <PrksInlineMessage class="prks-folder-tree__empty">No folders match your search.</PrksInlineMessage>
    <PrksButton
      variant="primary"
      class="prks-folder-tree__create-btn"
      :data-prks-create-folder-query="createQuery"
      @click="onCreate"
    >
      Create folder "{{ createQuery }}"
    </PrksButton>
  </div>
  <PrksInlineMessage v-else-if="tree.emptySearch" class="prks-folder-tree__empty">
    No folders match your search.
  </PrksInlineMessage>
  <div
    v-else
    class="prks-folder-tree"
    :class="{ 'prks-folder-tree--detail-nav': variant === 'detail' }"
    role="tree"
    :aria-label="variant === 'detail' ? 'Folder hierarchy' : undefined"
  >
    <FolderTreeNode
      v-for="node in tree.nodes"
      :key="node.id"
      :node="node"
      :filtering="filtering"
      @toggle="emit('toggle', $event)"
    />
  </div>
  </div>
</template>
