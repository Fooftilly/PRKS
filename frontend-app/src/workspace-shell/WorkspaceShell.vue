<script setup lang="ts">
import { computed, onMounted, onUnmounted, onUpdated, provide, ref, shallowRef, watch } from 'vue'
import { bindWorkspaceDnd, type WorkspaceDndSession } from '../workspace-dnd'
import { snapshotFromProjection } from '../workspace-dnd/snapshot'
import WorkspaceCanvas from './WorkspaceCanvas.vue'
import WorkspaceTabStrip from './WorkspaceTabStrip.vue'
import { browserWorkspaceIntents, workspaceIntentsKey } from './intents'
import { workspaceProjectionKey } from './projection'
import type { WorkspaceIntents, WorkspaceProjection } from './types'
import { collectSplitIds, visiblePaneTabIds } from './walk'

const props = defineProps<{
  projection?: WorkspaceProjection | null
  intents?: WorkspaceIntents
}>()

const live = shallowRef<WorkspaceProjection | null>(props.projection ?? null)
const tabsReady = ref(false)
const pageReady = ref(false)
let unsubscribe = (): void => {}
let dndSession: WorkspaceDndSession | null = null
let priorInitDrag: (() => void) | undefined

const projectionRef = computed(() => (props.projection !== undefined ? props.projection : live.value))

provide(workspaceProjectionKey, projectionRef)
provide(
  workspaceIntentsKey,
  props.intents ?? browserWorkspaceIntents(),
)

watch(
  () => props.projection,
  (value) => {
    if (value !== undefined) live.value = value ?? null
  },
)

watch(projectionRef, (next, prev) => {
  if (!next || !prev) return
  const prevIds = new Set(visiblePaneTabIds(prev).concat(collectSplitIds(prev.state.secondaryTree)))
  const nextIds = new Set(visiblePaneTabIds(next).concat(collectSplitIds(next.state.secondaryTree)))
  let removed = false
  for (const id of prevIds) {
    if (!nextIds.has(id)) removed = true
  }
  if (removed) window.prksWorkspaceCancelActiveDrag?.()
})

function routeSupportsTile(route: string): boolean {
  return typeof window.prksRouteSupportsTile === 'function'
    ? window.prksRouteSupportsTile(route)
    : true
}

function ensureDndBound(): void {
  if (dndSession) {
    dndSession.reconcile()
    return
  }
  if (!tabsReady.value || !pageReady.value) return
  dndSession = bindWorkspaceDnd({
    getSnapshot: () => snapshotFromProjection(projectionRef.value),
    routeSupportsTile,
    observeDom: false,
  })
  priorInitDrag = window.prksWorkspaceInitDrag
  window.prksWorkspaceInitDrag = () => {
    ensureDndBound()
    dndSession?.reconcile()
  }
}

function signalShellCommit(): void {
  const current = projectionRef.value
  if (!current || !tabsReady.value || !pageReady.value) return
  window.prksWorkspaceOnShellCommit?.(current)
  // Prefer shell-commit reconcile over MutationObserver (exactly one sync path).
  ensureDndBound()
  dndSession?.reconcile()
}

onMounted(() => {
  tabsReady.value = document.getElementById('prks-workspace-tabs') != null
  pageReady.value = document.getElementById('page-content') != null
  window.__prksWorkspaceShellOwned = true
  if (props.projection !== undefined) {
    ensureDndBound()
    return
  }
  const subscribe = window.prksWorkspaceSubscribe
  if (typeof subscribe === 'function') {
    unsubscribe = subscribe((next) => {
      live.value = next
    })
  }
  ensureDndBound()
})

onUpdated(signalShellCommit)

onUnmounted(() => {
  unsubscribe()
  unsubscribe = () => {}
  dndSession?.destroy()
  dndSession = null
  if (window.prksWorkspaceInitDrag && priorInitDrag !== undefined) {
    window.prksWorkspaceInitDrag = priorInitDrag
  }
  priorInitDrag = undefined
})
</script>

<template>
  <Teleport v-if="tabsReady && projectionRef" to="#prks-workspace-tabs">
    <WorkspaceTabStrip />
  </Teleport>
  <Teleport v-if="pageReady && projectionRef" to="#page-content">
    <WorkspaceCanvas />
  </Teleport>
</template>
