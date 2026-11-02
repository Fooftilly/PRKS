<script setup lang="ts">
import { onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { chromeKey, shellChromeFrom, type ShellChromeRoute, type ShellChromeSnapshot } from './chrome'
import SidebarNav from './SidebarNav.vue'
import TopRibbon from './TopRibbon.vue'

const props = defineProps<{
  snapshot?: ShellChromeSnapshot | null
}>()

const chrome = ref<ShellChromeSnapshot>(shellChromeFrom(props.snapshot))
const sidebarReady = ref(false)
const ribbonReady = ref(false)
let apply: ((snapshot: ShellChromeSnapshot) => void) | null = null

provide(chromeKey, chrome)

function assign(snapshot: Partial<ShellChromeSnapshot> | null | undefined): void {
  chrome.value = shellChromeFrom(snapshot)
}

function seedFromLocation(): void {
  if (typeof window.prksParseRoute !== 'function') return
  const route = window.prksParseRoute(window.location.hash || '#/folders') as ShellChromeRoute
  const origin = window.prksReadOriginForRoute?.(route) ?? null
  const href = window.prksSidebarHrefForRoute?.(route, origin) ?? null
  const progressStatus = route?.name === 'progress' ? route.params?.status ?? null : null
  assign({ route, activeHref: href, progressStatus })
}

watch(
  () => props.snapshot,
  (value) => {
    if (value) assign(value)
  },
)

onMounted(() => {
  sidebarReady.value = document.getElementById('sidebar') != null
  ribbonReady.value = document.getElementById('prks-top-ribbon') != null
  apply = (snapshot) => assign(snapshot)
  window.prksVueApplyShellChrome = apply
  if (props.snapshot) assign(props.snapshot)
  else if (window.__prksShellChrome) assign(window.__prksShellChrome)
  else seedFromLocation()
})

onUnmounted(() => {
  if (apply && window.prksVueApplyShellChrome === apply) delete window.prksVueApplyShellChrome
  apply = null
})
</script>

<template>
  <Teleport v-if="sidebarReady" to="#sidebar">
    <SidebarNav />
  </Teleport>
  <Teleport v-if="ribbonReady" to="#prks-top-ribbon">
    <TopRibbon />
  </Teleport>
</template>
