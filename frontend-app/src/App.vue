<template>
  <!-- Bootstrap fixture only. Not a product surface and not application state. -->
  <span data-prks-vue-bootstrap="ready" hidden></span>
  <Teleport v-if="perfHostReady" to="#prks-settings-perf-root">
    <PerformanceDiagnostics />
  </Teleport>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import PerformanceDiagnostics from './features/performance-diagnostics/PerformanceDiagnostics.vue'

const PERF_HOST_ID = 'prks-settings-perf-root'
const perfHostReady = ref(false)

onMounted(() => {
  perfHostReady.value = document.getElementById(PERF_HOST_ID) != null
})
</script>
