<script setup lang="ts">
import { onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import WorkspacePaneFrame from './WorkspacePaneFrame.vue'
import WorkspaceSplitter from './WorkspaceSplitter.vue'
import type { ProjectionNode, ProjectionSplit } from './types'

defineOptions({ name: 'WorkspaceTree' })

const props = defineProps<{
  node: ProjectionNode
  secondaryRoot?: boolean
}>()

const splitEl = ref<HTMLElement | null>(null)
const firstRef = ref<{ $el?: Node } | null>(null)
const secondRef = ref<{ $el?: Node } | null>(null)

function asElement(node: Node | null | undefined): HTMLElement | null {
  return node instanceof HTMLElement ? node : null
}

function childKey(node: ProjectionNode): string {
  return node.type === 'leaf' ? 'tab:' + node.tabId : 'split:' + node.id
}

function syncNested(): void {
  if (props.node.type !== 'split') return
  const container = splitEl.value
  const first = asElement(firstRef.value?.$el)
  const second = asElement(secondRef.value?.$el)
  if (!container) return
  window.prksWorkspaceWatchNestedSplit?.(props.node.id, container)
  if (first && second) {
    window.prksWorkspaceSyncNestedSeparator?.(container, props.node as ProjectionSplit, first, second)
  }
}

onMounted(syncNested)
onUpdated(syncNested)
onBeforeUnmount(() => {
  if (props.node.type === 'split') window.prksWorkspaceUnwatchNestedSplit?.(props.node.id)
})
</script>

<template>
  <WorkspacePaneFrame
    v-if="node.type === 'leaf'"
    :key="node.tabId"
    :tab-id="node.tabId"
    :secondary-root="secondaryRoot"
  />
  <div
    v-else
    :key="node.id"
    ref="splitEl"
    class="prks-workspace-split"
    :data-prks-split-id="node.id"
    :data-prks-axis="node.axis"
    :data-prks-secondary-root="secondaryRoot ? '1' : undefined"
  >
    <WorkspaceTree :key="childKey(node.first)" ref="firstRef" :node="node.first" />
    <WorkspaceSplitter :axis="node.axis" :split-id="node.id" />
    <WorkspaceTree :key="childKey(node.second)" ref="secondRef" :node="node.second" />
  </div>
</template>
