<script setup lang="ts">
import { inject } from 'vue'
import WorkspaceTab from './WorkspaceTab.vue'
import { workspaceProjectionKey } from './projection'

const projectionRef = inject(workspaceProjectionKey)
</script>

<template>
  <WorkspaceTab
    v-for="tab in projectionRef?.state.tabs || []"
    :key="tab.id"
    :tab="tab"
  />
</template>
