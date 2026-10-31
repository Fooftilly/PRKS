<template>
  <!-- Bootstrap fixture only. Not a product surface and not application state. -->
  <span data-prks-vue-bootstrap="ready" hidden></span>
  <Teleport v-if="perfHostReady" to="#prks-settings-perf-root">
    <PerformanceDiagnostics />
  </Teleport>
  <Teleport v-if="recoveryHostReady" to="#prks-settings-recovery-root">
    <EditorRecoveryDiagnostics />
  </Teleport>
  <CommandPalette />
  <ModalHost />
  <WorkspaceShell />
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import EditorRecoveryDiagnostics from './features/editor-recovery/EditorRecoveryDiagnostics.vue'
import PerformanceDiagnostics from './features/performance-diagnostics/PerformanceDiagnostics.vue'
import CommandPalette from './palette/CommandPalette.vue'
import ModalHost from './modal/ModalHost.vue'
import WorkspaceShell from './workspace-shell/WorkspaceShell.vue'

const PERF_HOST_ID = 'prks-settings-perf-root'
const perfHostReady = ref(false)
const RECOVERY_HOST_ID = 'prks-settings-recovery-root'
const recoveryHostReady = ref(false)

onMounted(() => {
  perfHostReady.value = document.getElementById(PERF_HOST_ID) != null
  recoveryHostReady.value = document.getElementById(RECOVERY_HOST_ID) != null
})
</script>
