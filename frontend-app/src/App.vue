<template>
  <!-- Bootstrap fixture only. Not a product surface and not application state. -->
  <span data-prks-vue-bootstrap="ready" hidden></span>
  <Teleport v-if="settingsHostReady" to="#prks-settings-root">
    <SettingsSurface />
  </Teleport>
  <CommandPalette />
  <ModalHost />
  <WorkspaceShell />
  <AppChrome />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import AppChrome from './app-shell/AppChrome.vue'
import SettingsSurface from './features/settings/SettingsSurface.vue'
import CommandPalette from './palette/CommandPalette.vue'
import ModalHost from './modal/ModalHost.vue'
import WorkspaceShell from './workspace-shell/WorkspaceShell.vue'

const settingsHostReady = ref(
  typeof document !== 'undefined' && document.getElementById('prks-settings-root') != null,
)
</script>
