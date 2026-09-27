<script setup lang="ts">
import { computed, inject, onMounted, onUpdated, ref } from 'vue'
import WorkspacePaneFrame from './WorkspacePaneFrame.vue'
import WorkspaceSplitter from './WorkspaceSplitter.vue'
import WorkspaceTree from './WorkspaceTree.vue'
import { workspaceProjectionKey } from './projection'
import type { ProjectionNode } from './types'

function treeKey(node: ProjectionNode): string {
  return node.type === 'leaf' ? 'tab:' + node.tabId : 'split:' + node.id
}

const projectionRef = inject(workspaceProjectionKey)
const canvasRef = ref<HTMLElement | null>(null)

const projection = computed(() => projectionRef?.value ?? null)
const visualTiled = computed(() => !!projection.value?.visualTiled)
const mainTabId = computed(() => projection.value?.state.mainTabId ?? null)
const secondaryTree = computed(() =>
  visualTiled.value ? (projection.value?.state.secondaryTree ?? null) : null,
)

function releaseClosedHosts(): void {
  const current = projection.value
  if (!current) return
  const open = new Set(current.state.tabs.map((tab) => tab.id))
  const ids = window.prksWorkspaceContentHostIds?.() ?? []
  for (const id of ids) {
    if (!open.has(id)) window.prksWorkspaceReleaseContentHost?.(id)
  }
}

function syncCanvas(): void {
  const canvas = canvasRef.value
  const current = projection.value
  if (!canvas || !current) return
  window.prksWorkspaceWatchCanvas?.(canvas)
  window.prksSyncDenseWorkspaceShell?.(current.visualTiled)
  window.prksWorkspaceSyncSplitSeparator?.(canvas, current.visualTiled, current.state)
  window.prksWorkspaceAfterShellRender?.()
  releaseClosedHosts()
}

onMounted(syncCanvas)
onUpdated(syncCanvas)
</script>

<template>
  <div
    ref="canvasRef"
    class="prks-workspace-canvas"
    :class="visualTiled ? 'prks-workspace-canvas--tiled' : 'prks-workspace-canvas--stacked'"
    data-prks-workspace-shell="vue"
  >
    <WorkspacePaneFrame v-if="mainTabId" :key="mainTabId" :tab-id="mainTabId" />
    <WorkspaceSplitter v-if="secondaryTree" axis="left-right" root />
    <WorkspaceTree
      v-if="secondaryTree"
      :key="treeKey(secondaryTree)"
      :node="secondaryTree"
      secondary-root
    />
  </div>
</template>
