<script setup lang="ts">
import { computed } from 'vue'
import FolderTreeNode from '../folder-library/FolderTreeNode.vue'
import { folderSubfolderNodes } from '../folder-library/tree'

const props = defineProps<{
  children: readonly unknown[]
}>()

const nodes = computed(() => folderSubfolderNodes(props.children))
</script>

<template>
  <template v-if="nodes.length">
    <div class="prks-page-header page-header"><h3>Subfolders</h3></div>
    <div class="prks-folder-tree prks-folder-tree--detail-subfolders" role="list">
      <FolderTreeNode
        v-for="node in nodes"
        :key="node.id"
        :node="node"
        role="listitem"
      />
    </div>
  </template>
</template>
