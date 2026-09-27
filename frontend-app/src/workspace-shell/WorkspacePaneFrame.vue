<script setup lang="ts">
import { computed, inject, onMounted, onUpdated, ref } from 'vue'
import WorkspacePaneHeader from './WorkspacePaneHeader.vue'
import { vOwnedClass } from './owned-class'
import { workspaceProjectionKey } from './projection'

const props = defineProps<{
  tabId: string
  secondaryRoot?: boolean
}>()

const HOST_MARKER = ' '

const projectionRef = inject(workspaceProjectionKey)
const slotRef = ref<HTMLElement | null>(null)

const isMain = computed(() => projectionRef?.value?.state.mainTabId === props.tabId)
const isFocused = computed(() => projectionRef?.value?.state.focusedTabId === props.tabId)
const visualTiled = computed(() => !!projectionRef?.value?.visualTiled)
const title = computed(
  () => projectionRef?.value?.state.tabs.find((item) => item.id === props.tabId)?.title || 'Page',
)
const mainLabel = computed(() => (visualTiled.value && isMain.value ? 'Main pane: ' + title.value : undefined))

const owned = computed(() => ({
  'prks-tile--main': isMain.value,
  'prks-tile--secondary': !isMain.value,
  'prks-tile--focused': isFocused.value,
}))

function placeHost(): void {
  const slot = slotRef.value
  if (!slot) return
  window.prksWorkspacePlaceContentHost?.(props.tabId, slot)
}

onMounted(placeHost)
onUpdated(placeHost)
</script>

<template>
  <section
    class="prks-tile"
    :data-prks-tab-id="tabId"
    :data-prks-secondary-root="secondaryRoot ? '1' : undefined"
    tabindex="-1"
    :aria-label="mainLabel"
    v-owned-class="owned"
  >
    <WorkspacePaneHeader :tab-id="tabId" />
    <!-- Constant v-html so Vue does not clear the stable content host on patch. -->
    <div ref="slotRef" class="prks-content-host-slot" v-html="HOST_MARKER"></div>
  </section>
</template>
