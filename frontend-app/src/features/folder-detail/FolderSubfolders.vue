<script setup lang="ts">
import { computed, onMounted, onUpdated, ref } from 'vue'
import FolderTreeNode from '../folder-library/FolderTreeNode.vue'
import { folderSubfolderNodes } from '../folder-library/tree'
import { refreshFolderIcons } from '../folder-library/tree-icons'

const props = defineProps<{
  children: readonly unknown[]
}>()

const listEl = ref<HTMLElement | null>(null)
const nodes = computed(() => folderSubfolderNodes(props.children))

function paintIcons(): void {
  refreshFolderIcons(listEl.value)
}

onMounted(paintIcons)
onUpdated(paintIcons)
</script>

<template>
  <template v-if="nodes.length">
    <div class="prks-page-header page-header"><h3>Subfolders</h3></div>
    <div ref="listEl" class="prks-folder-tree prks-folder-tree--detail-subfolders" role="list">
      <FolderTreeNode
        v-for="node in nodes"
        :key="node.id"
        :node="node"
        role="listitem"
      />
    </div>
  </template>
</template>
