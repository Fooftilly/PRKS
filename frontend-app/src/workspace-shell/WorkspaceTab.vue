<script setup lang="ts">
import { computed, inject } from 'vue'
import WorkspaceIcon from './WorkspaceIcon.vue'
import { workspaceIntentsKey } from './intents'
import { vOwnedClass } from './owned-class'
import { workspaceProjectionKey } from './projection'
import type { ProjectionTab } from './types'
import { collectLeafTabIds } from './walk'

const props = defineProps<{
  tab: ProjectionTab
}>()

const projectionRef = inject(workspaceProjectionKey)
const intents = inject(workspaceIntentsKey)

const flags = computed(() => {
  const projection = projectionRef?.value
  const state = projection?.state
  const leaves = projection?.visualTiled ? collectLeafTabIds(state?.secondaryTree) : []
  const isMain = !!state && props.tab.id === state.mainTabId
  const isTiled = !isMain && leaves.includes(props.tab.id)
  const isFocused = !!state && props.tab.id === state.focusedTabId
  return {
    isMain,
    isTiled,
    isFocused,
    isParked: !isMain && !isTiled,
  }
})

const status = computed(() => projectionRef?.value?.tabStatus?.[props.tab.id] || '')
const supportsTile = computed(() => {
  const fn = window.prksRouteSupportsTile
  return flags.value.isParked && (typeof fn === 'function' ? !!fn(props.tab.route) : false)
})

const owned = computed(() => ({
  'is-main': flags.value.isMain,
  'is-tiled': flags.value.isTiled,
  'is-focused': flags.value.isFocused,
  'is-parked': flags.value.isParked,
}))

function activate(): void {
  intents?.activate(props.tab.id)
}

function close(event: MouseEvent): void {
  event.preventDefault()
  event.stopPropagation()
  intents?.close(props.tab.id)
}

function tile(event: MouseEvent): void {
  event.preventDefault()
  event.stopPropagation()
  intents?.tile(props.tab.id)
}

function onKeydown(event: KeyboardEvent): void {
  window.prksWorkspaceTabKeydown?.(event)
}

function onContextMenu(event: MouseEvent): void {
  const target = event.target
  if (
    target instanceof Element &&
    target.closest('.prks-workspace-tab__close, .prks-workspace-tab__split')
  ) {
    return
  }
  event.preventDefault()
  intents?.openTabMenu(props.tab.id, event)
}

const statusLabel = computed(() => {
  if (status.value === 'error') return 'Save error'
  if (status.value === 'saving') return 'Saving'
  if (status.value === 'drafting') return 'Drafting'
  return ''
})
</script>

<template>
  <div
    class="prks-workspace-tab"
    :data-tab-id="tab.id"
    v-owned-class="owned"
    @contextmenu="onContextMenu"
  >
    <button
      type="button"
      class="prks-workspace-tab__activate"
      role="tab"
      :aria-selected="flags.isMain ? 'true' : 'false'"
      :tabindex="flags.isMain ? 0 : -1"
      :title="tab.title"
      :aria-busy="status === 'saving' ? 'true' : undefined"
      @click="activate"
      @keydown="onKeydown"
    >
      <WorkspaceIcon class="prks-workspace-tab__icon" :name="tab.icon || 'file-text'" svg-class="prks-workspace-tab__icon-svg" />
      <span class="prks-workspace-tab__title">{{ tab.title }}</span>
    </button>
    <span
      v-if="flags.isTiled"
      class="prks-workspace-tab__split-mark"
      aria-hidden="true"
    >
      <WorkspaceIcon name="columns-2" svg-class="prks-workspace-tab__icon-svg" />
    </span>
    <button
      v-if="supportsTile"
      type="button"
      class="prks-workspace-tab__split"
      :aria-label="'Open ' + tab.title + ' in split view'"
      title="Open in split view"
      :tabindex="flags.isMain ? 0 : -1"
      @click="tile"
    >
      <WorkspaceIcon name="columns-2" svg-class="prks-workspace-tab__icon-svg" />
    </button>
    <span
      v-if="status"
      class="prks-workspace-tab__status"
      :class="'prks-workspace-tab__status--' + status"
      :title="statusLabel"
      :aria-label="statusLabel"
    ></span>
    <button
      type="button"
      class="prks-workspace-tab__close"
      title="Close"
      :aria-label="'Close ' + tab.title"
      :tabindex="flags.isMain ? 0 : -1"
      @click="close"
    >
      <WorkspaceIcon name="x" svg-class="prks-workspace-tab__icon-svg" />
    </button>
  </div>
</template>
