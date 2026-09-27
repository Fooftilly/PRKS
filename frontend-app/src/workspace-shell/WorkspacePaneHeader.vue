<script setup lang="ts">
import { computed, inject } from 'vue'
import WorkspaceIcon from './WorkspaceIcon.vue'
import { workspaceIntentsKey } from './intents'
import { workspaceProjectionKey } from './projection'

const props = defineProps<{
  tabId: string
}>()

const projectionRef = inject(workspaceProjectionKey)
const intents = inject(workspaceIntentsKey)

const tab = computed(() => projectionRef?.value?.state.tabs.find((item) => item.id === props.tabId) ?? null)
const isMain = computed(() => projectionRef?.value?.state.mainTabId === props.tabId)
const visualTiled = computed(() => !!projectionRef?.value?.visualTiled)
const title = computed(() => tab.value?.title || 'Page')
const icon = computed(() => tab.value?.icon || 'file-text')
const mainLabel = computed(() => 'Main pane: ' + title.value)

function onMenu(event: MouseEvent): void {
  event.preventDefault()
  event.stopPropagation()
  const anchor = event.currentTarget instanceof HTMLElement ? event.currentTarget : undefined
  intents?.openTabMenu(props.tabId, event, anchor)
}

function onClose(event: MouseEvent): void {
  event.preventDefault()
  event.stopPropagation()
  intents?.close(props.tabId)
}
</script>

<template>
  <header
    class="prks-tile-header"
    :hidden="visualTiled ? undefined : true"
    :aria-label="visualTiled && isMain ? mainLabel : undefined"
  >
    <template v-if="visualTiled">
      <button
        v-if="!isMain"
        type="button"
        class="prks-tile-header__grip"
        tabindex="-1"
        aria-hidden="true"
        title="Drag to move or park this pane"
      >
        <WorkspaceIcon name="grip-vertical" svg-class="prks-tile-header__icon-svg" />
      </button>
      <WorkspaceIcon class="prks-tile-header__icon" :name="icon" svg-class="prks-tile-header__icon-svg" />
      <span class="prks-tile-header__title">{{ title }}</span>
      <span v-if="!isMain" class="prks-tile-header__actions">
        <button
          type="button"
          class="prks-icon-btn prks-icon-btn--ghost prks-tile-header__menu"
          aria-label="Pane actions"
          title="Pane actions"
          aria-haspopup="menu"
          aria-expanded="false"
          @click="onMenu"
        >
          <WorkspaceIcon name="ellipsis" svg-class="prks-tile-header__icon-svg" />
        </button>
        <button
          type="button"
          class="prks-icon-btn prks-icon-btn--ghost prks-tile-header__close"
          aria-label="Close"
          title="Close"
          @click="onClose"
        >
          <WorkspaceIcon name="x" svg-class="prks-tile-header__icon-svg" />
        </button>
      </span>
    </template>
  </header>
</template>
